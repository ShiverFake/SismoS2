"""Gera a vitrine estática do SismoS2 em dist/ (para publicar na Vercel ou em qualquer servidor de arquivos)."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from config import Config  # noqa: E402
from app import create_app  # noqa: E402
from app.controllers.analysis_controller import ALLOWED_FILES  # noqa: E402
from app.models.analysis import AnalysisRepository  # noqa: E402
from app.models.control import find_controls  # noqa: E402

SIZE_LIMIT_MB = 100
ATTR_RE = re.compile(r'(\s(?:href|src|action|poster)=")(/(?!/)[^"]*)"')
BASE_TOKEN = "__SISMOS2_BASE__"


def is_page(path: str) -> bool:
    last = path.rstrip("/").split("/")[-1]
    return not path.startswith(("/static/", "/api/")) and "." not in last


def relative(target: str, depth: int) -> str:
    """'/analise/abc#validacao' numa página de profundidade 2 → '../../analise/abc/#validacao'."""
    cut = min([i for i in (target.find("?"), target.find("#")) if i >= 0] or [len(target)])
    path, rest = target[:cut], target[cut:]
    if is_page(path):
        path = path.rstrip("/") + "/" if path != "/" else ""
    out = "../" * depth + path.lstrip("/")
    return (out or "./") + rest


def page_file(out: str, route: str) -> str:
    return os.path.join(out, route.strip("/"), "index.html") if route != "/" else os.path.join(out, "index.html")


def write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def build(out: str, downloads_url: str | None, with_ems: bool) -> dict:
    class VitrineConfig(Config):
        VITRINE = True
        DOWNLOADS_URL = downloads_url or None

    app = create_app(VitrineConfig)
    client = app.test_client()
    repo = AnalysisRepository(app.config["RESULTS_DIR"])

    featured = [a for a in repo.list() if a.featured and not a.control_of and a.status == "done"]
    if not featured:
        raise SystemExit("Nenhuma análise destacada: marque a estrela de pelo menos uma na galeria do servidor.")
    analyses = []
    for a in featured:
        analyses.append(a)
        analyses += [c for c in find_controls(repo, a) if c.status == "done"]
    ids = list(dict.fromkeys(a.id for a in analyses))

    if os.path.exists(out):
        is_dist = os.path.abspath(out).startswith(ROOT) and os.path.basename(os.path.abspath(out)) == "dist"
        if not (is_dist or os.path.exists(os.path.join(out, "index.html"))):
            raise SystemExit(f"{out} existe e não parece uma vitrine: escolha outra pasta.")
        shutil.rmtree(out)
    os.makedirs(out)
    shutil.copytree(os.path.join(ROOT, "app", "views", "static"), os.path.join(out, "static"))

    def get(route: str):
        r = client.get(route)
        return r.status_code, r.data

    # 1) dados que o JavaScript pede a /api/
    for aid in ids:
        for route in (f"/api/analises/{aid}", f"/api/analises/{aid}/prioridade",
                      f"/api/analises/{aid}/validacao", f"/api/analises/{aid}/controle"):
            status, data = get(route)
            if status == 200 or route.endswith(("/validacao", "/controle")):
                write(os.path.join(out, route.lstrip("/") + ".json"), data)
        folder = repo.directory(aid)
        for fname in sorted(ALLOWED_FILES):
            src = os.path.join(folder, fname)
            if fname.endswith(".tif") or not os.path.exists(src):
                continue
            dst = os.path.join(out, "api", "analises", aid, "arquivos", fname)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        for img in ("miniatura.png", "prioridade.png"):
            status, data = get(f"/analise/{aid}/{img}")
            if status == 200:
                write(os.path.join(out, "analise", aid, img), data)

    for a in analyses:
        if a.ems_activation and a.ems_aoi:
            route = f"/api/eventos/ems/{a.ems_activation}/aoi/{a.ems_aoi}/grading.geojson"
            status, data = get(route)
            if status == 200:
                write(os.path.join(out, route.lstrip("/")), data)

    ems = {"count": 0, "activations": [], "errors": [], "note": "Lista do Copernicus EMS não incluída nesta versão."}
    if with_ems:
        status, data = get("/api/eventos/ems")
        if status == 200:
            ems = json.loads(data)
        else:
            ems["errors"] = [f"Copernicus EMS indisponível no build (HTTP {status})."]
    ems["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for act in ems.get("activations", []):
        for aoi in act.get("aois", []):
            aoi.pop("analyze_url", None)
    write(os.path.join(out, "api", "eventos", "ems.json"), json.dumps(ems, ensure_ascii=False).encode())

    # 2) páginas HTML com links relativos
    routes = ["/", "/analises", "/eventos", "/metodologia", "/sobre"] + [f"/analise/{aid}" for aid in ids]
    for route in routes:
        status, data = get(route)
        if status != 200:
            raise SystemExit(f"{route} respondeu {status}")
        depth = 0 if route == "/" else len(route.strip("/").split("/"))
        html = data.decode("utf-8")
        html = ATTR_RE.sub(lambda m: f'{m.group(1)}{relative(m.group(2), depth)}"', html)
        html = html.replace(BASE_TOKEN, "../" * depth or "./")
        write(page_file(out, route), html.encode("utf-8"))

    return {"pages": routes, "analyses": ids, "featured": [a.id for a in featured], "ems": ems.get("count", 0)}


def check_links(out: str) -> list:
    """Links locais (href/src) de cada página que não existem em dist/."""
    missing = []
    for base, _dirs, files in os.walk(out):
        for f in files:
            if not f.endswith(".html"):
                continue
            page = os.path.join(base, f)
            html = open(page, encoding="utf-8").read()
            for m in re.finditer(r'\s(?:href|src)="([^"#?:]+)[^"]*"', html):
                target = m.group(1)
                if target.startswith(("http", "//", "mailto")) or not target:
                    continue
                path = os.path.normpath(os.path.join(base, target))
                if target.endswith("/") or os.path.isdir(path):
                    path = os.path.join(path, "index.html")
                if not os.path.exists(path):
                    missing.append((os.path.relpath(page, out), target))
    return missing


def folder_mb(path: str) -> float:
    return sum(os.path.getsize(os.path.join(b, f)) for b, _d, fs in os.walk(path) for f in fs) / 1e6


def optimize_pngs(out: str) -> float:
    """Recomprime os PNGs sem perda (Pillow optimize)."""
    from PIL import Image

    saved = 0
    for base, _d, files in os.walk(out):
        for f in files:
            if f.endswith(".png"):
                p = os.path.join(base, f)
                before = os.path.getsize(p)
                with Image.open(p) as im:
                    im.load()
                    im.save(p + ".tmp", format="PNG", optimize=True)
                if os.path.getsize(p + ".tmp") < before:
                    os.replace(p + ".tmp", p)
                    saved += before - os.path.getsize(p)
                else:
                    os.remove(p + ".tmp")
    return saved / 1e6


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--downloads-url", default=os.environ.get("SISMOS2_DOWNLOADS_URL"),
                    help="endereço externo dos GeoTIFFs: <url>/<id da análise>/<arquivo>.tif")
    ap.add_argument("--sem-ems", action="store_true", help="não busca a lista de ativações do Copernicus EMS")
    args = ap.parse_args()

    info = build(args.out, args.downloads_url, not args.sem_ems)
    size = folder_mb(args.out)
    if size > SIZE_LIMIT_MB:
        saved = optimize_pngs(args.out)
        print(f"dist/ passou de {SIZE_LIMIT_MB} MB ({size:.1f} MB): PNGs recomprimidos, {saved:.1f} MB a menos")
        size = folder_mb(args.out)
    missing = check_links(args.out)
    n_files = sum(len(fs) for _b, _d, fs in os.walk(args.out))
    print(f"vitrine em {args.out}: {len(info['pages'])} páginas, {len(info['analyses'])} análises "
          f"({len(info['featured'])} destacadas + controles), {info['ems']} ativações do EMS, {n_files} arquivos, {size:.1f} MB")
    if missing:
        print(f"{len(missing)} link(s) local(is) sem arquivo:")
        for page, target in missing[:30]:
            print(f"  {page}: {target}")
    else:
        print("links locais: todos encontrados")


if __name__ == "__main__":
    main()
