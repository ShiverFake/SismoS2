"""Model: catálogo de terremotos de exemplo (áreas e janelas de data pré-configuradas)."""
from dataclasses import asdict, dataclass
from typing import List, Optional


@dataclass(frozen=True)
class Earthquake:
    id: str
    name: str
    date: str               # data do evento (UTC)
    magnitude: float
    description: str
    bbox: tuple             # (oeste, sul, leste, norte) em graus
    before_start: str
    before_end: str
    after_start: str
    after_end: str
    max_cloud: int = 30
    # Produto oficial de dano (Copernicus EMS Rapid Mapping) para validação, se houver
    ems_activation: Optional[str] = None
    ems_aoi: Optional[int] = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["bbox"] = list(self.bbox)
        return data


class EarthquakeRepository:
    """Eventos com cobertura Sentinel-2 (lançado em 2015) usados como exemplo."""

    _EVENTS: List[Earthquake] = [
        Earthquake(
            id="turquia-2023",
            name="Turquia/Síria – Kahramanmaraş (Antakya)",
            date="2023-02-06", magnitude=7.8,
            description="Colapso generalizado de edifícios no centro de Antakya (Hatay).",
            bbox=(36.12, 36.18, 36.22, 36.25),
            before_start="2022-11-15", before_end="2023-02-05",
            after_start="2023-02-07", after_end="2023-03-31", max_cloud=40,
        ),
        Earthquake(
            id="marrocos-2023",
            name="Marrocos – Al Haouz (Alto Atlas)",
            date="2023-09-08", magnitude=6.8,
            description="Vilarejos de taipa destruídos e deslizamentos no vale do N'Fis.",
            bbox=(-8.25, 30.95, -8.10, 31.06),
            before_start="2023-07-15", before_end="2023-09-07",
            after_start="2023-09-09", after_end="2023-10-20", max_cloud=20,
            ems_activation="EMSR695", ems_aoi=7,   # AOI07 Talat N'Yaaqoub
        ),
        Earthquake(
            id="myanmar-kyaukse-2025",
            name="Myanmar – Sagaing (AOI Kyaukse do Copernicus EMS)",
            date="2025-03-28", magnitude=7.7,
            description="Área com mapa oficial de dano do Copernicus EMS (EMSR798, AOI07) para validação.",
            bbox=(95.825, 21.945, 95.880, 21.998),
            before_start="2025-02-01", before_end="2025-03-27",
            after_start="2025-03-29", after_end="2025-04-30", max_cloud=30,
            ems_activation="EMSR798", ems_aoi=7,
        ),
        Earthquake(
            id="myanmar-2025",
            name="Myanmar – Sagaing/Mandalay",
            date="2025-03-28", magnitude=7.7,
            description="Ruptura da falha de Sagaing; danos em Sagaing e na ponte Ava.",
            bbox=(95.93, 21.84, 96.10, 21.98),
            before_start="2025-01-15", before_end="2025-03-27",
            after_start="2025-03-29", after_end="2025-04-30", max_cloud=30,
        ),
        Earthquake(
            id="haiti-2021",
            name="Haiti – Nippes / Pic Macaya",
            date="2021-08-14", magnitude=7.2,
            description="Milhares de deslizamentos de terra no maciço de La Hotte.",
            bbox=(-74.00, 18.30, -73.80, 18.45),
            before_start="2021-06-01", before_end="2021-08-13",
            after_start="2021-08-15", after_end="2021-10-31", max_cloud=40,
        ),
        Earthquake(
            id="japao-noto-2024",
            name="Japão – Península de Noto (Wajima)",
            date="2024-01-01", magnitude=7.5,
            description="Incêndio no centro de Wajima, soerguimento costeiro e deslizamentos.",
            bbox=(136.86, 37.36, 136.94, 37.41),
            before_start="2023-10-01", before_end="2023-12-31",
            after_start="2024-01-02", after_end="2024-03-15", max_cloud=40,
        ),
    ]

    def all(self) -> List[Earthquake]:
        return list(self._EVENTS)

    def get(self, event_id: str) -> Optional[Earthquake]:
        return next((e for e in self._EVENTS if e.id == event_id), None)
