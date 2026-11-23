# SismoS2

Sistema web que aponta, nas semanas seguintes a um terremoto, quais quarteirões precisam de vistoria
primeiro, usando apenas imagens de satélite gratuitas. Ele compara imagens Sentinel-2 de antes e depois do
evento, mede a mudança espectral de cada pixel de 10 m e ordena a área em células de 200 m, da que mais mudou
para a que menos mudou. O resultado é validado contra mapas oficiais de dano e comparado com anos sem terremoto
no mesmo lugar.

**Versão de demonstração (somente leitura):** <https://sismo-s2.vercel.app>

---

## Funcionalidades

- **Nova análise:** assistente em 4 etapas (área, datas, opções e revisão). A área pode ser desenhada no mapa ou
  escolhida entre exemplos com mapa oficial de dano. Há par de aniversário (o "antes" um ano antes do "depois") e
  aviso quando a data ainda não tem imagem.
- **Processamento:** busca das cenas com menos nuvem e névoa no retângulo, no mesmo tile e órbita; máscara de
  nuvem (SCL), correção de relevo (C-correction com o Copernicus DEM), composição por mediana opcional, Change
  Vector Analysis em seis bandas e classificação da mudança por índices espectrais.
- **Prioridade de vistoria:** ranking das células de 200 m pela % de pixels com mudança, só entre células com
  construções (prédio do OpenStreetMap ou área construída no ESA WorldCover) e sem água.
- **Resultado:** mapa com camadas, modo lado a lado, leitura do valor de cada pixel, avisos de qualidade e
  downloads (GeoTIFF, GeoJSON, CSV e JSON).
- **Controles:** a mesma análise repetida em até três anos sem terremoto, na mesma época do ano, para medir o
  ruído de fundo do lugar.
- **Validação:** comparação com o Copernicus EMS (download automático) ou com um arquivo de referência, por
  prédio e por quadrícula de 50, 100 e 200 m, com AUC, intervalos de confiança por bootstrap, teste de DeLong
  (evento × controles), correção de Holm, curva de triagem e análise de sensibilidade.
- **Eventos:** terremotos recentes do USGS e ativações do Copernicus EMS num mapa, com atalho para criar a análise.
- **Vitrine estática:** gera uma versão somente leitura do site, sem servidor Python, com as análises destacadas.

## Resultados

AUC das células de 200 m: a chance de uma célula danificada ter mais mudança do que uma não danificada
(0,5 = acaso). Evento e anos de controle são avaliados nas mesmas células e com a mesma referência.

| Estudo de caso | Referência | AUC do evento (IC 95%) | AUC média dos controles | DeLong (p) | 10% das células visitadas alcançam |
|---|---|---|---|---|---|
| Antakya, Turquia (2023) | prédios destruídos mapeados pelo HOT no OpenStreetMap | **0,83** (0,75 a 0,90) | 0,59 (3 anos) | 0,002 | 30% dos prédios com dano |
| Talat N'Yaaqoub, Marrocos (2023) | Copernicus EMS EMSR695, AOI 07 | **0,71** (0,62 a 0,80) | 0,56 (3 anos) | 0,014 | 38% dos prédios com dano |

Nos dois casos a mudança separa as células danificadas melhor no ano do terremoto do que em anos normais, e
visitar as 10% células do topo do ranking alcança de três a quatro vezes mais prédios danificados do que
escolher ao acaso.

## Tecnologias

| Camada | Ferramentas |
|---|---|
| Servidor | Python 3, Flask (padrão MVC), Jinja2 |
| Processamento | NumPy, SciPy, Rasterio (GDAL), Affine, pystac-client, Shapely, Matplotlib, Pillow |
| Interface | HTML, CSS próprio, JavaScript puro, Leaflet e Leaflet.draw, fonte Inter |
| Testes e qualidade | pytest, ruff, vulture |

## Fontes de dados e atribuições

