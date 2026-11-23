"""Testes SEM rede: Copernicus EMS e USGS usando respostas JSON reais salvas em tests/fixtures/."""
import io
import json
import os
import sys
import zipfile
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.ems import EmsError, EmsNotPublic, EmsRepository  # noqa: E402
from app.models.http_cache import HttpError  # noqa: E402
from app.models.planning import analysis_dates, analysis_form_url, clamp_bbox, epicenter_bbox  # noqa: E402
from app.models.usgs import UsgsRepository, parse_feed  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
LIST_URL = "https://ems.test/list/"
DETAIL_URL = "https://ems.test/detail/"


def fixture(name):
    with open(os.path.join(FIX, name), "rb") as fh:
        return fh.read()


def _gra_zip() -> bytes:
    pts = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-8.18, 30.99]}, "properties": {"damage_gra": "Destroyed"}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-8.17, 30.98]}, "properties": {"damage_gra": "Damaged"}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-8.16, 30.97]}, "properties": {"damage_gra": "Possibly damaged"}}]}
    aoi = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": {
        "type": "Polygon", "coordinates": [[[-8.23, 30.94], [-8.14, 30.94], [-8.14, 31.02], [-8.23, 31.02], [-8.23, 30.94]]]}}]}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("EMSR695_AOI07_GRA_PRODUCT_v2/EMSR695_AOI07_GRA_PRODUCT_builtUpP_v1.json", json.dumps(pts))
        z.writestr("EMSR695_AOI07_GRA_PRODUCT_v2/EMSR695_AOI07_GRA_PRODUCT_areaOfInterestA_v1.json", json.dumps(aoi))
    return buf.getvalue()


class FakeWeb:
    """Responde URLs com as fixtures e conta as chamadas (para testar o cache)."""

    def __init__(self):
        self.calls = []
        page2 = json.loads(fixture("ems_list_page2.json"))
        page2["next"] = None
        p1 = json.loads(fixture("ems_list_page1.json"))
        p1["next"] = LIST_URL + "?limit=3&offset=3"
        self.routes = {
            LIST_URL + "?limit=100&offset=0": json.dumps(p1).encode(),
            LIST_URL + "?limit=3&offset=3": json.dumps(page2).encode(),
            DETAIL_URL + "?code=EMSR695": fixture("ems_detail_EMSR695.json"),
            "https://rapidmapping.emergency.copernicus.eu/backend/EMSR695/AOI07/GRA_PRODUCT/EMSR695_AOI07_GRA_PRODUCT_v2.zip": _gra_zip(),
            "https://usgs.test/week": fixture("usgs_4.5_week.json"),
        }

    def __call__(self, url):
        self.calls.append(url)
        if url == DETAIL_URL + "?code=EMSR648":
            raise HttpError(403, fixture("ems_detail_EMSR648_403.json").decode())
        if url not in self.routes:
            raise HttpError(404, f"não encontrado: {url}")
        return self.routes[url]


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def web():
    return FakeWeb()


@pytest.fixture
def ems(tmp_path, web):
    clock = Clock()
    repo = EmsRepository(LIST_URL, DETAIL_URL, str(tmp_path), list_ttl_s=3600, fetch=web, clock=clock)
    repo.clock = clock
    return repo


# ------------------------------------------------------------------- EMS
def test_list_follows_pagination_and_filters_earthquakes(ems, web):
    acts = ems.list_activations("Earthquake")
    assert [a.code for a in acts] == ["EMSR934"]
    a = acts[0]
    assert a.status == ("fechada" if a.closed else "aberta")
    assert a.countries == ["Panama"] and a.event_time.startswith("2026-10-09")
    assert a.centroid and len(a.centroid) == 2
    assert len(ems.list_activations(None)) == 6
    assert sum(LIST_URL in c for c in web.calls) == 2


def test_list_cache_expires_after_one_hour(ems, web):
    ems.list_activations()
    ems.clock.t += 3599
    ems.list_activations()
    assert sum(LIST_URL in c for c in web.calls) == 2
    ems.clock.t += 2
    ems.list_activations()
    assert sum(LIST_URL in c for c in web.calls) == 4


def test_detail_parses_aois_and_latest_gra_product(ems):
    act = ems.get_activation("emsr695")
    assert act.code == "EMSR695" and act.closed and act.status == "fechada"
    assert act.countries == ["Morocco"] and act.category == "Earthquake"
    aoi7 = next(a for a in act.aois if a.number == 7)
    assert aoi7.name == "Talat-NYaaqoub"
    assert aoi7.gra_download.endswith("EMSR695_AOI07_GRA_PRODUCT_v2.zip") and aoi7.gra_version == 2
    assert "WorldView-3" in aoi7.gra_image
    d = aoi7.to_dict()
    assert d["has_grading"] and d["geometry"]["type"] == "Polygon"
    assert d["bbox"][0] == pytest.approx(-8.231613, abs=1e-5)


def test_detail_403_becomes_clear_not_public_error(ems):
    with pytest.raises(EmsNotPublic) as exc:
        ems.get_activation("EMSR648")
    assert "403" in str(exc.value) and "EMSR656" in str(exc.value)


def test_invalid_code_rejected(ems):
    with pytest.raises(EmsError):
        ems.get_activation("EMSR6;drop")


def test_gra_product_downloaded_once_and_read(ems, web):
    ref = ems.read_grading("EMSR695", 7)
    assert ref.grade_counts() == {"none": 0, "possibly": 1, "damaged": 1, "destroyed": 1}
    assert ref.aoi is not None
    ems.clock.t += 10 * 86400
    ems.read_grading("EMSR695", 7)
    assert sum(c.endswith("_GRA_PRODUCT_v2.zip") for c in web.calls) == 1
    gj = ems.grading_geojson("EMSR695", 7)
    assert {f["properties"]["grade"] for f in gj["features"]} == {"destroyed", "damaged", "possibly"}


