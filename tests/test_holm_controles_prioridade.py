"""Testes: correção de Holm, regra de área válida dos controles, recuo de ano e prioridade de vistoria."""
import os
import sys
from datetime import date
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.change_detection import NODATA  # noqa: E402
from app.models.control import choose_control_pair, run_control_series  # noqa: E402
from app.models.grid_controls import apply_holm, holm  # noqa: E402
from app.models.priority import priority_cells, priority_summary  # noqa: E402


def test_holm_known_values():
    assert holm([0.01, 0.04, 0.03, 0.20]) == pytest.approx([0.04, 0.09, 0.09, 0.20])
    assert holm([0.5, None, 0.9]) == [pytest.approx(1.0), None, pytest.approx(1.0)]


def test_apply_holm_primary_is_not_corrected():
    d = lambda p: {"p_value": p}  # noqa: E731
    res = {"sizes": {
        "100": {"analyses": [{}, {"vs_event_delong": d(0.01)}], "controls_mean": {"vs_event_delong": d(0.02)}},
        "200": {"analyses": [{}, {"vs_event_delong": d(0.03)}], "controls_mean": {"vs_event_delong": d(0.04)}}}}
    out = apply_holm(res)
    primary = out["sizes"]["200"]["controls_mean"]["vs_event_delong"]
    assert primary["primary"] is True and "p_holm" not in primary
    assert out["multiple_testing"]["family_size"] == 3
    assert out["sizes"]["100"]["analyses"][1]["vs_event_delong"]["p_holm"] == pytest.approx(0.03)


def _clear(frac, n=100):
    m = np.zeros(n, bool); m[: int(frac * n)] = True
    return m


def test_pair_below_minimum_valid_area_is_rejected():
    d = [date(2022, 1, 20), date(2022, 2, 9)]
    assert choose_control_pair(d, [_clear(0.30), _clear(0.30)], 15, min_common_valid=0.70) is None
    assert choose_control_pair(d, [_clear(0.80), _clear(0.90)], 15, min_common_valid=0.70)["common_valid"] == 0.8


def test_series_goes_back_until_three_valid_years():
    outcome = {2022: "error", 2021: "done", 2020: "done", 2019: "error", 2018: "done", 2017: "done"}
    ran = []

    def run_year(y):
        ran.append(y)
        return SimpleNamespace(id=f"c{y}", status=outcome[y], error=None if outcome[y] == "done" else f"sem par em {y}")

    res = run_control_series(None, run_year, target=3, start_year=2022, min_year=2017)
    assert ran == [2022, 2021, 2020, 2019, 2018]
    assert res["ok_years"] == [2021, 2020, 2018] and res["complete"]
    assert [t["reason"] for t in res["tried"] if t["status"] == "error"] == ["sem par em 2022", "sem par em 2019"]
    res2 = run_control_series(None, run_year, target=3, start_year=2020, min_year=2019, existing_ok_years=[2021])
    assert res2["ok_years"] == [2021, 2020] and not res2["complete"]


def test_priority_bands_ties_and_zero_change():
    classes = np.zeros((1, 20), np.uint8)
    classes[0, :2] = 4
    classes[0, 2:4] = 4
    classes[0, 19] = NODATA
    cells = priority_cells(classes, 1)
    assert len(cells["id"]) == 19
    top = cells["band"][cells["change"] > 0]
    assert set(top) == {"top10"}
    assert set(cells["band"][cells["change"] == 0]) == {"rest"}
    s = priority_summary(cells, 40000.0)
    assert s["bands"][0]["n_cells"] == 4 and s["top10_area_km2"] == pytest.approx(0.16)
    assert s["n_cells"] == 19


def test_priority_ranking_order():
    classes = np.zeros((10, 100), np.uint8)
    for k in range(10):
        classes[: k, k * 10:(k + 1) * 10] = 4
    cells = priority_cells(classes, 10)
    order = dict(zip(cells["col"].tolist(), cells["band"].tolist()))
    assert order[9] == "top10" and order[8] == "p10_20" and order[7] == "p20_30" and order[0] == "rest"
