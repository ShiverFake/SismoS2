"""Model (serviço): orquestra busca → download → detecção → geração de camadas."""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import rasterio

from app.models import renderer
from app.models.analysis import Analysis, AnalysisRepository
from app.models.change_detection import CLASSES, NODATA, ChangeParams, compute_statistics, detect_changes
from app.models.landcover import BUILT_UP, read_worldcover
from app.models.buildings import osm_footprint_mask, osm_pixels, save_buildings
from app.models.water import save_water, water_mask
from app.models.quality import land_mask, quality_warnings, urban_stats
from app.models.control import choose_control_pair
from app.models.sentinel import (MAX_COMPOSITE, Grid, SentinelError, SentinelRepository, apply_fixed_bright,
                                 bright_fixed_surface, median_composite, relative_orbit, tile_of)
from app.models.terrain import (TerrainRepository, TerrainUnavailable, c_correction, illumination,
                                slope_aspect, steep_share)

log = logging.getLogger(__name__)


class ChangeDetectionService:
    def __init__(self, repository: AnalysisRepository, sentinel: SentinelRepository,
                 target_resolution: float = 10.0, max_dim: int = 1500,
                 terrain: Optional[TerrainRepository] = None, landcover_reader=read_worldcover,
                 osm_reader=None):
        self.repository = repository
        self.sentinel = sentinel
        self.target_resolution = target_resolution
        self.max_dim = max_dim
        self.terrain = terrain or TerrainRepository(sentinel.stac_url)
        self.landcover_reader = landcover_reader
        self.osm_reader = osm_reader   # grade → polígonos dos prédios do OSM (None: só o WorldCover)

    def _load_control_pair(self, analysis: Analysis, grid, step, notes: list) -> dict:
        """Controle: avalia todas as cenas da janela (mesmo tile; mesma órbita do evento se houver ≥ 2)
        e escolhe o par com intervalo mais parecido com o do evento e mais área válida."""
        w = analysis.pair_window
        step(5, f"Buscando cenas de {w['start']} a {w['end']} (controle {analysis.control_year})…")
        items = self.sentinel.search(tuple(analysis.bbox), w["start"], w["end"], analysis.max_cloud,
                                     prefer="earliest", tile=analysis.preferred_tile)
        if analysis.preferred_tile:
            items = [it for it in items if tile_of(it) == analysis.preferred_tile]
        same_orbit = [it for it in items if relative_orbit(it) == w.get("relative_orbit")]
        if len(same_orbit) >= 2:
            items = same_orbit
        if len(items) < 2:
            raise SentinelError(f"Menos de 2 cenas no tile {analysis.preferred_tile} entre {w['start']} e {w['end']}.")
        cands = self.sentinel.rank_candidates(items, grid, max_candidates=len(items),
                                              progress=lambda m: step(15, f"(controle) {m}"))
        dates = [c.item.datetime.date() for c in cands]
        min_valid = float(w.get("min_valid") or 0.0)
        pair = choose_control_pair(dates, [c.clear for c in cands], int(w["target_gap_days"]),
                                   min_common_valid=min_valid)
        if pair is None:
            any_pair = choose_control_pair(dates, [c.clear for c in cands], int(w["target_gap_days"]))
            detail = (f"o melhor par ({dates[any_pair['before']]} → {dates[any_pair['after']]}) tem só "
                      f"{100 * any_pair['common_valid']:.0f}% do retângulo válido nas duas datas"
                      if any_pair else "não há duas datas com área válida")
            raise SentinelError(f"Sem par válido em {analysis.control_year}: {detail} "
                                f"(mínimo {100 * min_valid:.0f}%, {len(cands)} cenas avaliadas).")
        b, a = cands[pair["before"]], cands[pair["after"]]
        # as datas da análise passam a ser as do par escolhido
        analysis.before_start = analysis.before_end = dates[pair["before"]].isoformat()
        analysis.after_start = analysis.after_end = dates[pair["after"]].isoformat()
        notes.append(f"Controle {analysis.control_year}: par {analysis.before_start} → {analysis.after_start} "
                     f"({pair['gap_days']} dias; alvo {w['target_gap_days']}) escolhido entre {len(cands)} cenas; "
                     f"{100 * pair['common_valid']:.0f}% do retângulo válido nas duas.")
        step(30, f"Baixando o par {analysis.before_start} → {analysis.after_start}…")
        return {"antes": [self.sentinel.load_candidate(b, grid)], "depois": [self.sentinel.load_candidate(a, grid)]}

    @staticmethod
    def _correct_topography(scene, slope, aspect) -> None:
        """C-correction de uma cena com o ângulo solar dela (view:sun_azimuth / view:sun_elevation)."""
        if scene.sun_azimuth is None or scene.sun_elevation is None:
            raise ValueError(f"a cena {scene.item_id} não informa o ângulo do sol")
        cos_i = illumination(slope, aspect, scene.sun_azimuth, scene.sun_elevation)
        fit = scene.clear_mask
        if scene.water_mask is not None:
            fit = fit & ~scene.water_mask
        scene.bands, coeffs = c_correction(scene.bands, cos_i, scene.sun_elevation, fit, slope)
        scene.topo = {"c": coeffs}

    def start(self, analysis: Analysis) -> None:
        self.repository.save(analysis)
        threading.Thread(target=self.run, args=(analysis,), daemon=True,
                         name=f"analysis-{analysis.id}").start()

    def run(self, analysis: Analysis) -> None:
        started = time.time()
        out_dir = self.repository.directory(analysis.id)

        def step(progress: int, message: str) -> None:
            analysis.status, analysis.progress, analysis.message = "processing", progress, message
            self.repository.save(analysis)
            log.info("[%s] %d%% %s", analysis.id, progress, message)

        try:
            bbox = tuple(analysis.bbox)
            grid = Grid.from_bbox(bbox, self.target_resolution, self.max_dim)
            step(3, f"Grade de saída: {grid.width}×{grid.height} px (~{grid.ground_resolution:.0f} m/px)")

            # ---- busca e escolha das cenas; carrega todas antes de combinar
            loaded, reference, notes = {}, None, []
            periods = (("antes", analysis.before_start, analysis.before_end, 5, "latest"),
                       ("depois", analysis.after_start, analysis.after_end, 30, "earliest"))
            if analysis.pair_window:
                # controle de vários anos: o par de cenas é escolhido dentro de uma janela
                loaded = self._load_control_pair(analysis, grid, step, notes)
                periods = ()
            for label, start, end, base, prefer in periods:
                step(base, f"Buscando cenas Sentinel-2 ({label}) de {start} a {end}…")
                # A cena "antes" é escolhida primeiro; a "depois" é buscada no MESMO tile
                # (e de preferência na mesma órbita) da cena "antes" escolhida (reference).
                items = self.sentinel.search(bbox, start, end, analysis.max_cloud, prefer=prefer,
                                             reference=reference, tile=analysis.preferred_tile)
                if not items:
                    raise SentinelError(
                        f"Nenhuma cena Sentinel-2 encontrada no período '{label}' ({start} a {end}) "
                        f"com menos de {analysis.max_cloud}% de nuvens."
                    )
                # Até 5 candidatas, ordenadas pela % de área limpa dentro do retângulo (SCL)
                ranked = self.sentinel.rank_candidates(
                    items, grid, progress=lambda m, b=base, lb=label: step(b + 5, f"({lb}) {m}"))
                best = ranked[0]
                # Composição por mediana: até 3 cenas do MESMO tile da melhor
                if analysis.median_composite:
                    chosen = [c for c in ranked if tile_of(c.item) == tile_of(best.item)][:MAX_COMPOSITE]
                else:
                    chosen = [best]
                loaded[label] = []
                for j, cand in enumerate(chosen, start=1):
                    step(base + 10 + 4 * j, f"Baixando bandas ({label}) {j}/{len(chosen)}: {cand.item.id} "
                                            f"({cand.clear_fraction:.0%} do retângulo sem nuvem)")
                    loaded[label].append(self.sentinel.load_candidate(cand, grid))
                reference = best.item
            all_scenes = loaded["antes"] + loaded["depois"]

            # ---- rocha clara × nuvem: "nuvem" no mesmo pixel nas duas datas com B11 > 0,3
            fixed_bright = bright_fixed_surface(loaded["antes"], loaded["depois"])
            if fixed_bright.any():
                apply_fixed_bright(all_scenes, fixed_bright)

            # ---- correção topográfica em CADA cena (cada uma com o seu sol), antes da mediana
            slope = None
            topo_info = {"requested": bool(analysis.topo_correction), "applied": False}
            if analysis.topo_correction:
                step(56, "Baixando o Copernicus DEM e corrigindo o relevo (C-correction)…")
                try:
                    # confere o sol de todas as cenas ANTES de mexer em alguma (evita corrigir só metade)
                    if any(s.sun_azimuth is None or s.sun_elevation is None for s in all_scenes):
                        raise ValueError("alguma cena não informa o ângulo do sol (view:sun_*)")
                    dem = self.terrain.dem_for_grid(grid)
                    slope, aspect = slope_aspect(dem, grid.ground_resolution)
                    for scene in all_scenes:
                        self._correct_topography(scene, slope, aspect)
                    n_fixed = sum(1 for s in all_scenes for v in s.topo["c"].values() if v["c"] is not None)
                    n_total = sum(len(s.topo["c"]) for s in all_scenes)
                    topo_info.update(applied=True, dem=self.terrain.collection, bands_corrected=n_fixed,
                                     bands_total=n_total,
                                     slope_mean_deg=round(float(np.degrees(np.nanmean(slope))), 2))
                    if n_fixed == 0:
                        notes.append("Correção topográfica: nenhuma banda tinha relação clara entre brilho e "
                                     "iluminação (r < 0,2) — o L2A já sai do Sen2Cor com correção de terreno.")
                except (TerrainUnavailable, ValueError) as exc:
                    notes.append(f"Correção topográfica não aplicada: {exc}")
                    topo_info["error"] = str(exc)
                    slope = None

            if len(loaded["antes"]) > 1 or len(loaded["depois"]) > 1:
                step(62, "Calculando a mediana das cenas…")
            before, after = median_composite(loaded["antes"]), median_composite(loaded["depois"])
            same_tile = before.tile == after.tile
            same_orbit = (before.relative_orbit is not None
                          and before.relative_orbit == after.relative_orbit)
            valid = before.clear_mask & after.clear_mask

            # ---- WorldCover: área construída (classe 50)
            step(66, "Lendo o ESA WorldCover (área construída)…")
            urban = None
            worldcover = None
            try:
                worldcover = self.landcover_reader(grid)
                if worldcover is not None:
                    urban = worldcover == BUILT_UP
                else:
                    notes.append("ESA WorldCover sem dados na área: solo exposto não foi separado em urbano/fora.")
            except Exception as exc:  # noqa: BLE001 — sem WorldCover a análise segue
                log.warning("WorldCover indisponível: %s", exc)
                notes.append(f"ESA WorldCover indisponível ({exc}): solo exposto não foi separado em urbano/fora.")

            step(75, "Executando Change Vector Analysis e classificação…")
            params = ChangeParams(threshold_method=analysis.threshold_method, k=analysis.k,
                                  min_region_px=analysis.min_region_px)
            # área construída da classificação: WorldCover 50 OU a até 10 m de um prédio do OSM (buildings.py).
            # A máscara "urban" (só WorldCover) continua nas estatísticas e em bands.npz.
            osm_rows = osm_cols = osm_mask = None
            if self.osm_reader is not None:
                try:
                    polys = self.osm_reader(grid)
                    osm_rows, osm_cols = osm_pixels(polys, grid.transform, valid.shape)
                    osm_mask = osm_footprint_mask(polys, grid.transform, valid.shape, grid.ground_resolution)
                except Exception as exc:  # noqa: BLE001 — sem OSM, fica só o WorldCover
                    log.warning("Prédios do OSM indisponíveis: %s", exc)
                    notes.append("Prédios do OpenStreetMap indisponíveis: a área construída veio só do WorldCover.")
            class_urban = urban
            if osm_mask is not None:
                class_urban = osm_mask if urban is None else (urban | osm_mask)
            result = detect_changes(before.bands, after.bands, valid, params, urban=class_urban)
            stats = compute_statistics(result, grid.pixel_area_m2)

            # Estatísticas de NDVI só em terra (fora: água pela SCL=6 ou NDWI>0)
            land = land_mask(valid, result.indices_before["ndwi"], result.indices_after["ndwi"],
                             before.water_mask, after.water_mask)
            ndvi_b, ndvi_a = result.indices_before["ndvi"][land], result.indices_after["ndvi"][land]
            median_dndvi = _nanmedian(result.dndvi[land])
            stats["dndvi_mean"] = _nanmean(result.dndvi[land])
            stats["dndvi_median"] = median_dndvi
            stats["land_fraction_of_valid"] = round(float(land.sum() / max(valid.sum(), 1)), 4)
            stats["valid_fraction"] = round(float(valid.mean()), 4)
            stats["bright_fixed_km2"] = round(float(fixed_bright.sum()) * grid.pixel_area_m2 / 1e6, 4)
            if slope is not None:
                stats["steep"] = steep_share(result.changed, slope, valid)
            if urban is not None:
                stats["urban"] = urban_stats(result.changed, urban, valid, grid.pixel_area_m2)

            # Avisos de qualidade
            warnings = quality_warnings(
                valid_fraction=float(valid.mean()), before=before.metadata(), after=after.metadata(),
                median_dndvi=median_dndvi, after_start=analysis.after_start, after_end=analysis.after_end)

            step(88, "Gerando camadas do mapa e GeoTIFFs…")
            stretch = renderer.rgb_stretch([(before.bands, before.data_mask), (after.bands, after.data_mask)])
            renderer.save_true_color(before.bands, before.data_mask, stretch, os.path.join(out_dir, "before.png"))
            renderer.save_true_color(after.bands, after.data_mask, stretch, os.path.join(out_dir, "after.png"))
            renderer.save_change_map(result.classes, os.path.join(out_dir, "change.png"))
            renderer.save_mask_hatch(~valid, os.path.join(out_dir, "mask.png"))
            dndvi_scale = renderer.save_dndvi(result.dndvi, os.path.join(out_dir, "dndvi.png"))
            mag_scale = renderer.save_magnitude(result.magnitude, os.path.join(out_dir, "magnitude.png"),
                                                result.threshold)
            renderer.save_geotiffs(result, grid, out_dir)
            # bandas (já corrigidas) guardadas em float16 para validar/comparar métodos sem baixar de novo
            extra = {}
            if urban is not None:
                extra["urban"] = urban
            if slope is not None:
                extra["slope_deg"] = np.degrees(slope).astype(np.float16)
            # água (SCL 6 em qualquer data ou WorldCover 80): fica fora do ranking de prioridade e da grade
            water = water_mask([before.water_mask, after.water_mask], worldcover, valid.shape)
            save_water(out_dir, water, [n for n, m in (("SCL antes", before.water_mask), ("SCL depois", after.water_mask),
                                                        ("WorldCover 2021", worldcover)) if m is not None])
            if urban is not None or osm_rows is not None:   # construções: prioridade e classe de dano
                save_buildings(out_dir, built=urban, osm_rows=osm_rows, osm_cols=osm_cols, osm_mask=osm_mask)
            np.savez_compressed(
                os.path.join(out_dir, "bands.npz"),
                valid=valid,
                **extra,
                **{f"before_{b}": v.astype(np.float16) for b, v in before.bands.items()},
                **{f"after_{b}": v.astype(np.float16) for b, v in after.bands.items()},
            )

            analysis.result = _clean({
                "bounds": grid.leaflet_bounds,
                "grid": {"width": grid.width, "height": grid.height, "crs": grid.crs,
                         "resolution_m": round(grid.ground_resolution, 2),
                         "ground_resolution_m": grid.ground_resolution,
                         "transform": list(grid.transform)[:6]},
                "before": before.metadata(),
                "after": after.metadata(),
                "same_tile": same_tile,
                "same_orbit": same_orbit,
                "warnings": warnings,
                "notes": notes,
                "topo": topo_info,
                "worldcover": urban is not None,
                "statistics": stats,
                "scales": renderer.legend_scales(dndvi_scale, mag_scale),
                "legend": [{"id": c, "name": i["name"], "color": "rgb({},{},{})".format(*i["color"][:3])}
                           for c, i in CLASSES.items() if c != 0 and (class_urban is not None or c != 5)],
                "elapsed_s": round(time.time() - started, 1),
                # NDVI: pixels válidos (sem nuvem nas duas datas) e sem água
                "ndvi_stats_mask": "válidos, sem água",
                "mean_ndvi_before": _nanmean(ndvi_b),
                "mean_ndvi_after": _nanmean(ndvi_a),
                "median_ndvi_before": _nanmedian(ndvi_b),
                "median_ndvi_after": _nanmedian(ndvi_a),
                "median_composite": bool(analysis.median_composite),
            })
            analysis.status, analysis.progress, analysis.message = "done", 100, "Concluído"
        except Exception as exc:  # noqa: BLE001 — qualquer falha vira mensagem para o usuário
            log.exception("Falha na análise %s", analysis.id)
            analysis.status = "error"
            analysis.error = str(exc) if isinstance(exc, (SentinelError, ValueError)) else f"{type(exc).__name__}: {exc}"
        finally:
            analysis.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.repository.save(analysis)


