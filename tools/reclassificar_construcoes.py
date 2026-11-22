"""Aplica a regra de área construída (WorldCover 50 OU até 10 m de um prédio do OSM) às análises já feitas.

Uso (na VM):  .venv/bin/python tools/reclassificar_construcoes.py [id ...]

Para cada análise concluída (controles incluídos):
- completa buildings.npz (WorldCover, centros e pegada dos prédios do OSM);
- move o solo exposto entre "possível dano em construções" (classe 2) e "possível deslizamento" (classe 5)
  pela nova máscara; as outras classes e a mudança total não mudam;
- regrava change_classes.tif, change.png e as áreas por classe em analysis.json;
- nas análises principais, regera a prioridade, a miniatura e a camada do comparador.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import rasterio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app  # noqa: E402
from app.models.analysis import AnalysisRepository  # noqa: E402
from app.models.buildings import ensure_buildings, urban_mask  # noqa: E402
from app.models.change_detection import CLASSES  # noqa: E402
from app.models.renderer import save_change_map  # noqa: E402

DAMAGE, SLIDE = 2, 5


def reclassify(folder: str, result: dict, urban: np.ndarray) -> dict:
    path = os.path.join(folder, "change_classes.tif")
    with rasterio.open(path) as src:
        classes, profile = src.read(1), src.profile
    before = {c: int((classes == c).sum()) for c in (DAMAGE, SLIDE)}
    soil = np.isin(classes, (DAMAGE, SLIDE))
    classes[soil & urban] = DAMAGE
    classes[soil & ~urban] = SLIDE
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(classes, 1)
    save_change_map(classes, os.path.join(folder, "change.png"))

    stats = result["statistics"]
    valid_px = stats.get("valid_pixels") or 1
    to_km2 = float(result["grid"]["ground_resolution_m"]) ** 2 / 1e6
    for row in stats.get("classes", []):
        n = int((classes == row["id"]).sum())
        row.update(pixels=n, area_km2=round(n * to_km2, 4), percent=round(100.0 * n / valid_px, 3))
    if not any(l.get("id") == SLIDE for l in result.get("legend", [])):
        info = CLASSES[SLIDE]
        result.setdefault("legend", []).append({"id": SLIDE, "name": info["name"],
                                                "color": "rgb({},{},{})".format(*info["color"][:3])})
    after = {c: int((classes == c).sum()) for c in (DAMAGE, SLIDE)}
    return {"before": before, "after": after, "km2": to_km2}


def main(ids) -> None:
    app = create_app()
    cfg = app.config
    repo = AnalysisRepository(cfg["RESULTS_DIR"])
    client = app.test_client()
    for a in repo.list():
        if a.status != "done" or (ids and a.id not in ids) or "transform" not in ((a.result or {}).get("grid") or {}):
            continue
        folder = repo.directory(a.id)
        b = ensure_buildings(folder, a.result, cfg["REFERENCE_DIR"], cfg["OVERPASS_URL"])
        urban = urban_mask(b)
        if urban is None:
            print(f"{a.id}  {a.name}: sem WorldCover nem OSM, mantida")
            continue
        info = reclassify(folder, a.result, urban)
        repo.save(a)
        km2 = info["km2"]
        line = (f"{a.id}  {a.name}: classe 2 (dano em construções) {info['before'][DAMAGE] * km2:.4f} -> "
                f"{info['after'][DAMAGE] * km2:.4f} km²; classe 5 (deslizamento) {info['before'][SLIDE] * km2:.4f} -> "
                f"{info['after'][SLIDE] * km2:.4f} km²; área construída {100 * urban.mean():.1f}% da grade")
        if not a.control_of:
            for f in ("priority_200.json", "thumb_priority.png", "priority_overlay.png"):
                if os.path.exists(os.path.join(folder, f)):
                    os.remove(os.path.join(folder, f))
            d = client.get(f"/api/analises/{a.id}/prioridade").get_json()
            client.get(f"/analise/{a.id}/miniatura.png")
            client.get(f"/analise/{a.id}/prioridade.png")
            line += (f"; ranking {d.get('n_cells')} células, sem construções {(d.get('empty') or {}).get('n_cells')}, "
                     f"top 10% {d.get('top10_n_cells')} células ({d.get('top10_area_km2')} km²)")
        print(line, flush=True)


if __name__ == "__main__":
    main(set(sys.argv[1:]))
