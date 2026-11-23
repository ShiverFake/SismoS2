"""Model: avaliação por quadrícula (grade regular em metros) — a escala em que equipes de campo atuam."""
from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np
from rasterio.features import geometry_mask
from rasterio.warp import transform as warp_transform
from rasterio.warp import transform_geom
from scipy import stats
from shapely.geometry import mapping

from app.models.water import water_cells

CELL_SIZES_M = (50, 100, 200)
DEFAULT_CELL_M = 100
MIN_BUILDINGS = 3
MIN_VALID_SHARE = 0.5
DAMAGED_CELL_SHARE = 0.20
DEFAULT_SYSTEM_THRESHOLD = 0.10
TRIAGE_FRACTIONS = (0.10, 0.20, 0.30)


# ----------------------------------------------------------- montagem das células
def area_mask(area, transform, shape) -> np.ndarray:
    """Pixels da grade dentro da área avaliada (polígono lon/lat → EPSG:3857)."""
    geom = transform_geom("EPSG:4326", "EPSG:3857", mapping(area))
    return geometry_mask([geom], out_shape=shape, transform=transform, invert=True)


def build_cells(cell_px: int, in_area: np.ndarray, valid: np.ndarray, changed: np.ndarray,
                magnitude: np.ndarray, unit_rows: np.ndarray, unit_cols: np.ndarray,
                unit_labels: np.ndarray, unit_is_osm: np.ndarray, water: np.ndarray = None) -> Dict[str, np.ndarray]:
    """Agrega pixels e prédios por célula de cell_px × cell_px pixels."""
    h, w = in_area.shape
    ncols = math.ceil(w / cell_px)
    rr, cc = np.indices((h, w))
    cell_of_px = (rr // cell_px) * ncols + (cc // cell_px)
    n_cells = math.ceil(h / cell_px) * ncols

    px_area = np.bincount(cell_of_px[in_area], minlength=n_cells)
    px_valid = np.bincount(cell_of_px[in_area & valid], minlength=n_cells)
    px_changed = np.bincount(cell_of_px[in_area & valid & changed], minlength=n_cells)
    mag_sum = np.bincount(cell_of_px[in_area & valid], weights=np.nan_to_num(magnitude[in_area & valid]),
                          minlength=n_cells)

    cell_of_unit = (unit_rows // cell_px) * ncols + (unit_cols // cell_px)
    rot = unit_labels >= 0
    n_osm = np.bincount(cell_of_unit[unit_is_osm], minlength=n_cells)
    n_labeled = np.bincount(cell_of_unit[rot], minlength=n_cells)
    n_damaged = np.bincount(cell_of_unit[unit_labels == 1], minlength=n_cells)

    with np.errstate(divide="ignore", invalid="ignore"):
        valid_share = np.where(px_area > 0, px_valid / px_area, 0.0)
        keep = (n_osm >= MIN_BUILDINGS) & (n_labeled > 0) & (valid_share >= MIN_VALID_SHARE)
        keep &= ~water_cells(water, cell_of_px, n_cells, base=in_area)
        ids = np.flatnonzero(keep)
        return {
            "id": ids, "row": ids // ncols, "col": ids % ncols,
            "n_buildings": n_labeled[ids], "n_osm": n_osm[ids], "n_damaged": n_damaged[ids],
            "damage_share": n_damaged[ids] / n_labeled[ids],
            "change_share": px_changed[ids] / px_valid[ids],
            "magnitude_mean": mag_sum[ids] / px_valid[ids],
            "valid_share": valid_share[ids],
        }


# ------------------------------------------------------------------- métricas
def auc_score(scores: np.ndarray, positive: np.ndarray) -> Optional[float]:
    """AUC = probabilidade de uma célula danificada ter pontuação maior que uma não danificada (Mann-Whitney; empates contam meio)."""
    pos, neg = scores[positive], scores[~positive]
    if not pos.size or not neg.size:
        return None
    ranks = stats.rankdata(np.concatenate([pos, neg]))
    u = ranks[:pos.size].sum() - pos.size * (pos.size + 1) / 2
    return float(u / (pos.size * neg.size))


def roc_curve(scores: np.ndarray, positive: np.ndarray, max_points: int = 60) -> List[List[float]]:
    """Pontos (taxa de falso positivo, taxa de verdadeiro positivo) para limiares decrescentes."""
    order = np.argsort(-scores, kind="mergesort")
    s, p = scores[order], positive[order]
    tps, fps = np.cumsum(p), np.cumsum(~p)
    last = np.r_[np.flatnonzero(np.diff(s)), len(s) - 1]
    tpr, fpr = tps[last] / max(p.sum(), 1), fps[last] / max((~p).sum(), 1)
    pts = np.column_stack([np.r_[0, fpr], np.r_[0, tpr]])
    if len(pts) > max_points:
        pts = pts[np.linspace(0, len(pts) - 1, max_points).astype(int)]
    return [[round(float(a), 4), round(float(b), 4)] for a, b in pts]


def expected_capture(change: np.ndarray, n_damaged: np.ndarray, k: float) -> float:
    """Prédios com dano esperados nas k células de maior % de mudança."""
    values, inverse = np.unique(-change, return_inverse=True)
    sizes = np.bincount(inverse)
    dmg = np.bincount(inverse, weights=n_damaged)
    got, left = 0.0, float(k)
    for size, d in zip(sizes, dmg):
        if left <= 0:
            break
        take = min(size, left)
        got += d * take / size
        left -= take
    return got


def triage_curve(change: np.ndarray, n_damaged: np.ndarray, steps: int = 50) -> Dict[str, list]:
    """Visitando as células em ordem decrescente de % de mudança: fração dos prédios com dano alcançados."""
    total = max(int(n_damaged.sum()), 1)
    n = len(change)
    by_oracle = np.cumsum(np.sort(n_damaged)[::-1]) / total
    xs = np.linspace(0, 1, steps + 1)
    k = np.clip(np.round(xs * n).astype(int), 0, n)
    system = [round(expected_capture(change, n_damaged, i) / total, 4) for i in k]
    oracle = [0.0 if i == 0 else round(float(by_oracle[i - 1]), 4) for i in k]
    return {"x": [round(float(x), 4) for x in xs], "system": system, "oracle": oracle,
            "random": [round(float(x), 4) for x in xs]}


def triage_table(change: np.ndarray, n_damaged: np.ndarray, fractions=TRIAGE_FRACTIONS) -> List[dict]:
    total = int(n_damaged.sum())
    oracle = np.sort(n_damaged)[::-1]
    rows = []
    for f in fractions:
        k = int(math.ceil(f * len(change)))
        got = expected_capture(change, n_damaged, k)
        n_tied = int((change == np.sort(change)[::-1][k - 1]).sum()) if k else 0
        rows.append({"fraction_cells": f, "n_cells": k, "damaged_buildings": round(got, 1),
                     "cells_tied_at_cutoff": n_tied,
                     "share_of_damaged": round(got / total, 4) if total else None,
                     "random_expected": f, "oracle_share": round(int(oracle[:k].sum()) / total, 4) if total else None,
                     "lift": round((got / total) / f, 2) if total and f else None})
    return rows


def grid_metrics(cells: Dict[str, np.ndarray], system_threshold: float = DEFAULT_SYSTEM_THRESHOLD,
                 damaged_share: float = DAMAGED_CELL_SHARE, n_boot: int = 1000, seed: int = 0) -> dict:
    change, damage = cells["change_share"], cells["damage_share"]
    n = len(change)
    out = {"n_cells": n, "n_damaged_cells": int((damage >= damaged_share).sum()),
           "damaged_cell_share": damaged_share, "system_threshold": system_threshold,
           "n_buildings": int(cells["n_buildings"].sum()), "n_damaged_buildings": int(cells["n_damaged"].sum())}
    if n < 5:
        out["error"] = "Menos de 5 células válidas — área pequena demais para este tamanho de célula."
        return out
    positive = damage >= damaged_share

    rho = stats.spearmanr(damage, change)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_boot, n))
    boot_rho, boot_auc = [], []
    for i in idx:
        d, c, p = damage[i], change[i], positive[i]
        if np.ptp(d) > 0 and np.ptp(c) > 0:
            boot_rho.append(stats.spearmanr(d, c).statistic)
        a = auc_score(c, p)
        if a is not None:
            boot_auc.append(a)
    ci = lambda v: [round(float(np.quantile(v, 0.025)), 4), round(float(np.quantile(v, 0.975)), 4)] if v else [None, None]  # noqa: E731
    out["spearman"] = {"rho": round(float(rho.statistic), 4), "p_value": float(rho.pvalue), "ci95": ci(boot_rho)}
    auc = auc_score(change, positive)
    out["auc"] = {"value": None if auc is None else round(auc, 4), "ci95": ci(boot_auc)}
    out["roc"] = roc_curve(change, positive) if positive.any() and (~positive).any() else []

    pred = change >= system_threshold
    tp, fp = int((pred & positive).sum()), int((pred & ~positive).sum())
    fn, tn = int((~pred & positive).sum()), int((~pred & ~positive).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    out["at_threshold"] = {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": round(precision, 4),
                           "recall": round(recall, 4), "f1": round(f1, 4),
                           "cells_flagged_share": round(float(pred.mean()), 4)}
    out["triage"] = triage_table(change, cells["n_damaged"])
    out["triage_curve"] = triage_curve(change, cells["n_damaged"])
    return out


# --------------------------------------------------------------- saídas (mapa e gráfico)
def cells_geojson(cells: Dict[str, np.ndarray], cell_px: int, transform, damaged_share: float = DAMAGED_CELL_SHARE) -> dict:
    """Células como polígonos lon/lat, com as métricas nas propriedades (camada do Leaflet)."""
    feats = []
    for i in range(len(cells["id"])):
        r0, c0 = int(cells["row"][i]) * cell_px, int(cells["col"][i]) * cell_px
        xs, ys = [], []
        for rr, cc in ((r0, c0), (r0, c0 + cell_px), (r0 + cell_px, c0 + cell_px), (r0 + cell_px, c0)):
            xs.append(transform.a * cc + transform.b * rr + transform.c)
            ys.append(transform.d * cc + transform.e * rr + transform.f)
        lons, lats = warp_transform("EPSG:3857", "EPSG:4326", xs, ys)
        ring = [[round(a, 6), round(b, 6)] for a, b in zip(lons, lats)]
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring + [ring[0]]]},
                      "properties": {"change": round(float(cells["change_share"][i]), 4),
                                     "damage": round(float(cells["damage_share"][i]), 4),
                                     "magnitude": round(float(cells["magnitude_mean"][i]), 4),
                                     "n_buildings": int(cells["n_buildings"][i]),
                                     "n_damaged": int(cells["n_damaged"][i]),
                                     "damaged": bool(cells["damage_share"][i] >= damaged_share)}})
    return {"type": "FeatureCollection", "features": feats}


