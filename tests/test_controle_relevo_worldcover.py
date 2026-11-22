"""Testes com dados simulados: área de controle, correção topográfica, WorldCover e rocha clara × nuvem."""
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.analysis import Analysis  # noqa: E402
from app.models.change_detection import NODATA, ChangeParams, detect_changes  # noqa: E402
from app.models.control import build_control, overlap_stats  # noqa: E402
from app.models.landcover import tile_name, worldcover_tiles  # noqa: E402
from app.models.sentinel import (Scene, apply_fixed_bright, bright_fixed_surface,  # noqa: E402
                                 masks_from_scl)
from app.models.terrain import c_correction, illumination, slope_aspect, steep_share  # noqa: E402

BANDS = ("blue", "green", "red", "nir", "swir16", "swir22")


# ======================================================= 1) ÁREA DE CONTROLE
def _event(**kw):
    base = dict(name="Antakya", bbox=[36.12, 36.17, 36.20, 36.24], before_start="2023-01-01",
                before_end="2023-02-05", after_start="2023-02-07", after_end="2023-03-15",
                max_cloud=40, threshold_method="otsu", k=1.5, min_region_px=8, median_composite=True,
                topo_correction=True, event_id="turquia-2023", result={"topo": {"applied": True}})
    base.update(kw)
    return Analysis.new(**base)


def test_control_same_options_dates_one_year_earlier_and_linked():
    ev = _event()
    ctl = build_control(ev)
    assert (ctl.before_start, ctl.before_end) == ("2022-01-01", "2022-02-05")
    assert (ctl.after_start, ctl.after_end) == ("2022-02-07", "2022-03-15")
    assert ctl.bbox == ev.bbox and ctl.bbox is not ev.bbox
    assert (ctl.threshold_method, ctl.k, ctl.min_region_px, ctl.median_composite, ctl.max_cloud) == \
           ("otsu", 1.5, 8, True, 40)
    assert ctl.topo_correction is True
    assert ctl.control_of == ev.id and ctl.id != ev.id
    assert "controle" in ctl.name


def test_control_of_old_analysis_without_topo_keeps_topo_off():
    ev = _event(result={})   # análise feita antes da correção topográfica existir
    assert build_control(ev).topo_correction is False


def test_overlap_stats_counts_only_common_valid_pixels():
    ev = np.array([[2, 2, 0, 0, 1, NODATA]], np.uint8)
    ct = np.array([[2, 0, 4, 0, NODATA, 3]], np.uint8)
    s = overlap_stats(ev, ct, pixel_area_m2=100.0)
    # comuns: colunas 0–3 (4 px). Evento mudou em 0 e 1 → 50%; controle em 0 e 2 → 50%; nos dois: 0
    assert s["event_changed_percent"] == 50.0 and s["control_changed_percent"] == 50.0
    assert s["both_changed_km2"] == pytest.approx(100 / 1e6)
    assert s["both_percent_of_event_change"] == 50.0
    assert s["excess_percent_points"] == 0.0
    assert s["_both"].tolist() == [[True, False, False, False, False, False]]


# ================================================ 2) CORREÇÃO TOPOGRÁFICA
def _plane(rise_east=0.0, rise_north=0.0, n=40, res=10.0):
    rows, cols = np.indices((n, n)).astype(np.float64)
    # linhas crescem para o SUL: subir para o norte = diminuir com a linha
    return (cols * res * rise_east - rows * res * rise_north).astype(np.float32)


def test_slope_and_aspect_of_synthetic_planes():
    slope, aspect = slope_aspect(_plane(rise_east=0.1), 10.0)
    assert np.degrees(slope[20, 20]) == pytest.approx(math.degrees(math.atan(0.1)), abs=0.01)
    assert np.degrees(aspect[20, 20]) == pytest.approx(270, abs=0.01)   # sobe p/ leste → virada p/ oeste
    _, aspect = slope_aspect(_plane(rise_north=0.2), 10.0)
    assert np.degrees(aspect[20, 20]) == pytest.approx(180, abs=0.01)   # sobe p/ norte → virada p/ sul


def test_illumination_flat_and_facing_sun():
    flat = np.zeros((2, 2), np.float32)
    cos_i = illumination(flat, flat, sun_azimuth_deg=158, sun_elevation_deg=31)
    assert np.allclose(cos_i, math.cos(math.radians(59)))
    # encosta de 59° virada exatamente para o sol → sol perpendicular → cos(i) = 1
    slope = np.full((1, 1), math.radians(59), np.float32)
    aspect = np.full((1, 1), math.radians(158), np.float32)
    assert illumination(slope, aspect, 158, 31)[0, 0] == pytest.approx(1.0, abs=1e-5)