def rerender_layers(repository: AnalysisRepository, analysis: Analysis) -> bool:
    """Regera dndvi.png/magnitude.png e as escalas a partir dos GeoTIFFs (sem baixar imagens)."""
    if analysis.status != "done" or not analysis.result:
        return False
    out_dir = repository.directory(analysis.id)
    paths = {n: os.path.join(out_dir, n) for n in ("dndvi.tif", "magnitude.tif", "change_classes.tif")}
    if not all(os.path.exists(p) for p in paths.values()):
        return False
    with rasterio.open(paths["dndvi.tif"]) as src:
        dndvi = src.read(1)
    with rasterio.open(paths["magnitude.tif"]) as src:
        magnitude = src.read(1)
    with rasterio.open(paths["change_classes.tif"]) as src:
        classes = src.read(1)
    renderer.save_change_map(classes, os.path.join(out_dir, "change.png"))
    renderer.save_mask_hatch(classes == NODATA, os.path.join(out_dir, "mask.png"))
    threshold = float(analysis.result["statistics"]["cva_threshold"])
    dndvi_scale = renderer.save_dndvi(dndvi, os.path.join(out_dir, "dndvi.png"))
    mag_scale = renderer.save_magnitude(magnitude, os.path.join(out_dir, "magnitude.png"), threshold)
    analysis.result["scales"] = _clean(renderer.legend_scales(dndvi_scale, mag_scale))
    repository.save(analysis)
    return True


def _nanmean(values: np.ndarray):
    return round(float(np.nanmean(values)), 4) if values.size else None


def _nanmedian(values: np.ndarray):
    return round(float(np.nanmedian(values)), 4) if values.size else None


def _clean(value):
    """Troca NaN/inf por None para o JSON ser válido no navegador."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value
