"""Model: uso do solo (ESA WorldCover 2021, 10 m) lido direto do bucket público na AWS."""
from __future__ import annotations

import logging
import math
from typing import List, Optional

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError
from rasterio.vrt import WarpedVRT

log = logging.getLogger(__name__)

WORLDCOVER_URL = ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
                  "ESA_WorldCover_10m_2021_v200_{tile}_Map.tif")
BUILT_UP = 50
TILE_DEG = 3


def tile_name(lat0: int, lon0: int) -> str:
    """Nome do tile pelo canto inferior esquerdo: N36E036, S03W009…"""
    return f"{'N' if lat0 >= 0 else 'S'}{abs(lat0):02d}{'E' if lon0 >= 0 else 'W'}{abs(lon0):03d}"


def worldcover_tiles(bbox) -> List[str]:
    """Tiles de 3°×3° que cobrem o bbox (oeste, sul, leste, norte) — pode ser mais de um."""
    west, south, east, north = bbox
    eps = 1e-9
    lats = range(math.floor(south / TILE_DEG) * TILE_DEG, math.floor((north - eps) / TILE_DEG) * TILE_DEG + 1, TILE_DEG)
    lons = range(math.floor(west / TILE_DEG) * TILE_DEG, math.floor((east - eps) / TILE_DEG) * TILE_DEG + 1, TILE_DEG)
    return [tile_name(la, lo) for la in lats for lo in lons]


def read_worldcover(grid) -> Optional[np.ndarray]:
    """Classes do WorldCover na grade da análise (uint8; 0 = sem dado)."""
    from app.models.sentinel import GDAL_ENV

    out = np.zeros((grid.height, grid.width), dtype=np.uint8)
    with rasterio.Env(**GDAL_ENV):
        for tile in worldcover_tiles(grid.bbox):
            try:
                with rasterio.open(WORLDCOVER_URL.format(tile=tile)) as src, WarpedVRT(
                        src, crs=grid.crs, transform=grid.transform, width=grid.width, height=grid.height,
                        resampling=Resampling.nearest, nodata=0) as vrt:
                    part = vrt.read(1)
            except RasterioIOError as exc:
                log.info("WorldCover %s indisponível: %s", tile, exc)
                continue
            fill = (out == 0) & (part > 0)
            out[fill] = part[fill]
    return out if out.any() else None
