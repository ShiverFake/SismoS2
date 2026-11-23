"""Gera a máscara de água (water.npz) das análises já feitas e regera a prioridade e as miniaturas."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app  # noqa: E402
from app.models.analysis import AnalysisRepository  # noqa: E402
from app.models.water import rebuild_water  # noqa: E402


def main(ids) -> None:
    app = create_app()
    cfg = app.config
    repo = AnalysisRepository(cfg["RESULTS_DIR"])
    todo = [a for a in repo.list() if a.status == "done" and (not ids or a.id in ids)]
    client = app.test_client()
    for a in todo:
        folder = repo.directory(a.id)
        if "transform" not in ((a.result or {}).get("grid") or {}):
            print(f"{a.id}  {a.name}: versão antiga sem grade salva, pulada")
            continue
        info = rebuild_water(folder, a.result, cfg["STAC_URL"], cfg["STAC_COLLECTION"])
        line = f"{a.id}  {a.name}: água {info['water_share']:.1%} ({', '.join(info['sources']) or 'sem fonte'})"
        if not a.control_of:
            for f in ("priority_200.json", "thumb_priority.png"):
                if os.path.exists(os.path.join(folder, f)):
                    os.remove(os.path.join(folder, f))
            r = client.get(f"/api/analises/{a.id}/prioridade").get_json()
            client.get(f"/analise/{a.id}/miniatura.png")
            line += f"; prioridade: {r.get('n_cells')} células, {r.get('n_water_cells')} de água fora"
        print(line, flush=True)


if __name__ == "__main__":
    main(set(sys.argv[1:]))
