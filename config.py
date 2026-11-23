"""Configurações da aplicação (podem ser sobrescritas por variáveis de ambiente)."""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or os.urandom(32).hex()

    RESULTS_DIR = os.environ.get("RESULTS_DIR", os.path.join(BASE_DIR, "data", "results"))

    STAC_URL = os.environ.get("STAC_URL", "https://earth-search.aws.element84.com/v1")
    STAC_COLLECTION = os.environ.get("STAC_COLLECTION", "sentinel-2-l2a")

    EMS_LIST_URL = os.environ.get(
        "EMS_LIST_URL", "https://rapidmapping.emergency.copernicus.eu/backend/dashboard-api/public-activations-info/")
    EMS_DETAIL_URL = os.environ.get(
        "EMS_DETAIL_URL", "https://rapidmapping.emergency.copernicus.eu/backend/dashboard-api/public-activations/")
    EMS_LIST_TTL_S = 3600

    USGS_FEEDS = {
        "week": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson",
        "month": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_month.geojson",
    }
    USGS_QUERY_URL = os.environ.get("USGS_QUERY_URL", "https://earthquake.usgs.gov/fdsnws/event/1/query")
    USGS_TTL_S = 900

    CACHE_DIR = os.environ.get("CACHE_DIR", os.path.join(BASE_DIR, "data", "cache"))

    REFERENCE_DIR = os.environ.get("REFERENCE_DIR", os.path.join(BASE_DIR, "data", "references"))
    OVERPASS_URL = os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
    MAX_CONTENT_LENGTH = 60 * 1024 * 1024

    MAX_AOT = float(os.environ.get("MAX_AOT", "0.25"))

    MIN_CONTROL_VALID = float(os.environ.get("MIN_CONTROL_VALID", "0.70"))
    CONTROL_TARGET = 3
    CONTROL_MIN_YEAR = 2017

    MAX_BBOX_DEG = float(os.environ.get("MAX_BBOX_DEG", "0.3"))
    MAX_DIM = int(os.environ.get("MAX_DIM", "1500"))
    TARGET_RESOLUTION = 10.0
