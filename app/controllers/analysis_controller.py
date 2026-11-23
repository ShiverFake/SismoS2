"""Controller da API REST (JSON) usada pelo front-end Leaflet."""
from __future__ import annotations

import os
from datetime import date

from flask import Blueprint, current_app, jsonify, request, send_from_directory

from app.models.analysis import Analysis, AnalysisRepository
from app.models.earthquake import EarthquakeRepository
from app.models.ems import EMS_CODE_RE
from app.models.pixel_probe import ProbeUnavailable, probe
from app.models.priority import compute_priority
from app.models.buildings import BUILDINGS_FILE, BUILDINGS_VERSION, ensure_buildings, osm_reader
from app.models.water import WATER_FILE, WATER_VERSION, load_water

import json

import rasterio
from affine import Affine

PRIORITY_GEOJSON = "priority_200.geojson"
from app.models.processing import ChangeDetectionService
from app.models.sentinel import SentinelRepository

api_bp = Blueprint("api", __name__)

ALLOWED_FILES = {
    "before.png", "after.png", "change.png", "dndvi.png", "magnitude.png",
    "change_classes.tif", "dndvi.tif", "magnitude.tif", "analysis.json",
    "validation.json", "validation.csv", "validation_units.geojson", "mask.png",
    "control_overlap.png", "control_comparison.json", "validation_neighborhood.png",
    *(f"validation_grid_{s}.{ext}" for s in (50, 100, 200) for ext in ("geojson", "png")),
    "validation_grid_controls_roc.png", "priority_200.geojson",
}


def _repo() -> AnalysisRepository:
    return AnalysisRepository(current_app.config["RESULTS_DIR"])



def _service() -> ChangeDetectionService:
    cfg = current_app.config
    return ChangeDetectionService(
        _repo(),
        SentinelRepository(cfg["STAC_URL"], cfg["STAC_COLLECTION"], max_aot=cfg.get("MAX_AOT")),
        target_resolution=cfg["TARGET_RESOLUTION"],
        max_dim=cfg["MAX_DIM"],
        osm_reader=osm_reader(cfg),
    )


class ValidationError(ValueError):
    pass


def _parse_date(data: dict, key: str) -> date:
    try:
        return date.fromisoformat(str(data.get(key, "")))
    except ValueError:
        raise ValidationError(f"Data inválida em '{key}' (use AAAA-MM-DD).")


