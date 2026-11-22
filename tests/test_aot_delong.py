"""Testes: deduplicação de versões, filtro de névoa (AOT), DeLong pareado, comparação na grade e calibração cruzada."""
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.change_detection import NODATA  # noqa: E402
from app.models.grid_controls import (best_f1_threshold, compare_grid, cross_calibration,  # noqa: E402
                                      delong_paired, f1_at, paired_bootstrap_auc)
from app.models.grid_eval import auc_score  # noqa: E402
from app.models.sentinel import Grid, SentinelError, SentinelRepository, latest_processing  # noqa: E402


class _Asset:
    def __init__(self):
        self.href = "x"
        self.extra_fields = {"raster:bands": [{"scale": 0.001, "offset": 0}]}


class _Item:
    def __init__(self, iid, day, baseline, gen, tile="36SYF", platform="sentinel-2a"):
        self.id = iid
        self.datetime = datetime(2020, 1, day, 8, tzinfo=timezone.utc)
        self.properties = {"s2:processing_baseline": baseline, "s2:generation_time": gen,
                           "platform": platform, "grid:code": f"MGRS-{tile}"}
        self.assets = {"scl": _Asset(), "aot": _Asset()}


def test_latest_processing_keeps_newest_version_per_date():
    items = [_Item("A_0", 11, "02.13", "2020-01-11"), _Item("A_1", 11, "05.00", "2023-06-16"),
             _Item("B_0", 16, "02.13", "2020-01-16"),
             _Item("C_0", 11, "02.13", "2020-01-11", tile="37SBA")]        # outro tile: não é duplicata
    kept = sorted(i.id for i in latest_processing(items))
    assert kept == ["A_1", "B_0", "C_0"]


class _FakeRepo(SentinelRepository):
    """Lê SCL e AOT de dicionários em memória em vez da rede."""
    def __init__(self, aot_dn, max_aot):
        super().__init__("x", "y", max_aot=max_aot)
        self.aot_dn = aot_dn

    def _read_asset(self, item, key, grid, resampling):
        if key == "scl":
            return np.full((grid.height, grid.width), 4, np.uint8)       # tudo vegetação (limpo)
        return np.full((grid.height, grid.width), self.aot_dn[item.id], np.uint16)


def test_hazy_scene_is_discarded_and_aot_recorded():
    grid = Grid.from_bbox((0, 0, 0.01, 0.01))
    items = [_Item("limpa", 1, "05.09", "x"), _Item("nevoa", 2, "05.09", "x")]
    repo = _FakeRepo({"limpa": 120, "nevoa": 350}, max_aot=0.25)            # AOT 0,12 e 0,35
    ranked = repo.rank_candidates(items, grid)
    assert [c.item.id for c in ranked] == ["limpa"] and ranked[0].aot == pytest.approx(0.12)
    assert len(_FakeRepo({"limpa": 120, "nevoa": 350}, max_aot=None).rank_candidates(items, grid)) == 2
    with pytest.raises(SentinelError) as e:
        _FakeRepo({"limpa": 300, "nevoa": 350}, max_aot=0.25).rank_candidates(items, grid)
    assert "AOT" in str(e.value)


def test_delong_known_cases():
    rng = np.random.default_rng(0)
    y = np.r_[np.ones(60, bool), np.zeros(140, bool)]
    good = np.r_[rng.normal(1, 1, 60), rng.normal(0, 1, 140)]
    noise = rng.normal(0, 1, 200)
    d = delong_paired(y, good, noise)
    assert d["auc_1"] == pytest.approx(auc_score(good, y), abs=1e-4)
    assert d["auc_2"] == pytest.approx(auc_score(noise, y), abs=1e-4)
    assert d["diff"] > 0.15 and d["p_value"] < 0.01 and d["ci95"][0] > 0
    same = delong_paired(y, good, good)
    assert same["diff"] == 0 and same["p_value"] is None              # variância zero: sem teste
    b = paired_bootstrap_auc(y, good, noise, n_boot=300)
    assert b["diff_ci95"][0] > 0 and b["p_value"] < 0.05
    assert delong_paired(np.array([True, False, False]), good[:3], noise[:3]) is None


def test_best_f1_and_cross_calibration():
    ch = np.array([0.0, 0.05, 0.1, 0.3, 0.4, 0.5])
    pos = np.array([False, False, False, True, True, True])
    best = best_f1_threshold(ch, pos)
    assert best["threshold"] == 0.3 and best["f1"] == 1.0
    assert f1_at(ch, pos, 0.05)["precision"] == pytest.approx(3 / 5)
    src = {"100": {"change": ch.tolist(), "damaged": pos.tolist()}}
    dst = {"100": {"change": [0.0, 0.2, 0.25, 0.35], "damaged": [False, True, True, True]}}
    cc = cross_calibration(src, dst)["100"]
    assert cc["threshold_from_source"] == 0.3
    assert cc["applied_to_target"]["recall"] == pytest.approx(1 / 3, abs=1e-4)   # limiar alto demais para o destino
    assert cc["target_own_best"]["f1"] == 1.0


def _layer(label, change):
    classes = np.where(change, 4, 0).astype(np.uint8)
    return {"label": label, "classes": classes, "magnitude": change.astype(float)}


def test_compare_grid_event_beats_random_control():
    rng = np.random.default_rng(1)
    h = w = 100                                          # 10×10 células de 10 px (100 m)
    dmg_cell = rng.random((10, 10)) < 0.3
    # evento: mudança concentrada nas células danificadas; controle: mudança aleatória
    ev = np.kron(dmg_cell, np.ones((10, 10), bool)) & (rng.random((h, w)) < 0.4)
    ev |= rng.random((h, w)) < 0.03
    ct = rng.random((h, w)) < 0.08
    ctl = _layer("Controle", ct)
    ctl["classes"][:5, :] = NODATA                       # faixa sem dado no controle → sai de todos
    rows, cols, labels = [], [], []
    for r in range(10):
        for c in range(10):
            for j in range(5):
                rows.append(r * 10 + 1 + j); cols.append(c * 10 + 1)
                labels.append(1 if (dmg_cell[r, c] and j < 3) else 0)
    out = compare_grid([_layer("Evento", ev), ctl], np.ones((h, w), bool), np.array(rows), np.array(cols),
                       np.array(labels), np.ones(len(rows), bool), res_m=10.0, sizes=(100,), n_boot=200)
    b = out["sizes"]["100"]
    assert out["common_valid_share"] == pytest.approx(0.95)
    ev_a, ct_a = b["analyses"]
    assert ev_a["auc"] > 0.9 and ct_a["auc"] < 0.75
    assert ct_a["vs_event_delong"]["diff"] > 0 and ct_a["vs_event_delong"]["p_value"] < 0.01
    assert b["controls_mean"]["auc"] == ct_a["auc"]
