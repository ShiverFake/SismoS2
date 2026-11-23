"""Model: busca e leitura de cenas Sentinel-2 L2A via STAC (Element84 Earth Search)."""
from __future__ import annotations

import logging
import math
import re
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import rasterio
from scipy import ndimage
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform as warp_transform
from shapely.geometry import box, shape

log = logging.getLogger(__name__)

REFLECTANCE_BANDS = ("blue", "green", "red", "nir", "swir16", "swir22")

SCL_INVALID = (0, 1, 3, 8, 9, 10, 11)
SCL_CLOUDY = (3, 8, 9, 10)
SCL_WATER = 6
CLOUD_DILATION_PX = 4
MAX_CANDIDATES = 5
MAX_COMPOSITE = 3

GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "AWS_NO_SIGN_REQUEST": "YES",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF,.tiff",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MAX_RETRY": "4",
    "GDAL_HTTP_RETRY_DELAY": "2",
    "VSI_CACHE": "TRUE",
}


class SentinelError(RuntimeError):
    """Erro de negócio exibido ao usuário (ex.: nenhuma cena encontrada)."""


@dataclass
class Grid:
    """Grade de saída comum às duas datas (Web Mercator, ~10 m no terreno)."""

    crs: str
    transform: object
    width: int
    height: int
    bbox: Tuple[float, float, float, float]
    ground_resolution: float

    @property
    def pixel_area_m2(self) -> float:
        return self.ground_resolution ** 2

    @property
    def leaflet_bounds(self) -> List[List[float]]:
        west, south, east, north = self.bbox
        return [[south, west], [north, east]]

    @classmethod
    def from_bbox(cls, bbox, target_resolution: float = 10.0, max_dim: int = 1500) -> "Grid":
        west, south, east, north = bbox
        lat = math.radians((south + north) / 2.0)
        xs, ys = warp_transform("EPSG:4326", "EPSG:3857", [west, east], [south, north])
        width_m, height_m = xs[1] - xs[0], ys[1] - ys[0]

        res = target_resolution / math.cos(lat)
        scale = max(1.0, max(width_m, height_m) / res / max_dim)
        res *= scale
        width = max(1, int(math.ceil(width_m / res)))
        height = max(1, int(math.ceil(height_m / res)))

        transform = from_origin(xs[0], ys[1], res, res)
        lons, lats = warp_transform(
            "EPSG:3857", "EPSG:4326",
            [xs[0], xs[0] + width * res], [ys[1] - height * res, ys[1]],
        )
        real_bbox = (lons[0], lats[0], lons[1], lats[1])
        return cls("EPSG:3857", transform, width, height, real_bbox, res * math.cos(lat))


@dataclass
class Scene:
    item_id: str
    datetime: str
    cloud_cover: float
    platform: str
    tile: str
    relative_orbit: Optional[int]
    processing_baseline: Optional[str]
    radiometry: str
    clear_fraction: float
    bands: Dict[str, np.ndarray] = field(repr=False)
    data_mask: np.ndarray = field(repr=False)
    clear_mask: np.ndarray = field(repr=False)
    water_mask: Optional[np.ndarray] = field(default=None, repr=False)
    members: List[dict] = field(default_factory=list)
    scl: Optional[np.ndarray] = field(default=None, repr=False)
    sun_azimuth: Optional[float] = None
    sun_elevation: Optional[float] = None
    topo: Optional[dict] = None
    aot: Optional[float] = None

    def metadata(self) -> dict:
        meta = {
            "item_id": self.item_id,
            "datetime": self.datetime,
            "cloud_cover": self.cloud_cover,
            "platform": self.platform,
            "tile": self.tile,
            "relative_orbit": self.relative_orbit,
            "processing_baseline": self.processing_baseline,
            "radiometry": self.radiometry,
            "clear_fraction": round(self.clear_fraction, 4),
            "sun_azimuth": round(self.sun_azimuth, 2) if self.sun_azimuth is not None else None,
            "sun_elevation": round(self.sun_elevation, 2) if self.sun_elevation is not None else None,
            "topo": self.topo,
            "aot": self.aot,
        }
        meta["n_scenes"] = len(self.members) or 1
        meta["scenes"] = self.members or [{k: meta[k] for k in
                                           ("item_id", "datetime", "clear_fraction", "sun_azimuth",
                                            "sun_elevation", "topo", "aot")}]
        return meta


@dataclass
class Candidate:
    """Cena candidata já avaliada pela SCL dentro do retângulo."""
    item: object
    clear: np.ndarray
    water: np.ndarray
    clear_fraction: float
    scl: Optional[np.ndarray] = None
    aot: Optional[float] = None


SCL_CLOUD_ONLY = (8, 9)
BRIGHT_B11 = 0.30


