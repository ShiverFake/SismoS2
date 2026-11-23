"""Testes das melhorias de qualidade: máscara SCL dilatada, mediana, máscara de terra e avisos."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.quality import anniversary_dates, land_mask, quality_warnings  # noqa: E402
from app.models.renderer import save_mask_hatch  # noqa: E402
from app.models.sentinel import Scene, masks_from_scl, median_composite  # noqa: E402

BANDS = ("blue", "green", "red", "nir", "swir16", "swir22")


# ----------------------------------------------------------- máscara SCL
def test_scl_cloud_dilated_by_4_pixels():
    scl = np.full((21, 21), 4, np.uint8)
    scl[10, 10] = 9
    clear, water = masks_from_scl(scl, dilation_px=4)
    assert not clear[10, 10]
    assert not clear[6, 6] and not clear[14, 14]
    assert clear[5, 10] and clear[10, 15]
    assert not water.any()


def test_scl_snow_and_nodata_invalid_but_not_dilated():
    scl = np.full((11, 11), 5, np.uint8)
    scl[5, 5] = 11
    scl[0, 0] = 0
    scl[8, 8] = 6
    clear, water = masks_from_scl(scl)
    assert not clear[5, 5] and not clear[0, 0]
    assert clear[5, 6] and clear[0, 1]
    assert clear[8, 8] and water[8, 8]


# ------------------------------------------------------------ mediana
def _scene(value, clear, tid="S2A_X", water=None):
    shape = clear.shape
    bands = {b: np.full(shape, value, np.float32) for b in BANDS}
    return Scene(item_id=tid, datetime="2023-01-01T08:00:00Z", cloud_cover=1.0, platform="sentinel-2a",
                 tile="36SYF", relative_orbit=121, processing_baseline="05.09", radiometry="x",
                 clear_fraction=float(clear.mean()), bands=bands, data_mask=np.ones(shape, bool),
                 clear_mask=clear, water_mask=water if water is not None else np.zeros(shape, bool))


def test_median_composite_ignores_masked_pixels():
    clear_all = np.ones((2, 2), bool)
    cloudy_px = clear_all.copy(); cloudy_px[0, 0] = False
    s1, s2, s3 = _scene(0.10, clear_all), _scene(0.20, clear_all), _scene(0.90, cloudy_px)
    comp = median_composite([s1, s2, s3])
    assert comp.bands["red"][0, 0] == pytest.approx(0.15)
    assert comp.bands["red"][1, 1] == pytest.approx(0.20)
    assert comp.clear_mask.all()
    meta = comp.metadata()
    assert meta["n_scenes"] == 3 and len(meta["scenes"]) == 3


def test_median_composite_pixel_cloudy_everywhere_is_invalid_but_filled():
    cloudy = np.zeros((1, 1), bool)
    comp = median_composite([_scene(0.3, cloudy), _scene(0.5, cloudy)])
    assert not comp.clear_mask[0, 0]
    assert comp.bands["red"][0, 0] == pytest.approx(0.4)


def test_single_scene_metadata_has_count_one():
    s = _scene(0.1, np.ones((1, 1), bool))
    assert median_composite([s]) is s
    assert s.metadata()["n_scenes"] == 1


# ---------------------------------------------------- terra e avisos
def test_land_mask_excludes_scl_water_and_positive_ndwi():
    valid = np.ones((1, 4), bool)
    ndwi_b = np.array([[-0.5, 0.2, -0.5, -0.5]])
    ndwi_a = np.array([[-0.5, -0.5, 0.1, -0.5]])
    scl_water = np.array([[False, False, False, True]])
    land = land_mask(valid, ndwi_b, ndwi_a, scl_water, None)
    assert land.tolist() == [[True, False, False, False]]


def test_anniversary_dates_handles_leap_day():
    assert anniversary_dates("2024-02-29", "2024-03-15") == {"before_start": "2023-02-28", "before_end": "2023-03-15"}


def _meta(dt, tile="36SYF", orbit=121):
    return {"datetime": dt, "tile": tile, "relative_orbit": orbit}


def test_quality_warnings_all_triggers():
    w = quality_warnings(valid_fraction=0.4, before=_meta("2023-01-01T08:00:00Z"),
                         after=_meta("2023-04-01T08:00:00Z", tile="37SBA"), median_dndvi=-0.2,
                         after_start="2023-02-07", after_end="2023-03-15")
    codes = [x["code"] for x in w]
    assert codes == ["area_valida", "tiles_diferentes", "intervalo_longo", "sazonal"]
    assert "risco de desalinhamento" in w[1]["message"]
    assert w[3]["action"] == {"type": "anniversary", "before_start": "2022-02-07", "before_end": "2022-03-15"}


def test_quality_warnings_clean_case():
    w = quality_warnings(valid_fraction=0.9, before=_meta("2023-01-25T08:00:00Z"),
                         after=_meta("2023-02-09T08:00:00Z"), median_dndvi=0.02,
                         after_start="2023-02-07", after_end="2023-03-15")
    assert w == []


def test_quality_warning_uses_mean_date_of_composite():
    before = {**_meta("2023-01-01T00:00:00Z"), "scenes": [{"datetime": "2023-01-01T00:00:00Z"},
                                                          {"datetime": "2023-01-21T00:00:00Z"}]}
    after = _meta("2023-03-12T00:00:00Z")
    w = quality_warnings(valid_fraction=1, before=before, after=after, median_dndvi=0,
                         after_start="2023-03-01", after_end="2023-03-31")
    assert w == []


def test_mask_hatch_only_on_masked_pixels(tmp_path):
    from PIL import Image
    masked = np.zeros((12, 12), bool); masked[:6] = True
    save_mask_hatch(masked, str(tmp_path / "m.png"))
    alpha = np.asarray(Image.open(tmp_path / "m.png"))[..., 3]
    assert (alpha[6:] == 0).all()
    assert set(np.unique(alpha[:6])) == {60, 190}


# ------------------------------------------------ ordem das candidatas
def test_candidates_ordered_by_clear_fraction_with_tie_tolerance():
    from app.models.sentinel import Candidate, order_by_clear_fraction
    cand = lambda name, frac: Candidate(name, None, None, frac)  # noqa: E731
    evaluated = [(0, cand("09/02", 0.9948)), (1, cand("14/02", 0.9998)), (2, cand("24/02", 0.8944)),
                 (3, cand("01/03", 1.0)), (4, cand("11/03 (eo:cloud_cover 24%)", 1.0))]
    order = [c.item for c in order_by_clear_fraction(evaluated)]
    assert order == ["09/02", "14/02", "01/03", "11/03 (eo:cloud_cover 24%)", "24/02"]
    evaluated = [(0, cand("perto", 0.60)), (1, cand("longe", 0.97))]
    assert [c.item for c in order_by_clear_fraction(evaluated)] == ["longe", "perto"]
