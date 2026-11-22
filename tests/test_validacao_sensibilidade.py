"""Testes: modo "ponto do GRA", diagnóstico da ligação GRA ↔ OSM e variantes do "possivelmente danificado"."""
import os
import sys

import numpy as np
import pytest
from shapely.geometry import Point, box

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.reference import ReferenceData, ReferenceFeature  # noqa: E402
from app.models.validation import (build_point_units, build_units, evaluate_variant,  # noqa: E402
                                   labels_for, link_diagnostics, match_reference)

AREA = box(-1, -1, 1, 1)
CASA = box(0, 0, 0.0001, 0.0001)            # ~11 m × 11 m
LONGE = box(0.01, 0.01, 0.0101, 0.0101)     # outro prédio, ~1,5 km dali


def _ref(*feats):
    return ReferenceData("teste", [ReferenceFeature(g, grade) for g, grade in feats], aoi=AREA)


def test_labels_three_variants():
    g = np.array(["destroyed", "damaged", "possibly", "none"], dtype=object)
    assert labels_for(g, "excluir").tolist() == [1, 1, -1, 0]
    assert labels_for(g, "dano").tolist() == [1, 1, 1, 0]
    assert labels_for(g, "sem_dano").tolist() == [1, 1, 0, 0]
    assert labels_for(g, False).tolist() == [1, 1, -1, 0] and labels_for(g, True).tolist() == [1, 1, 1, 0]
    with pytest.raises(ValueError):
        labels_for(g, "talvez")


def test_two_points_on_same_building_osm_mode_merges_point_mode_keeps():
    ref = _ref((Point(0.00003, 0.00005), "destroyed"), (Point(0.00007, 0.00005), "damaged"),
               (Point(0.005, 0.005), "possibly"))                                    # sem prédio a 10 m
    assignment = match_reference(ref, [CASA, LONGE])
    assert assignment == [[0], [0], []]
    osm, info = build_units(ref, [CASA, LONGE], AREA, assignment)
    assert sorted(osm.grade.tolist()) == ["destroyed", "none", "possibly"]           # o "damaged" sumiu (fundido)
    pts = build_point_units(ref, [CASA, LONGE], AREA, assignment)
    assert sorted(pts.grade.tolist()) == ["damaged", "destroyed", "none", "possibly"]  # todos os pontos entram
    assert sorted(pts.ref_index.tolist()) == [-1, 0, 1, 2]
    assert pts.origin[pts.ref_index == -1].tolist() == ["osm"]                       # CASA saiu dos negativos


def test_link_diagnostics_reasons():
    ref = _ref((Point(0.00003, 0.00005), "destroyed"), (Point(0.00007, 0.00005), "damaged"),
               (Point(0.0004, 0.00005), "damaged"),                                  # ~33 m da CASA: sem prédio a 10 m
               (Point(5, 5), "destroyed"))                                           # fora da área
    assignment = match_reference(ref, [CASA])
    diag = link_diagnostics(ref, [CASA], assignment, AREA, pixel_ok=np.array([True, True, False, True]))
    d, k = diag["destroyed"], diag["damaged"]
    assert (d["total"], d["ligados_osm"], d["sem_predio_osm_10m"], d["fora_da_area_comum"]) == (2, 1, 1, 1)
    assert (k["total"], k["ligados_osm"], k["sem_predio_osm_10m"]) == (2, 1, 1)
    assert k["fundidos_em_predio_compartilhado"] == 1 and d["fundidos_em_predio_compartilhado"] == 0
    assert k["distancia_predio_mais_proximo"]["20–50 m"] == 1
    assert k["em_pixel_invalido"] == 1


def test_evaluate_variant_recall_by_class_and_metrics():
    grades = np.array(["destroyed", "damaged", "possibly", "none", "none"], dtype=object)
    detected = np.array([True, False, True, True, False])
    ok = np.ones(5, bool)
    a = evaluate_variant(detected, grades, ok, "excluir")
    assert (a["tp"], a["fn"], a["fp"], a["tn"]) == (1, 1, 1, 1)
    b = evaluate_variant(detected, grades, ok, "dano")
    assert (b["tp"], b["fn"], b["fp"], b["tn"]) == (2, 1, 1, 1)
    c = evaluate_variant(detected, grades, ok, "sem_dano")
    assert (c["tp"], c["fn"], c["fp"], c["tn"]) == (1, 1, 2, 1)
    assert a["recall_by_class"]["destroyed"]["rate"] == 1.0 and a["recall_by_class"]["damaged"]["rate"] == 0.0
    assert a["recall_by_class"]["possibly"] == c["recall_by_class"]["possibly"]       # não depende da variante
    assert a["false_alarm_rate"] == 0.5
