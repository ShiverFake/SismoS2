"""Model: controle de qualidade da análise (máscara de terra e avisos para o usuário).

Funções puras (sem rede/disco) — fáceis de testar e de explicar no trabalho.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

import numpy as np

MIN_VALID_FRACTION = 0.50     # abaixo disso, pouca área para concluir algo
MAX_GAP_DAYS = 60             # acima disso, a estação do ano muda bastante
SEASONAL_DNDVI = -0.15        # ΔNDVI mediano da área toda abaixo disso → provável efeito sazonal


def land_mask(valid: np.ndarray, ndwi_before: np.ndarray, ndwi_after: np.ndarray,
              water_before: Optional[np.ndarray] = None, water_after: Optional[np.ndarray] = None) -> np.ndarray:
    """Pixels válidos que NÃO são água (SCL = 6 ou NDWI > 0 em alguma das datas).

    Água tem NDVI negativo e puxa a média para baixo; um rio que enche ou seca entre as datas
    também mexe no ΔNDVI. Por isso as estatísticas de NDVI usam só pixels de terra.
    """
    with np.errstate(invalid="ignore"):
        water = (np.nan_to_num(ndwi_before, nan=-1) > 0) | (np.nan_to_num(ndwi_after, nan=-1) > 0)
    for scl_water in (water_before, water_after):
        if scl_water is not None:
            water |= scl_water
    return valid & ~water


def urban_stats(changed: np.ndarray, urban: np.ndarray, valid: np.ndarray, pixel_area_m2: float) -> dict:
    """Área urbana (WorldCover 50) analisada e % dela que mudou."""
    urban_valid = urban & valid
    n_urban = int(urban_valid.sum())
    n_changed = int((changed & urban_valid).sum())
    return {
        "area_km2": round(n_urban * pixel_area_m2 / 1e6, 3),
        "percent_of_valid": round(100.0 * n_urban / max(int(valid.sum()), 1), 2),
        "changed_km2": round(n_changed * pixel_area_m2 / 1e6, 4),
        "changed_percent": round(100.0 * n_changed / n_urban, 3) if n_urban else 0.0,
    }


def anniversary_dates(after_start: str, after_end: str) -> dict:
    """Mesmo período do 'depois', um ano antes (29/02 vira 28/02)."""
    def minus_year(s: str) -> str:
        d = date.fromisoformat(s)
        try:
            return d.replace(year=d.year - 1).isoformat()
        except ValueError:
            return d.replace(year=d.year - 1, day=28).isoformat()
    return {"before_start": minus_year(after_start), "before_end": minus_year(after_end)}


def _mean_date(scene_meta: dict) -> datetime:
    stamps = [datetime.fromisoformat(s["datetime"].replace("Z", "+00:00")).timestamp()
              for s in scene_meta.get("scenes") or [scene_meta]]
    return datetime.fromtimestamp(sum(stamps) / len(stamps))


def quality_warnings(*, valid_fraction: float, before: dict, after: dict, median_dndvi: Optional[float],
                     after_start: str, after_end: str) -> List[dict]:
    """Avisos exibidos em amarelo no resultado. Cada aviso: {code, message, action?}."""
    out = []
    if valid_fraction < MIN_VALID_FRACTION:
        out.append({"code": "area_valida",
                    "message": f"Só {100 * valid_fraction:.0f}% do retângulo tem pixels válidos (sem nuvem nas duas "
                               "datas). Os resultados valem só para essa parte; tente ampliar os períodos ou "
                               "ligar a composição por mediana."})
    if before.get("tile") != after.get("tile"):
        out.append({"code": "tiles_diferentes",
                    "message": f"Tiles diferentes – risco de desalinhamento ({before.get('tile')} × "
                               f"{after.get('tile')}): bordas e ruas podem aparecer como falsa mudança."})
    elif (before.get("relative_orbit") is not None and after.get("relative_orbit") is not None
          and before["relative_orbit"] != after["relative_orbit"]):
        out.append({"code": "orbitas_diferentes",
                    "message": f"Órbitas diferentes (R{before['relative_orbit']} × R{after['relative_orbit']}): "
                               "o ângulo de visada muda e pode gerar falsos positivos."})
    gap = (_mean_date(after) - _mean_date(before)).days
    if gap > MAX_GAP_DAYS:
        out.append({"code": "intervalo_longo",
                    "message": f"Intervalo de {gap} dias entre as imagens (> {MAX_GAP_DAYS}): mudanças de estação, "
                               "lavouras e obras também aparecem como mudança."})
    if median_dndvi is not None and median_dndvi < SEASONAL_DNDVI:
        dates = anniversary_dates(after_start, after_end)
        out.append({"code": "sazonal",
                    "message": f"O ΔNDVI mediano da área toda é {median_dndvi:+.2f}: a vegetação inteira mudou, o que "
                               "indica efeito sazonal, não do terremoto. Sugestão: use o par de aniversário "
                               f"(antes = {dates['before_start']} a {dates['before_end']}).",
                    "action": {"type": "anniversary", **dates}})
    return out
