"""Model: algoritmos de detecção de mudanças (somente NumPy/SciPy, independente da web)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
from scipy import ndimage

NODATA = 255

CLASSES = {
    0: {"name": "Sem mudança", "color": (0, 0, 0, 0)},
    1: {"name": "Perda de vegetação (possível deslizamento)", "color": (245, 130, 32, 230)},
    2: {"name": "Solo exposto / entulho em área urbana (possível dano em construções)", "color": (220, 38, 38, 230)},
    3: {"name": "Mudança em corpos d'água", "color": (37, 99, 235, 230)},
    4: {"name": "Outras mudanças espectrais", "color": (250, 204, 21, 210)},
    5: {"name": "Solo exposto / possível deslizamento", "color": (192, 38, 211, 230)},
}
CHANGE_CLASS_IDS = tuple(c for c in CLASSES if c != 0)

BANDS = ("blue", "green", "red", "nir", "swir16", "swir22")


@dataclass
class ChangeParams:
    threshold_method: str = "kstd"
    k: float = 2.0
    min_region_px: int = 6
    normalize: bool = True
    ndvi_drop: float = 0.15
    ndwi_change: float = 0.15
    bsi_increase: float = 0.05


@dataclass
class ChangeResult:
    classes: np.ndarray
    changed: np.ndarray
    magnitude: np.ndarray
    threshold: float
    dndvi: np.ndarray
    indices_before: Dict[str, np.ndarray]
    indices_after: Dict[str, np.ndarray]
    valid: np.ndarray


# ------------------------------------------------------------------ índices
def normalized_difference(a: np.ndarray, b: np.ndarray, eps: float = 1e-4) -> np.ndarray:
    """(a - b) / (a + b), com NaN onde o denominador é ~0 e resultado limitado a [-1, 1]."""
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    den = a + b
    with np.errstate(divide="ignore", invalid="ignore"):
        out = (a - b) / den
    out[~np.isfinite(out) | (np.abs(den) < eps)] = np.nan
    return np.clip(out, -1.0, 1.0)


def spectral_indices(bands: Dict[str, np.ndarray]) -> Dict[str, np.ndarray]:
    return {
        "ndvi": normalized_difference(bands["nir"], bands["red"]),
        "ndwi": normalized_difference(bands["green"], bands["nir"]),
        "ndbi": normalized_difference(bands["swir16"], bands["nir"]),
        "bsi": normalized_difference(bands["swir16"] + bands["red"], bands["nir"] + bands["blue"]),
    }


# ------------------------------------------------------- etapas do pipeline
def match_radiometry(before: Dict[str, np.ndarray], after: Dict[str, np.ndarray],
                     valid: np.ndarray) -> Dict[str, np.ndarray]:
    """Ajuste linear média/desvio de cada banda 'antes' para a distribuição 'depois'."""
    out = {}
    for name in BANDS:
        x, y = before[name][valid], after[name][valid]
        sx = float(np.std(x))
        if x.size == 0 or sx == 0:
            out[name] = before[name]
            continue
        out[name] = ((before[name] - np.mean(x)) / sx * np.std(y) + np.mean(y)).astype(np.float32)
    return out


def change_vector_magnitude(before: Dict[str, np.ndarray], after: Dict[str, np.ndarray]) -> np.ndarray:
    diff = np.stack([after[b] - before[b] for b in BANDS])
    return np.sqrt(np.sum(diff ** 2, axis=0)).astype(np.float32)


def otsu_threshold(values: np.ndarray, bins: int = 256) -> float:
    values = values[np.isfinite(values)]
    if values.size == 0:
        return float("nan")
    hist, edges = np.histogram(values, bins=bins)
    hist = hist.astype(np.float64)
    centers = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = w0[-1] - w0
    s0 = np.cumsum(hist * centers)
    m0 = s0 / np.maximum(w0, 1)
    m1 = (s0[-1] - s0) / np.maximum(w1, 1)
    between = w0 * w1 * (m0 - m1) ** 2
    first = int(np.argmax(between))
    is_max = between >= between[first] * (1 - 1e-9)
    last = first
    while last + 1 < is_max.size and is_max[last + 1]:
        last += 1
    return float((edges[first + 1] + edges[last + 1]) / 2)


def compute_threshold(values: np.ndarray, method: str, k: float) -> float:
    values = values[np.isfinite(values)]
    if method == "otsu":
        return otsu_threshold(values)
    return float(np.mean(values) + k * np.std(values))


def remove_small_regions(mask: np.ndarray, min_pixels: int) -> np.ndarray:
    if min_pixels <= 1 or not mask.any():
        return mask
    labels, _ = ndimage.label(mask, structure=np.ones((3, 3), dtype=bool))
    sizes = np.bincount(labels.ravel())
    small = sizes < min_pixels
    small[0] = False
    return mask & ~small[labels]


# --------------------------------------------------------------- principal
def detect_changes(before: Dict[str, np.ndarray], after: Dict[str, np.ndarray],
                   valid: np.ndarray, params: ChangeParams | None = None,
                   urban: Optional[np.ndarray] = None) -> ChangeResult:
    """urban: área construída (WorldCover classe 50)."""
    params = params or ChangeParams()
    if int(valid.sum()) < 100:
        raise ValueError(
            "Poucos pixels válidos em comum entre as duas datas (nuvens ou sem cobertura). "
            "Tente ampliar as janelas de data ou o limite de nuvens."
        )

    ref_before = match_radiometry(before, after, valid) if params.normalize else before
    magnitude = change_vector_magnitude(ref_before, after)
    magnitude[~valid] = np.nan

    threshold = compute_threshold(magnitude[valid], params.threshold_method, params.k)
    changed = valid & (np.nan_to_num(magnitude, nan=0.0) > threshold)
    changed = remove_small_regions(changed, params.min_region_px)

    ib, ia = spectral_indices(before), spectral_indices(after)
    d = {k: ia[k] - ib[k] for k in ib}

    with np.errstate(invalid="ignore"):
        water = changed & (np.fmax(ib["ndwi"], ia["ndwi"]) > 0) & (np.abs(d["ndwi"]) > params.ndwi_change)
        veg = changed & ~water & (ib["ndvi"] > 0.3) & (d["ndvi"] < -params.ndvi_drop)
        soil = changed & ~water & ~veg & ((d["bsi"] > params.bsi_increase) | (d["ndbi"] > params.bsi_increase))
    other = changed & ~water & ~veg & ~soil

    classes = np.zeros(valid.shape, dtype=np.uint8)
    classes[veg] = 1
    classes[soil] = 2
    if urban is not None:
        classes[soil & ~urban] = 5
    classes[water] = 3
    classes[other] = 4
    classes[~valid] = NODATA

    dndvi = d["ndvi"].astype(np.float32)
    dndvi[~valid] = np.nan

    return ChangeResult(classes, changed, magnitude, threshold, dndvi, ib, ia, valid)


def compute_statistics(result: ChangeResult, pixel_area_m2: float) -> dict:
    valid_px = int(result.valid.sum())
    to_km2 = pixel_area_m2 / 1e6
    per_class = []
    for cid, info in CLASSES.items():
        n = int((result.classes == cid).sum())
        per_class.append({
            "id": cid,
            "name": info["name"],
            "color": "rgba({},{},{},{:.2f})".format(*info["color"][:3], max(info["color"][3], 60) / 255),
            "pixels": n,
            "area_km2": round(n * to_km2, 4),
            "percent": round(100.0 * n / valid_px, 3) if valid_px else 0.0,
        })
    changed_px = int(result.changed.sum())
    dndvi_valid = result.dndvi[result.valid]
    return {
        "valid_pixels": valid_px,
        "valid_area_km2": round(valid_px * to_km2, 3),
        "total_pixels": int(result.valid.size),
        "changed_pixels": changed_px,
        "changed_area_km2": round(changed_px * to_km2, 4),
        "changed_percent": round(100.0 * changed_px / valid_px, 3) if valid_px else 0.0,
        "cva_threshold": round(float(result.threshold), 5),
        "dndvi_mean": round(float(np.nanmean(dndvi_valid)), 4) if dndvi_valid.size else None,
        "classes": per_class,
    }
