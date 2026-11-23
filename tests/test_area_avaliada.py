"""Testes: área efetivamente avaliada pelo Copernicus (AOI ∩ imagem − não analisado) e contagem de damage_gra."""
import io
import json
import os
import sys
import zipfile

import numpy as np
import pytest
from shapely.geometry import Point, box

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.reference import area_km2, load_zip, raw_damage_values  # noqa: E402
from app.models.validation import build_units, labels_for  # noqa: E402


def _poly(w, s, e, n, **props):
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}}


def _pt(x, y, grade):
    return {"type": "Feature", "properties": {"damage_gra": grade}, "geometry": {"type": "Point", "coordinates": [x, y]}}


def _zip(layers: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, feats in layers.items():
            z.writestr(f"P/EMSR000_AOI01_GRA_PRODUCT_{name}_v1.json",
                       json.dumps({"type": "FeatureCollection", "features": feats}))
    return buf.getvalue()


def _product(with_footprint=True, with_not_analysed=True, extra_points=()):
    layers = {
        "builtUpP": [_pt(0.05, 0.05, "Destroyed"), _pt(0.06, 0.05, "Damaged"), _pt(0.07, 0.05, "Possibly damaged"),
                     _pt(0.08, 0.05, "No visible damage"), *extra_points],
        "areaOfInterestA": [_poly(0, 0, 0.2, 0.1)],
    }
    if with_footprint:
        layers["imageFootprintA"] = [_poly(0, 0, 0.1, 0.1)]
    if with_not_analysed:
        layers["notAnalysedA"] = [_poly(0, 0, 0.02, 0.1, obj_type="Not analysed")]
    return _zip(layers)


def test_raw_damage_values_counts_every_original_value():
    obj = {"features": [_pt(0, 0, "Destroyed"), _pt(0, 0, "Destroyed"), _pt(0, 0, "No visible damage"),
                        _pt(0, 0, "Not Applicable")]}
    assert raw_damage_values(obj) == {"Destroyed": 2, "No visible damage": 1, "Not Applicable": 1}


def test_explicit_no_damage_points_are_negatives_from_the_reference():
    ref = load_zip(_product(), "teste")
    assert ref.raw_values == {"Destroyed": 1, "Damaged": 1, "Possibly damaged": 1, "No visible damage": 1}
    assert ref.grade_counts()["none"] == 1


def test_evaluated_area_is_aoi_inside_image_minus_not_analysed():
    ref = load_zip(_product(), "teste")
    assert ref.aoi.bounds == pytest.approx((0.02, 0.0, 0.1, 0.1))
    b = ref.area_breakdown
    assert b["evaluated_km2"] < b["image_footprint_km2"] < b["aoi_km2"]
    assert b["evaluated_km2"] == pytest.approx(b["aoi_km2"] * 0.08 / 0.2, rel=0.01)
    assert b["layers"] == {"footprint": True, "not_analysed": True}


def test_without_extra_layers_the_area_is_the_aoi():
    ref = load_zip(_product(with_footprint=False, with_not_analysed=False), "teste")
    assert ref.aoi.bounds == pytest.approx((0, 0, 0.2, 0.1))
    assert ref.area_breakdown["evaluated_km2"] == ref.area_breakdown["aoi_km2"]


def test_osm_negatives_outside_evaluated_area_are_dropped():
    ref = load_zip(_product(), "teste")
    osm = [box(0.05, 0.08, 0.0501, 0.0801),
           box(0.15, 0.05, 0.1501, 0.0501),
           box(0.01, 0.05, 0.0101, 0.0501)]
    units, info = build_units(ref, osm, ref.aoi)
    kept = sorted(zip(np.round(units.lon, 4), units.grade, units.origin))
    lons = [k[0] for k in kept]
    assert pytest.approx(0.05005, abs=1e-4) in lons
    assert not any(abs(x - 0.15005) < 1e-3 or abs(x - 0.01005) < 1e-3 for x in lons)
    labels = labels_for(units.grade, "excluir")
    assert int((labels == 0).sum()) == 2


def test_area_km2_reasonable():
    assert area_km2(box(0, 0, 0.1, 0.1)) == pytest.approx(123.1, rel=0.01)
    assert area_km2(Point(0, 0)) == 0.0


def test_footprint_uses_only_the_image_that_graded_the_damage():
    pts = [dict(_pt(0.05, 0.05, "Destroyed"), properties={"damage_gra": "Destroyed", "dmg_src_id": 2})]
    z = _zip({"builtUpP": pts, "areaOfInterestA": [_poly(0, 0, 0.2, 0.1)],
              "imageFootprintA": [_poly(0, 0, 0.2, 0.1, obj_type="Image Footprint", or_src_id="1"),
                                  _poly(0, 0, 0.1, 0.1, obj_type="Image Footprint", or_src_id="2")]})
    ref = load_zip(z, "teste")
    assert ref.aoi.bounds == pytest.approx((0, 0, 0.1, 0.1))
    assert any("or_src_id" in n for n in ref.notes)