def _validate(data: dict) -> dict:
    try:
        west, south, east, north = (float(v) for v in data.get("bbox", []))
    except (TypeError, ValueError):
        raise ValidationError("Desenhe um retângulo no mapa (bbox com 4 números).")
    if not (-180 <= west < east <= 180 and -85 <= south < north <= 85):
        raise ValidationError("Coordenadas da área inválidas.")
    max_deg = current_app.config["MAX_BBOX_DEG"]
    if east - west > max_deg or north - south > max_deg:
        raise ValidationError(f"Área muito grande: cada lado deve ter no máximo {max_deg}°.")

    bs, be = _parse_date(data, "before_start"), _parse_date(data, "before_end")
    as_, ae = _parse_date(data, "after_start"), _parse_date(data, "after_end")
    if bs > be or as_ > ae:
        raise ValidationError("Em cada período a data inicial deve ser anterior à final.")
    if be >= as_:
        raise ValidationError("O período 'antes' deve terminar antes do início do período 'depois'.")
    if bs < date(2015, 6, 27):
        raise ValidationError("O Sentinel-2 só tem imagens a partir de 27/06/2015.")
    today = date.today()
    if as_ > today:
        raise ValidationError(
            f"O período 'depois' começa em {as_:%d/%m/%Y}, uma data que ainda não chegou: ainda não existem imagens "
            f"do Sentinel-2 desse período. Escolha um início até hoje ({today:%d/%m/%Y}). O satélite passa a cada "
            "5 dias e a imagem aparece no catálogo algumas horas depois.")

    try:
        max_cloud = int(data.get("max_cloud", 30))
        k = float(data.get("k", 2.0))
        min_region = int(data.get("min_region_px", 6))
    except (TypeError, ValueError):
        raise ValidationError("Parâmetros numéricos inválidos.")
    method = data.get("threshold_method", "kstd")
    if method not in ("kstd", "otsu"):
        raise ValidationError("Método de limiar deve ser 'kstd' ou 'otsu'.")
    if not (1 <= max_cloud <= 100 and 0.5 <= k <= 5 and 1 <= min_region <= 500):
        raise ValidationError("Parâmetros fora do intervalo permitido.")

    event_id = data.get("event_id") or None
    preset = EarthquakeRepository().get(event_id) if event_id else None
    if event_id and preset is None:
        event_id = None

    ems_activation = str(data.get("ems_activation") or "").strip().upper() or (preset.ems_activation if preset else None)
    ems_aoi = data.get("ems_aoi") or (preset.ems_aoi if preset else None)
    if ems_activation:
        try:
            ems_aoi = int(ems_aoi) if ems_aoi not in (None, "") else None
        except (TypeError, ValueError):
            raise ValidationError("Número de AOI do Copernicus EMS inválido.")
        if not EMS_CODE_RE.match(ems_activation) or (ems_aoi is not None and not 1 <= ems_aoi <= 99):
            raise ValidationError("Ativação do Copernicus EMS inválida (ex.: EMSR695, AOI 1–99).")
    else:
        ems_aoi = None

    name = (str(data.get("name") or "").strip() or "Análise sem nome")[:120]
    return dict(
        name=name, bbox=[west, south, east, north],
        before_start=bs.isoformat(), before_end=be.isoformat(),
        after_start=as_.isoformat(), after_end=ae.isoformat(),
        max_cloud=max_cloud, threshold_method=method, k=k, min_region_px=min_region,
        median_composite=str(data.get("median_composite", "")).lower() in ("1", "true", "on", "sim"),
        topo_correction=str(data.get("topo_correction", "true")).lower() not in ("0", "false", "off", "nao", "não"),
        event_id=event_id, ems_activation=ems_activation, ems_aoi=ems_aoi,
    )


@api_bp.errorhandler(ValidationError)
def _validation_error(exc):
    return jsonify(error=str(exc)), 400


@api_bp.post("/analises")
def create_analysis():
    data = request.get_json(silent=True) or {}
    analysis = Analysis.new(**_validate(data))
    _service().start(analysis)
    return jsonify(id=analysis.id, url=f"/analise/{analysis.id}"), 202


@api_bp.get("/analises/<analysis_id>")
def get_analysis(analysis_id: str):
    analysis = _repo().get(analysis_id)
    if analysis is None:
        return jsonify(error="Análise não encontrada"), 404
    return jsonify(analysis.to_dict())


@api_bp.post("/analises/<analysis_id>/destaque")
def set_featured(analysis_id: str):
    """Destacar (ou não) uma análise: as destacadas ficam no topo da galeria."""
    repo = _repo()
    analysis = repo.get(analysis_id)
    if analysis is None:
        return jsonify(error="Análise não encontrada"), 404
    if analysis.status in ("pending", "processing"):
        return jsonify(error="Aguarde o fim do processamento para destacar."), 409
    data = request.get_json(silent=True) or {}
    analysis.featured = bool(data.get("featured", not analysis.featured))
    repo.save(analysis)
    return jsonify(id=analysis.id, featured=analysis.featured)


