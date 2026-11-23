"""Controller das páginas HTML (Início, Nova análise, Análises, Resultado, Metodologia, Sobre)."""
from __future__ import annotations

import json
import os
from typing import List, Optional

import numpy as np
from flask import Blueprint, abort, current_app, redirect, render_template, request, send_file, url_for

from app.models.analysis import Analysis, AnalysisRepository
from app.models.control import find_controls
from app.models.earthquake import EarthquakeRepository
from app.models.buildings import BUILDINGS_FILE, ensure_buildings
from app.models.priority import BANDS
from app.models.reference import source_label
from app.models.renderer import save_priority_png, save_thumbnail
from app.models.water import WATER_FILE

main_bp = Blueprint("main", __name__)


@main_bp.app_template_filter("data_br")
def data_br(value) -> str:
    """'2023-02-09' ou '2023-02-09T08:00:00Z' → '09/02/2023'."""
    s = str(value or "")[:10]
    return "/".join(reversed(s.split("-"))) if len(s) == 10 else s


@main_bp.app_template_filter("br")
def br_number(value, digits: int = 2) -> str:
    """Número no formato brasileiro (vírgula decimal)."""
    if value is None:
        return "—"
    return f"{value:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _preset_for(a: Analysis):
    """Exemplo do catálogo ligado à análise (pelo evento ou pela ativação do Copernicus EMS)."""
    repo = EarthquakeRepository()
    if a.event_id and repo.get(a.event_id):
        return repo.get(a.event_id)
    return next((e for e in repo.all() if a.ems_activation and e.ems_activation == a.ems_activation), None)


def _reference_label(source: Optional[str]) -> str:
    return source_label(source or "")

THUMB_FILE = "thumb_priority.png"
PRIORITY_PNG = "priority_overlay.png"
THUMB_W = 640
FORM_PARAMS = ("refazer", "bbox", "nome", "ems", "aoi", "before_start", "before_end", "after_start", "after_end")


def _repo() -> AnalysisRepository:
    return AnalysisRepository(current_app.config["RESULTS_DIR"])


