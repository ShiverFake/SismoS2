"""Model: Copernicus EMS Rapid Mapping (ativações, AOIs e produtos de grading de dano)."""
from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import asdict, dataclass, field
from typing import Callable, List, Optional

from shapely import wkt
from shapely.geometry import mapping

from app.models.http_cache import HttpError, JsonCache, http_get
from app.models.reference import ReferenceData, area_km2, load_zip

EMS_CODE_RE = re.compile(r"^EMSR\d{3,4}$")
GRA_ZIP_FALLBACK = ("https://rapidmapping.emergency.copernicus.eu/backend/{code}/AOI{aoi:02d}/GRA_PRODUCT/"
                    "{code}_AOI{aoi:02d}_GRA_PRODUCT_v{v}.zip")


class EmsError(RuntimeError):
    """Erro exibido ao usuário."""


class EmsNotPublic(EmsError):
    pass


@dataclass
class EmsAoi:
    number: int
    name: str
    extent_wkt: str
    gra_download: Optional[str] = None
    gra_version: Optional[int] = None
    gra_image: Optional[str] = None
    gra_extent_wkt: Optional[str] = None

    @property
    def geometry(self):
        return wkt.loads(self.extent_wkt)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["bbox"] = [round(v, 6) for v in self.geometry.bounds]
        d["geometry"] = mapping(self.geometry)
        d["has_grading"] = self.gra_download is not None
        del d["extent_wkt"]
        d.pop("gra_extent_wkt", None)
        return d


@dataclass
class EmsActivation:
    code: str
    name: str
    category: str
    event_time: str
    closed: bool
    countries: List[str]
    centroid: Optional[List[float]]
    aois: List[EmsAoi] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "fechada" if self.closed else "aberta"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status
        d["url"] = f"https://rapidmapping.emergency.copernicus.eu/{self.code}"
        d["aois"] = [a.to_dict() for a in self.aois]
        return d


def _point(text: Optional[str]) -> Optional[List[float]]:
    m = re.match(r"POINT \(([-\d.]+) ([-\d.]+)\)", text or "")
    return [float(m.group(1)), float(m.group(2))] if m else None


def _countries(raw) -> List[str]:
    return [c["name"] if isinstance(c, dict) else str(c) for c in raw or []]


def parse_list_item(r: dict) -> EmsActivation:
    return EmsActivation(code=r["code"], name=r.get("name", ""), category=r.get("category", ""),
                         event_time=r.get("eventTime", ""),
                         closed=bool(r.get("closed")), countries=_countries(r.get("countries")),
                         centroid=_point(r.get("centroid")))


def parse_detail(r: dict) -> EmsActivation:
    act = parse_list_item(r)
    for a in r.get("aois") or []:
        gra = sorted([p for p in a.get("products") or [] if p.get("type") == "GRA" and p.get("downloadPath")],
                     key=lambda p: p.get("monitoringNumber") or 0)
        latest = gra[-1] if gra else None
        img = (latest or {}).get("images") or [{}]
        act.aois.append(EmsAoi(
            number=int(a["number"]), name=a.get("name", ""), extent_wkt=a["extent"],
            gra_download=latest["downloadPath"] if latest else None,
            gra_version=((latest or {}).get("version") or {}).get("number"),
            gra_image=f"{img[0].get('sensorName', '')} {str(img[0].get('acquisitionTime', ''))[:10]}".strip() or None,
            gra_extent_wkt=(latest or {}).get("extent")))
    return act


