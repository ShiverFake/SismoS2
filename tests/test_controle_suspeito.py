"""Testes: sinais de controle suspeito e exclusão de um controle da média (com motivo registrado)."""
import os
import sys

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.analysis import Analysis, AnalysisRepository  # noqa: E402
from app.models.control import control_flags  # noqa: E402


# ------------------------------------------------------------ sinais (função pura)
def test_flags_for_hazy_control_like_2022():
    flags = control_flags(63.1, 12.0, 0.56, noise=0.067, others_noise=[0.036, 0.033, 0.030])
    assert [f["code"] for f in flags] == ["nuvem_tile", "pouca_area", "ruido_alto"]
    assert "63%" in flags[0]["message"] and "2.0×" in flags[2]["message"]


def test_no_flags_for_clean_control():
    assert control_flags(17.9, 0.1, 0.92, noise=0.033, others_noise=[0.036, 0.030, 0.067]) == []


def test_flags_handle_missing_values():
    assert control_flags(None, None, None, None, []) == []
    assert control_flags(None, 55.0, None, 0.05, []) [0]["code"] == "nuvem_tile"


# ------------------------------------------- fluxo completo pelas rotas (Flask)
N = 20


def _write_layer(folder, magnitude, threshold):
    os.makedirs(folder, exist_ok=True)
    prof = dict(driver="GTiff", width=N, height=N, count=1, crs="EPSG:3857", transform=from_origin(0, N * 10, 10, 10))
    classes = np.where(magnitude > threshold, 4, 0).astype(np.uint8)
    with rasterio.open(os.path.join(folder, "change_classes.tif"), "w", dtype="uint8", **prof) as d:
        d.write(classes, 1)
    with rasterio.open(os.path.join(folder, "magnitude.tif"), "w", dtype="float32", **prof) as d:
        d.write(magnitude.astype(np.float32), 1)


def _result(threshold, cloud_before, valid, day_before, day_after):
    return {"statistics": {"cva_threshold": threshold, "valid_fraction": valid},
            "grid": {"ground_resolution_m": 10.0},
            "before": {"datetime": f"2021-02-{day_before:02d}T08:00:00Z", "cloud_cover": cloud_before, "tile": "36SYF"},
            "after": {"datetime": f"2021-02-{day_after:02d}T08:00:00Z", "cloud_cover": 1.0, "tile": "36SYF"}}


@pytest.fixture
def client(tmp_path):
    from app import create_app
    from config import Config

    class TestConfig(Config):
        RESULTS_DIR = str(tmp_path / "results")
        REFERENCE_DIR = str(tmp_path / "refs")
        TESTING = True

    app = create_app(TestConfig)
    repo = AnalysisRepository(TestConfig.RESULTS_DIR)
    rng = np.random.default_rng(0)
    base = dict(bbox=[36.12, 36.17, 36.2, 36.24], before_start="2023-01-01", before_end="2023-02-05",
                after_start="2023-02-07", after_end="2023-03-15", status="done", min_region_px=1)
    event = Analysis.new(name="Evento", result=_result(0.15, 0.1, 1.0, 1, 16), **base)
    clean = Analysis.new(name="C2021", control_year=2021, result=_result(0.12, 2.0, 1.0, 9, 24), **base)
    hazy = Analysis.new(name="C2022", control_year=2022, result=_result(0.21, 63.0, 0.56, 9, 19), **base)
    clean.control_of = hazy.control_of = event.id
    event.control_ids = [clean.id, hazy.id]
    for a, scale in ((event, 0.04), (clean, 0.03), (hazy, 0.09)):
        repo.save(a)
        _write_layer(repo.directory(a.id), rng.gamma(2.0, scale / 2.0, (N, N)), a.result["statistics"]["cva_threshold"])
    with app.test_client() as c:
        yield c, event, clean, hazy, repo


def test_hazy_control_is_flagged_but_still_counted(client):
    c, event, clean, hazy, _ = client
    data = c.get(f"/api/analises/{event.id}/controle").get_json()
    comp = data["comparison"]
    flags = {s["id"]: [f["code"] for f in s["flags"]] for s in comp["controls_status"]}
    assert flags[clean.id] == []
    assert set(flags[hazy.id]) == {"nuvem_tile", "pouca_area", "ruido_alto"}
    assert comp["n_included"] == 2 and comp["n_done"] == 2


def test_exclude_requires_reason_and_changes_the_mean(client):
    c, event, clean, hazy, repo = client
    before = c.get(f"/api/analises/{event.id}/controle").get_json()["comparison"]
    r = c.post(f"/api/analises/{event.id}/controle/{hazy.id}/excluir", json={"reason": ""})
    assert r.status_code == 400
    r = c.post(f"/api/analises/{event.id}/controle/{hazy.id}/excluir",
               json={"reason": "névoa: 63% de nuvem no tile", "flags": [{"code": "nuvem_tile"}]})
    assert r.status_code == 200 and r.get_json()["excluded"]["reason"] == "névoa: 63% de nuvem no tile"
    saved = repo.get(hazy.id).control_excluded
    assert saved["flags"] == ["nuvem_tile"] and saved["at"]
    after = c.get(f"/api/analises/{event.id}/controle").get_json()["comparison"]
    assert after["n_included"] == 1 and after["n_done"] == 2
    assert [row["label"] for row in after["controls"]] == ["Controle 2021"]
    assert after["common_threshold"] == pytest.approx(0.12) != before["common_threshold"]
    assert c.get(f"/api/analises/{hazy.id}/controle").get_json()["excluded"]["reason"]


def test_include_back_and_cannot_exclude_all(client):
    c, event, clean, hazy, _ = client
    c.post(f"/api/analises/{event.id}/controle/{hazy.id}/excluir", json={"reason": "teste de exclusão"})
    c.post(f"/api/analises/{event.id}/controle/{clean.id}/excluir", json={"reason": "teste de exclusão"})
    data = c.get(f"/api/analises/{event.id}/controle").get_json()
    assert "comparison" not in data and "inclua pelo menos um" in data["comparison_error"]
    c.post(f"/api/analises/{event.id}/controle/{hazy.id}/incluir")
    comp = c.get(f"/api/analises/{event.id}/controle").get_json()["comparison"]
    assert comp["n_included"] == 1 and comp["controls"][0]["label"] == "Controle 2022"


def test_cannot_exclude_control_of_another_event(client):
    c, event, clean, hazy, repo = client
    other = Analysis.new(name="Outro", bbox=[0, 0, 1, 1], before_start="2023-01-01", before_end="2023-01-02",
                         after_start="2023-01-03", after_end="2023-01-04", status="done")
    repo.save(other)
    r = c.post(f"/api/analises/{other.id}/controle/{hazy.id}/excluir", json={"reason": "não pertence"})
    assert r.status_code == 404
