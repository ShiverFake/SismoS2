"""Model: validação do mapa de mudanças contra dados oficiais de dano (avaliação por edificação)."""
from __future__ import annotations

import csv
import json
import logging
import math
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import shapely
from affine import Affine
from rasterio.warp import transform as warp_transform
from scipy import ndimage
from shapely.geometry import box

from app.models.analysis import Analysis, AnalysisRepository
from app.models.change_detection import BANDS, CHANGE_CLASS_IDS, ChangeParams, detect_changes
from app.models.grid_eval import (CELL_SIZES_M, DAMAGED_CELL_SHARE, DEFAULT_CELL_M, DEFAULT_SYSTEM_THRESHOLD,
                                   MIN_BUILDINGS, MIN_VALID_SHARE, area_mask, build_cells, cells_geojson,
                                   grid_metrics, plot_grid)
from app.models.control import _layer as control_layer
from app.models.control import find_controls
from app.models.grid_controls import compare_grid, plot_rocs
from app.models.water import load_water
from app.models.validation_stats import (bootstrap_ci, distance_to_nearest_destroyed, neighborhood_table,
                                          plot_neighborhood)
from app.models.reference import (GRADE_LABELS, SEVERITY, ReferenceData, ReferenceDataError,
                                  fetch_osm_buildings, load_upload, source_kind, source_label)

log = logging.getLogger(__name__)

MATCH_TOLERANCE_M = 10.0
METHODS: Sequence[Tuple[str, Optional[float]]] = (
    ("kstd", 1.0), ("kstd", 1.5), ("kstd", 2.0), ("kstd", 2.5), ("kstd", 3.0), ("otsu", None))
TARGETS = {
    "todas": (CHANGE_CLASS_IDS, "Todas as mudanças"),
    "entulho": ((2,), "Só solo exposto/entulho"),
}


# ------------------------------------------------------------------ métricas
def confusion_metrics(tp: int, fp: int, fn: int, tn: int) -> dict:
    n = tp + fp + fn + tn
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    oa = (tp + tn) / n if n else 0.0
    pe = ((tp + fp) * (tp + fn) + (fn + tn) * (fp + tn)) / (n * n) if n else 0.0
    kappa = (oa - pe) / (1 - pe) if pe < 1 else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "n": n,
            "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
            "overall_accuracy": round(oa, 4), "kappa": round(kappa, 4)}


# ------------------------------------------------------------- unidades
@dataclass
class Units:
    lon: np.ndarray
    lat: np.ndarray
    radius_m: np.ndarray
    grade: np.ndarray
    origin: np.ndarray
    ref_index: Optional[np.ndarray] = None

    def __len__(self) -> int:
        return len(self.lon)

    def subset(self, mask: np.ndarray) -> "Units":
        idx = self.ref_index[mask] if self.ref_index is not None else None
        return Units(*(getattr(self, f)[mask] for f in ("lon", "lat", "radius_m", "grade", "origin")), idx)


def _equivalent_radius_m(poly) -> float:
    lat = math.radians(poly.centroid.y)
    area_m2 = poly.area * (111_320 * math.cos(lat)) * 110_540
    return math.sqrt(area_m2 / math.pi)


def match_reference(ref: ReferenceData, buildings: List, tol_m: float = MATCH_TOLERANCE_M) -> List[List[int]]:
    """Para cada feição da referência, os prédios do OSM ligados a ela."""
    tol_deg = tol_m / 111_320
    tree = shapely.STRtree(buildings) if buildings else None
    out = []
    for feat in ref.features:
        geom, idx = feat.geometry, []
        if tree is not None:
            probe = geom.buffer(tol_deg) if geom.geom_type in ("Point", "MultiPoint") else geom
            idx = list(tree.query(probe, predicate="intersects"))
            if idx and geom.geom_type in ("Point", "MultiPoint"):
                idx = [min(idx, key=lambda i: buildings[i].distance(geom))]
        out.append(idx)
    return out


def _worse(a: str, b: str) -> str:
    return a if SEVERITY[a] >= SEVERITY[b] else b


