"""Completa buildings.npz (WorldCover 50 + prédios do OSM) das análises e regera a prioridade e as imagens.

Uso (na VM):  .venv/bin/python tools/regerar_construcoes.py [id ...]
Para cada análise principal concluída, mostra quantas células estavam no ranking antes e depois da
regra "só células com construções".
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app  # noqa: E402
from app.models.analysis import AnalysisRepository  # noqa: E402
from app.models.buildings import ensure_buildings, has_osm  # noqa: E402


def main(ids) -> None:
    app = create_app()
    cfg = app.config
    repo = AnalysisRepository(cfg["RESULTS_DIR"])
    client = app.test_client()
    for a in repo.list():
        if a.control_of or a.status != "done" or (ids and a.id not in ids):
            continue
        if "transform" not in ((a.result or {}).get("grid") or {}):
            continue
        folder = repo.directory(a.id)
        cache = os.path.join(folder, "priority_200.json")
        before = None
        if os.path.exists(cache):
            with open(cache, encoding="utf-8") as fh:
                before = json.load(fh).get("n_cells")
        b = ensure_buildings(folder, a.result, cfg["REFERENCE_DIR"], cfg["OVERPASS_URL"])
        for f in ("priority_200.json", "thumb_priority.png", "priority_overlay.png"):
            if os.path.exists(os.path.join(folder, f)):
                os.remove(os.path.join(folder, f))
        d = client.get(f"/api/analises/{a.id}/prioridade").get_json()
        client.get(f"/analise/{a.id}/miniatura.png")
        client.get(f"/analise/{a.id}/prioridade.png")
        empty = (d.get("empty") or {}).get("n_cells")
        src = [n for n, ok in (("WorldCover", b and b.get("built") is not None), ("OSM", has_osm(b))) if ok]
        n_osm = len(b["osm_rows"]) if has_osm(b) else 0
        print(f"{a.id}  {a.name}: ranking {before} -> {d.get('n_cells')} células; sem construções {empty}; "
              f"top 10% {d.get('top10_n_cells')} células ({d.get('top10_area_km2')} km²); fontes {'+'.join(src) or 'nenhuma'}; "
              f"{n_osm} prédios do OSM na grade", flush=True)


if __name__ == "__main__":
    main(set(sys.argv[1:]))