def test_aoi_without_gra_raises(ems):
    with pytest.raises(EmsError):
        ems.gra_zip_path("EMSR695", 99)


# ------------------------------------------------------------------ USGS
def test_parse_usgs_feed():
    quakes = parse_feed(json.loads(fixture("usgs_4.5_week.json")))
    assert len(quakes) == 5
    q = quakes[0]
    assert q.magnitude >= 4.5 and q.place and q.url.startswith("https://earthquake.usgs.gov/")
    assert q.time.endswith("+00:00") and isinstance(q.depth_km, float)
    assert all(a.time >= b.time for a, b in zip(quakes, quakes[1:]))


def test_usgs_feed_cached(tmp_path, web):
    repo = UsgsRepository({"week": "https://usgs.test/week"}, "https://usgs.test/q", str(tmp_path), fetch=web)
    assert len(repo.feed("week")) == 5 and len(repo.feed("week")) == 5
    assert web.calls.count("https://usgs.test/week") == 1
    with pytest.raises(ValueError):
        repo.feed("year")


def test_usgs_skips_non_earthquakes():
    data = {"features": [
        {"id": "a", "geometry": {"coordinates": [1, 2, 3]}, "properties": {"mag": 5, "time": 0, "type": "quarry blast"}},
        {"id": "b", "geometry": {"coordinates": [1, 2, 3]}, "properties": {"mag": 5, "time": 0, "type": "earthquake"}}]}
    assert [q.id for q in parse_feed(data)] == ["b"]


# ---------------------------------------------------------------- planejamento
def test_analysis_dates_35_days():
    d = analysis_dates(date(2023, 2, 6))
    assert d == {"before_start": "2023-01-02", "before_end": "2023-02-05",
                 "after_start": "2023-02-07", "after_end": "2023-03-13"}


def test_clamp_bbox_keeps_center():
    b = clamp_bbox([36.0, 36.0, 37.0, 36.2], 0.3)
    assert b == pytest.approx([36.35, 36.0, 36.65, 36.2])
    assert clamp_bbox([1, 1, 1.1, 1.1], 0.3) == pytest.approx([1, 1, 1.1, 1.1])


def test_form_url_contains_everything():
    url = analysis_form_url("EMSR695 – Talat", [-8.23, 30.94, -8.14, 31.02], date(2023, 9, 8), 0.3, "EMSR695", 7)
    assert url.startswith("/?") and "ems=EMSR695" in url and "aoi=7" in url
    assert "before_end=2023-09-07" in url and "after_start=2023-09-09" in url
    assert epicenter_bbox(10, 20) == [9.95, 19.95, 10.05, 20.05]


# ------------------------------------------------------- rotas (Flask, sem rede)
@pytest.fixture
def client(tmp_path, monkeypatch, web):
    from app import create_app
    from app.controllers import events_controller
    from config import Config

    class TestConfig(Config):
        RESULTS_DIR = str(tmp_path / "results")
        REFERENCE_DIR = str(tmp_path / "refs")
        CACHE_DIR = str(tmp_path / "cache")
        EMS_LIST_URL, EMS_DETAIL_URL = LIST_URL, DETAIL_URL
        USGS_FEEDS = {"week": "https://usgs.test/week", "month": "https://usgs.test/week"}

    monkeypatch.setattr(events_controller, "ems_repository",
                        lambda cfg: EmsRepository(LIST_URL, DETAIL_URL, cfg["CACHE_DIR"], fetch=web))
    monkeypatch.setattr(events_controller, "usgs_repository",
                        lambda cfg: UsgsRepository(cfg["USGS_FEEDS"], "https://usgs.test/q", cfg["CACHE_DIR"], fetch=web))
    with create_app(TestConfig).test_client() as c:
        yield c


def test_events_page_and_apis(client):
    assert client.get("/eventos").status_code == 200
    q = client.get("/api/eventos/terremotos?periodo=week&mag_min=4.5").get_json()
    assert q["count"] == 5 and q["quakes"][0]["analyze_url"].startswith("/?")
    assert client.get("/api/eventos/terremotos?periodo=year").status_code == 400
    e = client.get("/api/eventos/ems").get_json()
    assert [a["code"] for a in e["activations"]] == ["EMSR934"]
    assert e["errors"]
    from app.controllers import events_controller
    with client.application.app_context():
        repo = events_controller.ems_repository(client.application.config)
        d = events_controller._with_links(repo.get_activation("EMSR695"))
    aoi7 = next(a for a in d["aois"] if a["number"] == 7)
    assert "ems=EMSR695" in aoi7["analyze_url"] and "aoi=7" in aoi7["analyze_url"]
    g = client.get("/api/eventos/ems/EMSR695/aoi/7/grading.geojson").get_json()
    assert len(g["features"]) == 3


def test_analysis_keeps_ems_link(client, monkeypatch):
    from app.controllers import analysis_controller
    monkeypatch.setattr(analysis_controller.ChangeDetectionService, "start", lambda self, a: self.repository.save(a))
    body = {"bbox": [-8.23, 30.94, -8.14, 31.02], "before_start": "2023-08-04", "before_end": "2023-09-07",
            "after_start": "2023-09-09", "after_end": "2023-10-13", "ems_activation": "emsr695", "ems_aoi": "7"}
    r = client.post("/api/analises", json=body)
    assert r.status_code == 202
    a = client.get(f"/api/analises/{r.get_json()['id']}").get_json()
    assert a["ems_activation"] == "EMSR695" and a["ems_aoi"] == 7
    bad = client.post("/api/analises", json={**body, "ems_activation": "XYZ"})
    assert bad.status_code == 400
