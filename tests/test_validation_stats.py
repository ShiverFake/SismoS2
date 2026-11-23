"""Testes: bootstrap dos prédios, distância ao destruído mais próximo e tabela/gráfico de vizinhança."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.validation_stats import (bootstrap_ci, distance_to_nearest_destroyed,  # noqa: E402
                                         neighborhood_table, plot_neighborhood, wilson)


def _sample(n_pos=200, n_neg=800, rec=0.5, fa=0.1, seed=1):
    rng = np.random.default_rng(seed)
    grades = np.array(["destroyed"] * (n_pos // 2) + ["damaged"] * (n_pos // 2) + ["none"] * n_neg
                      + ["possibly"] * 50, dtype=object)
    labels = np.where(grades == "none", 0, np.where(grades == "possibly", -1, 1))
    p = np.where(grades == "none", fa, rec)
    detected = rng.random(len(grades)) < p
    return detected, labels, grades


def test_bootstrap_ci_contains_point_estimate_and_is_reproducible():
    d, l, g = _sample()
    a = bootstrap_ci(d, l, g, n_boot=500, seed=3)
    b = bootstrap_ci(d, l, g, n_boot=500, seed=3)
    assert a == b
    for k in ("precision", "recall", "f1", "kappa", "recall_destroyed", "recall_damaged", "recall_possibly"):
        m = a[k]
        assert m["lo"] <= m["value"] <= m["hi"], k
    assert a["recall"]["n"] == 1000
    assert a["recall_possibly"]["n"] == 50
    assert a["recall"]["hi"] - a["recall"]["lo"] < 0.2


def test_bootstrap_perfect_detector_has_degenerate_interval():
    g = np.array(["destroyed"] * 30 + ["none"] * 70, dtype=object)
    l = np.where(g == "none", 0, 1)
    ci = bootstrap_ci(g != "none", l, g, n_boot=200)
    assert ci["f1"] == {"value": 1.0, "lo": 1.0, "hi": 1.0, "n": 100}
    assert ci["recall_damaged"]["value"] is None


def test_distance_to_nearest_destroyed_excludes_itself():
    lon = np.array([0.0, 0.0001, 0.0003, 0.002])
    lat = np.zeros(4)
    g = np.array(["destroyed", "destroyed", "none", "damaged"], dtype=object)
    d = distance_to_nearest_destroyed(lon, lat, g)
    assert d[0] == pytest.approx(11.13, abs=0.1) and d[1] == pytest.approx(11.13, abs=0.1)
    assert d[2] == pytest.approx(22.26, abs=0.1)
    assert d[3] == pytest.approx(211.5, abs=0.5)
    only_one = distance_to_nearest_destroyed(lon[:1], lat[:1], g[:1])
    assert np.isinf(only_one[0])


def test_neighborhood_table_bands_and_rates():
    dist = np.array([5, 5, 15, 15, 30, 70, 500, 500])
    g = np.array(["damaged", "none", "damaged", "none", "possibly", "none", "damaged", "none"], dtype=object)
    det = np.array([True, True, False, False, True, False, False, False])
    rows = neighborhood_table(dist, det, g)
    assert [r["band"] for r in rows] == ["0–10 m", "10–20 m", "20–50 m", "50–100 m", "> 100 m"]
    assert rows[0]["damaged_recall"]["rate"] == 1.0 and rows[0]["false_alarm"]["rate"] == 1.0
    assert rows[1]["damaged_recall"]["rate"] == 0.0
    assert rows[2]["possibly_recall"] == {"n": 1, "detected": 1, "rate": 1.0, "lo": pytest.approx(0.2065, abs=1e-3), "hi": 1.0}
    assert rows[3]["damaged_recall"]["n"] == 0 and rows[3]["damaged_recall"]["rate"] is None


def test_wilson_interval():
    assert wilson(0, 0) == (None, None)
    lo, hi = wilson(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-3) and hi == pytest.approx(0.5962, abs=1e-3)


def test_plot_is_written(tmp_path):
    rows = neighborhood_table(np.array([5, 15, 30, 70, 500]), np.array([True, False, True, False, False]),
                              np.array(["damaged", "none", "possibly", "none", "damaged"], dtype=object))
    path = tmp_path / "viz.png"
    plot_neighborhood(rows, str(path), "teste")
    assert path.stat().st_size > 5000 and path.read_bytes()[:4] == b"\x89PNG"
