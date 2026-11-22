"""Model: dados de referência para validação (Copernicus EMS, arquivos do usuário, prédios do OSM).

- Copernicus EMS Rapid Mapping: produtos de "grading" com um ponto/polígono por edificação
  afetada e o grau de dano (Destroyed / Damaged / Possibly damaged).
- Arquivo enviado pelo usuário: GeoJSON ou .zip (formato EMS, UNOSAT ou genérico com um
  campo de grau de dano).
- OpenStreetMap (Overpass API): todas as edificações da área, usadas como população de prédios
  — os que não têm ponto de dano no EMS são considerados "sem dano".
"""
from __future__ import annotations

import hashlib
import io
import math
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from typing import List, Optional

from rasterio.warp import transform_geom
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.ops import unary_union

log = logging.getLogger(__name__)

USER_AGENT = "SismoS2/1.0 (trabalho academico; deteccao de mudancas Sentinel-2)"

SEVERITY = {"none": 0, "possibly": 1, "damaged": 2, "destroyed": 3}
GRADE_LABELS = {"destroyed": "Destruído", "damaged": "Danificado",
                "possibly": "Possivelmente danificado", "none": "Sem dano"}
DESTROYED_FLAGS = ("destroyed_building", "destroyed:building")   # exportações do OSM/HOT
DAMAGE_FIELDS = ("damage_gra", "main_damag", "damage_cla", "damage_class", "dmg_grade",
                 "grading", "damage", "dam_cat", "damagecat")


class ReferenceDataError(ValueError):
    """Erro de referência exibido ao usuário."""


@dataclass
class ReferenceFeature:
    geometry: object      # shapely (lon/lat)
    grade: str            # destroyed | damaged | possibly | none


@dataclass
class ReferenceData:
    source: str
    features: List[ReferenceFeature]
    aoi: Optional[object] = None          # polígono (lon/lat) da área avaliada pelo produtor
    notes: List[str] = field(default_factory=list)
    raw_values: dict = field(default_factory=dict)   # contagem de cada valor original do campo de dano
    area_breakdown: dict = field(default_factory=dict)  # km² da AOI, da imagem, das exclusões e da área final

    def grade_counts(self) -> dict:
        counts = {g: 0 for g in SEVERITY}
        for f in self.features:
            counts[f.grade] += 1
        return counts


# ------------------------------------------------------------------ parsing
def normalize_grade(value) -> Optional[str]:
    """Converte o texto de grau de dano (EMS, UNOSAT, etc.) para uma escala comum."""
    s = str(value or "").strip().lower()
    if not s or s in ("not applicable", "n/a", "na", "null", "unknown", "not analysed", "not analyzed"):
        return None
    if "destroy" in s:
        return "destroyed"
    if "possib" in s:
        return "possibly"
    if "no visible" in s or "no damage" in s or s in ("none", "undamaged", "intact", "not affected"):
        return "none"
    if "damag" in s or "severe" in s or "moderate" in s:
        return "damaged"
    return None


def _find_damage_field(props: dict) -> Optional[str]:
    lower = {k.lower(): k for k in props}
    for name in DAMAGE_FIELDS:
        if name in lower:
            return lower[name]
    for k_low, k in lower.items():
        # ignora campos de metadados do evento (damage_date, damage_event, damage_type…)
        if ("damag" in k_low or "grad" in k_low) and not any(x in k_low for x in ("date", "event", "type")):
            return k
    return None


def _crs_of(obj: dict) -> Optional[str]:
    name = ((obj.get("crs") or {}).get("properties") or {}).get("name", "")
    m = re.search(r"EPSG::?(\d+)", name)
    if m and m.group(1) != "4326":
        return f"EPSG:{m.group(1)}"
    return None  # CRS84 / EPSG:4326 / ausente (padrão GeoJSON)


def source_kind(source: str) -> str:
    """De onde veio a referência: "ems" (Copernicus EMS), "hot" (HOT/OpenStreetMap) ou "file" (outro arquivo)."""
    s = (source or "").lower()
    if "hotosm" in s or "hot_" in s or "humanitarian openstreetmap" in s:
        return "hot"
    if s.startswith("copernicus ems") or "emsr" in s:
        return "ems"
    return "file"


def source_label(source: str, short: bool = False) -> str:
    """Rótulo da referência para tabelas e gráficos, sempre a partir da fonte real (nunca fixo como "EMS")."""
    kind = source_kind(source)
    if kind == "hot":
        return "HOT/OSM" if short else "Prédios destruídos mapeados pelo HOT no OpenStreetMap"
    if kind == "ems":
        return "Copernicus EMS" if short else source
    name = (source or "").split(":", 1)[-1].strip()
    return "arquivo enviado" if short else f"Arquivo enviado ({name})" if name else "Arquivo enviado"