def test_c_correction_removes_illumination_effect():
    rng = np.random.default_rng(0)
    n = 120
    slope = np.radians(rng.uniform(0, 35, (n, n))).astype(np.float32)
    aspect = np.radians(rng.uniform(0, 360, (n, n))).astype(np.float32)
    cos_i = illumination(slope, aspect, 158, 31)
    true_refl = 0.05 + 0.25 * math.cos(math.radians(59))   # o que o pixel teria em terreno plano
    # o brilho observado depende da iluminação: ρ = 0,05 + 0,25·cos(i)
    bands = {b: (0.05 + 0.25 * cos_i + rng.normal(0, 0.002, (n, n))).astype(np.float32) for b in BANDS}
    corrected, coeffs = c_correction(bands, cos_i, 31, np.ones((n, n), bool), slope)
    assert np.std(corrected["red"]) < 0.25 * np.std(bands["red"])          # variação por relevo some
    assert np.mean(corrected["red"]) == pytest.approx(true_refl, abs=0.01)  # volta ao valor de terreno plano
    assert coeffs["red"]["c"] == pytest.approx(0.05 / 0.25, abs=0.02)       # c = b/m
    assert coeffs["red"]["r"] > 0.9


def test_c_correction_skips_band_without_relationship():
    n = 60
    rng = np.random.default_rng(1)
    slope = np.radians(rng.uniform(6, 30, (n, n))).astype(np.float32)
    aspect = np.radians(rng.uniform(0, 360, (n, n))).astype(np.float32)
    cos_i = illumination(slope, aspect, 158, 31)
    flat_band = {"red": np.full((n, n), 0.1, np.float32)}   # sem relação com cos(i) (m ≈ 0)
    out, coeffs = c_correction(flat_band, cos_i, 31, np.ones((n, n), bool), slope)
    assert coeffs["red"]["c"] is None and np.array_equal(out["red"], flat_band["red"])


def test_steep_share():
    slope = np.radians(np.array([[5, 25, 30, 10]], np.float32))
    changed = np.array([[True, True, False, False]])
    valid = np.ones((1, 4), bool)
    s = steep_share(changed, slope, valid)
    assert s["changed_percent"] == 50.0 and s["area_percent"] == 50.0


# ============================================================= 3) WORLDCOVER
def test_worldcover_tile_names():
    assert worldcover_tiles((36.12, 36.17, 36.20, 36.24)) == ["N36E036"]          # Antakya
    assert worldcover_tiles((-8.25, 30.95, -8.10, 31.06)) == ["N30W009"]          # Marrocos
    assert tile_name(-3, -9) == "S03W009"
    # bbox que cruza a linha de 39°E → dois tiles
    assert worldcover_tiles((38.9, 36.1, 39.1, 36.2)) == ["N36E036", "N36E039"]
    # cruza o equador e Greenwich → quatro tiles
    assert sorted(worldcover_tiles((-0.5, -0.5, 0.5, 0.5))) == ["N00E000", "N00W003", "S03E000", "S03W003"]
    # termina exatamente na borda → não pede o tile vizinho
    assert worldcover_tiles((36.5, 36.5, 39.0, 37.0)) == ["N36E036"]


def _vegetated(shape, seed):
    rng = np.random.default_rng(seed)
    base = {"blue": 0.03, "green": 0.06, "red": 0.04, "nir": 0.35, "swir16": 0.18, "swir22": 0.09}
    return {b: (v + rng.normal(0, 0.004, shape)).astype(np.float32) for b, v in base.items()}


def test_soil_class_split_by_urban_mask():
    shape = (60, 60)
    before = _vegetated(shape, 1)
    before["nir"][:] = 0.06   # NDVI baixo antes (≤ 0,3) → não vira "perda de vegetação"
    after = {b: v.copy() for b, v in _vegetated(shape, 2).items()}
    after["nir"][:] = 0.06
    for sl in ((slice(5, 15), slice(5, 15)), (slice(40, 50), slice(40, 50))):   # dois blocos de "entulho"
        after["swir16"][sl], after["red"][sl], after["blue"][sl] = 0.40, 0.25, 0.20
    urban = np.zeros(shape, bool)
    urban[:30] = True          # só o primeiro bloco está em área construída
    valid = np.ones(shape, bool)
    res = detect_changes(before, after, valid, ChangeParams(normalize=False), urban=urban)
    assert (res.classes[5:15, 5:15] == 2).mean() > 0.9     # urbano → possível dano em construções
    assert (res.classes[40:50, 40:50] == 5).mean() > 0.9   # fora → solo exposto / possível deslizamento
    res_no_wc = detect_changes(before, after, valid, ChangeParams(normalize=False))
    assert (res_no_wc.classes[40:50, 40:50] == 2).mean() > 0.9   # sem WorldCover: comportamento antigo


