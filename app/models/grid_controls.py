"""Model: evento × controles na avaliação por quadrícula — o sinal é do terremoto ou do lugar?"""
from __future__ import annotations

import math
from typing import List, Optional

import numpy as np
from scipy import stats

from app.models.change_detection import CHANGE_CLASS_IDS, NODATA
from app.models.grid_eval import (CELL_SIZES_M, DAMAGED_CELL_SHARE, auc_score, build_cells, roc_curve,
                                  triage_table)


# ------------------------------------------------------------------ DeLong pareado
def _psi_components(pos: np.ndarray, neg: np.ndarray):
    """V10 (por positivo) e V01 (por negativo) de DeLong: ψ = 1 se pos > neg, ½ se empate."""
    gt = (pos[:, None] > neg[None, :]).astype(float) + 0.5 * (pos[:, None] == neg[None, :])
    return gt.mean(axis=1), gt.mean(axis=0)


def delong_paired(positive: np.ndarray, s1: np.ndarray, s2: np.ndarray) -> Optional[dict]:
    """Teste de DeLong para a diferença entre duas AUCs medidas nas MESMAS unidades (DeLong et al., 1988)."""
    positive = np.asarray(positive, bool)
    m, n = int(positive.sum()), int((~positive).sum())
    if m < 2 or n < 2:
        return None
    v10, v01 = [], []
    for s in (np.asarray(s1, float), np.asarray(s2, float)):
        a, b = _psi_components(s[positive], s[~positive])
        v10.append(a); v01.append(b)
    auc = [float(v.mean()) for v in v10]
    s10, s01 = np.cov(np.vstack(v10)), np.cov(np.vstack(v01))
    cov = s10 / m + s01 / n
    var = float(cov[0, 0] + cov[1, 1] - 2 * cov[0, 1])
    diff = auc[0] - auc[1]
    if var <= 0:
        return {"auc_1": round(auc[0], 4), "auc_2": round(auc[1], 4), "diff": round(diff, 4),
                "se": 0.0, "z": None, "p_value": None, "ci95": [round(diff, 4), round(diff, 4)]}
    se = math.sqrt(var)
    z = diff / se
    return {"auc_1": round(auc[0], 4), "auc_2": round(auc[1], 4), "diff": round(diff, 4), "se": round(se, 4),
            "z": round(z, 3), "p_value": float(2 * stats.norm.sf(abs(z))),
            "ci95": [round(diff - 1.96 * se, 4), round(diff + 1.96 * se, 4)]}


def paired_bootstrap_auc(positive: np.ndarray, s1: np.ndarray, s2: np.ndarray, n_boot: int = 2000,
                         seed: int = 0) -> Optional[dict]:
    """Bootstrap pareado: reamostra as células (as mesmas para as duas pontuações) e mede AUC1 − AUC2."""
    positive = np.asarray(positive, bool)
    if positive.sum() < 2 or (~positive).sum() < 2:
        return None
    rng = np.random.default_rng(seed)
    n = len(positive)
    diffs = []
    for _ in range(n_boot):
        i = rng.integers(0, n, n)
        a1, a2 = auc_score(s1[i], positive[i]), auc_score(s2[i], positive[i])
        if a1 is not None and a2 is not None:
            diffs.append(a1 - a2)
    d = np.array(diffs)
    p = 2 * min((d <= 0).mean(), (d >= 0).mean())
    return {"diff_ci95": [round(float(np.quantile(d, 0.025)), 4), round(float(np.quantile(d, 0.975)), 4)],
            "p_value": float(min(1.0, p)), "n_boot": len(d)}


# --------------------------------------------------------------- múltiplas comparações
PRIMARY = {"size": "200", "against": "média dos controles"}


def holm(pvalues: List[Optional[float]]) -> List[Optional[float]]:
    """Correção de Holm–Bonferroni (controla a chance de QUALQUER falso positivo na família)."""
    idx = [i for i, p in enumerate(pvalues) if p is not None]
    m = len(idx)
    out: List[Optional[float]] = [None] * len(pvalues)
    running = 0.0
    for rank, i in enumerate(sorted(idx, key=lambda i: pvalues[i])):
        running = max(running, min(1.0, (m - rank) * pvalues[i]))
        out[i] = running
    return out


def apply_holm(result: dict) -> dict:
    """Marca a comparação principal (evento × média, 200 m: p sem correção) e aplica Holm às demais comparações deste local (todos os tamanhos × cada controle e × a média em 50/100 m)."""
    family = []
    for size, block in result["sizes"].items():
        if "error" in block:
            continue
        for a in block["analyses"][1:]:
            if a.get("vs_event_delong"):
                family.append(a["vs_event_delong"])
        mean = (block.get("controls_mean") or {}).get("vs_event_delong")
        if mean is not None:
            mean["primary"] = size == PRIMARY["size"]
            if not mean["primary"]:
                family.append(mean)
    adjusted = holm([d.get("p_value") for d in family])
    for d, p in zip(family, adjusted):
        d["p_holm"] = p
    result["multiple_testing"] = {"primary": PRIMARY, "method": "Holm", "family_size": len(family)}
    return result