def build_units(ref: ReferenceData, buildings: List, area,
                assignment: Optional[List[List[int]]] = None) -> Tuple[Units, dict]:
    """Modo "prédio OSM": cada prédio do OSM é uma unidade, com o grau mais grave dos pontos ligados a ele; feições sem prédio a até 10 m entram como unidades pontuais."""
    assignment = assignment if assignment is not None else match_reference(ref, buildings)
    grades = ["none"] * len(buildings)
    extra, matched = [], 0
    for feat, idx in zip(ref.features, assignment):
        if idx:
            matched += 1
            for i in idx:
                grades[i] = _worse(grades[i], feat.grade)
        else:
            extra.append(feat)

    lon, lat, radius, grade, origin = [], [], [], [], []
    for poly, g in zip(buildings, grades):
        c = poly.centroid
        lon.append(c.x); lat.append(c.y); radius.append(_equivalent_radius_m(poly))
        grade.append(g); origin.append("osm")
    for feat in extra:
        c = feat.geometry.centroid
        r = 0.0 if feat.geometry.geom_type in ("Point", "MultiPoint") else _equivalent_radius_m(feat.geometry)
        lon.append(c.x); lat.append(c.y); radius.append(r); grade.append(feat.grade); origin.append("referencia")

    units = Units(np.array(lon, float), np.array(lat, float), np.array(radius, float),
                  np.array(grade, dtype=object), np.array(origin, dtype=object))
    if len(units):
        units = units.subset(shapely.contains_xy(area, units.lon, units.lat))
    info = {"reference_features": len(ref.features), "matched_to_osm": matched,
            "unmatched_added_as_points": len(extra), "osm_buildings": len(buildings)}
    return units, info


def build_point_units(ref: ReferenceData, buildings: List, area,
                      assignment: Optional[List[List[int]]] = None) -> Units:
    """Modo "ponto do GRA": cada feição da referência é uma unidade própria (sem fundir pontos no mesmo prédio e sem depender do OSM); o mapa de mudança é amostrado num buffer em volta do ponto."""
    assignment = assignment if assignment is not None else match_reference(ref, buildings)
    used = {i for idx in assignment for i in idx}
    lon, lat, radius, grade, origin, ref_idx = [], [], [], [], [], []
    for j, feat in enumerate(ref.features):
        c = feat.geometry.centroid
        lon.append(c.x); lat.append(c.y); grade.append(feat.grade); origin.append("referencia"); ref_idx.append(j)
        radius.append(0.0 if feat.geometry.geom_type in ("Point", "MultiPoint") else _equivalent_radius_m(feat.geometry))
    for i, poly in enumerate(buildings):
        if i in used:
            continue
        c = poly.centroid
        lon.append(c.x); lat.append(c.y); radius.append(_equivalent_radius_m(poly)); grade.append("none")
        origin.append("osm"); ref_idx.append(-1)
    units = Units(np.array(lon, float), np.array(lat, float), np.array(radius, float),
                  np.array(grade, dtype=object), np.array(origin, dtype=object), np.array(ref_idx, int))
    return units.subset(shapely.contains_xy(area, units.lon, units.lat)) if len(units) else units


DIST_BUCKETS = ((20, "10–20 m"), (50, "20–50 m"), (100, "50–100 m"), (float("inf"), "> 100 m"))


def link_diagnostics(ref: ReferenceData, buildings: List, assignment: List[List[int]], area,
                     pixel_ok: np.ndarray) -> dict:
    """Por classe do GRA: quantas feições ligaram a um prédio do OSM, quantas não tinham prédio a ≤ 10 m (e a que distância está o prédio mais próximo), quantas foram fundidas num prédio que já tinha outra feição, e quantas caíram fora da área comum ou em pixel inválido."""
    per_building: Dict[int, List[int]] = {}
    for j, idx in enumerate(assignment):
        for i in idx:
            per_building.setdefault(i, []).append(j)
    absorbed = set()
    for js in per_building.values():
        if len(js) > 1:
            keep = max(js, key=lambda j: SEVERITY[ref.features[j].grade])
            absorbed |= {j for j in js if j != keep}
    tree = shapely.STRtree(buildings) if buildings else None
    out = {}
    for g in ("destroyed", "damaged", "possibly", "none"):
        js = [j for j, f in enumerate(ref.features) if f.grade == g]
        if not js:
            continue
        in_area = [j for j in js if area.contains(ref.features[j].geometry.centroid)]
        unmatched = [j for j in js if not assignment[j]]
        dist = {label: 0 for _, label in DIST_BUCKETS}
        for j in unmatched:
            if tree is None:
                dist["> 100 m"] += 1
                continue
            geom = ref.features[j].geometry
            d_m = buildings[tree.nearest(geom)].distance(geom) * 111_320 * math.cos(math.radians(geom.centroid.y))
            dist[next(label for limit, label in DIST_BUCKETS if d_m <= limit)] += 1
        out[g] = {
            "label": GRADE_LABELS[g], "total": len(js),
            "ligados_osm": len(js) - len(unmatched),
            "sem_predio_osm_10m": len(unmatched),
            "distancia_predio_mais_proximo": dist,
            "fundidos_em_predio_compartilhado": sum(1 for j in js if j in absorbed),
            "fora_da_area_comum": len(js) - len(in_area),
            "em_pixel_invalido": sum(1 for j in in_area if not pixel_ok[j]),
        }
    return out


