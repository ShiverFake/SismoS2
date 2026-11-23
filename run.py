"""Ponto de entrada: python run.py -> http://<ip-da-vm>:5000"""
import logging
import os

from app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("rasterio").setLevel(logging.WARNING)

app = create_app()

if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
        use_reloader=False,
    )