@api_bp.delete("/analises/<analysis_id>")
def delete_analysis(analysis_id: str):
    analysis = _repo().get(analysis_id)
    if analysis is None:
        return jsonify(error="Análise não encontrada"), 404
    if analysis.status in ("pending", "processing"):
        return jsonify(error="Aguarde o fim do processamento para excluir."), 409
    repo = _repo()
    if analysis.control_of:
        event = repo.get(analysis.control_of)
        if event:
            if event.control_id == analysis.id:
                event.control_id = None
            event.control_ids = [i for i in (event.control_ids or []) if i != analysis.id]
            repo.save(event)
    for cid in list(analysis.control_ids or []) + ([analysis.control_id] if analysis.control_id else []):
        control = repo.get(cid)
        if control and control.control_of == analysis.id:
            control.control_of = None
            repo.save(control)
    repo.delete(analysis_id)
    return "", 204


@api_bp.get("/analises/<analysis_id>/pixel")
def get_pixel(analysis_id: str):
    repo = _repo()
    analysis = repo.get(analysis_id)
    if analysis is None:
        return jsonify(error="Análise não encontrada"), 404
    if analysis.status != "done":
        return jsonify(error="Análise ainda não concluída"), 409
    try:
        lat, lon = float(request.args["lat"]), float(request.args["lon"])
    except (KeyError, ValueError):
        return jsonify(error="Informe lat e lon numéricos."), 400
    try:
        return jsonify(probe(repo.directory(analysis_id), analysis.result, lat, lon))
    except ProbeUnavailable as exc:
        return jsonify(error=str(exc)), 409


@api_bp.get("/analises/<analysis_id>/prioridade")
def get_priority(analysis_id: str):
    """Ranking das células de 200 m pela % de mudança (não precisa de referência de dano)."""
    repo = _repo()
    analysis = repo.get(analysis_id)
    if analysis is None:
        return jsonify(error="Análise não encontrada"), 404
    if analysis.status != "done" or "transform" not in ((analysis.result or {}).get("grid") or {}):
        return jsonify(error="Análise não concluída ou feita numa versão antiga (sem a grade salva)."), 409
    folder = repo.directory(analysis_id)
    cache = os.path.join(folder, "priority_200.json")
    cfg = current_app.config
    buildings = ensure_buildings(folder, analysis.result, cfg["REFERENCE_DIR"], cfg["OVERPASS_URL"])
    b_path = os.path.join(folder, BUILDINGS_FILE)
    b_stamp = os.path.getmtime(b_path) if os.path.exists(b_path) else None
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as fh:
            data = json.load(fh)
        has_water = os.path.exists(os.path.join(folder, WATER_FILE))
        if (data.get("finished_at") == analysis.finished_at and data.get("water_version") == WATER_VERSION
                and data.get("water_known", False) == has_water
                and data.get("buildings_version") == BUILDINGS_VERSION and data.get("buildings_stamp") == b_stamp):
            return jsonify(data)
    with rasterio.open(os.path.join(folder, "change_classes.tif")) as src:
        classes = src.read(1)
    grid = analysis.result["grid"]
    summary, gj = compute_priority(classes, Affine(*grid["transform"]), float(grid["ground_resolution_m"]),
                                   load_water(folder, classes.shape), buildings)
    with open(os.path.join(folder, PRIORITY_GEOJSON), "w", encoding="utf-8") as fh:
        json.dump(gj, fh)
    data = {**summary, "geojson": PRIORITY_GEOJSON, "finished_at": analysis.finished_at, "water_version": WATER_VERSION,
            "buildings_version": BUILDINGS_VERSION, "buildings_stamp": b_stamp,
            "note": "Ranking relativo da % de mudança dentro do retângulo — não é diagnóstico de dano."}
    with open(cache, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    return jsonify(data)


@api_bp.get("/analises/<analysis_id>/arquivos/<filename>")
def get_file(analysis_id: str, filename: str):
    repo = _repo()
    if filename not in ALLOWED_FILES or repo.get(analysis_id) is None:
        return jsonify(error="Arquivo não encontrado"), 404
    folder = repo.directory(analysis_id)
    if not os.path.exists(os.path.join(folder, filename)):
        return jsonify(error="Arquivo ainda não gerado"), 404
    return send_from_directory(folder, filename, as_attachment=filename.endswith(".tif"), max_age=0)
