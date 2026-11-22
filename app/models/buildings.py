"""Model: onde há construções. Usado no ranking de prioridade e na classe "possível dano em construções".

As duas fontes se completam: o ESA WorldCover (classe 50, área construída) acerta cidades de concreto mas
quase não vê vilarejos de taipa, que parecem solo exposto (no Marrocos, só 0,51% da AOI do EMSR695 é
construída no WorldCover); o OpenStreetMap tem esses prédios quando foram mapeados. Por isso a regra é OU:

- prioridade: uma célula de 200 m entra no ranking se tiver ≥ MIN_OSM_BUILDINGS prédio do OSM OU
  ≥ MIN_BUILT_SHARE de WorldCover 50. As outras aparecem como "sem construções";
- classificação: um pixel conta como área construída se for WorldCover 50 OU estiver dentro de um prédio
  do OSM ou a até OSM_BUFFER_M dele (urban_mask).

O arquivo buildings.npz da análise guarda a máscara do WorldCover, o pixel do centro de cada prédio do OSM e
a pegada dos prédios ampliada em OSM_BUFFER_M. Uma fonte que faltar (WorldCover sem dado, Overpass fora do ar)
fica de fora; sem nenhuma das duas, nada é filtrado.

A regra vale só para a prioridade: a avaliação por quadrícula da validação já exige ≥ 3 prédios do OSM
por célula e continua com as regras dela.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

BUILDINGS_FILE = "buildings.npz"
BUILT_UP = 50
MIN_BUILT_SHARE = 0.10
MIN_OSM_BUILDINGS = 1
OSM_BUFFER_M = 10.0
BUILDINGS_VERSION = 2   # 2: regra OU (WorldCover ou OSM) e pegada dos prédios do OSM


def save_buildings(folder: str, built: Optional[np.ndarray] = None, osm_rows=None, osm_cols=None,
                   osm_mask: Optional[np.ndarray] = None) -> None:
    """Grava o que se sabe; uma fonte None mantém a que já estava salva."""
    old = load_buildings(folder) or {}
    built = built if built is not None else old.get("built")
    osm_mask = osm_mask if osm_mask is not None else old.get("osm_mask")
    if osm_rows is None and old.get("osm_rows") is not None:
        osm_rows, osm_cols = old["osm_rows"], old["osm_cols"]
    data = {"version": BUILDINGS_VERSION}
    if built is not None:
        data["built"] = built.astype(bool)
    if osm_mask is not None:
        data["osm_mask"] = osm_mask.astype(bool)
    if osm_rows is not None:
        data["osm_rows"] = np.asarray(osm_rows, dtype=np.int32)
        data["osm_cols"] = np.asarray(osm_cols, dtype=np.int32)
    np.savez_compressed(os.path.join(folder, BUILDINGS_FILE), **data)


def load_buildings(folder: str, shape=None) -> Optional[dict]:
    path = os.path.join(folder, BUILDINGS_FILE)
    if not os.path.exists(path):
        return None
    with np.load(path) as z:
        out = {"built": z["built"] if "built" in z.files else None,
               "osm_mask": z["osm_mask"] if "osm_mask" in z.files else None,
               "osm_rows": z["osm_rows"] if "osm_rows" in z.files else None,
               "osm_cols": z["osm_cols"] if "osm_cols" in z.files else None}
    if shape is not None and out["built"] is not None and out["built"].shape != tuple(shape):
        log.warning("buildings.npz com tamanho diferente da grade: ignorado")
        return None
    return out


def has_osm(b: Optional[dict]) -> bool:
    return bool(b) and b.get("osm_rows") is not None


def building_cells(b: Optional[dict], cell_of_px: np.ndarray, n_cells: int, cell_px: int, ncols: int) -> Optional[np.ndarray]:
    """Bool por célula: tem construções (≥ 1 prédio do OSM OU ≥ 10% de WorldCover 50)?
    None quando não há nenhuma fonte (nada é filtrado)."""
    if not b or (b.get("built") is None and b.get("osm_rows") is None):
        return None
    ok = np.zeros(n_cells, dtype=bool)
    if b.get("built") is not None:
        total = np.bincount(cell_of_px.ravel(), minlength=n_cells)
        built = np.bincount(cell_of_px[b["built"]], minlength=n_cells)
        with np.errstate(divide="ignore", invalid="ignore"):
            ok |= (total > 0) & (built / np.maximum(total, 1) >= MIN_BUILT_SHARE)
    if b.get("osm_rows") is not None:
        cells = (b["osm_rows"] // cell_px) * ncols + (b["osm_cols"] // cell_px)
        ok |= np.bincount(cells, minlength=n_cells)[:n_cells] >= MIN_OSM_BUILDINGS
    return ok


def urban_mask(b: Optional[dict]) -> Optional[np.ndarray]:
    """Área construída da classificação: WorldCover 50 OU dentro/a até 10 m de um prédio do OSM."""
    if not b:
        return None
    parts = [m for m in (b.get("built"), b.get("osm_mask")) if m is not None]
    if not parts:
        return None
    out = parts[0].astype(bool).copy()
    for m in parts[1:]:
        out |= m
    return out


def osm_footprint_mask(polys, transform, shape, res_m: float, buffer_m: float = OSM_BUFFER_M) -> np.ndarray:
    """Pegada dos prédios na grade, ampliada em buffer_m (≈ 1 pixel de 10 m)."""
    from rasterio.features import rasterize
    from rasterio.warp import transform_geom
    from scipy.ndimage import binary_dilation
    from shapely.geometry import mapping

    if not polys:
        return np.zeros(shape, dtype=bool)
    geoms = [transform_geom("EPSG:4326", "EPSG:3857", mapping(p)) for p in polys]
    mask = rasterize(((g, 1) for g in geoms), out_shape=shape, transform=transform, fill=0,
                     all_touched=True, dtype="uint8").astype(bool)
    steps = max(1, int(round(buffer_m / max(res_m, 1e-6))))
    return binary_dilation(mask, iterations=steps)


def osm_pixels(polys, transform, shape):
    """Pixel (linha, coluna) do centro de cada prédio, só os que caem dentro da grade."""
    from rasterio.warp import transform as warp_transform

    if not polys:
        return np.zeros(0, np.int32), np.zeros(0, np.int32)
    lons = [p.centroid.x for p in polys]
    lats = [p.centroid.y for p in polys]
    xs, ys = warp_transform("EPSG:4326", "EPSG:3857", lons, lats)
    inv = ~transform
    cols, rows = inv * (np.asarray(xs), np.asarray(ys))
    rows, cols = np.floor(rows).astype(np.int64), np.floor(cols).astype(np.int64)
    inside = (rows >= 0) & (rows < shape[0]) & (cols >= 0) & (cols < shape[1])
    return rows[inside].astype(np.int32), cols[inside].astype(np.int32)


def ensure_buildings(folder: str, result: dict, cache_dir: str, overpass_url: str) -> Optional[dict]:
    """Completa buildings.npz (WorldCover e OSM) quando falta alguma fonte. Falhas de rede não interrompem."""
    from app.models.landcover import read_worldcover
    from app.models.reference import fetch_osm_buildings
    from app.models.water import grid_from_result

    b = load_buildings(folder)
    if b and b.get("built") is not None and has_osm(b) and b.get("osm_mask") is not None:
        return b
    grid = grid_from_result(result)
    shape = (grid.height, grid.width)
    built = rows = cols = footprint = None
    if not b or b.get("built") is None:
        try:
            wc = read_worldcover(grid)
            built = None if wc is None else wc == BUILT_UP
        except Exception as exc:  # noqa: BLE001
            log.warning("WorldCover indisponível para a prioridade: %s", exc)
    if not has_osm(b) or b.get("osm_mask") is None:
        try:
            polys = fetch_osm_buildings(grid.bbox, cache_dir, overpass_url, retries=2)   # cache em disco
            rows, cols = osm_pixels(polys, grid.transform, shape)
            footprint = osm_footprint_mask(polys, grid.transform, shape, grid.ground_resolution)
        except Exception as exc:  # noqa: BLE001
            log.warning("Prédios do OSM indisponíveis: %s", exc)
    if built is not None or rows is not None:
        save_buildings(folder, built, rows, cols, footprint)
    return load_buildings(folder, shape)


def osm_reader(cfg):
    """Função grade → prédios do OSM no retângulo dela (cache em disco), para o processamento."""
    from app.models.reference import fetch_osm_buildings
    return lambda grid: fetch_osm_buildings(grid.bbox, cfg["REFERENCE_DIR"], cfg["OVERPASS_URL"], retries=1)
