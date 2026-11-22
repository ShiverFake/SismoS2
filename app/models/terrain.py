"""Model: relevo (Copernicus DEM GLO-30) e correção topográfica (C-correction).

Por que corrigir: numa encosta virada para o sol o pixel fica mais claro, e na encosta oposta mais
escuro. Entre duas datas o sol muda de posição (azimute/elevação), então o brilho das encostas
muda mesmo sem nada ter acontecido no terreno — e o CVA vê isso como "mudança".

C-correction (Teillet et al., 1982):
    cos(i) = cos(zs)·cos(s) + sin(zs)·sin(s)·cos(φs − a)        (iluminação do pixel)
    ρ = m·cos(i) + b   (regressão por banda)   →   c = b / m
    ρ_corrigido = ρ · (cos(zs) + c) / (cos(i) + c)
onde zs = ângulo zenital do sol (90° − elevação), φs = azimute do sol, s = declividade e
a = aspecto (direção para onde a encosta está virada).
"""
from __future__ import annotations

import logging
import math
from typing import Dict, Optional, Tuple

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT

log = logging.getLogger(__name__)

DEM_COLLECTION = "cop-dem-glo-30"
DEM_NODATA = -32767.0
MIN_FIT_SLOPE_DEG = 5.0      # regressão só com pixels inclinados (em terreno plano cos(i) quase não varia)
MIN_FIT_PIXELS = 500
FACTOR_LIMITS = (0.25, 4.0)  # evita "explodir" pixels em sombra profunda (cos(i) + c ≈ 0)
MIN_CORRELATION = 0.20       # só corrige a banda se r(cos i, ρ) ≥ 0,2
STEEP_SLOPE_DEG = 20.0


class TerrainUnavailable(RuntimeError):
    pass


class TerrainRepository:
    """Lê o DEM do Earth Search e reamostra para a grade da análise."""

    def __init__(self, stac_url: str, collection: str = DEM_COLLECTION):
        self.stac_url = stac_url
        self.collection = collection

    def dem_for_grid(self, grid) -> np.ndarray:
        from pystac_client import Client

        from app.models.sentinel import GDAL_ENV

        items = list(Client.open(self.stac_url).search(collections=[self.collection], bbox=list(grid.bbox)).items())
        if not items:
            raise TerrainUnavailable("Nenhum tile do Copernicus DEM cobre a área.")
        dem = np.full((grid.height, grid.width), np.nan, dtype=np.float32)
        with rasterio.Env(**GDAL_ENV):
            for item in items:  # a área pode cruzar mais de um tile de 1°
                with rasterio.open(item.assets["data"].href) as src, WarpedVRT(
                        src, crs=grid.crs, transform=grid.transform, width=grid.width, height=grid.height,
                        resampling=Resampling.bilinear, src_nodata=DEM_NODATA, nodata=DEM_NODATA) as vrt:
                    part = vrt.read(1)
                fill = (part != DEM_NODATA) & np.isnan(dem)
                dem[fill] = part[fill]
        if np.isnan(dem).all():
            raise TerrainUnavailable("O DEM não tem dados na área.")
        return dem


def slope_aspect(dem: np.ndarray, resolution_m: float) -> Tuple[np.ndarray, np.ndarray]:
    """Declividade e aspecto em radianos.

    Aspecto = azimute (horário a partir do norte) da direção de descida: 0 = encosta virada para
    o norte, 90° = leste, 180° = sul, 270° = oeste. As linhas da grade crescem para o SUL.
    """
    dz_drow, dz_dcol = np.gradient(dem.astype(np.float64), resolution_m)
    dz_dx, dz_dn = dz_dcol, -dz_drow          # leste e norte
    slope = np.arctan(np.hypot(dz_dx, dz_dn))
    aspect = np.mod(np.arctan2(-dz_dx, -dz_dn), 2 * math.pi)
    return slope.astype(np.float32), aspect.astype(np.float32)


