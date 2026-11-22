"""Model: geração das camadas visuais (PNG para o Leaflet) e GeoTIFFs para download."""
from __future__ import annotations

import os
from typing import Dict, Sequence, Tuple

import numpy as np
import rasterio
from PIL import Image

from app.models.change_detection import CLASSES, NODATA, ChangeResult

DNDVI_STOPS = [(-0.5, (140, 81, 10)), (-0.15, (223, 194, 125)), (0.0, (245, 245, 245)),
               (0.15, (128, 205, 193)), (0.5, (1, 102, 94))]
MAGNITUDE_STOPS = [(0.0, (13, 8, 135)), (0.35, (126, 3, 168)), (0.65, (220, 57, 119)),
                   (0.85, (251, 155, 6)), (1.0, (240, 249, 33))]


def _apply_colormap(values: np.ndarray, stops: Sequence[Tuple[float, Tuple[int, int, int]]]) -> np.ndarray:
    xs = np.array([s[0] for s in stops], dtype=np.float32)
    cols = np.array([s[1] for s in stops], dtype=np.float32)
    v = np.clip(np.nan_to_num(values, nan=xs[0]), xs[0], xs[-1])
    rgb = np.stack([np.interp(v, xs, cols[:, c]) for c in range(3)], axis=-1)
    return rgb.astype(np.uint8)


def _save_rgba(rgb: np.ndarray, alpha: np.ndarray, path: str) -> None:
    rgba = np.dstack([rgb, alpha.astype(np.uint8)])
    Image.fromarray(rgba).save(path, optimize=True)


def rgb_stretch(scenes: Sequence[Tuple[Dict[str, np.ndarray], np.ndarray]]) -> Tuple[float, float]:
    """Mesmo contraste (percentis 2–98) para as duas datas, para a comparação ser justa."""
    samples = [np.stack([b["red"], b["green"], b["blue"]])[:, m].ravel() for b, m in scenes]
    allv = np.concatenate(samples) if samples else np.array([0.0, 0.3])
    allv = allv[np.isfinite(allv)]
    if allv.size == 0:
        return 0.0, 0.3
    low, high = np.percentile(allv, [2, 98])
    return float(low), float(max(high, low + 1e-3))


def save_true_color(bands: Dict[str, np.ndarray], data_mask: np.ndarray, stretch, path: str) -> None:
    low, high = stretch
    rgb = np.stack([bands["red"], bands["green"], bands["blue"]], axis=-1)
    rgb = np.clip((np.nan_to_num(rgb) - low) / (high - low), 0, 1) ** (1 / 1.2)
    _save_rgba((rgb * 255).astype(np.uint8), np.where(data_mask, 255, 0), path)


def save_change_map(classes: np.ndarray, path: str) -> None:
    lut = np.zeros((256, 4), dtype=np.uint8)
    for cid, info in CLASSES.items():
        lut[cid] = info["color"]
    lut[NODATA] = (0, 0, 0, 0)  # nuvem/sem dado: desenhado na camada própria de hachura (mask.png)
    Image.fromarray(lut[classes]).save(path, optimize=True)


HATCH_PERIOD = 6   # pixels entre as linhas diagonais


def save_mask_hatch(masked: np.ndarray, path: str) -> None:
    """Pixels mascarados (nuvem, sombra, sem dado) em hachura diagonal cinza semitransparente.

    A hachura deixa claro que ali não há informação (diferente de "sem mudança") sem esconder
    totalmente o satélite por baixo.
    """
    rows, cols = np.indices(masked.shape)
    on_line = (rows + cols) % HATCH_PERIOD < 2
    rgba = np.zeros(masked.shape + (4,), dtype=np.uint8)
    rgba[..., :3] = 70
    rgba[..., 3] = np.where(masked, np.where(on_line, 190, 60), 0)
    Image.fromarray(rgba).save(path, optimize=True)


DNDVI_RANGE = (-0.5, 0.5)
DNDVI_FADE = (0.03, 0.15)   # |ΔNDVI| < 0,03 transparente; opaco a partir de 0,15


def save_dndvi(dndvi: np.ndarray, path: str) -> dict:
    """ΔNDVI em escala divergente; valores perto de zero ficam transparentes (só aparece o que mudou)."""
    ramp = np.clip((np.abs(np.nan_to_num(dndvi)) - DNDVI_FADE[0]) / (DNDVI_FADE[1] - DNDVI_FADE[0]), 0, 1)
    alpha = np.where(np.isfinite(dndvi), ramp * 235, 0)
    _save_rgba(_apply_colormap(dndvi, DNDVI_STOPS), alpha, path)
    return {"min": DNDVI_RANGE[0], "max": DNDVI_RANGE[1], "fade": list(DNDVI_FADE)}


def save_magnitude(magnitude: np.ndarray, path: str, threshold: float) -> dict:
    """Magnitude CVA: transparente abaixo de metade do limiar, opaca a partir do limiar.

    A escala de cores vai de 0,5·limiar até o percentil 99,5, então só as mudanças relevantes
    ficam coloridas e o satélite continua visível por baixo.
    """
    finite = magnitude[np.isfinite(magnitude)]
    low = 0.5 * threshold
    top = float(np.percentile(finite, 99.5)) if finite.size else 2 * threshold
    top = max(top, 1.5 * threshold)
    norm = (magnitude - low) / (top - low)
    alpha = np.clip((np.nan_to_num(magnitude) - low) / (threshold - low), 0, 1) * 235
    alpha[~np.isfinite(magnitude)] = 0
    _save_rgba(_apply_colormap(norm, MAGNITUDE_STOPS), alpha, path)
    return {"min": round(low, 5), "max": round(top, 5), "threshold": round(float(threshold), 5)}


def _gradient(stops, lo: float, hi: float) -> list:
    return [[round((x - lo) / (hi - lo), 4), "rgb({},{},{})".format(*c)] for x, c in stops]


def legend_scales(dndvi_scale: dict, magnitude_scale: dict) -> dict:
    """Escalas das camadas contínuas, para o front-end desenhar as barras de cores."""
    return {
        "dndvi": {**dndvi_scale, "label": "ΔNDVI (depois − antes)",
                  "stops": _gradient(DNDVI_STOPS, *DNDVI_RANGE),
                  "ticks": [DNDVI_RANGE[0], 0.0, DNDVI_RANGE[1]],
                  "hint": "marrom = perda de vegetação · verde = ganho"},
        "magnitude": {**magnitude_scale, "label": "Magnitude CVA (Δ reflectância)",
                      "stops": _gradient(MAGNITUDE_STOPS, 0.0, 1.0),
                      "ticks": [magnitude_scale["min"], magnitude_scale["threshold"], magnitude_scale["max"]],
                      "hint": "▲ = limiar de mudança"},
    }


def save_geotiffs(result: ChangeResult, grid, out_dir: str) -> None:
    profile = dict(driver="GTiff", width=grid.width, height=grid.height, count=1,
                   crs=grid.crs, transform=grid.transform, compress="deflate")
    with rasterio.open(os.path.join(out_dir, "change_classes.tif"), "w",
                       dtype="uint8", nodata=NODATA, photometric="palette", **profile) as dst:
        dst.write_colormap(1, {cid: info["color"] for cid, info in CLASSES.items()})  # antes dos dados
        dst.write(result.classes, 1)
    for name, arr in (("dndvi.tif", result.dndvi), ("magnitude.tif", result.magnitude)):
        with rasterio.open(os.path.join(out_dir, name), "w",
                           dtype="float32", nodata=float("nan"), **profile) as dst:
            dst.write(arr.astype(np.float32), 1)