| Dado | Uso | Atribuição |
|---|---|---|
| Sentinel-2 L2A, via Earth Search (Element 84, AWS Open Data) | imagens antes e depois | Contém dados modificados do Copernicus Sentinel |
| Copernicus DEM GLO-30 | correção de relevo | © DLR e.V. 2010-2014 e © Airbus Defence and Space GmbH 2014-2018, fornecido no âmbito do COPERNICUS pela União Europeia e pela ESA |
| ESA WorldCover 2021 | área construída e água | © ESA WorldCover project 2021, contém dados modificados do Copernicus Sentinel (2021) processados pelo consórcio ESA WorldCover (CC BY 4.0) |
| Copernicus EMS Rapid Mapping | mapas oficiais de dano e lista de ativações | © União Europeia, Copernicus Emergency Management Service |
| USGS Earthquake Hazards Program | terremotos recentes e históricos | U.S. Geological Survey (domínio público) |
| OpenStreetMap e Humanitarian OpenStreetMap Team (HOT) | prédios e referência de Antakya | © colaboradores do OpenStreetMap (ODbL) |
| Mapas de fundo | visualização | Esri World Imagery (Esri, Maxar, Earthstar Geographics) e © colaboradores do OpenStreetMap |

## Estrutura do projeto (MVC)

```
sismos2/
├── run.py                  ponto de entrada do servidor Flask
├── config.py               configurações (todas podem vir de variáveis de ambiente)
├── requirements.txt        dependências do sistema
├── requirements-dev.txt    dependências de desenvolvimento (testes e análise de código)
├── setup_vm.sh             instalação no Ubuntu/Debian
├── servidor.sh             inicia, para e mostra o log do servidor em segundo plano
├── vercel.json             cabeçalhos de segurança e cache da vitrine estática
├── app/
│   ├── __init__.py         fábrica da aplicação
│   ├── models/             M: dados, algoritmos e acesso às fontes externas
│   │   ├── analysis.py         entidade Análise e persistência em JSON
│   │   ├── processing.py       serviço que orquestra busca, download, detecção e camadas
│   │   ├── sentinel.py         busca STAC e leitura das cenas Sentinel-2
│   │   ├── terrain.py          Copernicus DEM e correção topográfica
│   │   ├── landcover.py        ESA WorldCover
│   │   ├── change_detection.py CVA, limiar, índices espectrais e classes de mudança
│   │   ├── quality.py          máscara de terra e avisos de qualidade
│   │   ├── water.py            máscara de água
│   │   ├── buildings.py        onde há construções (WorldCover e OpenStreetMap)
│   │   ├── priority.py         ranking de prioridade de vistoria
│   │   ├── renderer.py         camadas PNG, miniaturas e GeoTIFFs
│   │   ├── plot_style.py       estilo dos gráficos
│   │   ├── control.py          anos de controle
│   │   ├── reference.py        referências de dano (EMS, arquivo, prédios do OSM)
│   │   ├── validation.py       validação por prédio
│   │   ├── validation_stats.py bootstrap e efeito de vizinhança
│   │   ├── grid_eval.py        avaliação por quadrícula
│   │   ├── grid_controls.py    evento × controles, DeLong e Holm
│   │   ├── ems.py, usgs.py     Copernicus EMS e USGS
│   │   ├── earthquake.py       catálogo de exemplos
│   │   ├── planning.py         parâmetros de uma análise a partir de um evento
│   │   ├── pixel_probe.py      valores de um pixel
│   │   └── http_cache.py       GET HTTP com cache em disco
│   ├── views/              V: apresentação
│   │   ├── templates/          páginas Jinja2 e componentes (components/ui.html)
│   │   └── static/             css/ (tokens, layout, widgets) e js/ (uma lógica por página)
│   └── controllers/        C: rotas
│       ├── main_controller.py       páginas HTML
│       ├── analysis_controller.py   API das análises
│       ├── control_controller.py    API dos anos de controle
│       ├── validation_controller.py API da validação
│       └── events_controller.py     página e API de eventos
├── tools/
│   ├── build_vitrine.py    gera a vitrine estática em dist/
│   └── servir_dist.py      serve dist/ localmente com os mesmos cabeçalhos da publicação
├── tests/                  testes automáticos (pytest) e fixtures com respostas reais das APIs
└── data/                   criada no uso: resultados das análises, caches e referências
```