def parse_geojson(obj: dict, source: str) -> List[ReferenceFeature]:
    src_crs = _crs_of(obj)
    feats, damage_field = [], None
    for f in obj.get("features", []):
        props, geom = f.get("properties") or {}, f.get("geometry")
        if not geom:
            continue
        flag = next((props[k] for k in DESTROYED_FLAGS if k in props), None)
        if flag is not None:
            # OpenStreetMap / HOT: destroyed:building = yes (só registra prédios destruídos)
            grade = "destroyed" if str(flag).strip().lower() in ("yes", "true", "1") else None
        else:
            if damage_field is None:
                damage_field = _find_damage_field(props)
            grade = normalize_grade(props.get(damage_field)) if damage_field else None
        if grade is None:
            continue
        if src_crs:
            geom = transform_geom(src_crs, "EPSG:4326", geom)
        feats.append(ReferenceFeature(shape(geom), grade))
    return feats


def _aoi_from_geojson(obj: dict):
    src_crs = _crs_of(obj)
    geoms = []
    for f in obj.get("features", []):
        g = f.get("geometry")
        if g:
            geoms.append(shape(transform_geom(src_crs, "EPSG:4326", g) if src_crs else g))
    return unary_union(geoms) if geoms else None


def load_zip(data: bytes, source: str) -> ReferenceData:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ReferenceDataError("O arquivo enviado não é um .zip válido.")
    names = [n for n in z.namelist() if not n.endswith("/")]
    jsons = [n for n in names if n.lower().endswith((".json", ".geojson"))]
    built = [n for n in jsons if re.search(r"builtup[ap]?_v\d+\.(geo)?json$", n, re.I)]
    aoi_files = [n for n in jsons if re.search(r"areaofinterest[a]?_v\d+\.(geo)?json$", n, re.I)]
    candidates = built or [n for n in jsons if n not in aoi_files]
    if not candidates:
        raise ReferenceDataError("O .zip não contém GeoJSON. Para shapefiles, converta para GeoJSON "
                              "(ex.: no QGIS) ou envie o .zip original do Copernicus EMS.")
    feats = []
    for name in candidates:
        try:
            feats += parse_geojson(json.loads(z.read(name)), source)
        except (ValueError, KeyError) as exc:
            log.warning("Ignorando %s: %s", name, exc)
    raw = {}
    for name in candidates:
        try:
            for k, v in raw_damage_values(json.loads(z.read(name))).items():
                raw[k] = raw.get(k, 0) + v
        except (ValueError, KeyError):
            pass

    # Área efetivamente avaliada = AOI ∩ cobertura da imagem VHR − áreas marcadas como não analisadas
    aoi = None
    if aoi_files:
        aoi = unary_union([_aoi_from_geojson(json.loads(z.read(n))) for n in aoi_files])
    footprint_files = [n for n in jsons if re.search(r"imagefootprint[a]?_v\d+\.(geo)?json$", n, re.I)]
    not_analysed_files = [n for n in jsons if re.search(r"not_?analy[sz]", n, re.I)]
    # imagens usadas para classificar o dano: dmg_src_id dos prédios ↔ or_src_id das pegadas de imagem
    damage_sources = set()
    for name in candidates:
        try:
            damage_sources |= {str(f["properties"].get("dmg_src_id")) for f in json.loads(z.read(name)).get("features", [])
                               if (f.get("properties") or {}).get("dmg_src_id") is not None}
        except (ValueError, KeyError):
            pass
    footprint, footprint_note = None, None
    if footprint_files:
        fps = [f for n in footprint_files for f in json.loads(z.read(n)).get("features", []) if f.get("geometry")]
        used = [f for f in fps if str((f.get("properties") or {}).get("or_src_id")) in damage_sources]
        if used and len(used) < len(fps):
            footprint_note = (f"Pegada de imagem: só a(s) imagem(ns) usada(s) para classificar o dano "
                              f"(or_src_id {sorted(damage_sources)}), {len(used)} de {len(fps)} polígonos.")
        footprint = unary_union([shape(f["geometry"]) for f in (used or fps)])
    excluded = [_aoi_from_geojson(json.loads(z.read(n))) for n in not_analysed_files]
    for n in aoi_files + footprint_files:  # feições marcadas "Not analysed" dentro dessas camadas
        excluded += [shape(f["geometry"]) for f in json.loads(z.read(n)).get("features", [])
                     if f.get("geometry") and _is_not_analysed(f.get("properties") or {})]
    excluded = unary_union([g for g in excluded if g is not None and not g.is_empty]) if excluded else None
    ref = ReferenceData(source, feats, aoi, raw_values=raw)
    if aoi is not None:
        evaluated = aoi
        breakdown = {"aoi_km2": area_km2(aoi)}
        if footprint is not None and not footprint.is_empty:
            evaluated = evaluated.intersection(footprint)
            breakdown["image_footprint_km2"] = area_km2(footprint)
        if excluded is not None and not excluded.is_empty:
            breakdown["not_analysed_km2"] = area_km2(excluded.intersection(evaluated))
            evaluated = evaluated.difference(excluded)
        breakdown["evaluated_km2"] = area_km2(evaluated)
        breakdown["layers"] = {"footprint": bool(footprint_files), "not_analysed": bool(not_analysed_files)}
        ref.aoi, ref.area_breakdown = evaluated, breakdown
        if footprint_note:
            ref.notes.append(footprint_note)
    return _finish(ref)


