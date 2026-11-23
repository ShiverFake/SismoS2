"""Testes: construções = ≥ 1 prédio do OSM OU WorldCover 50 ≥ 10% (prioridade) e WorldCover 50 OU até 10 m de um prédio do OSM (classe "possível dano em construções")."""
import os
import sys

import numpy as np
from affine import Affine

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.buildings import (building_cells, load_buildings, osm_footprint_mask, save_buildings,  # noqa: E402
                                  urban_mask)
from app.models.plot_style import br  # noqa: E402
from app.models.priority import compute_priority, priority_cells  # noqa: E402


def _cells_of(shape, cell_px):
    h, w = shape
    ncols = -(-w // cell_px)
    rr, cc = np.indices(shape)
    return (rr // cell_px) * ncols + (cc // cell_px), ncols


def test_building_cells_uses_osm_or_built_share():
    shape, cell_px = (4, 4), 2
    cell, ncols = _cells_of(shape, cell_px)
    built = np.zeros(shape, bool)
    built[0, 0] = True
    built[0, 2:4] = True
    b = {"built": built, "osm_rows": np.array([0, 3]), "osm_cols": np.array([3, 0]), }
    assert building_cells(b, cell, 4, cell_px, ncols).tolist() == [True, True, True, False]
    only_wc = {"built": built, "osm_rows": None, "osm_cols": None}
    assert building_cells(only_wc, cell, 4, cell_px, ncols).tolist() == [True, True, False, False]
    only_osm = {"built": None, "osm_rows": np.array([0, 3]), "osm_cols": np.array([3, 0])}
    assert building_cells(only_osm, cell, 4, cell_px, ncols).tolist() == [False, True, True, False]
    assert building_cells(None, cell, 4, cell_px, ncols) is None


def test_priority_ranks_only_cells_with_buildings():
    classes = np.zeros((4, 4), np.uint8)
    classes[2:4, 0:2] = 1
    classes[0, 2] = 4
    built = np.zeros((4, 4), bool)
    built[0:2, 2:4] = True
    b = {"built": built, "osm_rows": np.array([1]), "osm_cols": np.array([3])}
    cells = priority_cells(classes, 2, None, b)
    assert cells["id"].tolist() == [1] and cells["band"].tolist() == ["top10"]
    assert sorted(cells["empty"]["id"].tolist()) == [0, 2, 3]
    assert priority_cells(classes, 2)["band"][2] == "top10"


def test_priority_summary_and_geojson_show_empty_cells():
    classes = np.zeros((4, 4), np.uint8)
    classes[0, 2] = 4
    built = np.zeros((4, 4), bool)
    built[0:2, 2:4] = True
    b = {"built": built, "osm_rows": np.array([1]), "osm_cols": np.array([3])}
    summary, gj = compute_priority(classes, Affine(100, 0, 0, 0, -100, 400), 100.0, None, b)
    assert summary["n_cells"] == 1 and summary["top10_n_cells"] == 1
    assert summary["empty"]["n_cells"] == 3 and summary["empty"]["label"] == "Sem construções"
    bands = [f["properties"]["band"] for f in gj["features"]]
    assert bands.count("empty") == 3 and bands.count("top10") == 1
    assert all(f["properties"]["rank"] is None for f in gj["features"] if f["properties"]["band"] == "empty")


def test_buildings_file_keeps_known_sources(tmp_path):
    built = np.ones((3, 3), bool)
    save_buildings(str(tmp_path), built=built)
    assert load_buildings(str(tmp_path))["osm_rows"] is None
    save_buildings(str(tmp_path), osm_rows=[1], osm_cols=[2])
    b = load_buildings(str(tmp_path))
    assert b["built"].all() and b["osm_rows"].tolist() == [1] and b["osm_cols"].tolist() == [2]


def test_plot_numbers_use_decimal_comma():
    assert br(0.8303) == "0,83" and br(1234.5, 1) == "1.234,5" and br(None) == "—"


def test_urban_mask_is_worldcover_or_osm_footprint():
    built = np.array([[1, 0], [0, 0]], bool)
    osm = np.array([[0, 0], [0, 1]], bool)
    assert urban_mask({"built": built, "osm_mask": osm}).tolist() == [[True, False], [False, True]]
    assert urban_mask({"built": None, "osm_mask": osm}).tolist() == osm.tolist()
    assert urban_mask({"built": None, "osm_mask": None}) is None and urban_mask(None) is None


def test_osm_footprint_is_buffered_by_ten_metres():
    from rasterio.warp import transform as warp_transform
    from shapely.geometry import box
    t = Affine(10, 0, 0, 0, -10, 100)
    lon, lat = warp_transform("EPSG:3857", "EPSG:4326", [52, 58], [42, 48])
    mask = osm_footprint_mask([box(lon[0], lat[0], lon[1], lat[1])], t, (10, 10), res_m=10)
    assert mask[5, 5] and mask[4, 5] and mask[6, 5] and mask[5, 4] and mask[5, 6]
    assert not mask[2, 2] and not mask[8, 8]
    assert not osm_footprint_mask([], t, (10, 10), res_m=10).any()
