"""Model: estilo único dos gráficos (matplotlib) com as cores do site (app/views/static/css/tokens.css)."""
from __future__ import annotations

C = {
    "bg": "#10223a",
    "grid": "#26476e",
    "ink": "#e8eef6",
    "muted": "#9db0c7",
    "faint": "#6f86a1",
    "red": "#ef4444",
    "ok": "#34c47c",
    "warn": "#f2b84b",
    "info": "#6aa7ff",
    "violet": "#b48cff",
    "cyan": "#3cc8d8",
}
SERIES = [C["red"], C["info"], C["warn"], C["ok"], C["violet"], C["cyan"]]

RC = {
    "figure.facecolor": C["bg"], "axes.facecolor": C["bg"], "savefig.facecolor": C["bg"],
    "axes.edgecolor": C["grid"], "axes.labelcolor": C["muted"], "axes.titlecolor": C["ink"],
    "axes.titleweight": "semibold", "axes.titlesize": 10, "axes.labelsize": 9,
    "axes.spines.top": False, "axes.spines.right": False,
    "xtick.color": C["muted"], "ytick.color": C["muted"], "xtick.labelsize": 8, "ytick.labelsize": 8,
    "grid.color": C["grid"], "grid.alpha": 0.7, "grid.linewidth": 0.6,
    "text.color": C["ink"], "legend.facecolor": "#0a1626", "legend.edgecolor": C["grid"],
    "legend.labelcolor": C["ink"], "legend.framealpha": 0.9,
    "font.family": "sans-serif", "font.sans-serif": ["Inter", "DejaVu Sans"], "font.size": 9,
    "savefig.dpi": 150,
}


def br(value, digits: int = 2) -> str:
    """Número com vírgula decimal (pt-BR), para títulos, legendas e anotações."""
    if value is None or value != value:
        return "—"
    return f"{value:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def decimal_comma(ax, x: bool = True, y: bool = True) -> None:
    """Marcas dos eixos com vírgula decimal (0,2 · 0,4 …)."""
    from matplotlib.ticker import FuncFormatter

    fmt = FuncFormatter(lambda v, _pos: f"{v:g}".replace(".", ","))
    if x:
        ax.xaxis.set_major_formatter(fmt)
    if y:
        ax.yaxis.set_major_formatter(fmt)


def themed_pyplot():
    """pyplot (backend Agg) já com o tema do site aplicado."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(RC)
    return plt