def illumination(slope: np.ndarray, aspect: np.ndarray, sun_azimuth_deg: float, sun_elevation_deg: float) -> np.ndarray:
    """cos(i): cosseno do ângulo entre o sol e a normal do terreno (1 = sol a pino na encosta)."""
    zs = math.radians(90.0 - sun_elevation_deg)
    az = math.radians(sun_azimuth_deg)
    return (math.cos(zs) * np.cos(slope) + math.sin(zs) * np.sin(slope) * np.cos(az - aspect)).astype(np.float32)


def c_correction(bands: Dict[str, np.ndarray], cos_i: np.ndarray, sun_elevation_deg: float,
                 fit_mask: np.ndarray, slope: Optional[np.ndarray] = None,
                 sample: int = 200_000, seed: int = 0) -> Tuple[Dict[str, np.ndarray], Dict[str, Optional[float]]]:
    """Aplica a C-correction banda a banda. Devolve (bandas corrigidas, {banda: {"c", "r"}}).

    fit_mask: pixels usados na regressão (limpos, sem água). Bandas em que a regressão não faz
    sentido (inclinação m ≤ 0 ou correlação r < 0,2) ficam sem correção (c = None).
    """
    cos_zs = math.cos(math.radians(90.0 - sun_elevation_deg))
    fit = fit_mask & np.isfinite(cos_i)
    if slope is not None:
        steep = fit & (slope > math.radians(MIN_FIT_SLOPE_DEG))
        if steep.sum() >= MIN_FIT_PIXELS:
            fit = steep
    idx = np.flatnonzero(fit)
    if idx.size < MIN_FIT_PIXELS:
        return bands, {b: None for b in bands}
    if idx.size > sample:
        idx = np.random.default_rng(seed).choice(idx, sample, replace=False)
    x = cos_i.ravel()[idx]

    out, coeffs = {}, {}
    for name, rho in bands.items():
        y = rho.ravel()[idx]
        ok = np.isfinite(y)
        if ok.sum() < MIN_FIT_PIXELS or np.std(y[ok]) < 1e-6:   # banda constante: nada a corrigir
            out[name], coeffs[name] = rho, {"c": None, "r": None}
            continue
        r = float(np.corrcoef(x[ok], y[ok])[0, 1])
        m, b = np.polyfit(x[ok], y[ok], 1)
        # Sem relação clara entre brilho e iluminação, c = b/m fica instável e a "correção"
        # só acrescenta ruído. Acontece muito no L2A, que já sai do Sen2Cor com correção de terreno.
        if m <= 1e-6 or r < MIN_CORRELATION:
            out[name], coeffs[name] = rho, {"c": None, "r": round(r, 3)}
            continue
        c = b / m
        with np.errstate(divide="ignore", invalid="ignore"):
            factor = (cos_zs + c) / (cos_i + c)
        factor = np.where(np.isfinite(factor) & (cos_i + c > 0), factor, FACTOR_LIMITS[1])
        factor = np.where(np.isfinite(cos_i), np.clip(factor, *FACTOR_LIMITS), 1.0)
        out[name] = (rho * factor).astype(np.float32)
        coeffs[name] = {"c": round(float(c), 4), "r": round(r, 3)}
    return out, coeffs


def steep_share(changed: np.ndarray, slope: np.ndarray, valid: np.ndarray, limit_deg: float = STEEP_SLOPE_DEG) -> dict:
    """% da mudança (e da área válida) em declividade acima de `limit_deg`."""
    steep = np.nan_to_num(slope, nan=0.0) > math.radians(limit_deg)
    n_changed, n_valid = int(changed.sum()), int(valid.sum())
    return {
        "limit_deg": limit_deg,
        "changed_percent": round(100.0 * float((changed & steep).sum()) / n_changed, 2) if n_changed else 0.0,
        "area_percent": round(100.0 * float((valid & steep).sum()) / n_valid, 2) if n_valid else 0.0,
    }
