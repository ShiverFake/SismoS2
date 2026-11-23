"""Model: áreas de controle — a mesma análise repetida em anos sem terremoto."""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from itertools import combinations
from typing import List, Optional, Sequence

import numpy as np
import rasterio
from PIL import Image

from app.models.analysis import Analysis, AnalysisRepository
from app.models.change_detection import CHANGE_CLASS_IDS, NODATA, remove_small_regions
from app.models.quality import anniversary_dates

COMPARISON_FILE = "control_comparison.json"
COMPARISON_VERSION = 4
OVERLAP_PNG = "control_overlap.png"
OVERLAP_COLOR = (34, 211, 238, 235)
CONTROL_MAX_CLOUD = 80
DAY_PENALTY = 0.01


# ------------------------------------------------------------ criar controles
def event_gap_days(event: Analysis) -> int:
    """Intervalo (dias) entre as imagens antes e depois do evento (média das datas se for mediana)."""
    def mean_date(meta: dict) -> datetime:
        ts = [datetime.fromisoformat(s["datetime"].replace("Z", "+00:00")).timestamp()
              for s in meta.get("scenes") or [meta]]
        return datetime.fromtimestamp(sum(ts) / len(ts))
    r = event.result or {}
    return max(1, round((mean_date(r["after"]) - mean_date(r["before"])).total_seconds() / 86400))


def shift_to_year(iso: str, year: int) -> str:
    d = date.fromisoformat(iso)
    try:
        return d.replace(year=year).isoformat()
    except ValueError:
        return d.replace(year=year, day=28).isoformat()


def build_year_control(event: Analysis, year: int, min_valid: float = 0.0) -> Analysis:
    """Controle de um ano: procura o melhor par de cenas na mesma janela do calendário do evento (do início do período 'antes' ao fim do período 'depois'; em Antakya, 1/jan a 15/mar), no mesmo tile e de preferência na mesma órbita."""
    r = event.result or {}
    window = {"start": shift_to_year(event.before_start, year), "end": shift_to_year(event.after_end, year),
              "target_gap_days": event_gap_days(event),
              "relative_orbit": (r.get("before") or {}).get("relative_orbit"), "min_valid": min_valid}
    return Analysis.new(
        name=f"{event.name} – controle {year}",
        bbox=list(event.bbox),
        before_start=window["start"], before_end=window["end"],
        after_start=window["start"], after_end=window["end"],
        max_cloud=CONTROL_MAX_CLOUD, threshold_method=event.threshold_method, k=event.k,
        min_region_px=event.min_region_px, median_composite=False,
        topo_correction=event.topo_correction and "topo" in r,
        event_id=event.event_id, control_of=event.id, control_year=year,
        preferred_tile=(r.get("before") or {}).get("tile"), pair_window=window,
    )


def build_control(event: Analysis) -> Analysis:
    """Controle simples (versão anterior): os dois períodos deslocados 1 ano para trás."""
    before = anniversary_dates(event.before_start, event.before_end)
    after = anniversary_dates(event.after_start, event.after_end)
    return Analysis.new(
        name=f"{event.name} – controle (ano anterior)",
        bbox=list(event.bbox),
        before_start=before["before_start"], before_end=before["before_end"],
        after_start=after["before_start"], after_end=after["before_end"],
        max_cloud=event.max_cloud, threshold_method=event.threshold_method, k=event.k,
        min_region_px=event.min_region_px, median_composite=event.median_composite,
        topo_correction=event.topo_correction and "topo" in (event.result or {}),
        event_id=event.event_id, control_of=event.id,
        preferred_tile=((event.result or {}).get("before") or {}).get("tile"),
    )


def choose_control_pair(dates: Sequence[date], clears: Sequence[np.ndarray], target_gap_days: int,
                        day_penalty: float = DAY_PENALTY, min_common_valid: float = 0.0) -> Optional[dict]:
    """Escolhe o par (antes, depois) com intervalo mais parecido com o do evento e mais área válida."""
    best = None
    order = sorted(range(len(dates)), key=lambda i: dates[i])
    for i, j in combinations(order, 2):
        gap = (dates[j] - dates[i]).days
        if gap <= 0:
            continue
        common = float((clears[i] & clears[j]).mean())
        if common < min_common_valid:
            continue
        score = common - day_penalty * abs(gap - target_gap_days)
        if best is None or score > best["score"]:
            best = {"before": i, "after": j, "gap_days": gap, "common_valid": round(common, 4),
                    "score": round(score, 4)}
    return best