def masks_from_scl(scl: np.ndarray, dilation_px: int = CLOUD_DILATION_PX,
                   fixed_bright: Optional[np.ndarray] = None) -> Tuple[np.ndarray, np.ndarray]:
    """Máscara de pixels limpos e de água a partir da SCL."""
    cloudy = np.isin(scl, SCL_CLOUDY)
    invalid = np.isin(scl, SCL_INVALID)
    if fixed_bright is not None:
        cloudy &= ~fixed_bright
        invalid &= ~fixed_bright
    if dilation_px > 0 and cloudy.any():
        cloudy = ndimage.binary_dilation(cloudy, structure=np.ones((3, 3), bool), iterations=dilation_px)
    clear = (scl > 0) & ~invalid & ~cloudy
    return clear, scl == SCL_WATER


def bright_fixed_surface(before: List["Scene"], after: List["Scene"], b11_min: float = BRIGHT_B11) -> np.ndarray:
    """Pixels marcados como nuvem (SCL 8/9) em TODAS as cenas das DUAS datas e com B11 > 0,3 em todas."""
    def persistent(scenes):
        cloud = np.all([np.isin(s.scl, SCL_CLOUD_ONLY) for s in scenes], axis=0)
        bright = np.all([np.nan_to_num(s.bands["swir16"], nan=0.0) > b11_min for s in scenes], axis=0)
        return cloud & bright
    if any(s.scl is None for s in before + after):
        raise ValueError("Cenas sem SCL: não é possível separar superfície clara de nuvem.")
    return persistent(before) & persistent(after)


def apply_fixed_bright(scenes: List["Scene"], fixed_bright: np.ndarray) -> None:
    """Refaz a máscara de cada cena considerando a superfície clara fixa como válida."""
    for s in scenes:
        clear, _ = masks_from_scl(s.scl, fixed_bright=fixed_bright)
        s.clear_mask = clear & s.data_mask
        s.clear_fraction = float(s.clear_mask.mean())


CLEAR_TIE_TOLERANCE = 0.01


def order_by_clear_fraction(evaluated: List[Tuple[int, "Candidate"]],
                            tolerance: float = CLEAR_TIE_TOLERANCE) -> List["Candidate"]:
    """Ordena as candidatas pela área limpa no retângulo."""
    best = max(c.clear_fraction for _, c in evaluated)
    tied = lambda c: c.clear_fraction >= best - tolerance  # noqa: E731
    ordered = sorted(evaluated, key=lambda t: (0, t[0]) if tied(t[1]) else (1, -t[1].clear_fraction))
    return [c for _, c in ordered]


def median_composite(scenes: List["Scene"]) -> "Scene":
    """Mediana por pixel de várias cenas, ignorando os pixels mascarados (nuvem/sombra/sem dado)."""
    if len(scenes) == 1:
        return scenes[0]
    clear_any = np.any([s.clear_mask for s in scenes], axis=0)
    bands = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for b in scenes[0].bands:
            stack = np.stack([s.bands[b] for s in scenes])
            clear_stack = np.where(np.stack([s.clear_mask for s in scenes]), stack, np.nan)
            med_clear = np.nanmedian(clear_stack, axis=0)
            med_all = np.nanmedian(stack, axis=0)
            bands[b] = np.where(clear_any, med_clear, med_all).astype(np.float32)
    first = scenes[0]
    members = [{"item_id": s.item_id, "datetime": s.datetime, "clear_fraction": round(s.clear_fraction, 4),
                "sun_azimuth": s.sun_azimuth, "sun_elevation": s.sun_elevation, "topo": s.topo}
               for s in scenes]
    return Scene(
        item_id=f"mediana de {len(scenes)} cenas", datetime=first.datetime,
        cloud_cover=float(np.mean([s.cloud_cover for s in scenes])), platform=first.platform,
        tile=first.tile, relative_orbit=first.relative_orbit,
        processing_baseline=first.processing_baseline, radiometry=first.radiometry,
        clear_fraction=float(clear_any.mean()), bands=bands,
        data_mask=np.any([s.data_mask for s in scenes], axis=0), clear_mask=clear_any,
        water_mask=(np.any([s.water_mask for s in scenes if s.water_mask is not None], axis=0)
                    if any(s.water_mask is not None for s in scenes) else None),
        members=members,
        sun_azimuth=first.sun_azimuth, sun_elevation=first.sun_elevation,
        topo={"per_scene": True} if any(s.topo for s in scenes) else None,
    )


ProgressFn = Callable[[str], None]


