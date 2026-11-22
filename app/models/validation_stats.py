"""Model: estatísticas da validação — intervalos de confiança (bootstrap) e efeito de vizinhança.

Bootstrap: reamostra os PRÉDIOS com reposição (1000 vezes), recalcula as métricas em cada amostra e
usa os percentis 2,5 e 97,5 como intervalo de confiança de 95%. Mostra quanto as métricas variariam
com outro conjunto de prédios do mesmo tipo — com 2 mil prédios, uma diferença de 0,02 no F1 pode
ser só ruído.

Vizinhança: com pixels de 10 m, a mudança de um prédio que desabou "vaza" para os vizinhos. Se os
acertos nos danificados e os alarmes falsos nos prédios sem dano se concentram perto de prédios
destruídos, parte do desempenho vem da proximidade, não do dano do próprio prédio.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.spatial import cKDTree

BANDS_M: List[Tuple[float, float, str]] = [(0, 10, "0–10 m"), (10, 20, "10–20 m"), (20, 50, "20–50 m"),
                                          (50, 100, "50–100 m"), (100, math.inf, "> 100 m")]
CLASSES = ("destroyed", "damaged", "possibly")


# ------------------------------------------------------------------- bootstrap
def _metrics_from_counts(tp, fp, fn, tn):
    """Versão vetorizada (arrays de contagens) de precisão, recall, F1 e kappa."""
    tp, fp, fn, tn = (np.asarray(x, float) for x in (tp, fp, fn, tn))
    n = tp + fp + fn + tn
    with np.errstate(divide="ignore", invalid="ignore"):
        precision = np.where(tp + fp > 0, tp / (tp + fp), 0.0)
        recall = np.where(tp + fn > 0, tp / (tp + fn), 0.0)
        f1 = np.where(precision + recall > 0, 2 * precision * recall / (precision + recall), 0.0)
        po = (tp + tn) / n
        pe = ((tp + fp) * (tp + fn) + (fn + tn) * (fp + tn)) / (n * n)
        kappa = np.where(pe < 1, (po - pe) / (1 - pe), 0.0)
    return {"precision": precision, "recall": recall, "f1": f1, "kappa": kappa}


def bootstrap_ci(detected: np.ndarray, labels: np.ndarray, grades: np.ndarray,
                 n_boot: int = 1000, seed: int = 0, level: float = 0.95) -> Dict[str, dict]:
    """IC por bootstrap dos prédios.

    detected: prédio marcado como mudança; labels: 1 dano, 0 sem dano, −1 fora da matriz (ex.: "possivelmente"
    na variante (a) — continua na amostra para o recall da própria classe); grades: classe da referência.
    Devolve {métrica: {value, lo, hi}} para precisão, recall, F1, κ e recall de cada classe.
    """
    detected, labels, grades = np.asarray(detected, bool), np.asarray(labels), np.asarray(grades, dtype=object)
    n = len(detected)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_boot, n))             # cada linha = uma reamostragem dos prédios
    d, lab, g = detected[idx], labels[idx], grades[idx]
    pos, neg = lab == 1, lab == 0
    boot = _metrics_from_counts((d & pos).sum(1), (d & neg).sum(1), (~d & pos).sum(1), (~d & neg).sum(1))
    point = _metrics_from_counts(*(np.array([v]) for v in ((detected & (labels == 1)).sum(), (detected & (labels == 0)).sum(),
                                                          (~detected & (labels == 1)).sum(), (~detected & (labels == 0)).sum())))
    for c in CLASSES:
        sel = g == c
        with np.errstate(divide="ignore", invalid="ignore"):
            boot[f"recall_{c}"] = np.where(sel.sum(1) > 0, (d & sel).sum(1) / sel.sum(1), np.nan)
        own = grades == c
        point[f"recall_{c}"] = np.array([detected[own].mean() if own.any() else np.nan])
    alpha = (1 - level) / 2
    out = {}
    for k, values in boot.items():
        v = values[np.isfinite(values)]
        p = float(point[k][0])
        out[k] = {"value": None if math.isnan(p) else round(p, 4),
                  "lo": round(float(np.quantile(v, alpha)), 4) if v.size else None,
                  "hi": round(float(np.quantile(v, 1 - alpha)), 4) if v.size else None,
                  "n": int((grades == k.split("_", 1)[1]).sum()) if k.startswith("recall_") else int((labels >= 0).sum())}
    return out


# ------------------------------------------------------------------ vizinhança
def _xy_m(lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """Coordenadas locais em metros (equiretangular — bom para áreas de poucos km)."""
    lat0 = math.radians(float(np.mean(lat)))
    return np.column_stack([np.asarray(lon) * 111_320 * math.cos(lat0), np.asarray(lat) * 110_540])


def distance_to_nearest_destroyed(lon: np.ndarray, lat: np.ndarray, grades: np.ndarray) -> np.ndarray:
    """Distância (m) de cada prédio ao prédio 'destroyed' mais próximo, sem contar ele mesmo."""
    grades = np.asarray(grades, dtype=object)
    xy = _xy_m(lon, lat)
    dest = np.flatnonzero(grades == "destroyed")
    out = np.full(len(xy), np.inf)
    if dest.size == 0:
        return out
    tree = cKDTree(xy[dest])
    k = 2 if dest.size > 1 else 1
    dist, nn = tree.query(xy, k=k)
    if k == 1:
        dist, nn = dist[:, None], nn[:, None]
    # para um destruído, o vizinho mais próximo é ele mesmo (distância 0): usa o segundo
    is_self = dest[nn[:, 0]] == np.arange(len(xy))
    out = np.where(is_self, dist[:, 1] if k == 2 else np.inf, dist[:, 0])
    return out


def wilson(k: int, n: int, z: float = 1.96) -> Tuple[Optional[float], Optional[float]]:
    """IC 95% de Wilson para uma proporção (funciona bem com n pequeno)."""
    if n == 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return round(max(0.0, center - half), 4), round(min(1.0, center + half), 4)


def neighborhood_table(distance_m: np.ndarray, detected: np.ndarray, grades: np.ndarray) -> List[dict]:
    """Por faixa de distância ao destruído mais próximo: recall dos danificados e dos possivelmente
    danificados, e taxa de alarme falso nos prédios sem dano (com IC de Wilson)."""
    grades = np.asarray(grades, dtype=object)
    rows = []
    for lo, hi, label in BANDS_M:
        in_band = (distance_m >= lo) & (distance_m < hi)
        row = {"band": label, "lo_m": lo, "hi_m": None if math.isinf(hi) else hi}
        for key, grade in (("damaged_recall", "damaged"), ("possibly_recall", "possibly"),
                           ("false_alarm", "none")):
            sel = in_band & (grades == grade)
            n, k = int(sel.sum()), int(detected[sel].sum())
            lo_ci, hi_ci = wilson(k, n)
            row[key] = {"n": n, "detected": k, "rate": round(k / n, 4) if n else None, "lo": lo_ci, "hi": hi_ci}
        rows.append(row)
    return rows


def plot_neighborhood(rows: List[dict], path: str, title: str = "") -> None:
    """Gráfico de linhas (matplotlib, backend Agg) das 3 taxas por faixa de distância."""
    from app.models.plot_style import C, decimal_comma, themed_pyplot
    plt = themed_pyplot()

    x = np.arange(len(rows))
    fig, ax = plt.subplots(figsize=(7.2, 4.0), dpi=120)
    series = (("damaged_recall", "Recall: danificado", C["red"], "o"),
              ("possibly_recall", "Recall: possivelmente danificado", C["warn"], "s"),
              ("false_alarm", "Alarme falso: sem dano", C["faint"], "^"))
    for key, label, color, marker in series:
        y = np.array([np.nan if r[key]["rate"] is None else 100 * r[key]["rate"] for r in rows])
        lo = np.array([np.nan if r[key]["lo"] is None else 100 * r[key]["lo"] for r in rows])
        hi = np.array([np.nan if r[key]["hi"] is None else 100 * r[key]["hi"] for r in rows])
        ax.plot(x, y, marker=marker, color=color, label=label, linewidth=2)
        ax.fill_between(x, lo, hi, color=color, alpha=0.12, linewidth=0)
        for xi, yi, r in zip(x, y, rows):
            if np.isfinite(yi):
                ax.annotate(f"n={r[key]['n']}", (xi, yi), textcoords="offset points", xytext=(0, 6),
                            ha="center", fontsize=7, color=color)
    ax.set_xticks(x, [r["band"] for r in rows])
    ax.set_xlabel("Distância até o prédio destruído mais próximo")
    ax.set_ylabel("% marcado como mudança")
    ax.set_ylim(0, 100)
    decimal_comma(ax, x=False)   # o eixo x tem os rótulos das faixas de distância
    ax.grid(axis="y")
    ax.legend(fontsize=8, loc="upper right")
    if title:
        ax.set_title(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