# ------------------------------------------------------------------ dados das páginas
def _validation(repo: AnalysisRepository, a: Analysis) -> Optional[dict]:
    path = os.path.join(repo.directory(a.id), "validation.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        v = json.load(fh)
    return v if v.get("status") == "done" else None


def analysis_card(repo: AnalysisRepository, a: Analysis) -> dict:
    """O que a galeria mostra de cada análise (os controles vêm agrupados dentro dela)."""
    r = a.result or {}
    stats = r.get("statistics") or {}
    controls = find_controls(repo, a)
    v = _validation(repo, a) if a.status == "done" else None
    return {
        "id": a.id, "name": a.name, "status": a.status, "created_at": a.created_at,
        "featured": a.featured, "error": a.error,
        "before": [a.before_start, a.before_end], "after": [a.after_start, a.after_end],
        "changed_percent": stats.get("changed_percent"),
        "valid_area_km2": stats.get("valid_area_km2"),
        "n_controls": len([c for c in controls if c.status == "done" and not c.control_excluded]),
        "n_controls_total": len(controls),
        "validated": v is not None,
        "validation_source": v.get("source") if v else None,
        "has_thumb": a.status == "done" and "transform" in (r.get("grid") or {}),
        "top10_n_cells": _cached_priority(repo, a).get("top10_n_cells"),
        "auc200": main_auc(v),
    }


def main_auc(v: Optional[dict]) -> Optional[float]:
    """AUC das células de 200 m mostrada no site: a do resultado principal (evento × controles, nas células válidas em todos os anos); sem controles, a da avaliação por quadrícula do evento."""
    if not v:
        return None
    gc = (((v.get("grid_controls") or {}).get("sizes") or {}).get("200") or {})
    if gc.get("analyses") and "error" not in gc:
        return gc["analyses"][0].get("auc")
    return ((((v.get("grid") or {}).get("sizes") or {}).get("200") or {}).get("auc") or {}).get("value")


def _cached_priority(repo: AnalysisRepository, a: Analysis) -> dict:
    """Resumo da prioridade já calculado (priority_200.json); vazio se a página da análise ainda não foi aberta."""
    path = os.path.join(repo.directory(a.id), "priority_200.json")
    if a.status != "done" or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def case_study(repo: AnalysisRepository, a: Analysis) -> Optional[dict]:
    """Estudo de caso: análise com validação E comparação com controles (AUC 200 m evento × média)."""
    v = _validation(repo, a)
    gc = (v or {}).get("grid_controls") or {}
    b200 = (gc.get("sizes") or {}).get("200") or {}
    if not b200 or "error" in b200 or not b200.get("analyses"):
        return None
    event = b200["analyses"][0]
    mean = b200.get("controls_mean") or {}
    delong = mean.get("vs_event_delong") or {}
    preset = _preset_for(a)
    return {
        "id": a.id, "name": a.name,
        "where": preset.name if preset else (a.ems_activation and f"Copernicus EMS {a.ems_activation}") or "",
        "magnitude": preset.magnitude if preset else None,
        "reference": _reference_label(v.get("source")),
        "auc_event": event.get("auc"), "auc_event_ci": event.get("auc_ci95"),
        "auc_controls": mean.get("auc"), "n_controls": len(b200["analyses"]) - 1,
        "p_value": delong.get("p_value"), "diff": delong.get("diff"),
        "n_cells": b200.get("n_cells"),
        "triage10": (event.get("triage") or [{}])[0].get("share_of_damaged"),
        "dates": [a.result["before"]["datetime"][:10], a.result["after"]["datetime"][:10]],
    }


def seismogram_path(width: int = 1200, height: int = 180, n: int = 900, seed: int = 6) -> str:
    """Traço de sismograma (SVG) para o início: ruído, chegada da onda P, pico da onda S e decaimento."""
    rng = np.random.default_rng(seed)
    t = np.linspace(0, 1, n)
    mid = height / 2
    noise = rng.normal(0, 1, n)
    env = 0.012 + 0.10 * (t > 0.40) * np.exp(-(t - 0.40) * 9) + 0.88 * (t > 0.47) * np.exp(-(t - 0.47) * 6.5)
    osc = np.sin(2 * np.pi * 85 * t + rng.uniform(0, 6.28)) * 0.55 + noise * 0.45
    y = mid - osc * env * (height * 0.47)
    x = t * width
    return "M" + " L".join(f"{a:.1f} {b:.1f}" for a, b in zip(x, y))


def hero_compare(repo: AnalysisRepository, ordered: List[Analysis]) -> Optional[dict]:
    """Análise do comparador antes/depois do início: a destacada de Antakya; senão a primeira destacada."""
    usable = [a for a in ordered if a.status == "done" and "transform" in ((a.result or {}).get("grid") or {})
              and os.path.exists(os.path.join(repo.directory(a.id), "before.png"))]
    featured = [a for a in usable if a.featured]
    pick = (next((a for a in featured if "antakya" in a.name.lower()), None) or (featured[0] if featured else None))
    if pick is None:
        return None
    g, r = pick.result["grid"], pick.result
    preset = _preset_for(pick)
    return {"id": pick.id, "name": pick.name, "place": preset.name if preset else pick.name,
            "width": int(g["width"]), "height": int(g["height"]),
            "before": url_for("api.get_file", analysis_id=pick.id, filename="before.png"),
            "after": url_for("api.get_file", analysis_id=pick.id, filename="after.png"),
            "priority": url_for("main.priority_png", analysis_id=pick.id),
            "dates": [r["before"]["datetime"][:10], r["after"]["datetime"][:10]],
            "bands": [(label, color) for _, key, label, color in BANDS if key != "rest"]}


def top_level(repo: AnalysisRepository) -> List[Analysis]:
    """Análises principais: os controles não aparecem como itens separados."""
    vitrine = current_app.config.get("VITRINE")
    return [a for a in repo.list() if not a.control_of and (a.featured or not vitrine)]


# ------------------------------------------------------------------ páginas
@main_bp.route("/")
def index():
    if any(k in request.args for k in FORM_PARAMS):
        return redirect(url_for("main.new_analysis", **request.args.to_dict()))
    repo = _repo()
    cases, seen = [], set()
    ordered = sorted(top_level(repo), key=lambda a: not a.featured)
    for a in ordered:
        if a.status != "done":
            continue
        key = a.ems_activation or a.event_id or a.name
        if key in seen:
            continue
        c = case_study(repo, a)
        if c:
            seen.add(key)
            cases.append(c)
    n_done = sum(1 for a in top_level(repo) if a.status == "done")
    return render_template("home.html", active_page="inicio", cases=cases[:2], n_done=n_done,
                           seismogram=seismogram_path(), compare=hero_compare(repo, ordered))


@main_bp.route("/nova")
def new_analysis():
    return render_template(
        "new.html", active_page="nova",
        earthquakes=[e.to_dict() for e in EarthquakeRepository().all()],
        max_bbox_deg=current_app.config["MAX_BBOX_DEG"],
    )


@main_bp.route("/analises")
def analyses():
    repo = _repo()
    cards = [analysis_card(repo, a) for a in top_level(repo)]
    cards.sort(key=lambda c: not c["featured"])
    return render_template("analyses.html", active_page="analises", cards=cards)


@main_bp.route("/analise/<analysis_id>")
def analysis_page(analysis_id: str):
    repo = _repo()
    analysis = repo.get(analysis_id)
    if analysis is None:
        abort(404)
    event = EarthquakeRepository().get(analysis.event_id) if analysis.event_id else None
    ems = None
    if analysis.ems_activation:
        ems = {"code": analysis.ems_activation, "aoi": analysis.ems_aoi}
    elif event and event.ems_activation:
        ems = {"code": event.ems_activation, "aoi": event.ems_aoi}
    validation = _validation(repo, analysis) if analysis.status == "done" else None
    controls = find_controls(repo, analysis) if not analysis.control_of else []
    folder = repo.directory(analysis.id)
    available = set(os.listdir(folder)) if os.path.isdir(folder) else set()
    return render_template("analysis.html", active_page="analises", analysis=analysis, event=event, ems=ems,
                           validation=validation, controls=controls, available=available, auc=main_auc(validation),
                           parent=repo.get(analysis.control_of) if analysis.control_of else None)


def _cached_png(analysis_id: str, filename: str, render) :
    """PNG gerado a partir da análise e refeito quando as classes ou a máscara de água mudam."""
    repo = _repo()
    a = repo.get(analysis_id)
    if a is None or a.status != "done" or "transform" not in ((a.result or {}).get("grid") or {}):
        abort(404)
    folder = repo.directory(a.id)
    path = os.path.join(folder, filename)
    cfg = current_app.config
    ensure_buildings(folder, a.result, cfg["REFERENCE_DIR"], cfg["OVERPASS_URL"])
    deps = [os.path.join(folder, f) for f in ("change_classes.tif", WATER_FILE, BUILDINGS_FILE)
            if os.path.exists(os.path.join(folder, f))]
    if not os.path.exists(path) or any(os.path.getmtime(path) < os.path.getmtime(d) for d in deps):
        render(a, folder, path)
    return send_file(path, mimetype="image/png", max_age=300)


@main_bp.route("/analise/<analysis_id>/miniatura.png")
def thumbnail(analysis_id: str):
    """Miniatura: imagem 'depois' com as faixas de prioridade de vistoria (células de 200 m) por cima."""
    return _cached_png(analysis_id, THUMB_FILE, lambda a, folder, out: save_thumbnail(a.result, folder, out, THUMB_W))


@main_bp.route("/analise/<analysis_id>/prioridade.png")
def priority_png(analysis_id: str):
    """Só as faixas de prioridade (fundo transparente), na grade da análise: vai por cima da imagem 'depois'."""
    return _cached_png(analysis_id, PRIORITY_PNG, lambda a, folder, out: save_priority_png(a.result, folder, out))


@main_bp.route("/metodologia")
def methodology():
    repo = _repo()
    figures, cases_used = [], set()
    for a in top_level(repo):
        c = case_study(repo, a) if a.status == "done" else None
        key = a.ems_activation or a.event_id or a.name
        if c and len(cases_used) < 2 and key not in cases_used:
            cases_used.add(key)
            folder = repo.directory(a.id)
            for fname, caption in (
                    ("validation_grid_controls_roc.png", "Curvas ROC (células de 200 m) do evento e dos controles"),
                    ("validation_grid_200.png", "Dispersão % dano × % mudança e curva de triagem (200 m)"),
                    ("validation_neighborhood.png", "Recall e alarme falso pela distância ao prédio destruído mais próximo")):
                if os.path.exists(os.path.join(folder, fname)):
                    figures.append({"case": c["name"], "src": url_for("api.get_file", analysis_id=a.id, filename=fname),
                                    "caption": caption, "file": fname})
    return render_template("methodology.html", active_page="metodologia", figures=figures)


@main_bp.route("/sobre")
def about():
    return render_template("about.html", active_page="sobre")