class SentinelRepository:
    def __init__(self, stac_url: str, collection: str, max_aot: Optional[float] = None):
        self.stac_url = stac_url
        self.collection = collection
        self.max_aot = max_aot

    # ------------------------------------------------------------------ busca
    def search(self, bbox, start: str, end: str, max_cloud: float, limit: int = 60,
               prefer: str = "cloud", reference=None, tile: Optional[str] = None) -> list:
        """prefer: 'cloud' (menos nuvem), 'latest' (mais recente) ou 'earliest' (mais antiga)."""
        from pystac_client import Client

        client = Client.open(self.stac_url)
        search = client.search(
            collections=[self.collection],
            bbox=list(bbox),
            datetime=f"{start}T00:00:00Z/{end}T23:59:59Z",
            query={"eo:cloud_cover": {"lt": max_cloud}},
            max_items=limit,
        )
        items = latest_processing(list(search.items()))
        aoi = box(*bbox)

        def coverage(item) -> float:
            return shape(item.geometry).intersection(aoi).area / aoi.area

        def secondary(item) -> float:
            ts = item.datetime.timestamp() if item.datetime else 0.0
            if prefer == "latest":
                return -ts
            if prefer == "earliest":
                return ts
            return item.properties.get("eo:cloud_cover", 100.0)

        ref_tile = tile_of(reference) if reference is not None else tile
        ref_orbit = relative_orbit(reference) if reference is not None else None
        if ref_tile:
            same_tile = [it for it in items if tile_of(it) == ref_tile]
            if same_tile:
                items = same_tile
            else:
                log.warning("Nenhuma cena do tile %s em %s→%s; usando outros tiles", ref_tile, start, end)

        def orbit_mismatch(item) -> bool:
            return ref_orbit is not None and relative_orbit(item) != ref_orbit

        items.sort(key=lambda it: (coverage(it) < 0.99, orbit_mismatch(it), secondary(it)))
        log.info("STAC %s→%s: %d cenas (tile ref=%s, órbita ref=%s)", start, end, len(items), ref_tile, ref_orbit)
        return items

    def rank_candidates(self, items: list, grid: Grid, max_candidates: int = MAX_CANDIDATES,
                        progress: Optional[ProgressFn] = None) -> List[Candidate]:
        """Avalia até `max_candidates` cenas pela SCL e ordena pela % de área limpa NO RETÂNGULO."""
        evaluated = []
        subset = items[:max_candidates]
        for i, item in enumerate(subset):
            if progress:
                progress(f"medindo nuvens no retângulo: cena {i + 1}/{len(subset)} ({item.id})")
            try:
                scl = self._read_asset(item, "scl", grid, Resampling.nearest)
            except Exception as exc:
                log.warning("Falha lendo SCL de %s: %s", item.id, exc)
                continue
            clear, water = masks_from_scl(scl)
            aot = self._aot_median(item, grid, clear)
            evaluated.append((i, Candidate(item, clear, water, float(clear.mean()), scl, aot)))
        if not evaluated:
            raise SentinelError("Não foi possível ler nenhuma das cenas encontradas.")
        if self.max_aot is not None:
            hazy = [c for _, c in evaluated if c.aot is not None and c.aot > self.max_aot]
            evaluated = [(i, c) for i, c in evaluated if c.aot is None or c.aot <= self.max_aot]
            if hazy:
                log.info("Descartadas por névoa (AOT > %.2f): %s", self.max_aot,
                         [(c.item.id, round(c.aot, 3)) for c in hazy])
            if not evaluated:
                raise SentinelError(
                    f"Todas as cenas avaliadas têm névoa acima do limite (AOT > {self.max_aot}): "
                    + ", ".join(f"{c.item.datetime.date()} AOT {c.aot:.2f}" for c in hazy)
                    + ". Amplie o período ou ajuste MAX_AOT no config.py.")
        return order_by_clear_fraction(evaluated)

    def _aot_median(self, item, grid: Grid, clear: np.ndarray) -> Optional[float]:
        """AOT mediana (espessura óptica do aerossol a 550 nm) nos pixels limpos do retângulo."""
        if "aot" not in item.assets:
            return None
        try:
            dn = self._read_asset(item, "aot", grid, Resampling.nearest).astype(np.float32)
        except Exception as exc:  # noqa: BLE001
            log.warning("Falha lendo AOT de %s: %s", item.id, exc)
            return None
        info = (item.assets["aot"].extra_fields.get("raster:bands") or [{}])[0]
        values = dn[clear & (dn > 0)] * float(info.get("scale", 0.001)) + float(info.get("offset", 0.0))
        return round(float(np.median(values)), 4) if values.size >= 100 else None

    # ---------------------------------------------------------------- leitura
    def load_candidate(self, cand: Candidate, grid: Grid) -> Scene:
        scene = self.load_scene(cand.item, grid, cand.clear, cand.clear_fraction, cand.water, cand.scl)
        scene.aot = cand.aot
        return scene

    def load_scene(self, item, grid: Grid, clear_mask: np.ndarray, clear_fraction: float,
                   water_mask: Optional[np.ndarray] = None, scl: Optional[np.ndarray] = None) -> Scene:
        def read(key):
            asset = item.assets[key]
            dn = self._read_asset(item, key, grid, Resampling.bilinear).astype(np.float32)
            scale, offset, _ = reflectance_params(item, asset)
            refl = np.clip(dn * scale + offset, 0.0, None)
            refl[dn == 0] = np.nan
            return key, refl

        with ThreadPoolExecutor(max_workers=3) as pool:
            bands = dict(pool.map(read, REFLECTANCE_BANDS))

        data_mask = np.all([np.isfinite(b) for b in bands.values()], axis=0)
        props = item.properties
        return Scene(
            item_id=item.id,
            datetime=props.get("datetime", ""),
            cloud_cover=float(props.get("eo:cloud_cover", float("nan"))),
            platform=props.get("platform", ""),
            tile=tile_of(item) or "",
            relative_orbit=relative_orbit(item),
            processing_baseline=props.get("s2:processing_baseline"),
            radiometry=reflectance_params(item, item.assets["red"])[2],
            clear_fraction=clear_fraction,
            bands=bands,
            data_mask=data_mask,
            clear_mask=clear_mask & data_mask,
            water_mask=water_mask,
            scl=scl,
            sun_azimuth=_float_or_none(props.get("view:sun_azimuth")),
            sun_elevation=_float_or_none(props.get("view:sun_elevation")),
        )

    @staticmethod
    def _read_asset(item, key: str, grid: Grid, resampling: Resampling) -> np.ndarray:
        if key not in item.assets:
            raise SentinelError(f"A cena {item.id} não possui o asset '{key}'.")
        href = item.assets[key].href
        with rasterio.Env(**GDAL_ENV):
            with rasterio.open(href) as src:
                with WarpedVRT(
                    src,
                    crs=grid.crs,
                    transform=grid.transform,
                    width=grid.width,
                    height=grid.height,
                    resampling=resampling,
                    src_nodata=src.nodata if src.nodata is not None else 0,
                    nodata=0,
                ) as vrt:
                    return vrt.read(1)


