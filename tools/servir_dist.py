"""Serve dist/ como a Vercel serviria: com os cabeçalhos do vercel.json e o redirecionamento para a barra final."""
from __future__ import annotations

import http.server
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
with open(os.path.join(ROOT, "vercel.json"), encoding="utf-8") as fh:
    CONFIG = json.load(fh)


def rule_regex(source: str) -> re.Pattern:
    """Converte o "source" do vercel.json (estilo path-to-regexp, só o que usamos) em regex."""
    return re.compile("^" + source.replace("(.*)", "(.*)") + "$")


RULES = [(rule_regex(r["source"]), r["headers"]) for r in CONFIG.get("headers", [])]


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIST, **kwargs)

    def end_headers(self):
        path = self.path.split("?")[0]
        for rx, headers in RULES:
            if rx.match(path):
                for h in headers:
                    self.send_header(h["key"], h["value"])
        super().end_headers()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8200
    print(f"vitrine em http://localhost:{port}/ (Ctrl+C para parar)")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
