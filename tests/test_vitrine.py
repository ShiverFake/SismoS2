"""Testes da vitrine estática (tools/build_vitrine.py): links relativos, só análises destacadas e modo somente leitura."""
import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from app.models.analysis import Analysis, AnalysisRepository  # noqa: E402
import build_vitrine as bv  # noqa: E402


def test_relative_links_by_depth():
    assert bv.relative("/", 0) == "./"
    assert bv.relative("/analises", 0) == "analises/"
    assert bv.relative("/analise/abc#validacao", 2) == "../../analise/abc/#validacao"
    assert bv.relative("/static/css/site.css", 1) == "../static/css/site.css"
    assert bv.relative("/api/analises/abc/arquivos/after.png?t=1", 2) == "../../api/analises/abc/arquivos/after.png?t=1"
    assert bv.relative("/analise/abc/miniatura.png", 0) == "analise/abc/miniatura.png"


@pytest.fixture
def results(tmp_path, monkeypatch):
    from config import Config
    monkeypatch.setattr(Config, "RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setattr(Config, "REFERENCE_DIR", str(tmp_path / "refs"))
    repo = AnalysisRepository(str(tmp_path / "results"))
    base = dict(bbox=[36.1, 36.19, 36.18, 36.23], before_start="2023-01-01", before_end="2023-02-05",
                after_start="2023-02-07", after_end="2023-03-15", status="done",
                result={"statistics": {"changed_percent": 3.1, "valid_area_km2": 55.8, "changed_area_km2": 1.7},
                        "grid": {"ground_resolution_m": 10.0},
                        "before": {"datetime": "2023-01-25T08:00:00Z"}, "after": {"datetime": "2023-02-09T08:00:00Z"}})
    star = Analysis.new(name="Antakya destacada", featured=True, **base)
    ctl = Analysis.new(name="Antakya destacada – controle 2021", control_of=star.id, control_year=2021, **base)
    star.control_ids = [ctl.id]
    other = Analysis.new(name="Outra sem destaque", **base)
    for a in (star, ctl, other):
        repo.save(a)
    open(os.path.join(repo.directory(star.id), "after.png"), "wb").write(b"png")
    open(os.path.join(repo.directory(star.id), "change_classes.tif"), "wb").write(b"tif")
    return tmp_path, star, ctl, other


def test_build_publishes_only_featured_read_only(results):
    tmp, star, ctl, other = results
    out = str(tmp / "dist")
    info = bv.build(out, None, with_ems=False)
    assert set(info["analyses"]) == {star.id, ctl.id} and other.id not in info["analyses"]
    for page in ("index.html", "analises/index.html", "eventos/index.html", "metodologia/index.html",
                 "sobre/index.html", f"analise/{star.id}/index.html", f"analise/{ctl.id}/index.html"):
        assert os.path.exists(os.path.join(out, page)), page
    assert json.load(open(os.path.join(out, "api", "analises", f"{star.id}.json")))["id"] == star.id
    assert os.path.exists(os.path.join(out, "api", "analises", star.id, "arquivos", "after.png"))
    assert not os.path.exists(os.path.join(out, "api", "analises", star.id, "arquivos", "change_classes.tif"))
    assert os.path.exists(os.path.join(out, "api", "eventos", "ems.json"))

    gallery = open(os.path.join(out, "analises", "index.html"), encoding="utf-8").read()
    assert "Antakya destacada" in gallery and "Outra sem destaque" not in gallery
    assert 'class="star"' not in gallery and "/nova" not in gallery
    assert 'id="f-text"' in gallery and 'id="f-status"' not in gallery and 'id="f-val"' not in gallery
    assert "Células prioritárias" in gallery and "AUC" in gallery and "Mudança<b>" not in gallery
    page = open(os.path.join(out, "analise", star.id, "index.html"), encoding="utf-8").read()
    assert 'window.SISMOS2_STATIC = { base: "../../" }' in page
    assert 'href="../../static/css/site.css"' in page and 'href="/' not in page and 'src="/' not in page
    assert "Versão de demonstração" not in page and "nav-demo" not in page
    nav = page[page.index('<nav class="nav"'):page.index("</nav>")]
    assert [l for l in ("Início", "Análises", "Eventos", "Metodologia", "Sobre") if l in nav] ==         ["Início", "Análises", "Eventos", "Metodologia", "Sobre"] and "Nova análise" not in nav
    assert "<span>Refazer</span>" not in page and "/nova" not in page
    assert page.count("no-vitrine") >= 5
    assert "GeoTIFFs não estão publicados" in page
    assert bv.check_links(out) == []
