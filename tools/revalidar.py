"""Refaz as validações já concluídas com os mesmos parâmetros (para aplicar regras novas e regerar os gráficos)."""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app  # noqa: E402
from app.models.analysis import AnalysisRepository  # noqa: E402

EMS_RE = re.compile(r"Copernicus EMS (EMSR\d+) – AOI (\d+)")


def main(ids) -> None:
    app = create_app()
    repo = AnalysisRepository(app.config["RESULTS_DIR"])
    client = app.test_client()
    for a in repo.list():
        path = os.path.join(repo.directory(a.id), "validation.json")
        if (ids and a.id not in ids) or not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            v = json.load(fh)
        if v.get("status") != "done":
            continue
        form = {"buffer_m": v.get("buffer_m") or 10, "possible_mode": v.get("possible_mode") or "excluir",
                "unit_mode": v.get("unit_mode") or "osm", "grid_threshold": v.get("grid_threshold") or 0.10}
        m = EMS_RE.match(v.get("source") or "")
        if m:
            res = client.post(f"/api/analises/{a.id}/validacao", json={**form, "source": "ems",
                                                                       "activation": m.group(1), "aoi": int(m.group(2))})
        else:
            name = (v.get("source") or "").split(":", 1)[-1].strip()
            ref = os.path.join(app.config["REFERENCE_DIR"], name)
            if not os.path.exists(ref):
                print(f"{a.id}: arquivo de referência {name!r} não encontrado, pulada")
                continue
            with open(ref, "rb") as fh:
                data = {**{k: str(x) for k, x in form.items()}, "source": "file", "file": (io.BytesIO(fh.read()), name)}
            res = client.post(f"/api/analises/{a.id}/validacao", data=data, content_type="multipart/form-data")
        if res.status_code != 202:
            print(f"{a.id}: não iniciou ({res.status_code}) {res.get_json()}")
            continue
        t0 = time.time()
        while True:
            time.sleep(3)
            st = client.get(f"/api/analises/{a.id}/validacao").get_json() or {}
            if st.get("status") in ("done", "error"):
                break
        auc = (((st.get("grid_controls") or {}).get("sizes") or {}).get("200") or {}).get("analyses") or [{}]
        print(f"{a.id}  {a.name}: {st.get('status')} em {time.time() - t0:.0f} s"
              f"{'; AUC 200 m ' + str(auc[0].get('auc')) if auc[0].get('auc') else ''}"
              f"{'; erro: ' + str(st.get('error')) if st.get('status') == 'error' else ''}", flush=True)


if __name__ == "__main__":
    main(set(sys.argv[1:]))
