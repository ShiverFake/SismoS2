# SismoS2 — Detecção de mudanças pós-terremoto com Sentinel-2

Aplicação web em **Python (Flask)**, organizada no padrão **MVC**, que compara imagens ópticas
Sentinel-2 de antes e depois de um terremoto e mostra as áreas alteradas num mapa **Leaflet**.

## Estrutura (MVC)

```
deteccao-terremoto-s2/
├── run.py                      # ponto de entrada
├── config.py                   # configurações
├── requirements.txt
├── setup_vm.sh                 # instalação automática na VM (Ubuntu/Debian)
├── app/
│   ├── __init__.py             # fábrica da aplicação Flask
│   ├── models/                 # M — dados e regras de negócio
│   │   ├── earthquake.py       #   catálogo de terremotos de exemplo
│   │   ├── sentinel.py         #   busca STAC + leitura das bandas (COG/AWS)
│   │   ├── change_detection.py #   algoritmos: normalização, CVA, limiar, classificação
│   │   ├── renderer.py         #   geração das camadas PNG e GeoTIFF
│   │   ├── analysis.py         #   entidade Análise + persistência em JSON
│   │   ├── reference.py        #   referência: Copernicus EMS, arquivo enviado, prédios do OSM
│   │   ├── validation.py       #   validação por edificação, métricas e comparação de métodos
│   │   └── processing.py       #   serviço que orquestra o processamento em segundo plano
│   ├── views/                  # V — apresentação
│   │   ├── templates/          #   base, index, analysis, methodology (Jinja2)
│   │   └── static/             #   css/style.css, js/ (Leaflet)
│   └── controllers/            # C — rotas
│       ├── main_controller.py  #   páginas HTML
│       ├── analysis_controller.py  # API REST JSON
│       └── validation_controller.py # API da validação
├── data/results/<id>/          # saídas de cada análise
└── tests/                      # testes com dados sintéticos (pytest)
```

## Instalação na VM

```bash
# no Windows (PowerShell), envia o projeto para a VM:
scp -r C:\Users\SEU-USUARIO\deteccao-terremoto-s2 usuario@IP-DA-VM:~/

# na VM:
ssh usuario@IP-DA-VM
cd ~/deteccao-terremoto-s2
bash setup_vm.sh          # instala python3-venv, dependências e roda os testes
. .venv/bin/activate
python run.py
```

Acesse pelo navegador do Windows: **http://IP-DA-VM:5000**

> A VM precisa de acesso à internet (catálogo STAC e imagens na AWS; mapas base do Leaflet).

## Como usar

1. Escolha um **terremoto de exemplo** (Turquia 2023, Marrocos 2023, Myanmar 2025, Haiti 2021,
   Japão 2024) ou desenhe um retângulo no mapa e informe as datas.
2. Clique em **Processar imagens**. A página de resultado mostra o progresso.
3. Ao concluir: alterne entre as camadas *Antes*, *Depois*, *Mapa de mudanças*, *ΔNDVI* e
   *Magnitude CVA*, ajuste a opacidade, use **Comparar lado a lado** e baixe os GeoTIFFs.

## Metodologia (resumo)

1. Busca de cenas Sentinel-2 L2A no catálogo STAC Earth Search; escolha da cena com mais
   pixels sem nuvem *dentro da área* (banda SCL).
2. Reprojeção das duas datas para a mesma grade (EPSG:3857, ~10 m) e conversão para reflectância.
3. Normalização radiométrica relativa (média/desvio por banda).
4. **Change Vector Analysis** em B02, B03, B04, B08, B11, B12 com limiar μ + k·σ ou Otsu.
5. Remoção de regiões pequenas e classificação por regras com ΔNDVI, ΔNDWI, ΔBSI e ΔNDBI:
   perda de vegetação (deslizamentos), solo exposto/entulho (danos), água, outras.

Detalhes e limitações completos na página **/metodologia** da aplicação.

## Validação com dados oficiais

Na página de resultado, a seção **Validação com dados oficiais** compara o mapa de mudanças com
os mapas de dano do **Copernicus EMS Rapid Mapping** (download automático pelo código da ativação
e da AOI) ou com um arquivo enviado (GeoJSON ou .zip no formato EMS/UNOSAT).

- Unidade de avaliação: **edificação**. Os prédios vêm do OpenStreetMap; os pontos do EMS
  (Destruído/Danificado/Possivelmente danificado) são ligados ao prédio mais próximo (≤ 10 m).
- Métricas: matriz de confusão, precisão, recall, F1, exatidão global e **kappa**, detecção por grau
  de dano e linha de base "tudo é dano".
- **Comparação de métodos**: μ + kσ (k = 1 … 3) e Otsu × "todas as mudanças" / "só entulho",
  exportável em CSV.

Exemplos com referência oficial já configurada:

| Exemplo | Referência | Melhor F1 | κ |
|---|---|---|---|
| Marrocos 2023 – Talat N'Yaaqoub | EMSR695 AOI 07 | 0,52 (μ + 1σ) | 0,30 |
| Myanmar 2025 – Kyaukse | EMSR798 AOI 07 | ≈ 0,09 | ≈ 0,05 |

A ativação da Turquia (EMSR648) não está disponível na API pública do EMS. Para Antakya, baixe
uma referência (EMS ou UNOSAT) manualmente e use a opção **Arquivo**.

## API

| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/terremotos` | eventos de exemplo |
| GET | `/api/analises` | lista análises |
| POST | `/api/analises` | cria análise (JSON: `bbox`, `before_start`, `before_end`, `after_start`, `after_end`, …) |
| GET | `/api/analises/<id>` | status e resultado |
| DELETE | `/api/analises/<id>` | exclui |
| GET | `/api/analises/<id>/arquivos/<nome>` | PNG/GeoTIFF/CSV/GeoJSON gerados |
| POST | `/api/analises/<id>/validacao` | inicia validação (JSON `source=ems, activation, aoi` ou formulário com `file`) |
| GET | `/api/analises/<id>/validacao` | status e resultado da validação |

## Testes

```bash
python -m pytest -q
```
