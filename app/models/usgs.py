"""Model: terremotos do USGS (feeds GeoJSON em tempo real e consulta histórica FDSN).

Estrutura do feed (conferida com curl em summary/4.5_week.geojson):
  {type, metadata: {generated, url, title, count, ...}, bbox,
   features: [{id, geometry: {coordinates: [lon, lat, profundidade_km]},
               properties: {mag, place, time (ms UTC), updated, url, detail, alert, tsunami,
                            sig, magType, type, title, ...}}]}
A consulta FDSN (fdsnws/event/1/query?format=geojson) devolve a mesma estrutura e serve para
períodos fora da última semana/mês (ex.: fevereiro de 2023).
"""
from __future__ import annotations

import json
import os
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from app.models.http_cache import JsonCache, http_get


@dataclass
class Quake:
    id: str
    magnitude: Optional[float]
    place: str
    time: str                 # ISO 8601 UTC
    depth_km: Optional[float]
    lon: float
    lat: float
    url: str
    title: str
    alert: Optional[str] = None
    tsunami: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def parse_feed(data: dict) -> List[Quake]:
    out = []
    for f in data.get("features") or []:
        p, coords = f.get("properties") or {}, (f.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2 or p.get("time") is None:
            continue
        if p.get("type") not in (None, "earthquake"):
            continue  # explosões, pedreiras etc.
        out.append(Quake(
            id=f.get("id", ""), magnitude=p.get("mag"), place=p.get("place") or "",
            time=datetime.fromtimestamp(p["time"] / 1000, tz=timezone.utc).isoformat(timespec="seconds"),
            depth_km=coords[2] if len(coords) > 2 else None, lon=coords[0], lat=coords[1],
            url=p.get("url") or "", title=p.get("title") or "", alert=p.get("alert"),
            tsunami=bool(p.get("tsunami"))))
    return sorted(out, key=lambda q: q.time, reverse=True)


class UsgsRepository:
    MAX_QUERY_EVENTS = 2000

    def __init__(self, feeds: Dict[str, str], query_url: str, cache_dir: str, ttl_s: float = 900,
                 fetch: Callable[[str], bytes] = http_get):
        self.feeds = feeds
        self.query_url = query_url
        self.ttl = ttl_s
        self.fetch = fetch
        self.cache = JsonCache(os.path.join(cache_dir, "usgs"))

    def feed(self, name: str = "week") -> List[Quake]:
        if name not in self.feeds:
            raise ValueError(f"Feed desconhecido: {name}")
        data = self.cache.get(f"usgs_feed_{name}", self.ttl, lambda: json.loads(self.fetch(self.feeds[name])))
        return parse_feed(data)

    def query(self, start: str, end: str, min_magnitude: float = 4.5) -> List[Quake]:
        """Consulta histórica (datas AAAA-MM-DD, fim incluído)."""
        params = {"format": "geojson", "starttime": start, "endtime": f"{end}T23:59:59",
                  "minmagnitude": min_magnitude, "orderby": "time", "limit": self.MAX_QUERY_EVENTS}
        url = f"{self.query_url}?{urllib.parse.urlencode(params)}"
        # período passado não muda: cache longo; período que inclui hoje: TTL normal
        ttl = None if end < datetime.now(timezone.utc).date().isoformat() else self.ttl
        return parse_feed(self.cache.get(f"usgs_q_{start}_{end}_{min_magnitude}", ttl,
                                         lambda: json.loads(self.fetch(url))))


def usgs_repository(cfg) -> UsgsRepository:
    return UsgsRepository(cfg["USGS_FEEDS"], cfg["USGS_QUERY_URL"], cfg["CACHE_DIR"], cfg["USGS_TTL_S"])