O **model** não conhece a web: recebe e devolve dados. O **controller** recebe a requisição, chama o model e
escolhe a resposta (página ou JSON). A **view** só apresenta: templates Jinja2 no servidor e JavaScript que lê a
API para o mapa, as abas e os gráficos.

## Instalação no Ubuntu

Requisitos: Ubuntu 22.04 ou mais novo (ou Debian), Python 3.10+, acesso à internet (as imagens e os dados são
lidos das fontes públicas na hora). Não é preciso conta nem chave de acesso.

Na pasta do projeto:

```bash
bash setup_vm.sh
```

O script instala o `python3-venv` se faltar, cria o ambiente `.venv`, instala as dependências e roda os testes.
Para fazer à mão:

```bash
sudo apt install -y python3-venv python3-pip
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Opcional: `sudo apt install -y fonts-inter` para os gráficos usarem a mesma fonte do site.

## Como rodar

```bash
bash servidor.sh start      # em segundo plano; também: stop, restart, status, log
# ou, no terminal:
.venv/bin/python run.py
```

Abra <http://localhost:5000>. Para acessar de outro computador da rede, use o IP da máquina no lugar de
`localhost` (a porta 5000 precisa estar liberada no firewall).

Uma análise leva cerca de um minuto. Os resultados ficam em `data/results/<id>/`.

Principais variáveis de ambiente (todas opcionais): `PORT`, `HOST`, `RESULTS_DIR`, `CACHE_DIR`,
`REFERENCE_DIR`, `MAX_BBOX_DEG` (lado máximo do retângulo, padrão 0,3°), `MAX_AOT` (névoa máxima aceita,
padrão 0,25) e `SECRET_KEY`.

## Testes

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/ruff check --select F app tools tests *.py
```

Os testes usam dados sintéticos e respostas gravadas das APIs (`tests/fixtures/`): não acessam a rede.

## Vitrine estática

A vitrine é uma cópia somente leitura do site com as análises marcadas com a estrela na página *Análises* (e os
anos de controle de cada uma). As páginas são geradas pelos mesmos templates, sem os botões que gravam no
servidor; os dados da API viram arquivos `.json` e os GeoTIFFs ficam de fora por causa do tamanho.

```bash
.venv/bin/python tools/build_vitrine.py                   # gera dist/
.venv/bin/python tools/build_vitrine.py --downloads-url URL  # GeoTIFFs num endereço externo
.venv/bin/python tools/servir_dist.py                      # http://localhost:8200
```

O script confere todos os links locais e informa o tamanho de `dist/`, que pode ser publicada em qualquer
hospedagem de arquivos estáticos.

## Limitações

- Resolução de 10 m: prédios isolados pequenos raramente aparecem; o sistema indica quarteirões, não prédios.
- Imagens ópticas dependem de céu limpo; a imagem "depois" pode estar dias ou semanas distante do evento.
- Lavouras, obras, estação do ano e ângulo do sol também mudam a imagem. Os anos de controle medem esse ruído,
  mas não o eliminam.
- O ranking é relativo dentro do retângulo e indica onde olhar primeiro; não é um diagnóstico de dano.
- Área construída: o ESA WorldCover marca só 0,51% da área do Marrocos como construída (casas de taipa parecem
  solo exposto), por isso a regra combina WorldCover e OpenStreetMap; onde os prédios não foram mapeados no OSM,
  eles continuam invisíveis.
- A validação depende da referência: mapas oficiais podem estar incompletos e o OSM pode estar desatualizado.

A explicação completa de cada etapa, das fórmulas e dos testes estatísticos está na página
[Metodologia](https://sismo-s2.vercel.app/metodologia/).
