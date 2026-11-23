"""Testes da validação (métricas, graus de dano, ligação EMS↔OSM, detecção por edificação)."""
import io
import json
import os
import sys
import zipfile

import numpy as np
import pytest
from shapely.geometry import Point, box

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.reference import load_upload, load_zip, normalize_grade  # noqa: E402
from app.models.validation import (build_units, confusion_metrics, detect_units,  # noqa: E402
                                   labels_for)
from app.models.reference import ReferenceData, ReferenceFeature  # noqa: E402


def test_confusion_metrics_known_values():
    m = confusion_metrics(tp=20, fp=5, fn=10, tn=15)
    assert m["precision"] == pytest.approx(0.8)
    assert m["recall"] == pytest.approx(20 / 30, abs=1e-4)
    assert m["overall_accuracy"] == pytest.approx(0.7)
    assert m["kappa"] == pytest.approx(0.4)
    assert m["f1"] == pytest.approx(2 * 0.8 * (2 / 3) / (0.8 + 2 / 3), abs=1e-4)


def test_confusion_metrics_degenerate():
    m = confusion_metrics(0, 0, 0, 0)
    assert m["f1"] == 0 and m["kappa"] == 0


@pytest.mark.parametrize("text,expected", [
    ("Destroyed", "destroyed"), ("Damaged", "damaged"), ("Possibly damaged", "possibly"),
    ("Severe Damage", "damaged"), ("Moderate Damage", "damaged"), ("Possible Damage", "possibly"),
    ("No Visible Damage", "none"), ("Not Applicable", None), ("", None),
])
def test_normalize_grade(text, expected):
    assert normalize_grade(text) == expected


def test_labels_for_possible_option():
    grades = np.array(["destroyed", "damaged", "possibly", "none"], dtype=object)
    assert labels_for(grades, False).tolist() == [1, 1, -1, 0]
    assert labels_for(grades, True).tolist() == [1, 1, 1, 0]


def test_build_units_links_points_to_buildings():
    b1 = box(0, 0, 0.0001, 0.0001)
    b2 = box(0.001, 0, 0.0011, 0.0001)
    ref = ReferenceData("teste", [
        ReferenceFeature(Point(0.00005, 0.00005), "damaged"),
        ReferenceFeature(Point(0.00006, 0.00005), "destroyed"),
        ReferenceFeature(Point(0.005, 0.005), "damaged"),
    ], aoi=box(-1, -1, 1, 1))
    units, info = build_units(ref, [b1, b2], box(-1, -1, 1, 1))
    assert len(units) == 3
    assert units.grade.tolist() == ["destroyed", "none", "damaged"]
    assert units.origin.tolist() == ["osm", "osm", "referencia"]
    assert info["matched_to_osm"] == 2 and info["unmatched_added_as_points"] == 1
    assert 5 < units.radius_m[0] < 8


def test_detect_units_uses_radius_plus_buffer():
    pred = np.zeros((50, 50), bool)
    pred[10, 10] = True
    rows, cols = np.array([10, 10, 10]), np.array([10, 12, 20])
    det = detect_units(pred, rows, cols, radius_m=np.array([0.0, 5.0, 0.0]), buffer_m=10.0, res_m=10.0)
    assert det.tolist() == [True, False, False]
    det = detect_units(pred, rows, cols, radius_m=np.array([0.0, 10.0, 0.0]), buffer_m=10.0, res_m=10.0)
    assert det.tolist() == [True, True, False]


def _ems_like_zip() -> bytes:
    pts = {"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
           "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [-8.18, 30.99]},
                         "properties": {"damage_gra": "Destroyed", "obj_type": "11-Residential Buildings"}},
                        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-8.17, 30.98]},
                         "properties": {"damage_gra": "Possibly damaged"}}]}
    aoi = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
           "geometry": {"type": "Polygon", "coordinates": [[[-8.2, 30.9], [-8.1, 30.9], [-8.1, 31.0], [-8.2, 31.0], [-8.2, 30.9]]]}}]}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("X/EMSR000_AOI01_GRA_PRODUCT_builtUpP_v1.json", json.dumps(pts))
        z.writestr("X/EMSR000_AOI01_GRA_PRODUCT_areaOfInterestA_v1.json", json.dumps(aoi))
        z.writestr("X/leiame.txt", "x")
    return buf.getvalue()


def test_load_ems_style_zip():
    ref = load_zip(_ems_like_zip(), "teste")
    assert ref.grade_counts()["destroyed"] == 1 and ref.grade_counts()["possibly"] == 1
    assert ref.aoi.bounds == pytest.approx((-8.2, 30.9, -8.1, 31.0))


def test_load_geojson_upload_without_aoi():
    gj = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [36.16, 36.2]},
         "properties": {"Main_Damag": "Destroyed"}}]}
    ref = load_upload("unosat.geojson", json.dumps(gj).encode())
    assert len(ref.features) == 1 and ref.aoi is not None and ref.notes
