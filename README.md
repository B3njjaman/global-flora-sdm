# global-flora-sdm

**Modelos de distribución de especies (SDM) para flora — alcance global, enfoque ensemble, con proyección a 2050 (CMIP6).**

Pipeline reproducible en stack Python para modelar la distribución potencial de 21 especies de flora con registros en GBIF, combinando múltiples algoritmos (GLM, GAM, RF, GBM, MaxEnt) y proyectando bajo escenarios de cambio climático.

---

## Objetivo

Modelar la distribución potencial de 21 especies de flora a **escala global** (resolución 2.5 arc-min, ~5 km), mediante un **ensemble** de cinco algoritmos, y **proyectar a 2050** bajo múltiples GCMs y SSPs de CMIP6. El diseño es incremental: la iteración 1 usa variables bioclimáticas + topografía; el roadmap contempla hidrogeología, índices temporales (NDVI/EVI) y deep learning.

La especificación metodológica completa está en [`docs/proyecto_sdm.md`](docs/proyecto_sdm.md).

## Datos

- **Ocurrencias:** GBIF — 13.354 registros, 21 especies (`data/raw/gbif_distribucion_especies.xlsx`).
- **Predictoras (presente):** WorldClim v2.1 bioclim (10 capas) + elevación + topografía derivada.
- **Predictoras (futuro):** WorldClim Future / CMIP6 — ≥4 GCMs × ≥2 SSPs, período 2041–2060.

> Las capas raster (WorldClim/CMIP6) **no se versionan** en el repositorio por tamaño; se descargan ejecutando `scripts/02_capas_presente.py`. Ver `.gitignore`.

## Pipeline

| Etapa | Script | Salida |
|---|---|---|
| 1. Limpieza de ocurrencias | `scripts/01_limpieza.py` | `data/processed/ocurrencias_limpias.gpkg` |
| 2. Capas presente (WorldClim) | `scripts/02_capas_presente.py` | `data/raw/worldclim_present/` |
| 3. Terreno (slope/aspect/northness) | `scripts/03_terrain.py` | `data/processed/rasters_aligned/` |
| 4. Dataset modelable | `scripts/04_extraccion.py` | `data/processed/species_datasets/*.parquet` |
| 5. Ensemble (5 algoritmos) | `scripts/05_modelado.py` | `data/modeling/ensemble_models/` |
| 6. Validación y métricas | `scripts/06_validacion.py` | `outputs/tables/` |
| 7. Idoneidad presente | `scripts/07b_present_suitability.py` | `outputs/maps/*_present_suitability.tif` |
| 7b. Proyección a 2050 *(diferida)* | `scripts/07_forecast_2050.py` | `outputs/maps/_forecast_deferred/` |
| 8. Mapas y figuras | `scripts/08_mapas.py` | `outputs/figures/`, `outputs/maps/` |

Las etapas son **secuenciales**: cada una consume las salidas de las anteriores.

> **Estado de la iteración 1.** Se entregan: ocurrencias limpias, capas alineadas,
> **14 modelos ensemble**, **validación completa** (TSS/AUC/Boyce/Brier/MESS, CV espacial)
> e **idoneidad del presente** (mapas GeoTIFF + figuras). El **forecast a 2050** está
> implementado y validado a nivel de datos (las 8 capas CMIP6 GCM×SSP se descargan y
> proyectan), pero **se difiere como mejora**: el cálculo de MESS a escala global
> (~37 millones de píxeles) es el cuello de botella —incluso vectorizado con búsqueda
> binaria— y requiere optimización adicional (procesamiento por bloques / submuestreo de
> referencia). Las proyecciones futuras parciales ya calculadas quedan en
> `outputs/maps/_forecast_deferred/`. Ver §Roadmap.

## Instalación

```bash
# con uv (recomendado)
uv venv && uv pip install -e .

# o con pip
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -e .
```

> Nota Windows: `cartopy` y `elapid` pueden requerir wheels precompilados o conda. Ver `docs/`.

## Uso

```bash
python scripts/01_limpieza.py
python scripts/02_capas_presente.py
# ... etapas 3–8 en orden
```

## Estructura

```
global-flora-sdm/
├── data/{raw,processed,modeling}/   # datos (capas pesadas gitignored)
├── scripts/                         # pipeline 01–08
├── outputs/{figures,maps,tables}/   # resultados
├── docs/proyecto_sdm.md             # especificación metodológica
├── pyproject.toml
└── README.md
```

## Metodología y validación

Sigue el protocolo ODMAP (Zurell et al. 2020). Validación con CV espacial (block CV 500–1000 km), métricas solo-presencia (Boyce/CBI), MESS para extrapolación, e hindcasting para validar el forecast. Detalle completo en [`docs/proyecto_sdm.md`](docs/proyecto_sdm.md).

## Licencia

MIT.
