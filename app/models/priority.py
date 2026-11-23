"""Model: "Prioridade de vistoria" — ranking das células de 200 m pela % de pixels com mudança."""
from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np
from rasterio.warp import transform as warp_transform

from app.models.change_detection import CHANGE_CLASS_IDS, NODATA
from app.models.buildings import building_cells
from app.models.water import water_cells

PRIORITY_CELL_M = 200
MIN_VALID_SHARE = 0.5
BANDS = [
    (0.10, "top10", "Top 10%", "#b91c1c"),
    (0.20, "p10_20", "10–20%", "#f97316"),
    (0.30, "p20_30", "20–30%", "#facc15"),
    (math.inf, "rest", "Demais", "#94a3b8"),
]
EMPTY = ("empty", "Sem construções", "#cbd5e1")


def band_of(rank_frac: float, change: float) -> str:
    if change <= 0:
        return "rest"
    return next(key for limit, key, _, _ in BANDS if rank_frac < limit)


def priority_cells(classes: np.ndarray, cell_px: int, water: Optional[np.ndarray] = None,
                   buildings: Optional[dict] = None) -> Dict[str, np.ndarray]:
    """Agrega o mapa de classes em células de cell_px × cell_px e calcula a posição no ranking."""
    h, w = classes.shape
    ncols = math.ceil(w / cell_px)
    rr, cc = np.indices((h, w))
    cell = (rr // cell_px) * ncols + (cc // cell_px)
    n_all = math.ceil(h / cell_px) * ncols
    valid = classes != NODATA
    changed = np.isin(classes, CHANGE_CLASS_IDS) & valid
    px_total = np.bincount(cell.ravel(), minlength=n_all)
    px_valid = np.bincount(cell[valid], minlength=n_all)
    px_changed = np.bincount(cell[changed], minlength=n_all)
    with np.errstate(divide="ignore", invalid="ignore"):
        keep = (px_total > 0) & (px_valid / np.maximum(px_total, 1) >= MIN_VALID_SHARE)
    wet = water_cells(water, cell, n_all)
    n_water = int((keep & wet).sum())
    keep &= ~wet
    with_build = building_cells(buildings, cell, n_all, cell_px, ncols)
    empty_ids = np.flatnonzero(keep & ~with_build) if with_build is not None else np.zeros(0, dtype=np.int64)
    if with_build is not None:
        keep &= with_build
    ids = np.flatnonzero(keep)
    change = px_changed[ids] / px_valid[ids]
    position = len(change) - np.searchsorted(np.sort(change), change, side="right")
    rank_frac = position / max(len(change), 1)
    bands = np.array([band_of(f, c) for f, c in zip(rank_frac, change)], dtype=object)
    return {"id": ids, "row": ids // ncols, "col": ids % ncols, "change": change, "rank": position + 1,
            "rank_frac": rank_frac, "band": bands, "px_total": px_total[ids], "n_water_cells": n_water,
            "water_known": water is not None, "buildings_known": with_build is not None,
            "empty": {"id": empty_ids, "row": empty_ids // ncols, "col": empty_ids % ncols,
                      "px_total": px_total[empty_ids]}}


def priority_summary(cells: Dict[str, np.ndarray], pixel_area_m2: float) -> dict:
    km2 = pixel_area_m2 / 1e6
    out = {"cell_m": PRIORITY_CELL_M, "n_cells": int(len(cells["id"])), "bands": [],
           "n_water_cells": cells.get("n_water_cells", 0), "water_known": bool(cells.get("water_known"))}
    for _, key, label, color in BANDS:
        sel = cells["band"] == key
        out["bands"].append({"key": key, "label": label, "color": color, "n_cells": int(sel.sum()),
                             "area_km2": round(float(cells["px_total"][sel].sum()) * km2, 3),
                             "min_change": round(float(cells["change"][sel].min()), 4) if sel.any() else None})
    out["top10_area_km2"] = out["bands"][0]["area_km2"]
    out["top10_n_cells"] = out["bands"][0]["n_cells"]
    empty = cells.get("empty") or {"id": [], "px_total": np.zeros(0)}
    out["empty"] = {"key": EMPTY[0], "label": EMPTY[1], "color": EMPTY[2], "n_cells": int(len(empty["id"])),
                    "area_km2": round(float(np.sum(empty["px_total"])) * km2, 3)}
    out["buildings_known"] = bool(cells.get("buildings_known"))
    out["total_area_km2"] = round(float(cells["px_total"].sum()) * km2, 3)
    return out


def priority_geojson(cells: Dict[str, np.ndarray], cell_px: int, transform, pixel_area_m2: float) -> dict:
    labels = {key: label for _, key, label, _ in BANDS}
    feats = []
    for i in range(len(cells["id"])):
        r0, c0 = int(cells["row"][i]) * cell_px, int(cells["col"][i]) * cell_px
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [_ring(transform, r0, c0, cell_px)]},
                      "properties": {"rank": int(cells["rank"][i]), "change": round(float(cells["change"][i]), 4),
                                     "band": cells["band"][i], "band_label": labels[cells["band"][i]],
                                     "area_km2": round(float(cells["px_total"][i]) * pixel_area_m2 / 1e6, 4)}})
    empty = cells.get("empty")
    if empty is not None:
        for r, c, px in zip(empty["row"], empty["col"], empty["px_total"]):
            r0, c0 = int(r) * cell_px, int(c) * cell_px
            feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [_ring(transform, r0, c0, cell_px)]},
                          "properties": {"rank": None, "change": None, "band": EMPTY[0], "band_label": EMPTY[1],
                                         "area_km2": round(float(px) * pixel_area_m2 / 1e6, 4)}})
    return {"type": "FeatureCollection", "features": feats}


def _ring(transform, r0: int, c0: int, cell_px: int) -> list:
    xs, ys = [], []
    for rr, cc in ((r0, c0), (r0, c0 + cell_px), (r0 + cell_px, c0 + cell_px), (r0 + cell_px, c0)):
        xs.append(transform.a * cc + transform.b * rr + transform.c)
        ys.append(transform.d * cc + transform.e * rr + transform.f)
    lons, lats = warp_transform("EPSG:3857", "EPSG:4326", xs, ys)
    ring = [[round(a, 6), round(b, 6)] for a, b in zip(lons, lats)]
    return ring + [ring[0]]


def compute_priority(classes: np.ndarray, transform, ground_res_m: float, water: Optional[np.ndarray] = None,
                     buildings: Optional[dict] = None) -> List:
    """Devolve (resumo, geojson) para a análise."""
    cell_px = max(1, int(round(PRIORITY_CELL_M / ground_res_m)))
    cells = priority_cells(classes, cell_px, water, buildings)
    area = ground_res_m ** 2
    return priority_summary(cells, area), priority_geojson(cells, cell_px, transform, area)