class EmsRepository:
    def __init__(self, list_url: str, detail_url: str, cache_dir: str, list_ttl_s: float = 3600,
                 fetch: Callable[[str], bytes] = http_get, clock=None):
        self.list_url = list_url
        self.detail_url = detail_url
        self.products_dir = os.path.join(cache_dir, "ems_products")
        self.ttl = list_ttl_s
        self.fetch = fetch
        self.cache = JsonCache(os.path.join(cache_dir, "ems"), **({"clock": clock} if clock else {}))

    def _json(self, url: str):
        import json
        return json.loads(self.fetch(url))

    # ---------------------------------------------------------------- lista
    def list_activations(self, category: Optional[str] = "Earthquake", page_size: int = 100) -> List[EmsActivation]:
        """Todas as ativações públicas (paginadas pelo campo `next`), filtradas por categoria."""
        def fetch_all():
            url = f"{self.list_url}?{urllib.parse.urlencode({'limit': page_size, 'offset': 0})}"
            out = []
            while url:
                page = self._json(url)
                out += page.get("results") or []
                url = page.get("next")
            return out
        raw = self.cache.get("ems_list", self.ttl, fetch_all)
        acts = [parse_list_item(r) for r in raw]
        if category:
            acts = [a for a in acts if a.category.lower() == category.lower()]
        return sorted(acts, key=lambda a: a.event_time, reverse=True)

    # -------------------------------------------------------------- detalhe
    def get_activation(self, code: str) -> EmsActivation:
        code = code.strip().upper()
        if not EMS_CODE_RE.match(code):
            raise EmsError("Código de ativação inválido (ex.: EMSR695).")

        def fetch():
            try:
                data = self._json(f"{self.detail_url}?{urllib.parse.urlencode({'code': code})}")
            except HttpError as exc:
                if exc.status in (401, 403):
                    raise EmsNotPublic(
                        f"A ativação {code} não é pública na API do Copernicus EMS (HTTP {exc.status}: exige "
                        "autenticação). Ativações anteriores à EMSR656 não estão disponíveis por esta API; "
                        "se você tiver o produto de grading (.zip ou GeoJSON), use a opção 'Arquivo'.") from None
                raise EmsError(str(exc)) from None
            results = data.get("results") or []
            if not results:
                raise EmsError(f"Ativação {code} não encontrada.")
            return results[0]
        return parse_detail(self.cache.get(f"ems_detail_{code}", self.ttl, fetch))

    # ------------------------------------------------- produto de grading (GRA)
    def gra_zip_path(self, code: str, aoi: int) -> str:
        """Baixa (uma vez só) o .zip do produto GRA da AOI."""
        path = os.path.join(self.products_dir, f"{code}_AOI{aoi:02d}_GRA.zip")
        if os.path.exists(path):
            return path
        act = self.get_activation(code)
        match = next((a for a in act.aois if a.number == aoi), None)
        if match is None:
            raise EmsError(f"{code} não tem a AOI {aoi}. AOIs: {[a.number for a in act.aois]}")
        urls = [match.gra_download] if match.gra_download else []
        urls += [GRA_ZIP_FALLBACK.format(code=code, aoi=aoi, v=v) for v in (3, 2, 1)]
        for url in dict.fromkeys(urls):
            try:
                data = self.fetch(url)
            except HttpError:
                continue
            if data[:2] == b"PK":
                os.makedirs(self.products_dir, exist_ok=True)
                with open(path + ".tmp", "wb") as fh:
                    fh.write(data)
                os.replace(path + ".tmp", path)
                return path
        raise EmsError(f"A AOI {aoi} de {code} não tem produto de grading (GRA) disponível para download.")

    def read_grading(self, code: str, aoi: int) -> ReferenceData:
        with open(self.gra_zip_path(code, aoi), "rb") as fh:
            ref = load_zip(fh.read(), f"Copernicus EMS {code} – AOI {aoi:02d}")
        try:
            match = next((a for a in self.get_activation(code).aois if a.number == aoi), None)
        except EmsError:
            match = None
        if match and match.gra_extent_wkt and ref.aoi is not None:
            extent = wkt.loads(match.gra_extent_wkt)
            ref.area_breakdown["gra_extent_km2"] = area_km2(extent)
            ref.aoi = ref.aoi.intersection(extent)
            ref.area_breakdown["evaluated_km2"] = area_km2(ref.aoi)
        return ref

    def grading_geojson(self, code: str, aoi: int) -> dict:
        """Prédios do grading como GeoJSON (pontos ou polígonos) com o grau de dano, para o mapa."""
        ref = self.read_grading(code, aoi)
        feats = [{"type": "Feature", "geometry": mapping(f.geometry), "properties": {"grade": f.grade}}
                 for f in ref.features]
        return {"type": "FeatureCollection", "features": feats,
                "properties": {"source": ref.source, "counts": ref.grade_counts(),
                               "aoi": mapping(ref.aoi) if ref.aoi is not None else None}}


def ems_repository(cfg) -> EmsRepository:
    """Cria o repositório a partir da configuração do Flask (config.py)."""
    return EmsRepository(cfg["EMS_LIST_URL"], cfg["EMS_DETAIL_URL"], cfg["CACHE_DIR"], cfg["EMS_LIST_TTL_S"])
