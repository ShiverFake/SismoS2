"""Controller da validação (API JSON): inicia e consulta a validação de uma análise."""
from __future__ import annotations

import os
import re

from flask import Blueprint, current_app, jsonify, request

from app.models.analysis import AnalysisRepository
from app.models.ems import ems_repository
from app.models.grid_controls import cross_calibration
from app.models.validation import POSSIBLE_MODES, UNIT_MODES, ValidationService

validation_bp = Blueprint("validation", __name__)

_EMS_RE = re.compile(r"^EMSR\d{3,4}$")


def _service() -> ValidationService:
    cfg = current_app.config
    return ValidationService(AnalysisRepository(cfg["RESULTS_DIR"]), cfg["REFERENCE_DIR"], cfg["OVERPASS_URL"],
                             ems=ems_repository(cfg))


def _bad(msg: str, code: int = 400):
    return jsonify(error=msg), code


@validation_bp.post("/analises/<analysis_id>/validacao")
def start_validation(analysis_id: str):
    service = _service()
    analysis = service.repository.get(analysis_id)
    if analysis is None:
        return _bad("Análise não encontrada", 404)
    if analysis.status != "done":
        return _bad("A análise precisa estar concluída para ser validada.", 409)
    current = service.load(analysis_id)
    if current and current.get("status") == "processing":
        return _bad("Já existe uma validação em andamento.", 409)

    data = request.form if request.files or request.form else (request.get_json(silent=True) or {})
    try:
        buffer_m = float(data.get("buffer_m", 10))
    except (TypeError, ValueError):
        return _bad("Tolerância inválida.")
    if not 0 <= buffer_m <= 50:
        return _bad("A tolerância deve ficar entre 0 e 50 m.")
    possible = str(data.get("possible_mode") or "").strip()
    if not possible:
        legacy = str(data.get("include_possible", "")).lower() in ("1", "true", "on", "sim")
        possible = "dano" if legacy else "excluir"
    unit_mode = str(data.get("unit_mode") or "osm").strip()
    if possible not in POSSIBLE_MODES or unit_mode not in UNIT_MODES:
        return _bad("Modo de validação inválido.")
    try:
        grid_threshold = float(data.get("grid_threshold", 0.10))
    except (TypeError, ValueError):
        return _bad("Limiar da quadrícula inválido.")
    if not 0.01 <= grid_threshold <= 0.9:
        return _bad("O limiar da quadrícula deve ficar entre 1% e 90%.")
    spec = {"source": data.get("source", "ems"), "buffer_m": buffer_m, "possible_mode": possible,
            "unit_mode": unit_mode, "grid_threshold": grid_threshold}

    upload = None
    if spec["source"] == "ems":
        activation = str(data.get("activation", "")).strip().upper()
        try:
            aoi = int(data.get("aoi", 0))
        except (TypeError, ValueError):
            aoi = 0
        if not _EMS_RE.match(activation) or not 1 <= aoi <= 99:
            return _bad("Informe o código da ativação (ex.: EMSR695) e o número da AOI (1–99).")
        spec.update(activation=activation, aoi=aoi)
    elif spec["source"] == "file":
        f = request.files.get("file")
        if f is None or not f.filename:
            return _bad("Selecione um arquivo GeoJSON ou .zip.")
        if not f.filename.lower().endswith((".zip", ".json", ".geojson")):
            return _bad("Formato não suportado: use .geojson, .json ou .zip.")
        upload = (os.path.basename(f.filename), f.read())
        spec["filename"] = upload[0]
    else:
        return _bad("Fonte de referência inválida.")

    service.start(analysis, spec, upload)
    return jsonify(status="processing"), 202


@validation_bp.get("/analises/<analysis_id>/validacao/calibracao")
def cross_calibration_route(analysis_id: str):
    """?com=<id>: limiar de melhor F1 na grade desta análise aplicado à outra, e vice-versa."""
    other_id = request.args.get("com", "")
    service = _service()
    a, b = service.load(analysis_id), service.load(other_id) if service.repository.is_valid_id(other_id) else None
    if not a or not b or a.get("status") != "done" or b.get("status") != "done":
        return _bad("As duas análises precisam ter validação concluída.", 404)
    scores = lambda v: {s: m["cells_scores"] for s, m in ((v.get("grid") or {}).get("sizes") or {}).items()  # noqa: E731
                        if "cells_scores" in m}
    sa, sb = scores(a), scores(b)
    if not sa or not sb:
        return _bad("Refaça a validação das duas análises (versão sem pontuação por célula).", 409)
    return jsonify({"this": a.get("source"), "other": b.get("source"),
                    "this_to_other": cross_calibration(sa, sb), "other_to_this": cross_calibration(sb, sa)})


@validation_bp.get("/analises/<analysis_id>/validacao")
def get_validation(analysis_id: str):
    service = _service()
    if service.repository.get(analysis_id) is None:
        return _bad("Análise não encontrada", 404)
    state = service.load(analysis_id)
    return jsonify(state or {"status": "none"})
