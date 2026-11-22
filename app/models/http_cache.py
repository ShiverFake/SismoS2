"""Model (infra): GET HTTP e cache em disco com validade (TTL) para as APIs públicas."""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

USER_AGENT = "SismoS2/1.0 (trabalho academico; deteccao de mudancas Sentinel-2)"


class HttpError(RuntimeError):
    def __init__(self, status: Optional[int], message: str):
        super().__init__(message)
        self.status = status


def http_get(url: str, timeout: int = 120) -> bytes:
    """GET simples (segue redirects). Erros HTTP viram HttpError com o código."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()[:300].decode("utf-8", "replace")
        raise HttpError(exc.code, f"HTTP {exc.code} em {url}: {body}") from None
    except urllib.error.URLError as exc:
        raise HttpError(None, f"Sem acesso a {url}: {exc.reason}") from None


class JsonCache:
    """Guarda respostas JSON em disco; `get` só chama a rede se o arquivo não existir ou tiver vencido."""

    def __init__(self, folder: str, clock: Callable[[], float] = time.time):
        self.folder = folder
        self.clock = clock

    def _path(self, key: str) -> str:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)[:60]
        return os.path.join(self.folder, f"{safe}_{hashlib.md5(key.encode()).hexdigest()[:8]}.json")

    def get(self, key: str, ttl_s: Optional[float], fetch: Callable[[], object]):
        """ttl_s=None → cache permanente."""
        path = self._path(key)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                saved = json.load(fh)
            if ttl_s is None or self.clock() - saved["saved_at"] < ttl_s:
                return saved["data"]
        data = fetch()
        os.makedirs(self.folder, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"saved_at": self.clock(), "data": data}, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return data
