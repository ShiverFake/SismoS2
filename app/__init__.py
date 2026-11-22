"""Fábrica da aplicação Flask (padrão MVC).

- app/models      -> regras de negócio e acesso a dados (Sentinel-2, algoritmos, persistência)
- app/views       -> templates HTML (Jinja2) + estáticos (CSS/JS com Leaflet)
- app/controllers -> rotas Flask que ligam requisições aos models e escolhem a view
"""
import os

from flask import Flask

from config import Config


def create_app(config_class=Config) -> Flask:
    app = Flask(
        __name__,
        template_folder="views/templates",
        static_folder="views/static",
    )
    app.config.from_object(config_class)
    os.makedirs(app.config["RESULTS_DIR"], exist_ok=True)

    os.makedirs(app.config["REFERENCE_DIR"], exist_ok=True)

    from app.models.analysis import AnalysisRepository
    if not app.config.get("VITRINE"):   # o build da vitrine só lê: não mexe em análises que o servidor está rodando
        AnalysisRepository(app.config["RESULTS_DIR"]).mark_interrupted()

    from app.controllers.main_controller import main_bp
    from app.controllers.analysis_controller import api_bp
    from app.controllers.validation_controller import validation_bp
    from app.controllers.control_controller import control_bp
    from app.controllers.events_controller import events_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(validation_bp, url_prefix="/api")
    app.register_blueprint(control_bp, url_prefix="/api")
    app.register_blueprint(events_bp)   # /eventos (página) e /api/eventos/... (JSON)
    return app
