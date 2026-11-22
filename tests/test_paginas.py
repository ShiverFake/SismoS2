"""Testes das páginas do site: status 200, controles agrupados na galeria, início com números reais e
redirecionamento das URLs antigas."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.analysis import Analysis, AnalysisRepository  # noqa: E402


def _result(changed=3.14, area=55.8):
    return {"statistics": {"changed_percent": changed, "valid_area_km2": area, "changed_area_km2": 1.74,
                           "cva_threshold": 0.15, "valid_fraction": 0.99},
            "grid": {"ground_resolution_m": 10.0},
            "before": {"datetime": "2023-01-25T08:00:00Z", "tile": "36SYF"},
            "after": {"datetime": "2023-02-09T08:00:00Z", "tile": "36SYF"}}


def _validation(auc_event, auc_controls, p, triage):
    """validation.json mínimo com a comparação evento × controles nas células de 200 m."""
    return {"status": "done", "source": "Copernicus EMS EMSR695 – AOI 07",
            "grid": {"sizes": {"200": {"auc": {"value": auc_event}}}},
            "grid_controls": {"sizes": {"200": {
                "n_cells": 371,
                "analyses": [{"label": "Evento", "auc": auc_event, "auc_ci95": [0.75, 0.9],
                              "triage": [{"share_of_damaged": triage}]},
                             {"label": "Controle 2020", "auc": 0.55}, {"label": "Controle 2021", "auc": 0.57}],
                "controls_mean": {"auc": auc_controls, "vs_event_delong": {"p_value": p, "diff": auc_event - auc_controls}},
            }}}}


@pytest.fixture
def site(tmp_path):
    from app import create_app
    from config import Config

    class TestConfig(Config):
        RESULTS_DIR = str(tmp_path / "results")
        REFERENCE_DIR = str(tmp_path / "refs")
        TESTING = True

    app = create_app(TestConfig)
    repo = AnalysisRepository(TestConfig.RESULTS_DIR)
    base = dict(bbox=[-8.5, 30.9, -8.4, 31.0], before_start="2023-07-15", before_end="2023-09-07",
                after_start="2023-09-09", after_end="2023-10-20", status="done")
    event = Analysis.new(name="Marrocos teste", ems_activation="EMSR695", ems_aoi=7, result=_result(), **base)
    controls = [Analysis.new(name=f"Marrocos teste – controle {y}", control_year=y, control_of=event.id,
                             result=_result(1.2), **base) for y in (2020, 2021)]
    event.control_ids = [c.id for c in controls]
    plain = Analysis.new(name="Sem validação", result=_result(4.2, 272.0), **base)
    for a in (event, *controls, plain):
        repo.save(a)
    with open(os.path.join(repo.directory(event.id), "validation.json"), "w", encoding="utf-8") as fh:
        json.dump(_validation(0.71, 0.56, 0.014, 0.38), fh)
    with app.test_client() as c:
        yield c, event, controls, plain


@pytest.mark.parametrize("url", ["/", "/nova", "/analises", "/eventos", "/metodologia", "/sobre"])
def test_pages_return_200(site, url):
    c, *_ = site
    r = c.get(url)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'class="site-header"' in html and "Fontes de dados" in html


def test_result_page_has_tabs_and_kpis(site):
    c, event, *_ = site
    html = c.get(f"/analise/{event.id}").get_data(as_text=True)
    for key in ("mapa", "validacao", "controles", "cenas", "downloads"):
        assert f'data-tab="{key}"' in html and f'id="panel-{key}"' in html
    assert "55,8 km²" in html and "3,1%" in html          # KPIs vêm do resultado salvo
    assert "Células prioritárias" in html and "Mudança detectada</div>" not in html   # o KPI de % saiu da linha
    assert "tende a ficar entre 2 e 5% em qualquer par de datas" in html            # a % foi para Cenas e qualidade
    assert "0,71" in html                                   # AUC da validação (células de 200 m)
    assert 'data-tab="controles"' in html and ">2<" in html  # aba Controles mostra 2 controles


def test_gallery_groups_controls_inside_main_analysis(site):
    c, event, controls, plain = site
    html = c.get("/analises").get_data(as_text=True)
    assert html.count('class="a-card"') == 2               # evento + análise sem validação; controles não
    for ctl in controls:
        assert ctl.name not in html and f"/analise/{ctl.id}" not in html
    assert f"/analise/{event.id}" in html and f"/analise/{plain.id}" in html
    assert 'data-val="sim"' in html and 'data-val="nao"' in html


def test_home_shows_numbers_from_saved_analyses(site):
    c, event, *_ = site
    html = c.get("/").get_data(as_text=True)
    assert "Marrocos – Al Haouz" in html                    # nome do exemplo ligado pela ativação EMSR695
    assert "0,71" in html and "0,56" in html                # AUC do evento e média dos controles
    assert "0,014" in html                                  # p do DeLong
    assert "29/08/2023" not in html and "25/01/2023" in html  # datas das cenas salvas, em dd/mm/aaaa
    assert f"/analise/{event.id}" in html


def test_home_without_case_studies_shows_empty_state(tmp_path):
    from app import create_app
    from config import Config

    class TestConfig(Config):
        RESULTS_DIR = str(tmp_path / "vazio")
        REFERENCE_DIR = str(tmp_path / "refs")
        TESTING = True

    html = create_app(TestConfig).test_client().get("/").get_data(as_text=True)
    assert "Nenhum estudo de caso validado ainda" in html


def test_old_urls_redirect_to_new_pages(site):
    c, event, *_ = site
    r = c.get("/?bbox=36.1,36.19,36.18,36.23&nome=Antakya&before_start=2023-01-01")
    assert r.status_code == 302 and r.headers["Location"].startswith("/nova?")
    assert "bbox=36.1" in r.headers["Location"] and "nome=Antakya" in r.headers["Location"]
    r = c.get(f"/?refazer={event.id}")
    assert r.status_code == 302 and r.headers["Location"] == f"/nova?refazer={event.id}"


def test_unknown_analysis_is_404(site):
    c, *_ = site
    assert c.get("/analise/000000000000").status_code == 404


# ------------------------------------------------------------- ajustes finais
def _payload(**kw):
    p = dict(name="Teste", bbox=[36.1, 36.19, 36.18, 36.23], before_start="2023-01-01", before_end="2023-02-05",
             after_start="2023-02-07", after_end="2023-03-15")
    p.update(kw)
    return p


def test_future_after_period_is_blocked(site):
    from datetime import date, timedelta
    c, *_ = site
    start = date.today() + timedelta(days=3)
    r = c.post("/api/analises", json=_payload(before_start="2025-01-01", before_end="2025-02-01",
                                              after_start=start.isoformat(), after_end=(start + timedelta(days=20)).isoformat()))
    assert r.status_code == 400
    msg = r.get_json()["error"]
    assert "ainda não existem imagens" in msg and start.strftime("%d/%m/%Y") in msg


def test_featured_toggle_moves_card_to_top(site):
    c, event, controls, plain = site
    r = c.post(f"/api/analises/{event.id}/destaque", json={"featured": True})
    assert r.status_code == 200 and r.get_json()["featured"] is True
    html = c.get("/analises").get_data(as_text=True)
    assert html.index(f'data-id="{event.id}"') < html.index(f'data-id="{plain.id}"')
    assert 'class="a-card featured"' in html and 'aria-pressed="true"' in html
    assert c.post(f"/api/analises/{event.id}/destaque", json={"featured": False}).get_json()["featured"] is False


def test_featured_refused_while_processing(site):
    c, event, *_ = site
    from app.models.analysis import Analysis, AnalysisRepository
    repo = AnalysisRepository(c.application.config["RESULTS_DIR"])
    busy = Analysis.new(name="Rodando", bbox=[0, 0, 0.1, 0.1], before_start="2023-01-01", before_end="2023-01-10",
                        after_start="2023-01-11", after_end="2023-01-20", status="processing")
    repo.save(busy)
    assert c.post(f"/api/analises/{busy.id}/destaque", json={"featured": True}).status_code == 409
    assert c.post("/api/analises/000000000000/destaque").status_code == 404


def test_gallery_defaults_to_done_and_shows_error_reason(site):
    c, *_ = site
    from app.models.analysis import Analysis, AnalysisRepository
    repo = AnalysisRepository(c.application.config["RESULTS_DIR"])
    bad = Analysis.new(name="Sem imagem", bbox=[0, 0, 0.1, 0.1], before_start="2023-01-01", before_end="2023-01-10",
                       after_start="2023-01-11", after_end="2023-01-20", status="error",
                       error="Nenhuma cena Sentinel-2 encontrada no período 'depois'.")
    repo.save(bad)
    html = c.get("/analises").get_data(as_text=True)
    assert '<option value="done" selected>Concluídas</option>' in html
    assert 'class="a-error"' in html and "Nenhuma cena Sentinel-2 encontrada" in html


def test_reference_label_comes_from_real_source():
    from app.models.reference import source_kind, source_label
    assert source_kind("Copernicus EMS EMSR695 – AOI 07") == "ems"
    assert source_label("Copernicus EMS EMSR695 – AOI 07", short=True) == "Copernicus EMS"
    assert source_kind("Arquivo enviado: hotosm_antakya_destroyed.geojson") == "hot"
    assert source_label("Arquivo enviado: hotosm_antakya_destroyed.geojson", short=True) == "HOT/OSM"
    assert source_label("Arquivo enviado: unosat_dano.zip") == "Arquivo enviado (unosat_dano.zip)"
    assert source_label("Arquivo enviado: unosat_dano.zip", short=True) == "arquivo enviado"


def test_home_compare_uses_featured_analysis(site):
    c, event, *_ = site
    from app.models.analysis import AnalysisRepository
    repo = AnalysisRepository(c.application.config["RESULTS_DIR"])
    html = c.get("/").get_data(as_text=True)
    assert 'id="cmp"' not in html                       # sem análise destacada: sem comparador
    a = repo.get(event.id)
    a.featured = True
    a.result["grid"].update(transform=[10, 0, 0, 0, -10, 0], width=40, height=30, ground_resolution_m=10)
    repo.save(a)
    open(os.path.join(repo.directory(a.id), "before.png"), "wb").close()
    html = c.get("/").get_data(as_text=True)
    assert 'id="cmp"' in html and "aspect-ratio: 40 / 30" in html
    assert f"/analise/{event.id}/prioridade.png" in html and "Marrocos – Al Haouz" in html


def test_main_auc_prefers_event_vs_controls():
    from app.controllers.main_controller import main_auc
    v = {"grid": {"sizes": {"200": {"auc": {"value": 0.7046}}}},
         "grid_controls": {"sizes": {"200": {"analyses": [{"auc": 0.7056}, {"auc": 0.55}]}}}}
    assert main_auc(v) == 0.7056                       # o mesmo número do Início e do resultado principal
    assert main_auc({"grid": v["grid"]}) == 0.7046     # sem controles: a quadrícula do evento
    assert main_auc(None) is None
