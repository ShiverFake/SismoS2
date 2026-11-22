"""Model: entidade Análise e seu repositório (persistência em JSON no disco)."""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

_ID_RE = re.compile(r"^[a-f0-9]{12}$")
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Analysis:
    id: str
    name: str
    bbox: List[float]
    before_start: str
    before_end: str
    after_start: str
    after_end: str
    max_cloud: int = 30
    threshold_method: str = "kstd"
    k: float = 2.0
    min_region_px: int = 6
    median_composite: bool = False   # mediana de até 3 cenas por período
    topo_correction: bool = True     # C-correction com o Copernicus DEM
    event_id: Optional[str] = None
    control_id: Optional[str] = None   # análise de controle (ano anterior) desta análise
    control_of: Optional[str] = None   # se esta É um controle: id da análise do evento
    preferred_tile: Optional[str] = None  # tile MGRS a preferir na cena "antes" (o controle usa o do evento)
    control_ids: List[str] = field(default_factory=list)  # controles de vários anos deste evento
    control_year: Optional[int] = None    # se esta É um controle: o ano
    pair_window: Optional[dict] = None    # controle: janela e intervalo-alvo para escolher o par de cenas
    control_excluded: Optional[dict] = None  # controle fora da média: {"reason", "at", "flags"}
    ems_activation: Optional[str] = None  # ativação do Copernicus EMS vinculada (ex.: EMSR695)
    ems_aoi: Optional[int] = None         # AOI dessa ativação com o produto de grading
    featured: bool = False                # "Destacar": fica no topo da galeria e alimenta o comparador do início
    status: str = "pending"          # pending | processing | done | error
    progress: int = 0
    message: str = "Na fila"
    error: Optional[str] = None
    created_at: str = field(default_factory=_now)
    finished_at: Optional[str] = None
    result: Optional[dict] = None

    @classmethod
    def new(cls, **kwargs) -> "Analysis":
        return cls(id=uuid.uuid4().hex[:12], **kwargs)

    def to_dict(self) -> dict:
        return asdict(self)


class AnalysisRepository:
    FILE = "analysis.json"

    def __init__(self, results_dir: str):
        self.results_dir = results_dir

    @staticmethod
    def is_valid_id(analysis_id: str) -> bool:
        return bool(_ID_RE.match(analysis_id or ""))

    def directory(self, analysis_id: str) -> str:
        if not self.is_valid_id(analysis_id):
            raise ValueError("id inválido")
        return os.path.join(self.results_dir, analysis_id)

    def save(self, analysis: Analysis) -> None:
        folder = self.directory(analysis.id)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, self.FILE)
        with _LOCK:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(analysis.to_dict(), fh, ensure_ascii=False, indent=2)
            os.replace(tmp, path)

    def get(self, analysis_id: str) -> Optional[Analysis]:
        if not self.is_valid_id(analysis_id):
            return None
        path = os.path.join(self.directory(analysis_id), self.FILE)
        if not os.path.exists(path):
            return None
        with _LOCK, open(path, encoding="utf-8") as fh:
            return Analysis(**json.load(fh))

    def list(self) -> List[Analysis]:
        if not os.path.isdir(self.results_dir):
            return []
        items = [self.get(name) for name in os.listdir(self.results_dir)]
        return sorted((a for a in items if a), key=lambda a: a.created_at, reverse=True)

    def delete(self, analysis_id: str) -> bool:
        if not self.get(analysis_id):
            return False
        shutil.rmtree(self.directory(analysis_id), ignore_errors=True)
        return True

    def mark_interrupted(self) -> None:
        """Análises que estavam rodando quando o servidor caiu viram 'error'."""
        for a in self.list():
            if a.status in ("pending", "processing"):
                a.status, a.error = "error", "Processamento interrompido (servidor reiniciado)."
                self.save(a)
            vpath = os.path.join(self.directory(a.id), "validation.json")
            if os.path.exists(vpath):
                with open(vpath, encoding="utf-8") as fh:
                    state = json.load(fh)
                if state.get("status") == "processing":
                    state.update(status="error", error="Validação interrompida (servidor reiniciado).")
                    with open(vpath, "w", encoding="utf-8") as fh:
                        json.dump(state, fh, ensure_ascii=False)
