"""Testes da avaliação por quadrícula (células, AUC, Spearman, triagem, GeoJSON e gráfico)."""
import os
import sys

import numpy as np
import pytest
from affine import Affine

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.grid_eval import (auc_score, build_cells, cells_geojson, grid_metrics,  # noqa: E402
                                  plot_grid, roc_curve, triage_table)


def _scene():
    """Grade 20×20 px; células de 10 px → 4 células."""
    h = w = 20
    in_area = np.ones((h, w), bool)
    valid = np.ones((h, w), bool)
    valid[10:, 10:] = False
    changed = np.zeros((h, w), bool)
    changed[:10, :10] = True
    changed[:10, 10:12] = True
    magnitude = changed.astype(float)
    rows, cols, labels, is_osm = [], [], [], []
    for r0, c0, dmg in ((0, 0, 3), (0, 10, 1), (10, 0, 0), (10, 10, 2)):
        for j in range(4):
            rows.append(r0 + 1 + j); cols.append(c0 + 1)
            labels.append(1 if j < dmg else 0); is_osm.append(j < 3)
    rows.append(11); cols.append(1); labels.append(-1); is_osm.append(True)
    return in_area, valid, changed, magnitude, *(np.array(x) for x in (rows, cols, labels, is_osm))


def test_build_cells_counts_and_filters():
    in_area, valid, changed, magnitude, rows, cols, labels, is_osm = _scene()
    c = build_cells(10, in_area, valid, changed, magnitude, rows, cols, labels, is_osm)
    assert c["id"].tolist() == [0, 1, 2]
    assert c["change_share"].tolist() == pytest.approx([1.0, 0.2, 0.0])
    assert c["damage_share"].tolist() == pytest.approx([0.75, 0.25, 0.0])
    assert c["n_buildings"].tolist() == [4, 4, 4]
    assert c["n_osm"].tolist() == [3, 3, 4]


def test_cells_need_three_osm_buildings():
    in_area, valid, changed, magnitude, rows, cols, labels, is_osm = _scene()
    is_osm = np.zeros_like(is_osm)
    is_osm[:2] = True
    c = build_cells(10, in_area, valid, changed, magnitude, rows, cols, labels, is_osm)
    assert len(c["id"]) == 0


def test_auc_and_roc():
    scores = np.array([0.9, 0.8, 0.3, 0.2, 0.1])
    pos = np.array([True, True, False, True, False])
    assert auc_score(scores, pos) == pytest.approx(5 / 6)
    assert auc_score(scores, np.ones(5, bool)) is None
    roc = roc_curve(scores, pos)
    assert roc[0] == [0.0, 0.0] and roc[-1] == [1.0, 1.0]


def test_triage_vs_random_and_oracle():
    change = np.array([0.9, 0.5, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    dmg = np.array([5, 0, 3, 0, 0, 2, 0, 0, 0, 0])
    t = triage_table(change, dmg)
    assert t[0]["n_cells"] == 1 and t[0]["share_of_damaged"] == 0.5 and t[0]["random_expected"] == 0.1
    assert t[0]["lift"] == 5.0 and t[0]["oracle_share"] == 0.5
    assert t[2]["share_of_damaged"] == 0.8 and t[2]["oracle_share"] == 1.0


def test_grid_metrics_end_to_end(tmp_path):
    rng = np.random.default_rng(0)
    n = 200
    damage = rng.beta(0.6, 3, n)
    change = np.clip(0.5 * damage + rng.normal(0, 0.05, n), 0, 1)
    nb = rng.integers(3, 20, n)
    cells = {"id": np.arange(n), "row": np.arange(n) // 20, "col": np.arange(n) % 20, "n_buildings": nb,
             "n_osm": nb, "n_damaged": np.round(damage * nb).astype(int), "damage_share": damage,
             "change_share": change, "magnitude_mean": change, "valid_share": np.ones(n)}
    m = grid_metrics(cells, system_threshold=0.10, n_boot=200)
    assert m["spearman"]["rho"] > 0.7 and m["spearman"]["ci95"][0] <= m["spearman"]["rho"] <= m["spearman"]["ci95"][1]
    assert m["auc"]["value"] > 0.85
    assert m["triage"][0]["lift"] > 1.5
    assert set(m["at_threshold"]) >= {"precision", "recall", "f1", "tp", "fp", "fn", "tn"}
    plot_grid(cells, m, str(tmp_path / "g.png"), "teste")
    assert (tmp_path / "g.png").read_bytes()[:4] == b"\x89PNG"
    few = {k: v[:3] for k, v in cells.items()}
    assert "error" in grid_metrics(few)


def test_cells_geojson_polygons():
    in_area, valid, changed, magnitude, rows, cols, labels, is_osm = _scene()
    c = build_cells(10, in_area, valid, changed, magnitude, rows, cols, labels, is_osm)
    tr = Affine(10.0, 0, 0, 0, -10.0, 1000.0)
    gj = cells_geojson(c, 10, tr)
    assert len(gj["features"]) == 3
    f0 = gj["features"][0]
    assert f0["properties"]["damaged"] is True and f0["properties"]["change"] == 1.0
    ring = f0["geometry"]["coordinates"][0]
    assert len(ring) == 5 and ring[0] == ring[-1]


def test_triage_ties_use_expected_value():
    from app.models.grid_eval import expected_capture
    change = np.array([0.5] + [0.0] * 9)
    dmg = np.array([0] * 9 + [9])
    assert expected_capture(change, dmg, 2) == pytest.approx(1.0)
    t = triage_table(change, dmg, fractions=(0.2,))
    assert t[0]["damaged_buildings"] == pytest.approx(1.0) and t[0]["cells_tied_at_cutoff"] == 9
    flat = np.zeros(10)
    assert triage_table(flat, dmg, fractions=(0.3,))[0]["share_of_damaged"] == pytest.approx(0.3)
