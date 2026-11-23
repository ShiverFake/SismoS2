"""Model: máscara de água da análise, para tirar rios, lagos e mar do ranking de prioridade e da grade."""
from __future__ import annotations

import logging
import os
from typing import Iterable, Optional

import numpy as np

log = logging.getLogger(__name__)

WATER_FILE = "water.npz"
WATER_SCL = 6
WORLDCOVER_WATER = 80
MAX_WATER_SHARE = 0.5
WATER_VERSION = 1


def water_mask(scl_water: Iterable[Optional[np.ndarray]], worldcover: Optional[np.ndarray],
               shape) -> np.ndarray:
    """União das fontes de água disponíveis (as ausentes são ignoradas)."""
    out = np.zeros(shape, dtype=bool)
    for m in scl_water:
        if m is not None:
            out |= m.astype(bool)
    if worldcover is not None:
        out |= worldcover == WORLDCOVER_WATER
    return out


def save_water(folder: str, water: np.ndarray, sources: list) -> None:
    np.savez_compressed(os.path.join(folder, WATER_FILE), water=water.astype(bool),
                        sources=np.array(sources, dtype=object), version=WATER_VERSION)


def load_water(folder: str, shape=None) -> Optional[np.ndarray]:
    """Máscara salva (ou None se a análise ainda não tem)."""
    path = os.path.join(folder, WATER_FILE)
    if not os.path.exists(path):
        return None
    with np.load(path, allow_pickle=True) as z:
        water = z["water"].astype(bool)
    if shape is not None and water.shape != tuple(shape):
        log.warning("water.npz com tamanho %s diferente da grade %s: ignorado", water.shape, shape)
        return None
    return water


def water_cells(water: Optional[np.ndarray], cell_of_px: np.ndarray, n_cells: int,
                base: Optional[np.ndarray] = None) -> np.ndarray:
    """Células com mais de MAX_WATER_SHARE de água (entre os pixels de `base`, se dado)."""
    if water is None:
        return np.zeros(n_cells, dtype=bool)
    sel = np.ones(water.shape, bool) if base is None else base
    total = np.bincount(cell_of_px[sel], minlength=n_cells)
    wet = np.bincount(cell_of_px[sel & water], minlength=n_cells)
    with np.errstate(divide="ignore", invalid="ignore"):
        return (total > 0) & (wet / np.maximum(total, 1) > MAX_WATER_SHARE)


# ------------------------------------------------------------------ análises antigas
def grid_from_result(result: dict):
    """Reconstrói a Grid da análise a partir do que ficou salvo em analysis.json."""
    from affine import Affine
    from app.models.sentinel import Grid

    g = result["grid"]
    (south, west), (north, east) = result["bounds"]
    return Grid(g.get("crs", "EPSG:3857"), Affine(*g["transform"]), int(g["width"]), int(g["height"]),
                (west, south, east, north), float(g["ground_resolution_m"]))