_ORBIT_RE = re.compile(r"_R(\d{3})_")


def tile_of(item) -> Optional[str]:
    """Tile MGRS (ex.: '37SBA')."""
    props = item.properties
    tile = props.get("s2:mgrs_tile") or props.get("grid:code")
    if not tile:
        return None
    return str(tile).removeprefix("MGRS-")


def relative_orbit(item) -> Optional[int]:
    """Órbita relativa (1–143)."""
    props = item.properties
    if props.get("sat:relative_orbit") is not None:
        return int(props["sat:relative_orbit"])
    match = _ORBIT_RE.search(str(props.get("s2:product_uri") or ""))
    return int(match.group(1)) if match else None


def latest_processing(items: list) -> list:
    """Uma cena por (tile, data, satélite): a do processamento mais recente."""
    best = {}
    for it in items:
        p = it.properties
        key = (tile_of(it), it.datetime.date() if it.datetime else None, p.get("platform"))
        rank = (_baseline(p) or 0.0, str(p.get("s2:generation_time") or ""), it.id)
        if key not in best or rank > best[key][0]:
            best[key] = (rank, it)
    kept = [v[1] for v in best.values()]
    if len(kept) < len(items):
        log.info("Deduplicação: %d versões antigas descartadas", len(items) - len(kept))
    return kept


def _float_or_none(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _baseline(props: dict) -> Optional[float]:
    try:
        return float(props.get("s2:processing_baseline"))
    except (TypeError, ValueError):
        return None


def reflectance_params(item, asset) -> Tuple[float, float, str]:
    """Escala e offset para converter DN em reflectância de superfície: ρ = DN·escala + offset."""
    props = item.properties
    bands = asset.extra_fields.get("raster:bands") or [{}]
    info = bands[0] if bands else {}
    scale = float(info.get("scale", 1e-4))
    applied = props.get("earthsearch:boa_offset_applied")
    baseline = _baseline(props)
    if applied is True:
        return scale, 0.0, "offset já removido pelo provedor (boa_offset_applied)"
    if applied is False:
        if baseline is not None and baseline >= 4.0:
            return scale, -0.1, f"offset −0,1 aplicado (baseline {baseline:05.2f})"
        return scale, 0.0, f"sem offset (baseline {baseline:05.2f})" if baseline else "sem offset"
    if "offset" in info:
        return scale, float(info["offset"]), "offset do raster:bands"
    if baseline is not None and baseline >= 4.0:
        return scale, -0.1, f"offset −0,1 aplicado (baseline {baseline:05.2f})"
    return scale, 0.0, "sem offset"
