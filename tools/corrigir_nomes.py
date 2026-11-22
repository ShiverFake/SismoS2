"""Corrige a acentuação dos nomes das análises já salvas e marca as análises em destaque.

Uso (na VM):  .venv/bin/python tools/corrigir_nomes.py          # só mostra o que mudaria
              .venv/bin/python tools/corrigir_nomes.py --gravar # grava
Só o campo "name" (e "featured") de analysis.json é alterado.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import Config  # noqa: E402
from app.models.analysis import AnalysisRepository  # noqa: E402

# trechos trocados em todos os nomes (inclusive nos controles, que herdam o nome do evento)
REPLACEMENTS = [
    ("EMSR695 AOI07 (via Eventos)", "Marrocos 2023 – Talat N'Yaaqoub (EMSR695, AOI 07)"),
    ("correcao topografica", "correção topográfica"),
    ("cena unica", "cena única"),
    ("Talat N Yaaqoub", "Talat N'Yaaqoub"),
    ("Antakya 2023 - ", "Antakya 2023 – "),
    ("Marrocos 2023 - ", "Marrocos 2023 – "),
    ("Myanmar 2025 - ", "Myanmar 2025 – "),
]
FEATURED = {"9ea9d74fe815", "47412403ace4"}   # Antakya com correção topográfica e EMSR695


def fixed(name: str) -> str:
    for a, b in REPLACEMENTS:
        name = name.replace(a, b)
    return name


def main(write: bool) -> None:
    repo = AnalysisRepository(Config.RESULTS_DIR)
    for a in repo.list():
        new_name, new_feat = fixed(a.name), a.featured or a.id in FEATURED
        if new_name == a.name and new_feat == a.featured:
            continue
        print(f"{a.id}  {a.name!r} -> {new_name!r}{'  [destaque]' if new_feat and not a.featured else ''}")
        if write:
            a.name, a.featured = new_name, new_feat
            repo.save(a)
    print("gravado" if write else "(prévia: rode com --gravar para aplicar)")


if __name__ == "__main__":
    main("--gravar" in sys.argv)