def run_control_series(event: Analysis, run_year, target: int, start_year: int, min_year: int,
                       existing_ok_years=()) -> dict:
    """Roda controles ano a ano, para trás, até ter `target` controles válidos."""
    ok_years = sorted(set(existing_ok_years), reverse=True)
    tried = []
    for year in range(start_year, min_year - 1, -1):
        if len(ok_years) >= target:
            break
        if year in ok_years:
            continue
        control = run_year(year)
        tried.append({"year": year, "id": control.id, "status": control.status, "reason": control.error})
        if control.status == "done":
            ok_years.append(year)
    return {"ok_years": sorted(ok_years, reverse=True), "tried": tried, "complete": len(ok_years) >= target}


# ------------------------------------------------------------- comparação
def _pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 3) if d else 0.0


def magnitude_summary(values: np.ndarray) -> dict:
    v = values[np.isfinite(values)]
    if not v.size:
        return {"mean": None, "median": None, "p90": None, "p95": None}
    return {"mean": round(float(v.mean()), 5), "median": round(float(np.median(v)), 5),
            "p90": round(float(np.percentile(v, 90)), 5), "p95": round(float(np.percentile(v, 95)), 5)}


def mean_std_z(event_value: float, control_values: List[float]) -> dict:
    """Média ± desvio dos controles e z do evento = (evento − média) / desvio (precisa de ≥ 2 controles)."""
    n = len(control_values)
    mean = float(np.mean(control_values)) if n else None
    std = float(np.std(control_values, ddof=1)) if n >= 2 else None
    z = (event_value - mean) / std if std not in (None, 0.0) else None
    return {"event": event_value, "controls_mean": None if mean is None else round(mean, 3),
            "controls_std": None if std is None else round(std, 3), "n_controls": n,
            "z": None if z is None else round(z, 2)}


def multi_control_stats(event: dict, controls: List[dict], urban: Optional[np.ndarray],
                        pixel_area_m2: float, min_region_px: int) -> dict:
    """Compara o evento com N controles."""
    common = event["classes"] != NODATA
    for c in controls:
        common &= c["classes"] != NODATA
    urban_common = (urban & common) if urban is not None else None
    n_common = int(common.sum())
    n_urban = int(urban_common.sum()) if urban_common is not None else 0
    t_common = float(np.mean([c["threshold"] for c in controls]))

    def above(layer, threshold):
        m = (np.nan_to_num(layer["magnitude"], nan=0.0) > threshold) & (layer["classes"] != NODATA)
        return remove_small_regions(m, min_region_px) & common

    def measures(layer):
        own = np.isin(layer["classes"], CHANGE_CLASS_IDS) & common
        com = above(layer, t_common)
        row = {"label": layer["label"], "threshold": round(float(layer["threshold"]), 5),
               "own_total_percent": _pct(int(own.sum()), n_common),
               "common_total_percent": _pct(int(com.sum()), n_common)}
        if urban_common is not None:
            row["own_urban_percent"] = _pct(int((own & urban_common).sum()), n_urban)
            row["common_urban_percent"] = _pct(int((com & urban_common).sum()), n_urban)
            row["urban_magnitude"] = magnitude_summary(layer["magnitude"][urban_common])
        for k in ("dates", "gap_days", "id", "year"):
            if k in layer:
                row[k] = layer[k]
        return row, own

    ev_row, ev_own = measures(event)
    rows, any_ctl = [], np.zeros_like(common)
    for c in controls:
        row, own = measures(c)
        any_ctl |= own
        ev_at = above(event, c["threshold"])
        row["event_at_this_threshold_total_percent"] = _pct(int(ev_at.sum()), n_common)
        if urban_common is not None:
            row["event_at_this_threshold_urban_percent"] = _pct(int((ev_at & urban_common).sum()), n_urban)
        rows.append(row)

    metrics = ["own_total_percent", "common_total_percent"]
    if urban_common is not None:
        metrics += ["own_urban_percent", "common_urban_percent"]
    aggregates = {m: mean_std_z(ev_row[m], [r[m] for r in rows]) for m in metrics}
    if urban_common is not None:
        for q in ("median", "p90", "p95"):
            vals = [r["urban_magnitude"][q] for r in rows if r["urban_magnitude"][q] is not None]
            if ev_row["urban_magnitude"][q] is not None and vals:
                aggregates[f"urban_magnitude_{q}"] = mean_std_z(ev_row["urban_magnitude"][q], vals)

    both_any = ev_own & any_ctl
    km2 = pixel_area_m2 / 1e6
    return {
        "common_threshold": round(t_common, 5),
        "common_valid_km2": round(n_common * km2, 3),
        "urban_common_km2": round(n_urban * km2, 3) if urban_common is not None else None,
        "event": ev_row,
        "controls": rows,
        "aggregates": aggregates,
        "both_changed_km2": round(int(both_any.sum()) * km2, 4),
        "both_percent_of_event_change": _pct(int(both_any.sum()), int(ev_own.sum())),
        "_both": both_any,
    }


