"""Model: monta os parâmetros de uma nova análise a partir de um evento (USGS ou Copernicus EMS)."""
from __future__ import annotations

import urllib.parse
from datetime import date, datetime, timedelta
from typing import Optional, Sequence

WINDOW_DAYS = 35
EPICENTER_HALF_DEG = 0.05      # sem AOI (só o epicentro): retângulo de ~0,1° em volta


def event_date(iso: str) -> date:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).date()


def analysis_dates(when: date, days: int = WINDOW_DAYS) -> dict:
    """antes = 35 dias antes até a véspera; depois = dia seguinte até 35 dias depois."""
    return {"before_start": (when - timedelta(days=days)).isoformat(),
            "before_end": (when - timedelta(days=1)).isoformat(),
            "after_start": (when + timedelta(days=1)).isoformat(),
            "after_end": (when + timedelta(days=days)).isoformat()}


def clamp_bbox(bbox: Sequence[float], max_deg: float) -> list:
    """Reduz o retângulo, mantendo o centro, para cada lado ter no máximo `max_deg` graus."""
    west, south, east, north = bbox
    cx, cy = (west + east) / 2, (south + north) / 2
    half_w, half_h = min(east - west, max_deg) / 2, min(north - south, max_deg) / 2
    return [round(cx - half_w, 5), round(cy - half_h, 5), round(cx + half_w, 5), round(cy + half_h, 5)]


def epicenter_bbox(lon: float, lat: float, half: float = EPICENTER_HALF_DEG) -> list:
    return [round(lon - half, 5), round(lat - half, 5), round(lon + half, 5), round(lat + half, 5)]


def analysis_form_url(name: str, bbox: Sequence[float], when: date, max_deg: float,
                      ems_activation: Optional[str] = None, ems_aoi: Optional[int] = None) -> str:
    """Link para o formulário de nova análise já preenchido (lido por new.js na página /nova)."""
    params = {"nome": name, "bbox": ",".join(str(v) for v in clamp_bbox(bbox, max_deg)),
              **analysis_dates(when)}
    if ems_activation:
        params["ems"] = ems_activation
    if ems_aoi:
        params["aoi"] = ems_aoi
    return "/?" + urllib.parse.urlencode(params)
