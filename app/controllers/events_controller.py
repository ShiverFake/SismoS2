"""Controller da página "Eventos": terremotos do USGS e ativações do Copernicus EMS."""
from __future__ import annotations

from datetime import date, timedelta

from flask import Blueprint, current_app, jsonify, render_template, request

from app.models.ems import EmsError, EmsNotPublic, ems_repository
from app.models.http_cache import HttpError
from app.models.planning import analysis_form_url, epicenter_bbox, event_date
from app.models.usgs import usgs_repository

events_bp = Blueprint("events", __name__)

PERIODS = {"week": 7, "month": 30}


@events_bp.route("/eventos")
def events_page():
    return render_template("events.html", max_bbox_deg=current_app.config["MAX_BBOX_DEG"])


def _max_deg() -> float:
    return current_app.config["MAX_BBOX_DEG"]


@events_bp.get("/api/eventos/terremotos")
def list_quakes():
    """?periodo=week|month ou ?inicio=AAAA-MM-DD&fim=AAAA-MM-DD ; &mag_min=4.5"""
    try:
        mag_min = float(request.args.get("mag_min", 4.5))
        start, end = request.args.get("inicio"), request.args.get("fim")
        repo = usgs_repository(current_app.config)
        if start and end:
            d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
            if d1 < d0 or (d1 - d0).days > 366:
                return jsonify(error="Período inválido (máximo de 1 ano)."), 400
            quakes = repo.query(d0.isoformat(), d1.isoformat(), max(mag_min, 2.5))
            source = "USGS FDSN (consulta histórica)"
        else:
            period = request.args.get("periodo", "week")
            if period not in PERIODS:
                return jsonify(error="Período deve ser 'week' ou 'month'."), 400
            quakes = repo.feed(period)
            source = f"USGS feed 4.5+ ({'7' if period == 'week' else '30'} dias)"
    except ValueError:
        return jsonify(error="Parâmetros inválidos."), 400
    except HttpError as exc:
        return jsonify(error=f"USGS indisponível: {exc}"), 502
    out = []
    for q in quakes:
        if q.magnitude is None or q.magnitude < mag_min:
            continue
        d = q.to_dict()
        d["analyze_url"] = analysis_form_url(q.title or q.place, epicenter_bbox(q.lon, q.lat),
                                             event_date(q.time), _max_deg())
        out.append(d)
    return jsonify(source=source, count=len(out), quakes=out)


@events_bp.get("/api/eventos/ems")
def list_activations():
    """Ativações de terremoto do Copernicus EMS (com as AOIs de cada uma)."""
    repo = ems_repository(current_app.config)
    try:
        acts = repo.list_activations("Earthquake")
    except (HttpError, EmsError) as exc:
        return jsonify(error=f"Copernicus EMS indisponível: {exc}"), 502
    start, end = request.args.get("inicio"), request.args.get("fim")
    if start:
        acts = [a for a in acts if a.event_time[:10] >= start]
    if end:
        acts = [a for a in acts if a.event_time[:10] <= end]
    out, errors = [], []
    for a in acts:
        try:
            out.append(_with_links(repo.get_activation(a.code)))
        except (HttpError, EmsError) as exc:
            errors.append(f"{a.code}: {exc}")
            out.append(_with_links(a))
    return jsonify(count=len(out), activations=out, errors=errors,
                   note="A API pública do Copernicus EMS só lista ativações a partir da EMSR656.")


@events_bp.get("/api/eventos/ems/<code>")
def activation_detail(code: str):
    try:
        return jsonify(_with_links(ems_repository(current_app.config).get_activation(code)))
    except EmsNotPublic as exc:
        return jsonify(error=str(exc), not_public=True), 403
    except (HttpError, EmsError) as exc:
        return jsonify(error=str(exc)), 502


@events_bp.get("/api/eventos/ems/<code>/aoi/<int:aoi>/grading.geojson")
def grading(code: str, aoi: int):
    """Prédios do produto de grading (GRA) com o grau de dano — camada "Dano oficial"."""
    try:
        return jsonify(ems_repository(current_app.config).grading_geojson(code.upper(), aoi))
    except EmsNotPublic as exc:
        return jsonify(error=str(exc), not_public=True), 403
    except (HttpError, EmsError, ValueError) as exc:
        return jsonify(error=str(exc)), 502


def _with_links(act) -> dict:
    d = act.to_dict()
    when = event_date(act.event_time) if act.event_time else date.today() - timedelta(days=1)
    for aoi in d["aois"]:
        aoi["analyze_url"] = analysis_form_url(f"{act.name} – {aoi['name']}", aoi["bbox"], when, _max_deg(),
                                               act.code, aoi["number"])
    return d
