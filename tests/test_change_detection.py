"""Testes dos algoritmos com dados sintéticos (não precisam de internet)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.change_detection import (  # noqa: E402
    ChangeParams, compute_statistics, detect_changes, normalized_difference,
    otsu_threshold, remove_small_regions,
)
from app.models.sentinel import Grid  # noqa: E402

BANDS = ("blue", "green", "red", "nir", "swir16", "swir22")


def vegetated_scene(shape=(120, 120), seed=0):
    rng = np.random.default_rng(seed)
    base = {"blue": 0.03, "green": 0.06, "red": 0.04, "nir": 0.35, "swir16": 0.18, "swir22": 0.09}
    return {b: (v + rng.normal(0, 0.005, shape)).astype(np.float32) for b, v in base.items()}


def test_normalized_difference_handles_zero():
    out = normalized_difference(np.array([0.0, 0.5]), np.array([0.0, 0.1]))
    assert np.isnan(out[0])
    assert out[1] == pytest.approx(0.4 / 0.6, rel=1e-5)


def test_normalized_difference_bounded_with_negative_reflectance():
    out = normalized_difference(np.array([0.001, -0.02, 0.3]), np.array([-0.00095, 0.01995, 0.05]))
    assert np.isnan(out[0]) and np.isnan(out[1])
    assert np.nanmax(np.abs(out)) <= 1.0


def test_otsu_separates_bimodal():
    rng = np.random.default_rng(1)
    values = np.concatenate([rng.normal(0.1, 0.02, 5000), rng.normal(0.8, 0.05, 1000)])
    assert 0.2 < otsu_threshold(values) < 0.7


def test_remove_small_regions():
    mask = np.zeros((20, 20), bool)
    mask[2, 2] = True
    mask[10:15, 10:15] = True
    cleaned = remove_small_regions(mask, 6)
    assert not cleaned[2, 2]
    assert cleaned[10:15, 10:15].all()


def test_landslide_detected_as_vegetation_loss():
    before = vegetated_scene(seed=1)
    after = vegetated_scene(seed=2)
    sl = (slice(40, 60), slice(40, 60))
    after["red"][sl], after["nir"][sl], after["swir16"][sl] = 0.20, 0.25, 0.32
    after["green"][sl], after["blue"][sl], after["swir22"][sl] = 0.16, 0.12, 0.26
    valid = np.ones(before["red"].shape, bool)

    result = detect_changes(before, after, valid, ChangeParams(normalize=False))
    assert result.changed[sl].mean() > 0.95
    assert (result.classes[sl] == 1).mean() > 0.95
    outside = result.changed.copy()
    outside[sl] = False
    assert outside.mean() < 0.01

    stats = compute_statistics(result, pixel_area_m2=100.0)
    assert stats["changed_area_km2"] == pytest.approx(400 * 100 / 1e6, rel=0.1)


def test_masked_pixels_are_nodata():
    before, after = vegetated_scene(seed=3), vegetated_scene(seed=4)
    valid = np.ones(before["red"].shape, bool)
    valid[:10] = False
    result = detect_changes(before, after, valid)
    assert (result.classes[:10] == 255).all()


def test_too_few_valid_pixels_raises():
    before, after = vegetated_scene(seed=5), vegetated_scene(seed=6)
    with pytest.raises(ValueError):
        detect_changes(before, after, np.zeros(before["red"].shape, bool))


def test_grid_resolution_and_size_limit():
    g = Grid.from_bbox((36.12, 36.18, 36.22, 36.25), target_resolution=10, max_dim=1500)
    assert g.ground_resolution == pytest.approx(10, rel=0.01)
    assert 850 < g.width < 950
    big = Grid.from_bbox((0, 0, 0.3, 0.3), max_dim=500)
    assert max(big.width, big.height) <= 501


class _FakeItem:
    def __init__(self, **props):
        self.properties = props


def test_tile_and_orbit_from_earth_search_properties():
    from app.models.sentinel import relative_orbit, tile_of
    item = _FakeItem(**{"grid:code": "MGRS-37SBA",
                        "s2:product_uri": "S2A_MSIL2A_20230209T081101_N0509_R078_T37SBA_20230209T121905.SAFE"})
    assert tile_of(item) == "37SBA"
    assert relative_orbit(item) == 78
    assert relative_orbit(_FakeItem(**{"sat:relative_orbit": 21})) == 21
    assert tile_of(_FakeItem()) is None and relative_orbit(_FakeItem()) is None


class _FakeAsset:
    def __init__(self, raster_bands):
        self.extra_fields = {"raster:bands": raster_bands} if raster_bands is not None else {}


@pytest.mark.parametrize("props,bands,expected_offset", [
    ({"earthsearch:boa_offset_applied": True, "s2:processing_baseline": "05.09"},
     [{"scale": 0.0001, "offset": -0.1}], 0.0),
    ({"earthsearch:boa_offset_applied": False, "s2:processing_baseline": "05.09"}, [{"scale": 0.0001}], -0.1),
    ({"earthsearch:boa_offset_applied": False, "s2:processing_baseline": "03.01"}, [{"scale": 0.0001}], 0.0),
    ({"s2:processing_baseline": "05.09"}, [{"scale": 0.0001, "offset": -0.1}], -0.1),
    ({"s2:processing_baseline": "04.00"}, None, -0.1),
    ({"s2:processing_baseline": "02.14"}, None, 0.0),
])
def test_reflectance_offset_rules(props, bands, expected_offset):
    from app.models.sentinel import reflectance_params
    scale, offset, _ = reflectance_params(_FakeItem(**props), _FakeAsset(bands))
    assert scale == pytest.approx(1e-4)
    assert offset == pytest.approx(expected_offset)


def test_magnitude_layer_transparent_below_half_threshold(tmp_path):
    from PIL import Image
    from app.models.renderer import legend_scales, save_dndvi, save_magnitude
    mag = np.array([[0.01, 0.05, 0.12, 0.30, np.nan]], dtype=np.float32)
    scale = save_magnitude(mag, str(tmp_path / "m.png"), threshold=0.10)
    alpha = np.asarray(Image.open(tmp_path / "m.png"))[..., 3][0]
    assert alpha[0] == 0 and alpha[1] == 0
    assert alpha[2] == 235 and alpha[3] == 235
    assert alpha[4] == 0
    assert scale["min"] == pytest.approx(0.05) and scale["threshold"] == pytest.approx(0.10)
    d = np.array([[0.0, 0.02, -0.3, 0.4]], dtype=np.float32)
    dscale = save_dndvi(d, str(tmp_path / "d.png"))
    da = np.asarray(Image.open(tmp_path / "d.png"))[..., 3][0]
    assert da[0] == 0 and da[1] == 0 and da[2] == 235 and da[3] == 235
    sc = legend_scales(dscale, scale)
    assert sc["magnitude"]["stops"][0][0] == 0 and sc["magnitude"]["stops"][-1][0] == 1
    assert sc["dndvi"]["stops"][2][0] == pytest.approx(0.5)


def test_pixel_probe(tmp_path):
    import json as _json
    import rasterio
    from rasterio.transform import from_origin
    from app.models.pixel_probe import probe
    h, w = 4, 5
    tr = from_origin(0, 400, 100, 100)
    arrays = {f"{p}_{b}": np.full((h, w), 0.1, np.float16) for p in ("before", "after") for b in BANDS}
    arrays["before_nir"][2, 3], arrays["after_nir"][2, 3] = 0.5, 0.15
    np.savez_compressed(tmp_path / "bands.npz", valid=np.ones((h, w), bool), **arrays)
    prof = dict(driver="GTiff", width=w, height=h, count=1, crs="EPSG:3857", transform=tr)
    cls = np.zeros((h, w), np.uint8); cls[2, 3] = 1
    with rasterio.open(tmp_path / "change_classes.tif", "w", dtype="uint8", **prof) as d:
        d.write(cls, 1)
    with rasterio.open(tmp_path / "magnitude.tif", "w", dtype="float32", **prof) as d:
        d.write(np.full((h, w), 0.2, np.float32), 1)
    result = {"grid": {"width": w, "height": h, "transform": list(tr)[:6], "resolution_m": 100},
              "statistics": {"cva_threshold": 0.1}}
    from rasterio.warp import transform as wt
    lon, lat = wt("EPSG:3857", "EPSG:4326", [350], [150])
    p = probe(str(tmp_path), result, lat[0], lon[0])
    assert p["inside"] and (p["row"], p["col"]) == (2, 3)
    assert p["ndvi_before"] == pytest.approx(0.667, abs=0.01) and p["ndvi_after"] == pytest.approx(0.2, abs=0.01)
    assert p["dndvi"] == pytest.approx(-0.467, abs=0.01)
    assert p["class_id"] == 1 and p["changed"] and p["magnitude"] == pytest.approx(0.2)
    assert probe(str(tmp_path), result, 10.0, 10.0)["inside"] is False