BOOTSTRAP_N = 1000
CONTROLS_ROC_PNG = "validation_grid_controls_roc.png"
GRID_FILES = {s: (f"validation_grid_{s}.geojson", f"validation_grid_{s}.png") for s in CELL_SIZES_M}
NEIGHBORHOOD_PNG = "validation_neighborhood.png"

POSSIBLE_MODES = {"excluir": "(a) excluídos da avaliação", "dano": "(b) contados como dano",
                  "sem_dano": "(c) contados como sem dano"}
UNIT_MODES = {"osm": "Prédio do OSM", "ponto": "Ponto do GRA"}


def locate(units: Units, transform: Affine, shape: Tuple[int, int]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    if not len(units):
        empty = np.array([], int)
        return empty, empty, np.array([], bool)
    xs, ys = warp_transform("EPSG:4326", "EPSG:3857", units.lon.tolist(), units.lat.tolist())
    inv = ~transform
    xs, ys = np.asarray(xs), np.asarray(ys)
    cols = np.floor(inv.a * xs + inv.b * ys + inv.c).astype(int)
    rows = np.floor(inv.d * xs + inv.e * ys + inv.f).astype(int)
    inside = (rows >= 0) & (rows < shape[0]) & (cols >= 0) & (cols < shape[1])
    return rows.clip(0, shape[0] - 1), cols.clip(0, shape[1] - 1), inside


def labels_for(grades: np.ndarray, possible="excluir") -> np.ndarray:
    """1 = dano, 0 = sem dano, −1 = fora da avaliação."""
    if possible is True:
        possible = "dano"
    elif possible is False or possible is None:
        possible = "excluir"
    if possible not in POSSIBLE_MODES:
        raise ValueError(f"Tratamento inválido para 'possivelmente danificado': {possible}")
    lab = np.zeros(len(grades), dtype=np.int8)
    lab[np.isin(grades, ["destroyed", "damaged"])] = 1
    lab[grades == "possibly"] = {"excluir": -1, "dano": 1, "sem_dano": 0}[possible]
    return lab


def evaluate_variant(detected: np.ndarray, grades: np.ndarray, pixel_ok: np.ndarray, possible: str) -> dict:
    """Métricas de uma combinação (modo de unidade × tratamento do 'possivelmente') + recall por classe."""
    labels = labels_for(grades, possible)
    ev = pixel_ok & (labels >= 0)
    d, t = detected[ev], labels[ev] == 1
    m = confusion_metrics(int((d & t).sum()), int((d & ~t).sum()), int((~d & t).sum()), int((~d & ~t).sum()))
    recall_by_class = {}
    for g in ("destroyed", "damaged", "possibly"):
        sel = pixel_ok & (grades == g)
        n = int(sel.sum())
        recall_by_class[g] = {"n": n, "detected": int(detected[sel].sum()),
                              "rate": round(float(detected[sel].mean()), 4) if n else None}
    false_alarm = pixel_ok & (grades == "none")
    return {**m, "n_positive": int(t.sum()), "n_negative": int((~t).sum()),
            "recall_by_class": recall_by_class,
            "false_alarm_rate": round(float(detected[false_alarm].mean()), 4) if false_alarm.any() else None}


def detect_units(pred: np.ndarray, rows, cols, radius_m, buffer_m: float, res_m: float) -> np.ndarray:
    if not pred.any() or not len(rows):
        return np.zeros(len(rows), bool)
    dist_px = ndimage.distance_transform_edt(~pred)
    return dist_px[rows, cols] * res_m <= radius_m + buffer_m


# --------------------------------------------------------------- serviço
class ValidationService:
    FILE = "validation.json"
    _lock = threading.Lock()

    def __init__(self, repository: AnalysisRepository, reference_dir: str, overpass_url: str, ems=None):
        self.repository = repository
        self.reference_dir = reference_dir
        self.overpass_url = overpass_url
        self.ems = ems

    # --- persistência do estado da validação (arquivo próprio, ao lado da análise)
    def path(self, analysis_id: str) -> str:
        return os.path.join(self.repository.directory(analysis_id), self.FILE)

    def load(self, analysis_id: str) -> Optional[dict]:
        p = self.path(analysis_id)
        if not os.path.exists(p):
            return None
        with self._lock, open(p, encoding="utf-8") as fh:
            return json.load(fh)

    def _save(self, analysis_id: str, state: dict) -> None:
        p = self.path(analysis_id)
        with self._lock:
            with open(p + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(state, fh, ensure_ascii=False, indent=1)
            os.replace(p + ".tmp", p)

    def start(self, analysis: Analysis, spec: dict, upload: Optional[Tuple[str, bytes]] = None) -> None:
        state = {"status": "processing", "progress": 0, "message": "Na fila", "params": spec,
                 "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        self._save(analysis.id, state)
        threading.Thread(target=self.run, args=(analysis, spec, upload, state), daemon=True).start()

    def run(self, analysis: Analysis, spec: dict, upload, state: dict) -> None:
        def step(p: int, msg: str) -> None:
            state.update(progress=p, message=msg)
            self._save(analysis.id, state)
            log.info("[validação %s] %d%% %s", analysis.id, p, msg)

        try:
            state.update(self._run(analysis, spec, upload, step))
            state.update(status="done", progress=100, message="Concluído")
        except Exception as exc:  # noqa: BLE001
            log.exception("Falha na validação de %s", analysis.id)
            state.update(status="error", error=str(exc) if isinstance(exc, (ReferenceDataError, ValueError))
                         else f"{type(exc).__name__}: {exc}")
        state["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._save(analysis.id, state)

    def _run(self, analysis: Analysis, spec: dict, upload, step) -> dict:
        out_dir = self.repository.directory(analysis.id)
        bands_path = os.path.join(out_dir, "bands.npz")
        result = analysis.result or {}
        if not os.path.exists(bands_path) or "transform" not in result.get("grid", {}):
            raise ValueError("Esta análise foi feita numa versão anterior do sistema (sem as bandas salvas). "
                             "Crie a análise novamente para poder validá-la.")
        buffer_m = float(spec.get("buffer_m", 10.0))
        possible = spec.get("possible_mode") or ("dano" if spec.get("include_possible") else "excluir")
        unit_mode = spec.get("unit_mode", "osm")
        if possible not in POSSIBLE_MODES or unit_mode not in UNIT_MODES:
            raise ValueError("Modo de validação inválido.")

        # 1) referência
        step(5, "Carregando dados de referência…")
        if spec["source"] == "ems":
            if self.ems is None:
                raise ReferenceDataError("Copernicus EMS não configurado.")
            ref = self.ems.read_grading(spec["activation"], int(spec["aoi"]))
        else:
            ref = load_upload(*upload)
        area = ref.aoi.intersection(box(*analysis.bbox))
        if area.is_empty or area.area < 1e-8:
            raise ReferenceDataError("A área da referência não se sobrepõe à área desta análise. "
                                     f"(AOI da referência: {[round(v, 4) for v in ref.aoi.bounds]})")

        # 2) prédios do OSM
        step(15, "Baixando edificações do OpenStreetMap…")
        buildings = fetch_osm_buildings(area.bounds, self.reference_dir, self.overpass_url)
        step(30, f"Ligando {len(ref.features)} feições da referência a {len(buildings)} prédios do OSM…")
        assignment = match_reference(ref, buildings)
        units_osm, link_info = build_units(ref, buildings, area, assignment)
        units_pt = build_point_units(ref, buildings, area, assignment)
        units = units_osm if unit_mode == "osm" else units_pt
        if not len(units):
            raise ReferenceDataError("Nenhuma edificação encontrada na área comum.")

        # 3) bandas salvas
        step(40, "Carregando bandas da análise…")
        npz = np.load(bands_path)
        before = {b: npz[f"before_{b}"].astype(np.float32) for b in BANDS}
        after = {b: npz[f"after_{b}"].astype(np.float32) for b in BANDS}
        valid = npz["valid"]
        water = load_water(out_dir, valid.shape)
        urban = npz["urban"] if "urban" in npz.files else None
        transform = Affine(*result["grid"]["transform"])
        res_m = float(result["grid"]["ground_resolution_m"])
        rows, cols, inside = locate(units, transform, valid.shape)
        labels = labels_for(units.grade, possible)
        evaluable = inside & valid[rows, cols] & (labels >= 0)
        if not evaluable.any():
            raise ReferenceDataError("Nenhuma edificação avaliável (todas fora da área ou sob nuvem).")
        y_true = labels[evaluable] == 1

        # 4) experimentos
        current = (analysis.threshold_method, None if analysis.threshold_method == "otsu" else float(analysis.k))
        methods = list(METHODS) + ([current] if current not in METHODS else [])
        rows_out, current_detect = [], None
        for i, (method, k) in enumerate(methods):
            step(45 + int(45 * i / len(methods)), f"Avaliando {self._method_label(method, k)}…")
            params = ChangeParams(threshold_method=method, k=k or 2.0, min_region_px=analysis.min_region_px)
            res = detect_changes(before, after, valid, params, urban=urban)
            for target, (classes, target_label) in TARGETS.items():
                pred = np.isin(res.classes, classes)
                detected = detect_units(pred, rows, cols, units.radius_m, buffer_m, res_m)
                d = detected[evaluable]
                m = confusion_metrics(int((d & y_true).sum()), int((d & ~y_true).sum()),
                                      int((~d & y_true).sum()), int((~d & ~y_true).sum()))
                is_current = (method, k) == current and target == "todas"
                rows_out.append({"method": method, "k": k, "method_label": self._method_label(method, k),
                                 "target": target, "target_label": target_label,
                                 "threshold": round(float(res.threshold), 5),
                                 "changed_percent": round(100 * float(pred[valid].mean()), 3),
                                 "is_current": is_current, **m})
                if is_current:
                    current_detect, current_pred, current_magnitude = detected, pred, res.magnitude

        # 5) análise de sensibilidade
        step(90, "Análise de sensibilidade…")
        sensitivity = []
        for mode, mode_units in (("osm", units_osm), ("ponto", units_pt)):
            r_, c_, in_ = locate(mode_units, transform, valid.shape)
            ok = in_ & valid[r_, c_]
            det = detect_units(current_pred, r_, c_, mode_units.radius_m, buffer_m, res_m)
            for variant in POSSIBLE_MODES:
                sensitivity.append({"unit_mode": mode, "unit_label": UNIT_MODES[mode], "possible_mode": variant,
                                    "possible_label": POSSIBLE_MODES[variant],
                                    "is_selected": mode == unit_mode and variant == possible,
                                    **evaluate_variant(det, mode_units.grade, ok, variant)})
        feat_units = units_pt.subset(units_pt.ref_index >= 0) if len(units_pt) else units_pt
        pixel_ok = np.zeros(len(ref.features), bool)
        if len(feat_units):
            r_, c_, in_ = locate(feat_units, transform, valid.shape)
            pixel_ok[feat_units.ref_index] = in_ & valid[r_, c_]
        diagnostics = link_diagnostics(ref, buildings, assignment, area, pixel_ok)

        step(91, "Bootstrap (1000 reamostragens) e efeito de vizinhança…")
        ok_sel = inside & valid[rows, cols]
        confidence = bootstrap_ci(current_detect[ok_sel], labels[ok_sel], units.grade[ok_sel],
                                  n_boot=BOOTSTRAP_N, seed=0)
        dist_dest = distance_to_nearest_destroyed(units.lon[ok_sel], units.lat[ok_sel], units.grade[ok_sel])
        neighborhood = neighborhood_table(dist_dest, current_detect[ok_sel], units.grade[ok_sel])
        plot_neighborhood(neighborhood, os.path.join(out_dir, NEIGHBORHOOD_PNG),
                          f"{source_label(ref.source)}, unidade: {UNIT_MODES[unit_mode]}")

        step(91, "Avaliação por quadrícula (50, 100 e 200 m)…")
        grid_threshold = float(spec.get("grid_threshold", DEFAULT_SYSTEM_THRESHOLD))
        in_area = area_mask(area, transform, valid.shape)
        g_rows, g_cols, g_inside = locate(units_osm, transform, valid.shape)
        g_labels = labels_for(units_osm.grade, "excluir")
        grid_out = {}
        for size in CELL_SIZES_M:
            cell_px = max(1, int(round(size / res_m)))
            cells = build_cells(cell_px, in_area, valid, current_pred, current_magnitude,
                                g_rows[g_inside], g_cols[g_inside], g_labels[g_inside],
                                (units_osm.origin == "osm")[g_inside], water)
            gm = grid_metrics(cells, system_threshold=grid_threshold)
            gm.update(cell_m=size, cell_px=cell_px, geojson=GRID_FILES[size][0], plot=GRID_FILES[size][1])
            with open(os.path.join(out_dir, GRID_FILES[size][0]), "w", encoding="utf-8") as fh:
                json.dump(cells_geojson(cells, cell_px, transform), fh)
            if "error" not in gm:
                plot_grid(cells, gm, os.path.join(out_dir, GRID_FILES[size][1]),
                          f"{source_label(ref.source)}, células de {size} m ({gm['n_cells']} células)")
            gm["cells_scores"] = {"change": [round(float(x), 5) for x in cells["change_share"]],
                                  "damaged": [bool(x) for x in cells["damage_share"] >= DAMAGED_CELL_SHARE]}
            grid_out[str(size)] = gm

        grid_controls = None
        controls = [c for c in find_controls(self.repository, analysis)
                    if c.status == "done" and not c.control_excluded]
        if controls:
            step(91, f"Grade: evento × {len(controls)} controles (DeLong)…")
            layers = [control_layer(self.repository, analysis, "Evento")]
            layers += [control_layer(self.repository, c, f"Controle {c.control_year}" if c.control_year else "Controle")
                       for c in controls]
            if all(l["classes"].shape == layers[0]["classes"].shape for l in layers):
                grid_controls = compare_grid(layers, in_area, g_rows[g_inside], g_cols[g_inside], g_labels[g_inside],
                                             (units_osm.origin == "osm")[g_inside], res_m, water=water)
                grid_controls["controls"] = [{"id": c.id, "year": c.control_year,
                                              "dates": [c.result["before"]["datetime"][:10], c.result["after"]["datetime"][:10]],
                                              "aot": [c.result["before"].get("aot"), c.result["after"].get("aot")]}
                                             for c in controls]
                b200 = grid_controls["sizes"].get("200", {})
                if b200 and "error" not in b200:
                    plot_rocs(b200, os.path.join(out_dir, CONTROLS_ROC_PNG),
                              f"Evento × controles, células de 200 m ({b200['n_cells']} células)")
                    grid_controls["roc_plot"] = CONTROLS_ROC_PNG

        # 6) saídas
        step(92, "Gravando resultados…")
        best = max(rows_out, key=lambda r: (r["f1"], r["kappa"]))
        current_row = next(r for r in rows_out if r["is_current"])
        prevalence = float(y_true.mean())
        urban_block = None
        npz_urban = npz["urban"] if "urban" in npz.files else None
        if npz_urban is not None:
            in_urban = npz_urban[rows, cols]
            sel = in_urban[evaluable]
            d, t = current_detect[evaluable][sel], y_true[sel]
            urban_block = {**confusion_metrics(int((d & t).sum()), int((d & ~t).sum()),
                                               int((~d & t).sum()), int((~d & ~t).sum())),
                           "n_buildings": int(sel.sum()), "n_positive": int(t.sum()),
                           "share_of_evaluated": round(float(sel.mean()), 4)}
        by_grade = {}
        for g in ("destroyed", "damaged", "possibly", "none"):
            sel = inside & valid[rows, cols] & (units.grade == g)
            n = int(sel.sum())
            by_grade[g] = {"label": GRADE_LABELS[g], "n": n,
                           "detected": int(current_detect[sel].sum()) if n else 0,
                           "rate": round(float(current_detect[sel].mean()), 4) if n else None}

        self._write_csv(os.path.join(out_dir, "validation.csv"), rows_out)
        self._write_units(os.path.join(out_dir, "validation_units.geojson"),
                          units, labels, current_detect, inside & valid[rows, cols])
        notes = list(ref.notes)
        if link_info["unmatched_added_as_points"]:
            notes.append(f"{link_info['unmatched_added_as_points']} feições da referência não tinham prédio "
                         "correspondente no OSM e foram incluídas como edificações pontuais.")
        notes.append("Prédios do OSM sem registro na referência são considerados 'sem dano' — a referência "
                     "mapeia apenas edificações afetadas dentro da área avaliada.")
        counts = ref.grade_counts()
        if counts["destroyed"] and not (counts["damaged"] or counts["possibly"]):
            notes.append("A referência só registra prédios DESTRUÍDOS: prédios danificados (não destruídos) entram "
                         "como 'sem dano', o que subestima a precisão.")
        counts = ref.grade_counts()
        return {
            "source": ref.source,
            "source_kind": source_kind(ref.source),
            "source_label": source_label(ref.source),
            "source_short": source_label(ref.source, short=True),
            "reference_counts": counts,
            "reference_raw_values": ref.raw_values,
            "evaluated_area": ref.area_breakdown,
            "negatives_source": ("referência" if counts["none"] else "OpenStreetMap (prédios sem registro na referência)"),
            "negatives_by_origin": {
                "osm_sem_registro": int((evaluable & (labels == 0) & (units.origin == "osm")).sum()),
                "pontos_da_referencia": int((evaluable & (labels == 0) & (units.origin == "referencia")).sum())},
            "link": link_info,
            "area_bounds": [round(v, 5) for v in area.bounds],
            "n_units": int(len(units)),
            "n_evaluable": int(evaluable.sum()),
            "n_positive": int(y_true.sum()),
            "n_negative": int((~y_true).sum()),
            "prevalence": round(prevalence, 4),
            "all_positive_baseline": confusion_metrics(int(y_true.sum()), int((~y_true).sum()), 0, 0),
            "buffer_m": buffer_m,
            "unit_mode": unit_mode,
            "unit_label": UNIT_MODES[unit_mode],
            "possible_mode": possible,
            "possible_label": POSSIBLE_MODES[possible],
            "include_possible": possible == "dano",
            "sensitivity": sensitivity,
            "grid_controls": grid_controls,
            "grid": {"default_size": DEFAULT_CELL_M, "threshold": grid_threshold, "sizes": grid_out,
                     "rules": {"min_buildings": MIN_BUILDINGS, "min_valid_share": MIN_VALID_SHARE,
                               "damaged_cell_share": DAMAGED_CELL_SHARE}},
            "confidence_intervals": {"n_boot": BOOTSTRAP_N, "level": 0.95, "metrics": confidence},
            "neighborhood": {"rows": neighborhood, "plot": NEIGHBORHOOD_PNG,
                             "note": "distância entre centroides até o prédio 'Destroyed' mais próximo (sem contar ele mesmo)"},
            "link_diagnostics": diagnostics,
            "current": current_row,
            "best": best,
            "by_grade": by_grade,
            "urban": urban_block,
            "experiments": rows_out,
            "notes": notes,
        }

    @staticmethod
    def _method_label(method: str, k: Optional[float]) -> str:
        return "Otsu" if method == "otsu" else f"μ + {k:g}σ"

    @staticmethod
    def _write_csv(path: str, rows: List[dict]) -> None:
        cols = ["method_label", "target_label", "threshold", "changed_percent", "tp", "fp", "fn", "tn",
                "precision", "recall", "f1", "overall_accuracy", "kappa", "is_current"]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

    @staticmethod
    def _write_units(path: str, units: Units, labels, detected, evaluable) -> None:
        feats = []
        for i in range(len(units)):
            if not evaluable[i]:
                continue
            if labels[i] == 1:
                outcome = "tp" if detected[i] else "fn"
            elif labels[i] == 0:
                outcome = "fp" if detected[i] else "tn"
            else:
                outcome = "ignored"
            feats.append({"type": "Feature",
                          "geometry": {"type": "Point", "coordinates": [round(units.lon[i], 6), round(units.lat[i], 6)]},
                          "properties": {"grade": units.grade[i], "outcome": outcome, "origin": units.origin[i]}})
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"type": "FeatureCollection", "features": feats}, fh)