NOT_ANALYSED_RE = re.compile(r"not\s*analy[sz]ed|n[aã]o\s*analisad", re.I)


def _is_not_analysed(props: dict) -> bool:
    return any(isinstance(v, str) and NOT_ANALYSED_RE.search(v) for v in props.values())


def raw_damage_values(obj: dict) -> dict:
    """Conta cada valor ORIGINAL do campo de dano (ex.: 'Destroyed', 'No visible damage')."""
    counts, damage_field = {}, None
    for f in obj.get("features", []):
        props = f.get("properties") or {}
        flag = next((k for k in DESTROYED_FLAGS if k in props), None)
        key = flag or damage_field or _find_damage_field(props)
        if flag is None and damage_field is None:
            damage_field = key
        if key:
            v = str(props.get(key))
            counts[v] = counts.get(v, 0) + 1
    return counts


def area_km2(geom) -> float:
    """Área aproximada (km²) de um polígono em graus, corrigindo a longitude pela latitude."""
    if geom is None or geom.is_empty:
        return 0.0
    lat = math.radians(geom.centroid.y)
    return round(geom.area * 111.32 * math.cos(lat) * 110.57, 3)


def load_upload(filename: str, data: bytes) -> ReferenceData:
    source = f"Arquivo enviado: {os.path.basename(filename)}"
    if filename.lower().endswith(".zip"):
        return load_zip(data, source)
    try:
        obj = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ReferenceDataError("Arquivo inválido: envie um GeoJSON (.geojson/.json) ou um .zip.")
    return _finish(ReferenceData(source, parse_geojson(obj, source)))


def _finish(ref: ReferenceData) -> ReferenceData:
    if not ref.features:
        raise ReferenceDataError("Nenhuma feição com grau de dano reconhecido (ex.: campo 'damage_gra' "
                              "com 'Destroyed', 'Damaged', 'Possibly damaged').")
    if ref.aoi is None:
        # sem AOI oficial: usa o envoltório convexo das feições + 100 m
        ref.aoi = unary_union([f.geometry for f in ref.features]).convex_hull.buffer(0.001)
        ref.notes.append("A referência não trouxe a área avaliada (AOI); foi usado o envoltório "
                         "convexo das feições + ~100 m.")
    return ref


# O download dos produtos do Copernicus EMS fica em app/models/ems.py (EmsRepository).


# ------------------------------------------------------- OpenStreetMap
def fetch_osm_buildings(bbox, cache_dir: str, overpass_url: str, retries: int = 4) -> List[Polygon]:
    """Edificações (ways com tag building) do OSM dentro do bbox (oeste, sul, leste, norte)."""
    os.makedirs(cache_dir, exist_ok=True)
    west, south, east, north = (round(v, 5) for v in bbox)
    key = hashlib.md5(f"{west},{south},{east},{north}".encode()).hexdigest()[:12]
    path = os.path.join(cache_dir, f"osm_buildings_{key}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        query = f'[out:json][timeout:180];way["building"]({south},{west},{north},{east});out geom;'
        body = urllib.parse.urlencode({"data": query}).encode()
        data, last_error = None, None
        for attempt in range(retries):
            try:
                req = urllib.request.Request(overpass_url, data=body, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=240) as resp:
                    data = json.loads(resp.read())
                break
            except (urllib.error.URLError, json.JSONDecodeError) as exc:
                last_error = exc
                time.sleep(10 * (attempt + 1))  # Overpass limita a taxa de requisições
        if data is None:
            raise ReferenceDataError(f"Não foi possível obter os prédios do OpenStreetMap: {last_error}")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    polys = []
    for el in data.get("elements", []):
        pts = [(p["lon"], p["lat"]) for p in el.get("geometry") or []]
        if len(pts) >= 4:
            poly = Polygon(pts)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if isinstance(poly, MultiPolygon):
                poly = max(poly.geoms, key=lambda g: g.area)
            if not poly.is_empty:
                polys.append(poly)
    return polys
