"""Controller das áreas de controle: cria controles de vários anos e compara com o evento."""
from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from app.models.analysis import AnalysisRepository
import threading

from app.models.control import (build_year_control, compare_controls, event_gap_days, find_controls,
                                run_control_series)
from app.models.buildings import osm_reader
from app.models.processing import ChangeDetectionService
from app.models.sentinel import SentinelRepository

control_bp = Blueprint("control", __name__)

MIN_YEAR = 2017


def _repo() -> AnalysisRepository:
    return AnalysisRepository(current_app.config["RESULTS_DIR"])


def _service(repo: AnalysisRepository) -> ChangeDetectionService:
    cfg = current_app.config
    return ChangeDetectionService(repo, SentinelRepository(cfg["STAC_URL"], cfg["STAC_COLLECTION"], max_aot=cfg.get("MAX_AOT")),
                                  target_resolution=cfg["TARGET_RESOLUTION"], max_dim=cfg["MAX_DIM"],
                                  osm_reader=osm_reader(cfg))


def _summary(control) -> dict:
    return {"id": control.id, "url": f"/analise/{control.id}", "year": control.control_year,
            "status": control.status, "progress": control.progress, "message": control.message,
            "error": control.error, "before": [control.before_start, control.before_end],
            "after": [control.after_start, control.after_end], "excluded": control.control_excluded}


@control_bp.post("/analises/<analysis_id>/controle")
def start_controls(analysis_id: str):
    repo = _repo()
    event = repo.get(analysis_id)
    if event is None:
        return jsonify(error="Análise não encontrada"), 404
    if event.control_of:
        return jsonify(error="Esta análise já é um controle; rode os controles a partir do evento."), 400
    if event.status != "done":
        return jsonify(error="A análise precisa estar concluída."), 409

    event_year = int(event.after_start[:4])
    data = request.get_json(silent=True) or {}
    if not data.get("years"):
        return _start_auto(repo, event, event_year, refazer=bool(data.get("refazer")))
    try:
        years = sorted({int(y) for y in data.get("years") or range(event_year - 3, event_year)})
    except (TypeError, ValueError):
        return jsonify(error="Anos inválidos."), 400
    if not years or any(y < MIN_YEAR or y >= event_year for y in years) or len(years) > 6:
        return jsonify(error=f"Escolha de 1 a 6 anos entre {MIN_YEAR} e {event_year - 1}."), 400

    existing = {c.control_year: c for c in find_controls(repo, event)
                if c.control_year and c.status in ("pending", "processing", "done")}
    service, created = _service(repo), []
    for year in years:
        if year in existing:
            continue
        control = build_year_control(event, year)
        event.control_ids = list(event.control_ids or []) + [control.id]
        created.append(control)
    repo.save(event)
    for control in created:
        service.start(control)
    return jsonify(controls=[_summary(c) for c in find_controls(repo, event)]), 202 if created else 200


_running_series = set()
_series_lock = threading.Lock()


def _start_auto(repo, event, event_year: int, refazer: bool):
    """Modo automático: anos para trás até CONTROL_TARGET controles com área válida ≥ MIN_CONTROL_VALID."""
    cfg = current_app.config
    min_valid, target, min_year = cfg["MIN_CONTROL_VALID"], cfg["CONTROL_TARGET"], cfg["CONTROL_MIN_YEAR"]
    with _series_lock:
        if event.id in _running_series:
            return jsonify(error="Os controles desta análise já estão rodando."), 409
        _running_series.add(event.id)
    existing = find_controls(repo, event)
    if refazer:
        for c in existing:
            if c.status in ("pending", "processing"):
                _running_series.discard(event.id)
                return jsonify(error="Há controle em processamento; aguarde."), 409
            repo.delete(c.id)
        event.control_ids, event.control_id, existing = [], None, []
    ok_years = []
    for c in existing:
        vf = ((c.result or {}).get("statistics") or {}).get("valid_fraction")
        if c.status == "done" and vf is not None and vf < min_valid and not c.control_excluded:
            c.control_excluded = {"reason": f"Regra automática: só {100 * vf:.0f}% do retângulo válido nas duas "
                                            f"datas (mínimo {100 * min_valid:.0f}%).", "flags": ["pouca_area"],
                                  "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            repo.save(c)
        elif c.status == "done" and not c.control_excluded and c.control_year:
            ok_years.append(c.control_year)
    repo.save(event)
    service = _service(repo)

    def run_year(year: int):
        control = build_year_control(event, year, min_valid=min_valid)
        fresh = repo.get(event.id)
        fresh.control_ids = list(fresh.control_ids or []) + [control.id]
        repo.save(fresh)
        service.repository.save(control)
        service.run(control)
        return control

    def worker():
        try:
            run_control_series(event, run_year, target, event_year - 1, min_year, ok_years)
        finally:
            _running_series.discard(event.id)

    threading.Thread(target=worker, daemon=True, name=f"controles-{event.id}").start()
    return jsonify(status="running", mode="auto", target=target, min_valid=min_valid,
                   already_valid=sorted(ok_years, reverse=True)), 202


@control_bp.get("/analises/<analysis_id>/controle")
def get_controls(analysis_id: str):
    repo = _repo()
    event = repo.get(analysis_id)
    if event is None:
        return jsonify(error="Análise não encontrada"), 404
    if event.control_of:
        return jsonify(status="is_control", event_id=event.control_of, year=event.control_year,
                       event_url=f"/analise/{event.control_of}", excluded=event.control_excluded)
    controls = find_controls(repo, event)
    if not controls:
        return jsonify(status="none", default_years=list(range(int(event.after_start[:4]) - 3,
                                                               int(event.after_start[:4]))))
    running = any(c.status in ("pending", "processing") for c in controls) or event.id in _running_series
    out = {"status": "running" if running else "done", "controls": [_summary(c) for c in controls]}
    if event.status == "done" and any(c.status == "done" for c in controls):
        try:
            out["comparison"] = compare_controls(repo, event, controls)
            out["event_gap_days"] = event_gap_days(event)
        except (ValueError, OSError) as exc:
            out["comparison_error"] = str(exc)
    return jsonify(out)


def _event_and_control(event_id: str, control_id: str):
    repo = _repo()
    event, control = repo.get(event_id), repo.get(control_id)
    if event is None or control is None or control.control_of != event.id:
        return repo, None, None
    return repo, event, control


@control_bp.post("/analises/<event_id>/controle/<control_id>/excluir")
def exclude_control(event_id: str, control_id: str):
    """Tira um controle da média (o motivo é obrigatório e fica gravado na análise de controle)."""
    repo, event, control = _event_and_control(event_id, control_id)
    if control is None:
        return jsonify(error="Controle não encontrado para esta análise."), 404
    data = request.get_json(silent=True) or {}
    reason = str(data.get("reason") or "").strip()
    if not 5 <= len(reason) <= 300:
        return jsonify(error="Escreva o motivo da exclusão (5 a 300 caracteres)."), 400
    control.control_excluded = {"reason": reason, "flags": [f["code"] for f in data.get("flags") or []
                                                            if isinstance(f, dict) and "code" in f],
                                "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    repo.save(control)
    return jsonify(_summary(control))


@control_bp.post("/analises/<event_id>/controle/<control_id>/incluir")
def include_control(event_id: str, control_id: str):
    """Devolve o controle para a média."""
    repo, event, control = _event_and_control(event_id, control_id)
    if control is None:
        return jsonify(error="Controle não encontrado para esta análise."), 404
    control.control_excluded = None
    repo.save(control)
    return jsonify(_summary(control))