def overlap_stats(event_classes: np.ndarray, control_classes: np.ndarray, pixel_area_m2: float,
                  urban: Optional[np.ndarray] = None) -> dict:
    """Comparação par a par (um controle), com o limiar próprio de cada análise."""
    common = (event_classes != NODATA) & (control_classes != NODATA)
    ev = np.isin(event_classes, CHANGE_CLASS_IDS) & common
    ct = np.isin(control_classes, CHANGE_CLASS_IDS) & common
    both = ev & ct
    n_common, n_ev = int(common.sum()), int(ev.sum())
    km2 = pixel_area_m2 / 1e6
    out_urban = {}
    if urban is not None:
        u = urban & common
        n_u = int(u.sum())
        out_urban = {"urban_common_km2": round(n_u * km2, 3),
                     "event_urban_changed_percent": _pct(int((ev & u).sum()), n_u),
                     "control_urban_changed_percent": _pct(int((ct & u).sum()), n_u)}
    return {
        **out_urban,
        "common_valid_km2": round(n_common * km2, 3),
        "event_changed_km2": round(n_ev * km2, 4),
        "event_changed_percent": _pct(n_ev, n_common),
        "control_changed_km2": round(int(ct.sum()) * km2, 4),
        "control_changed_percent": _pct(int(ct.sum()), n_common),
        "both_changed_km2": round(int(both.sum()) * km2, 4),
        "both_percent_of_event_change": _pct(int(both.sum()), n_ev),
        "event_only_km2": round(int((ev & ~ct).sum()) * km2, 4),
        "excess_percent_points": round(_pct(n_ev, n_common) - _pct(int(ct.sum()), n_common), 3),
        "_both": both,
    }


# ------------------------------------------------------- leitura e cache
def find_controls(repo: AnalysisRepository, event: Analysis) -> List[Analysis]:
    ids = list(dict.fromkeys(list(event.control_ids or []) + ([event.control_id] if event.control_id else [])))
    found = [repo.get(i) for i in ids]
    return sorted([a for a in found if a], key=lambda a: (a.control_year or 0, a.created_at))


def _layer(repo: AnalysisRepository, a: Analysis, label: str) -> dict:
    folder = repo.directory(a.id)
    with rasterio.open(os.path.join(folder, "change_classes.tif")) as src:
        classes = src.read(1)
    with rasterio.open(os.path.join(folder, "magnitude.tif")) as src:
        magnitude = src.read(1)
    r = a.result
    return {"label": label, "id": a.id, "classes": classes, "magnitude": magnitude,
            "threshold": float(r["statistics"]["cva_threshold"]),
            "dates": [r["before"]["datetime"][:10], r["after"]["datetime"][:10]],
            "gap_days": event_gap_days(a), "year": a.control_year}


def noise_median(layer: dict, urban: Optional[np.ndarray]) -> Optional[float]:
    """Magnitude CVA mediana nos pixels válidos da própria análise (só urbanos, se houver WorldCover)."""
    valid = layer["classes"] != NODATA
    if urban is not None and (urban & valid).any():
        valid = valid & urban
    v = layer["magnitude"][valid]
    v = v[np.isfinite(v)]
    return round(float(np.median(v)), 5) if v.size else None


