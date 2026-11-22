"""Regera as camadas visuais (ΔNDVI e magnitude) das análises já concluídas.

Útil depois de mudar o estilo das camadas: não baixa imagens, usa os GeoTIFFs salvos.
Uso:  python atualizar_camadas.py
"""
from app.models.analysis import AnalysisRepository
from app.models.processing import rerender_layers
from config import Config

if __name__ == "__main__":
    repo = AnalysisRepository(Config.RESULTS_DIR)
    for analysis in repo.list():
        ok = rerender_layers(repo, analysis)
        print(f"{'atualizada' if ok else 'ignorada '}  {analysis.id}  {analysis.name}")