# ---------------------------------------------------------------- comparação na grade
def changed_and_valid(classes: np.ndarray):
    return np.isin(classes, CHANGE_CLASS_IDS), classes != NODATA


def _auc_ci(score, positive, n_boot=1000, seed=0):
    rng = np.random.default_rng(seed)
    n = len(score)
    vals = [auc_score(score[i], positive[i]) for i in (rng.integers(0, n, n) for _ in range(n_boot))]
    vals = [v for v in vals if v is not None]
    return [round(float(np.quantile(vals, 0.025)), 4), round(float(np.quantile(vals, 0.975)), 4)] if vals else [None, None]


def compare_grid(layers: List[dict], in_area: np.ndarray, unit_rows, unit_cols, unit_labels, unit_is_osm,
                 res_m: float, sizes=CELL_SIZES_M, n_boot: int = 1000, water=None) -> dict:
    """layers[0] = evento; layers[1:] = controles."""
    common_valid = np.ones(in_area.shape, bool)
    changed = []
    for layer in layers:
        ch, ok = changed_and_valid(layer["classes"])
        common_valid &= ok
        changed.append(ch)
    out = {"common_valid_share": round(float((common_valid & in_area).sum() / max(in_area.sum(), 1)), 4),
           "labels": [l["label"] for l in layers], "sizes": {}}
    for size in sizes:
        cell_px = max(1, int(round(size / res_m)))
        cells = [build_cells(cell_px, in_area, common_valid, ch, layer["magnitude"], unit_rows, unit_cols,
                             unit_labels, unit_is_osm, water) for ch, layer in zip(changed, layers)]
        positive = cells[0]["damage_share"] >= DAMAGED_CELL_SHARE
        n_cells = len(positive)
        block = {"n_cells": n_cells, "n_damaged_cells": int(positive.sum()), "analyses": []}
        if n_cells < 5 or positive.sum() < 2 or (~positive).sum() < 2:
            block["error"] = "Células insuficientes (ou sem células danificadas/não danificadas) para comparar."
            out["sizes"][str(size)] = block
            continue
        scores = [c["change_share"] for c in cells]
        for k, (layer, sc) in enumerate(zip(layers, scores)):
            a = {"label": layer["label"], "year": layer.get("year"), "is_event": k == 0,
                 "auc": round(auc_score(sc, positive), 4), "auc_ci95": _auc_ci(sc, positive, n_boot, seed=k),
                 "triage": [{"fraction_cells": t["fraction_cells"], "share_of_damaged": t["share_of_damaged"]}
                            for t in triage_table(sc, cells[0]["n_damaged"])],
                 "roc": roc_curve(sc, positive)}
            if k > 0:
                a["vs_event_delong"] = delong_paired(positive, scores[0], sc)
                a["vs_event_bootstrap"] = paired_bootstrap_auc(positive, scores[0], sc, n_boot=n_boot, seed=k)
            block["analyses"].append(a)
        ctl = block["analyses"][1:]
        if ctl:
            block["controls_mean"] = {
                "auc": round(float(np.mean([c["auc"] for c in ctl])), 4),
                "auc_sd": round(float(np.std([c["auc"] for c in ctl], ddof=1)), 4) if len(ctl) > 1 else None,
                "triage": [round(float(np.mean([c["triage"][i]["share_of_damaged"] for c in ctl])), 4)
                           for i in range(len(ctl[0]["triage"]))]}
            mean_score = np.mean(scores[1:], axis=0)
            block["controls_mean"]["vs_event_delong"] = delong_paired(positive, scores[0], mean_score)
        out["sizes"][str(size)] = block
    return apply_holm(out)


# -------------------------------------------------------------- calibração cruzada
def plot_rocs(block: dict, path: str, title: str = "") -> None:
    """Curvas ROC do evento e dos controles (mesmas células)."""
    from app.models.plot_style import C, SERIES, br, decimal_comma, themed_pyplot
    plt = themed_pyplot()

    fig, ax = plt.subplots(figsize=(5.6, 4.6), dpi=120)
    palette = SERIES
    for k, a in enumerate(block["analyses"]):
        pts = np.array(a["roc"])
        if not len(pts):
            continue
        ax.plot(pts[:, 0], pts[:, 1], color=palette[k % len(palette)], lw=2.4 if a["is_event"] else 1.4,
                ls="-" if a["is_event"] else "--", label=f"{a['label']} (AUC {br(a['auc'])})")
    ax.plot([0, 1], [0, 1], color=C["faint"], lw=1, ls=":", label="acaso")
    ax.set_xlabel("Taxa de falso positivo (células não danificadas apontadas)")
    ax.set_ylabel("Taxa de verdadeiro positivo")
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    decimal_comma(ax)
    ax.grid(True)
    ax.legend(fontsize=7, loc="lower right")
    if title:
        ax.set_title(title, fontsize=9)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