def compare_controls(repo: AnalysisRepository, event: Analysis, controls: List[Analysis]) -> dict:
    """Comparação evento × controles concluídos (com cache) e camada 'mudou no evento e em algum controle'."""
    done = [c for c in controls if c.status == "done"]
    if not done:
        raise ValueError("Nenhum controle concluído ainda.")
    out_dir = repo.directory(event.id)
    key = [[c.id, c.finished_at, (c.control_excluded or {}).get("reason")] for c in done]
    cached = os.path.join(out_dir, COMPARISON_FILE)
    if os.path.exists(cached):
        with open(cached, encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("version") == COMPARISON_VERSION and data.get("key") == key:
            return data

    ev = _layer(repo, event, "Evento")
    layers = {c.id: _layer(repo, c, f"Controle {c.control_year}" if c.control_year else "Controle") for c in done}
    if any(l["classes"].shape != ev["classes"].shape for l in layers.values()):
        raise ValueError("As grades do evento e dos controles são diferentes.")
    urban = None
    npz_path = os.path.join(out_dir, "bands.npz")
    if os.path.exists(npz_path):
        npz = np.load(npz_path)
        urban = npz["urban"] if "urban" in npz.files else None

    noise = {"event": noise_median(ev, urban), **{cid: noise_median(l, urban) for cid, l in layers.items()}}
    status = []
    for c in done:
        r = c.result
        others = [v for k, v in noise.items() if k != c.id and v is not None]
        flags = control_flags(r["before"].get("cloud_cover"), r["after"].get("cloud_cover"),
                              r["statistics"].get("valid_fraction"), noise[c.id], others)
        status.append({"id": c.id, "year": c.control_year, "label": layers[c.id]["label"],
                       "flags": flags, "noise_median": noise[c.id],
                       "excluded": c.control_excluded})

    included = [layers[c.id] for c in done if not c.control_excluded]
    if not included:
        raise ValueError("Todos os controles estão fora da média — inclua pelo menos um de volta.")
    data = multi_control_stats(ev, included, urban, float(event.result["grid"]["ground_resolution_m"]) ** 2,
                               event.min_region_px)
    rgba = np.zeros(ev["classes"].shape + (4,), dtype=np.uint8)
    rgba[data.pop("_both")] = OVERLAP_COLOR
    Image.fromarray(rgba).save(os.path.join(out_dir, OVERLAP_PNG), optimize=True)
    data.update(version=COMPARISON_VERSION, key=key, event_gap_days=ev["gap_days"], controls_status=status,
                event_noise_median=noise["event"], n_included=len(included), n_done=len(done))
    with open(cached, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    return data


# ------------------------------------------------------- controle suspeito
FLAG_TILE_CLOUD = 50.0
FLAG_MIN_VALID = 0.60
FLAG_NOISE_RATIO = 1.5


def control_flags(cloud_before: Optional[float], cloud_after: Optional[float], valid_fraction: Optional[float],
                  noise: Optional[float], others_noise: List[float]) -> List[dict]:
    """Sinais de que um controle pode não representar um "ano normal" (só alerta; não exclui sozinho)."""
    flags = []
    clouds = [c for c in (cloud_before, cloud_after) if c is not None]
    if clouds and max(clouds) > FLAG_TILE_CLOUD:
        flags.append({"code": "nuvem_tile",
                      "message": f"uma das cenas tem {max(clouds):.0f}% de nuvem no tile (> {FLAG_TILE_CLOUD:.0f}%): "
                                 "névoa fina pode passar pela máscara SCL"})
    if valid_fraction is not None and valid_fraction < FLAG_MIN_VALID:
        flags.append({"code": "pouca_area",
                      "message": f"só {100 * valid_fraction:.0f}% do retângulo válido nas duas datas "
                                 f"(< {100 * FLAG_MIN_VALID:.0f}%)"})
    if noise is not None and others_noise:
        ref = float(np.median(others_noise))
        if ref > 0 and noise > FLAG_NOISE_RATIO * ref:
            flags.append({"code": "ruido_alto",
                          "message": f"magnitude mediana {noise:.3f} = {noise / ref:.1f}× a das outras análises "
                                     f"({ref:.3f}): ruído alto para um ano sem terremoto"})
    return flags
