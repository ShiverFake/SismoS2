"""Testes: células com mais de 50% de água ficam fora do ranking de prioridade e da grade de validação."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.grid_eval import build_cells  # noqa: E402
from app.models.priority import priority_cells  # noqa: E402
from app.models.water import (WORLDCOVER_WATER, load_water, save_water, water_cells,  # noqa: E402
                              water_mask)


def test_water_mask_is_union_of_scl_and_worldcover():
    scl_before = np.array([[1, 0], [0, 0]], bool)
    scl_after = np.array([[0, 1], [0, 0]], bool)
    wc = np.array([[0, 0], [WORLDCOVER_WATER, 50]], np.uint8)
    m = water_mask([scl_before, None, scl_after], wc, (2, 2))
    assert m.tolist() == [[True, True], [True, False]]
    assert not water_mask([None], None, (2, 2)).any()


def test_water_cells_uses_strict_half():
    cell = np.array([[0, 0], [1, 1]])
    water = np.array([[True, False], [True, True]])
    assert water_cells(water, cell, 2).tolist() == [False, True]
    assert water_cells(None, cell, 2).tolist() == [False, False]


def test_priority_excludes_water_cells_from_ranking():
    classes = np.zeros((4, 4), np.uint8)
    classes[0:2, 0:2] = 3
    classes[0, 2] = 4
    water = np.zeros((4, 4), bool)
    water[0:2, 0:2] = True
    without = priority_cells(classes, 2)
    with_water = priority_cells(classes, 2, water)
    assert 0 in without["id"] and without["band"][list(without["id"]).index(0)] == "top10"
    assert 0 not in with_water["id"] and with_water["n_water_cells"] == 1
    assert len(with_water["id"]) == len(without["id"]) - 1
    assert with_water["band"][list(with_water["id"]).index(1)] == "top10"


def test_grid_validation_excludes_water_cells():
    shape = (4, 4)
    in_area = np.ones(shape, bool)
    valid = np.ones(shape, bool)
    changed = np.zeros(shape, bool)
    mag = np.zeros(shape, np.float32)
    rows = np.array([0, 0, 1, 0, 0, 1])
    cols = np.array([0, 1, 0, 2, 3, 2])
    labels = np.array([1, 0, 0, 1, 0, 0])
    osm = np.ones(6, bool)
    water = np.zeros(shape, bool)
    water[0:2, 2:4] = True
    assert sorted(build_cells(2, in_area, valid, changed, mag, rows, cols, labels, osm)["id"]) == [0, 1]
    assert list(build_cells(2, in_area, valid, changed, mag, rows, cols, labels, osm, water)["id"]) == [0]


def test_water_file_roundtrip(tmp_path):
    w = np.zeros((3, 5), bool)
    w[1, 2] = True
    save_water(str(tmp_path), w, ["SCL x"])
    assert load_water(str(tmp_path)).tolist() == w.tolist()
    assert load_water(str(tmp_path), (5, 3)) is None
    assert load_water(str(tmp_path / "nada")) is None
