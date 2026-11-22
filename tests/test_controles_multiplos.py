"""Testes: controles de vários anos, limiar comum, escore z e referência do OSM/HOT (prédios destruídos)."""
import json
import os
import sys
from datetime import date

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.analysis import Analysis  # noqa: E402
from app.models.change_detection import NODATA  # noqa: E402
from app.models.control import (build_year_control, choose_control_pair, mean_std_z,  # noqa: E402
                                multi_control_stats, shift_to_year)
from app.models.reference import load_upload  # noqa: E402


def _event():
    return Analysis.new(
        name="Antakya", bbox=[36.12, 36.17, 36.20, 36.24], before_start="2023-01-01", before_end="2023-02-05",
        after_start="2023-02-07", after_end="2023-03-15", max_cloud=40, threshold_method="kstd", k=2.0,
        min_region_px=6, topo_correction=True,
        result={"topo": {"applied": True},
                "before": {"datetime": "2023-01-25T08:00:00Z", "tile": "36SYF", "relative_orbit": 121},
                "after": {"datetime": "2023-02-09T08:00:00Z", "tile": "36SYF", "relative_orbit": 121}})


# ------------------------------------------------------------- criar controles
def test_year_control_window_target_gap_tile_and_orbit():
    c = build_year_control(_event(), 2021)
    assert c.pair_window == {"start": "2021-01-01", "end": "2021-03-15", "target_gap_days": 15,
                             "relative_orbit": 121, "min_valid": 0.0}
    assert c.preferred_tile == "36SYF" and c.control_year == 2021 and c.control_of
    assert c.median_composite is False and c.topo_correction is True and c.k == 2.0


def test_shift_to_year_leap_day():
    assert shift_to_year("2024-02-29", 2023) == "2023-02-28"


def _clear(frac, n=100):
    m = np.zeros(n, bool)
    m[: int(frac * n)] = True
    return m


def test_choose_pair_prefers_target_gap_and_valid_area():
    d = [date(2021, 1, 10), date(2021, 1, 25), date(2021, 1, 30), date(2021, 3, 10)]
    clears = [_clear(1.0), _clear(0.70), _clear(0.95), _clear(1.0)]
    best = choose_control_pair(d, clears, target_gap_days=15)
    # (10/01, 30/01): 20 dias, 95% → 0,95 − 0,05 = 0,90 vence (10/01, 25/01): 15 dias, 70% → 0,70
    # e vence (10/01, 10/03): 59 dias, 100% → 1 − 0,44 = 0,56
    assert (best["before"], best["after"], best["gap_days"]) == (0, 2, 20)
    assert best["common_valid"] == pytest.approx(0.95)


def test_choose_pair_uses_intersection_of_clear_masks():
    a = np.array([True, True, False, False]); b = np.array([False, False, True, True])
    c = np.array([True, True, True, False])
    best = choose_control_pair([date(2021, 1, 1), date(2021, 1, 16), date(2021, 1, 17)], [a, b, c], 15)
    assert (best["before"], best["after"]) == (0, 2)      # a∩b = 0%; a∩c = 50%


def test_choose_pair_needs_two_dates():
    assert choose_control_pair([date(2021, 1, 1)], [_clear(1)], 15) is None


# ---------------------------------------------------------- z e limiar comum
def test_mean_std_z():
    r = mean_std_z(5.0, [1.0, 2.0, 3.0])
    assert r["controls_mean"] == 2.0 and r["controls_std"] == 1.0 and r["z"] == 3.0
    assert mean_std_z(5.0, [2.0])["z"] is None            # 1 controle: sem desvio, sem z


def _layer(label, magnitude, threshold, invalid=None):
    mag = np.array(magnitude, np.float32)
    classes = np.where(mag > threshold, 4, 0).astype(np.uint8)
    if invalid is not None:
        classes[invalid] = NODATA
        mag[invalid] = np.nan
    return {"label": label, "classes": classes, "magnitude": mag, "threshold": threshold}


def test_common_threshold_reclassifies_everyone_and_uses_common_area():
    # 1 linha × 10 pixels; os 5 primeiros são urbanos
    urban = np.array([[True] * 5 + [False] * 5])
    # evento: limiar próprio alto (0,30) porque a imagem toda mudou bastante
    ev = _layer("Evento", [[0.25, 0.25, 0.25, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.40]], 0.30)
    c1 = _layer("C1", [[0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.15]], 0.10)
    c2 = _layer("C2", [[0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.25, 0.05]], 0.20, invalid=(0, 8))
    s = multi_control_stats(ev, [c1, c2], urban, pixel_area_m2=1e6, min_region_px=1)   # pixel de 1 km²
    assert s["common_threshold"] == pytest.approx(0.15)              # média dos limiares dos controles
    assert s["common_valid_km2"] == pytest.approx(9.0)    # pixel 8 inválido no C2 sai da área comum
    ev_row = s["event"]
    # limiar próprio: só o pixel 9 (0,40) mudou → 1/9; limiar comum 0,15: pixels 0,1,2 e 9 → 4/9
    assert ev_row["own_total_percent"] == pytest.approx(100 / 9, abs=0.01)
    assert ev_row["common_total_percent"] == pytest.approx(400 / 9, abs=0.01)
    assert ev_row["own_urban_percent"] == 0.0 and ev_row["common_urban_percent"] == 60.0
    # evento reclassificado com o limiar do C1 (0,10) e do C2 (0,20)
    assert s["controls"][0]["event_at_this_threshold_urban_percent"] == 60.0
    assert s["controls"][1]["event_at_this_threshold_urban_percent"] == 60.0
    # magnitude urbana do evento: 0,25 0,25 0,25 0,05 0,05
    assert ev_row["urban_magnitude"]["median"] == pytest.approx(0.25)
    assert s["aggregates"]["common_urban_percent"]["controls_mean"] == 0.0
    assert s["aggregates"]["common_urban_percent"]["z"] is None      # desvio 0 → sem z


def test_min_region_filter_applies_to_common_threshold():
    ev = _layer("Evento", [[0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]], 1.0)
    c1 = _layer("C1", [[0.0] * 8], 0.2)
    s = multi_control_stats(ev, [c1], None, 100.0, min_region_px=2)
    assert s["event"]["common_total_percent"] == 0.0                 # pixel isolado é ruído


# ------------------------------------------------- referência OSM/HOT (destruídos)
def test_hotosm_destroyed_buildings_are_read_as_destroyed():
    gj = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"destroyed_building": "yes", "damage_event": "#TürkiyeEQ060223",
                                           "damage_type": "earthquake", "damage_date": "2023-02-06"},
         "geometry": {"type": "Polygon", "coordinates": [[[36.16, 36.20], [36.1601, 36.20], [36.1601, 36.2001],
                                                          [36.16, 36.2001], [36.16, 36.20]]]}},
        {"type": "Feature", "properties": {"destroyed_building": "no"},
         "geometry": {"type": "Point", "coordinates": [36.17, 36.21]}}]}
    ref = load_upload("hotosm_tur_destroyed_buildings.geojson", json.dumps(gj).encode())
    assert ref.grade_counts()["destroyed"] == 1 and len(ref.features) == 1
