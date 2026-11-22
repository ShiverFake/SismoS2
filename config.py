"""Configurações da aplicação (podem ser sobrescritas por variáveis de ambiente)."""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "trocar-em-producao")

    # Onde cada análise grava seus resultados (PNG, GeoTIFF e JSON)
    RESULTS_DIR = os.environ.get("RESULTS_DIR", os.path.join(BASE_DIR, "data", "results"))

    # Catálogo STAC público da Element84 (Sentinel-2 L2A em COG na AWS, sem autenticação)
    STAC_URL = os.environ.get("STAC_URL", "https://earth-search.aws.element84.com/v1")
    STAC_COLLECTION = os.environ.get("STAC_COLLECTION", "sentinel-2-l2a")

    # Copernicus EMS Rapid Mapping (público, sem chave). Ativações anteriores à EMSR656 não estão
    # na API pública (ex.: EMSR648 – Turquia 2023 responde 403).
    EMS_LIST_URL = os.environ.get(
        "EMS_LIST_URL", "https://rapidmapping.emergency.copernicus.eu/backend/dashboard-api/public-activations-info/")
    EMS_DETAIL_URL = os.environ.get(
        "EMS_DETAIL_URL", "https://rapidmapping.emergency.copernicus.eu/backend/dashboard-api/public-activations/")
    EMS_LIST_TTL_S = 3600            # lista e detalhes: 1 h de cache; produtos GRA: cache permanente

    # USGS (público, sem chave): feeds de tempo real e consulta histórica (FDSN) para períodos maiores
    USGS_FEEDS = {
        "week": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson",
        "month": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_month.geojson",
    }
    USGS_QUERY_URL = os.environ.get("USGS_QUERY_URL", "https://earthquake.usgs.gov/fdsnws/event/1/query")
    USGS_TTL_S = 900

    # Cache em disco das APIs externas
    CACHE_DIR = os.environ.get("CACHE_DIR", os.path.join(BASE_DIR, "data", "cache"))

    # Validação: cache de referências (EMS, OSM) e API Overpass do OpenStreetMap
    REFERENCE_DIR = os.environ.get("REFERENCE_DIR", os.path.join(BASE_DIR, "data", "references"))
    OVERPASS_URL = os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
    MAX_CONTENT_LENGTH = 60 * 1024 * 1024   # upload de referência até 60 MB

    # Névoa: descarta cenas com AOT (aerossol a 550 nm, asset "aot" do L2A) mediana > MAX_AOT nos pixels
    # limpos do retângulo. Medido em todas as cenas candidatas com ≥ 50% limpo (versão mais recente) de
    # 2020–2023: Antakya jan–mar (22 cenas; mediana 0,18; p90 0,29; intervalo natural entre 0,236 e 0,294)
    # e Atlas marroquino ago–out (50 cenas; mediana 0,14; p90 0,26). Com 0,25 saem ~14% das cenas nos dois
    # casos (Antakya: 0,29/0,32/0,35; Marrocos: 0,26–0,50, poeira), e as 4 cenas dos eventos (0,08–0,21) ficam.
    MAX_AOT = float(os.environ.get("MAX_AOT", "0.25"))

    # Controles: área válida mínima do retângulo nas DUAS datas do par (regra objetiva, sem exclusão manual).
    # Se um ano não tiver par com essa área, o motivo fica registrado e o sistema tenta o ano anterior,
    # até completar CONTROL_TARGET controles válidos ou chegar a CONTROL_MIN_YEAR.
    MIN_CONTROL_VALID = float(os.environ.get("MIN_CONTROL_VALID", "0.70"))
    CONTROL_TARGET = 3
    CONTROL_MIN_YEAR = 2017

    # Limites para manter o processamento leve dentro da VM
    MAX_BBOX_DEG = float(os.environ.get("MAX_BBOX_DEG", "0.3"))   # lado máximo da área (graus)
    MAX_DIM = int(os.environ.get("MAX_DIM", "1500"))              # pixels no maior lado da grade
    TARGET_RESOLUTION = 10.0                                      # metros (bandas de 10 m do S2)
