"""Model: valores de um pixel da análise (usado no clique do mapa)."""
from __future__ import annotations

import math
import os
from typing import Optional

import numpy as np
import rasterio
from affine import Affine
from rasterio.warp import transform as warp_transform
from rasterio.windows import Window

from app.models.change_detection import CLASSES, NODATA, normalized_difference


class ProbeUnavailable(ValueError):
    pass


def _value(arr: np.ndarray, row: int, col: int) -> Optional[float]:
    v = float(arr[row, col])
    return round(v, 4) if math.isfinite(v) else None


def probe(out_dir: str, result: dict, lat: float, lon: float) -> dict:
    grid = result.get("grid", {})
    bands_path = os.path.join(out_dir, "bands.npz")
    if "transform" not in grid or not os.path.exists(bands_path):
        raise ProbeUnavailable("Esta análise não tem as bandas salvas; crie-a novamente para consultar pixels.")

    xs, ys = warp_transform("EPSG:4326", "EPSG:3857", [lon], [lat])
    inv = ~Affine(*grid["transform"])
    col_f = inv.a * xs[0] + inv.b * ys[0] + inv.c
    row_f = inv.d * xs[0] + inv.e * ys[0] + inv.f
    row, col = int(math.floor(row_f)), int(math.floor(col_f))
    base = {"lat": lat, "lon": lon, "row": row, "col": col}
    if not (0 <= row < grid["height"] and 0 <= col < grid["width"]):
        return {**base, "inside": False}

    npz = np.load(bands_path)
    pick = lambda key: npz[key][row:row + 1, col:col + 1].astype(np.float32)  # noqa: E731
    ndvi_b = normalized_difference(pick("before_nir"), pick("before_red"))
    ndvi_a = normalized_difference(pick("after_nir"), pick("after_red"))
    valid = bool(npz["valid"][row, col])
    slope_deg = round(float(npz["slope_deg"][row, col]), 1) if "slope_deg" in npz.files else None
    urban = bool(npz["urban"][row, col]) if "urban" in npz.files else None

    def read_tif(name):
        with rasterio.open(os.path.join(out_dir, name)) as src:
            return src.read(1, window=Window(col, row, 1, 1))[0, 0]

    class_id = int(read_tif("change_classes.tif"))
    magnitude = float(read_tif("magnitude.tif"))
    threshold = (result.get("statistics") or {}).get("cva_threshold")
    nb, na = _value(ndvi_b, 0, 0), _value(ndvi_a, 0, 0)
    return {
        **base,
        "inside": True,
        "valid": valid,
        "ndvi_before": nb,
        "ndvi_after": na,
        "dndvi": round(na - nb, 4) if nb is not None and na is not None else None,
        "magnitude": round(magnitude, 4) if math.isfinite(magnitude) else None,
        "threshold": threshold,
        "changed": class_id not in (0, NODATA),
        "class_id": class_id,
        "class_name": "Nuvem / sem dado" if class_id == NODATA else CLASSES[class_id]["name"],
        "class_color": ("rgb(120,120,120)" if class_id == NODATA else "rgb(203,213,225)" if class_id == 0
                        else "rgb({},{},{})".format(*CLASSES[class_id]["color"][:3])),
        "resolution_m": grid.get("resolution_m"),
        "slope_deg": slope_deg if slope_deg is None or math.isfinite(slope_deg) else None,
        "urban": urban,
    }