# ===================================================== 4) ROCHA CLARA × NUVEM
def _scene(scl, b11):
    shape = scl.shape
    bands = {b: np.full(shape, 0.1, np.float32) for b in BANDS}
    bands["swir16"] = np.asarray(b11, np.float32)
    clear, water = masks_from_scl(scl)
    return Scene(item_id="x", datetime="2023-01-01T00:00:00Z", cloud_cover=0, platform="s2", tile="T",
                 relative_orbit=1, processing_baseline="05.09", radiometry="", clear_fraction=float(clear.mean()),
                 bands=bands, data_mask=np.ones(shape, bool), clear_mask=clear, water_mask=water, scl=scl)


def test_bright_quarry_flagged_as_cloud_in_both_dates_becomes_valid():
    scl = np.full((21, 21), 5, np.uint8)
    scl[10, 10] = 9                                  # "nuvem" no mesmo pixel nas duas datas
    b11 = np.full((21, 21), 0.2, np.float32); b11[10, 10] = 0.45
    before, after = _scene(scl.copy(), b11), _scene(scl.copy(), b11)
    assert not before.clear_mask[10, 10] and not before.clear_mask[7, 7]   # antes: nuvem + borda de 4 px
    bright = bright_fixed_surface([before], [after])
    assert bright[10, 10] and bright.sum() == 1
    apply_fixed_bright([before, after], bright)
    assert before.clear_mask[10, 10] and after.clear_mask[10, 10]          # pedreira volta a ser válida
    assert before.clear_mask[7, 7] and before.clear_fraction == 1.0       # e não gera borda dilatada


def test_real_cloud_is_not_turned_into_bright_surface():
    scl_b = np.full((5, 5), 5, np.uint8); scl_b[2, 2] = 9
    scl_a = np.full((5, 5), 5, np.uint8)              # na outra data o pixel está limpo → nuvem real
    b11 = np.full((5, 5), 0.5, np.float32)
    assert not bright_fixed_surface([_scene(scl_b, b11)], [_scene(scl_a, b11)]).any()
    # nuvem nas duas datas, mas SWIR baixo (≤ 0,3) → não é rocha clara
    scl_a[2, 2] = 8
    dark = np.full((5, 5), 0.2, np.float32)
    assert not bright_fixed_surface([_scene(scl_b, dark)], [_scene(scl_a, dark)]).any()


def test_bright_rule_ignores_shadow_and_cirrus():
    scl = np.full((5, 5), 5, np.uint8); scl[2, 2] = 10   # cirrus nas duas datas
    b11 = np.full((5, 5), 0.5, np.float32)
    assert not bright_fixed_surface([_scene(scl, b11)], [_scene(scl.copy(), b11)]).any()


def test_bright_rule_with_composite_requires_cloud_in_every_scene():
    cloud = np.full((3, 3), 9, np.uint8)
    clear = np.full((3, 3), 5, np.uint8)
    b11 = np.full((3, 3), 0.5, np.float32)
    after = [_scene(cloud.copy(), b11)]
    assert bright_fixed_surface([_scene(cloud, b11), _scene(cloud.copy(), b11)], after).all()
    assert not bright_fixed_surface([_scene(cloud, b11), _scene(clear, b11)], after).any()


def test_c_correction_skips_band_with_weak_correlation():
    # brilho dominado pelo uso do solo (ruído grande), relação fraca com a iluminação → não corrige
    rng = np.random.default_rng(2)
    n = 120
    slope = np.radians(rng.uniform(6, 30, (n, n))).astype(np.float32)
    aspect = np.radians(rng.uniform(0, 360, (n, n))).astype(np.float32)
    cos_i = illumination(slope, aspect, 158, 31)
    band = {"nir": (0.2 + 0.01 * cos_i + rng.normal(0, 0.05, (n, n))).astype(np.float32)}
    out, coeffs = c_correction(band, cos_i, 31, np.ones((n, n), bool), slope)
    assert coeffs["nir"]["c"] is None and coeffs["nir"]["r"] < 0.2
    assert np.array_equal(out["nir"], band["nir"])


def test_control_prefers_event_tile():
    ev = _event(result={"topo": {"applied": True}, "before": {"tile": "36SYF"}})
    assert build_control(ev).preferred_tile == "36SYF"


def test_overlap_stats_urban_on_common_area():
    ev = np.array([[2, 2, 0, 5, NODATA]], np.uint8)
    ct = np.array([[0, 0, 0, 5, 0]], np.uint8)
    urban = np.array([[True, True, True, False, True]])
    s = overlap_stats(ev, ct, 1e6, urban)   # pixels de 1 km² (as áreas são arredondadas em km²)
    # área urbana comum: colunas 0–2 (a 4 é sem dado no evento). Evento mudou 2/3, controle 0/3
    assert s["urban_common_km2"] == pytest.approx(3.0)
    assert s["event_urban_changed_percent"] == pytest.approx(66.667, abs=0.01)
    assert s["control_urban_changed_percent"] == 0.0