def plot_grid(cells: Dict[str, np.ndarray], metrics: dict, path: str, title: str = "") -> None:
    """Dois painéis: dispersão (% dano × % mudança) e curva de triagem (sistema × aleatório × oráculo)."""
    from app.models.plot_style import C, br, decimal_comma, themed_pyplot
    plt = themed_pyplot()

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9.6, 4.0), dpi=120)
    dmg, chg = 100 * cells["damage_share"], 100 * cells["change_share"]
    damaged = cells["damage_share"] >= metrics["damaged_cell_share"]
    size = 10 + 3 * np.sqrt(cells["n_buildings"])
    a1.scatter(dmg[~damaged], chg[~damaged], s=size[~damaged], c=C["faint"], alpha=0.55, linewidths=0, label="célula não danificada")
    a1.scatter(dmg[damaged], chg[damaged], s=size[damaged], c=C["red"], alpha=0.75, linewidths=0, label="célula danificada (≥ 20%)")
    a1.axhline(100 * metrics["system_threshold"], color=C["info"], ls="--", lw=1, label="limiar do sistema")
    a1.axvline(100 * metrics["damaged_cell_share"], color=C["red"], ls=":", lw=1)
    sp = metrics.get("spearman", {})
    ci = sp.get("ci95") or [None, None]
    a1.set_title(f"ρ de Spearman = {br(sp.get('rho'))}  (IC 95% {br(ci[0])} a {br(ci[1])})", fontsize=9)
    a1.set_xlabel("% dos prédios com dano (referência)")
    a1.set_ylabel("% dos pixels com mudança (sistema)")
    a1.legend(fontsize=7, loc="upper left")
    a1.grid(True)

    tc = metrics.get("triage_curve")
    if tc:
        x = 100 * np.array(tc["x"])
        a2.plot(x, 100 * np.array(tc["system"]), color=C["red"], lw=2.2, label="sistema (maior % de mudança primeiro)")
        a2.plot(x, 100 * np.array(tc["oracle"]), color=C["ok"], lw=1.2, ls="--", label="oráculo (máximo possível)")
        a2.plot(x, 100 * np.array(tc["random"]), color=C["faint"], lw=1.2, ls=":", label="aleatório")
        for row in metrics["triage"]:
            a2.annotate(f"{100 * row['share_of_damaged']:.0f}%", (100 * row["fraction_cells"], 100 * row["share_of_damaged"]),
                        textcoords="offset points", xytext=(4, -10), fontsize=7, color=C["red"])
    auc = metrics.get("auc", {}).get("value")
    a2.set_title(f"Triagem, AUC = {br(auc)}" if auc is not None else "Triagem", fontsize=9)
    for ax in (a1, a2):
        decimal_comma(ax)
    a2.set_xlabel("% das células visitadas")
    a2.set_ylabel("% dos prédios com dano alcançados")
    a2.set_xlim(0, 100); a2.set_ylim(0, 100)
    a2.legend(fontsize=7, loc="lower right")
    a2.grid(True)
    if title:
        fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
