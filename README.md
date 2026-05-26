# global-flora-sdm

**Modelos de distribución de especies (SDM) para flora endemica de Chile — alcance regional, enfoque ensemble, con proyeccion a 2050 (CMIP6). (Iteracion 3)**

Pipeline reproducible en stack Python para modelar la distribución potencial de 14 especies de flora endemica chilena con registros en GBIF, combinando multiples algoritmos (GLM, GAM, RF, GBM, MaxEnt) y proyectando bajo escenarios de cambio climatico. La calibracion se acota a **Chile continental**; la prediccion y los mapas se recortan a **Sudamerica**.

---

## Objetivo

Modelar la distribucion potencial de 14 especies de flora, principalmente endemicas de Chile, a **escala regional** (resolucion 2.5 arc-min, ~5 km), mediante un **ensemble** de cinco algoritmos, y **proyectar a 2050** bajo multiples GCMs y SSPs de CMIP6. La calibracion se realiza dentro de Chile porque las especies son endemicas chilenas: calibrar contra un background planetario infla artificialmente la discriminacion (el modelo aprende "Atacama vs. planeta", no "nicho dentro de Chile"). El diseno es incremental: la iteracion 1 uso variables bioclimaticas + topografia; la iteracion 2 depuro el CV espacial y el background; la iteracion 3 acota el marco geografico a Chile/Sudamerica.

La especificacion metodologica completa esta en [`docs/proyecto_sdm.md`](docs/proyecto_sdm.md).

## Datos

- **Ocurrencias:** GBIF — registros de 14 especies (`data/raw/gbif_distribucion_especies.xlsx`), filtrados a Chile continental para background y calibracion.
- **Predictoras (presente):** WorldClim v2.1 bioclim (10 capas) + elevacion + topografia derivada; recortadas al bbox de Chile para entrenamiento.
- **Predictoras (futuro):** WorldClim Future / CMIP6 — >=4 GCMs x >=2 SSPs, periodo 2041-2060; predicciones recortadas al bbox de Sudamerica.

> Las capas raster (WorldClim/CMIP6) **no se versionan** en el repositorio por tamano; se descargan ejecutando `scripts/02_capas_presente.py`. Ver `.gitignore`.

## Pipeline

| Etapa | Script | Salida |
|---|---|---|
| 1. Limpieza de ocurrencias | `scripts/01_limpieza.py` | `data/processed/ocurrencias_limpias.gpkg` |
| 2. Capas presente (WorldClim) | `scripts/02_capas_presente.py` | `data/raw/worldclim_present/` |
| 3. Terreno (slope/aspect/northness) | `scripts/03_terrain.py` | `data/processed/rasters_aligned/` |
| 4. Dataset modelable | `scripts/04_extraccion.py` | `data/processed/species_datasets/*.parquet` |
| 5. Ensemble (5 algoritmos) | `scripts/05_modelado.py` | `data/modeling/ensemble_models/` |
| 6. Validación y métricas | `scripts/06_validacion.py` | `outputs/tables/` |
| 7. Idoneidad presente | `scripts/07b_present_suitability.py` | `outputs/maps/*_present_suitability.tif` (recortado a Sudamerica) |
| 7b. Proyeccion a 2050 *(diferida)* | `scripts/07_forecast_2050.py` | `outputs/maps/_forecast_deferred/` (recortado a Sudamerica) |
| 8. Mapas y figuras | `scripts/08_mapas.py` | `outputs/figures/`, `outputs/maps/` |

Las etapas son **secuenciales**: cada una consume las salidas de las anteriores.

> **Estado de la iteracion 3.** Se entregan: ocurrencias limpias (filtradas a Chile),
> capas alineadas, **14 modelos ensemble calibrados regionalmente**, **validacion completa**
> (TSS/AUC/Boyce, CV espacial) e **idoneidad del presente** (mapas GeoTIFF recortados a
> Sudamerica + figuras). El **forecast a 2050** esta implementado y recortado a Sudamerica,
> pero **se difiere como mejora**: el calculo de MESS aun requiere optimizacion adicional.
> Las proyecciones futuras parciales quedan en `outputs/maps/_forecast_deferred/`. Ver
> Roadmap en [`docs/proyecto_sdm.md`](docs/proyecto_sdm.md).

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

## Resultados (iteracion 3 — calibracion regional Chile)

14 modelos ensemble entrenados y validados con **CV espacial adaptativo**, calibrados
dentro de Chile continental (ensemble equal-weight).

### Metricas medias (CV espacial, ensemble equal-weight)

| Iteracion | AUC | TSS | Boyce | Nota |
|---|---|---|---|---|
| 2 (global, superada) | 0.944 | 0.822 | 0.68 | Infladas: "Atacama vs. planeta" |
| **3 (regional Chile)** | **0.884** | **0.707** | **0.42** | **Reales: nicho dentro de Chile** |

**Las metricas bajaron a proposito.** No es una regresion: el inflado artificial
desaparecio. Al calibrar contra background planetario el modelo aprendia a distinguir
Chile del resto del mundo (tarea trivial), lo que producía AUC ~0.99 ecologicamente
irrelevantes. Ahora la pregunta es "donde dentro de Chile es habitable para esta
especie" — un problema genuinamente dificil. El **Boyce (CBI)** es la metrica mas
honesta para datos solo-presencia; las demas son complementarias.

### Por que bajaron los numeros y por que eso es correcto

- **Antes:** background = mundo entero. El modelo separaba puntos en Chile de
  pseudo-ausencias en Siberia o el Pacifico -> discriminacion trivial.
- **Ahora:** background = Chile. El modelo debe aprender el nicho dentro de un pais
  con gradiente latitudinal y altitudinal real -> problema ecologico verdadero.
- La bajada de metricas refleja honestidad, no error de implementacion.

### Resultados por especie

| Especie | n_pres (Chile) | AUC | TSS | Boyce | Calidad |
|---|---|---|---|---|---|
| skytanthus_acutus | 117 | 0.952 | 0.860 | +0.79 | Solida |
| krameria_cistoidea | 234 | 0.910 | 0.703 | +0.89 | Solida |
| nolana_sedifolia | 74 | 0.929 | 0.775 | +0.79 | Solida |
| nolana_divaricata | 64 | 0.929 | 0.798 | +0.77 | Solida |
| eulychnia_acida | 165 | 0.951 | 0.823 | +0.59 | Buena |
| oxalis_gigantea | 99 | 0.950 | 0.797 | +0.59 | Buena |
| miqueliopuntia_miquelii | 136 | 0.958 | 0.896 | +0.49 | Buena |
| encelia_canescens | 209 | 0.938 | 0.812 | +0.36 | Aceptable |
| neltuma_chilensis | 84 | 0.855 | 0.647 | +0.19 | Floja (mapa poco confiable) |
| cumulopuntia_sphaerica | 111 | 0.750 | 0.393 | +0.15 | Floja (mapa poco confiable) |
| pleurophora_pungens | 59 | 0.743 | 0.561 | -0.23 | Floja (mapa NO confiable) |
| senna_cumingii | 114 | 0.937 | 0.782 | -0.66 | Floja (mapa NO confiable) |
| schinus_areira* | 72 | 0.803 | 0.492 | +0.98 | Inestable (ver nota) |
| atriplex_semibaccata* | 8 | 0.770 | 0.559 | +0.23 | Sin modelar (ver nota) |

*\* Especies introducidas. Sus registros GBIF son globales; al acotar a Chile quedan
con muy pocas presencias. **atriplex_semibaccata (n=8) esta por debajo del umbral
minimo de 50 presencias y NO deberia modelarse**; schinus_areira (n=72) es inestable.
Sus mapas deben interpretarse con extrema cautela o descartarse.*

### Lectura honesta del ensemble

- **Sólidas (Boyce alto):** krameria (0.89), skytanthus (0.79), nolana divaricata
  (0.77), nolana sedifolia (0.79). Sus mapas son ecologicamente interpretables.
- **Buenas:** eulychnia y oxalis (Boyce 0.59). Mapas utiles con cautela normal.
- **Expuestas como flojas** (antes ocultas por el inflado): cumulopuntia (Boyce 0.15),
  neltuma (0.19), pleurophora (-0.23), senna (-0.66). Sus mapas NO son confiables
  y no deben usarse para toma de decisiones sin mejora de datos.
- **Marco roto por datos insuficientes en Chile:** atriplex (n=8, debajo del umbral
  minimo), schinus (n=72, inestable como introducida). Requieren marco de invasoras
  o datos adicionales de Chile.

Informe completo en [`docs/informe_modelo.md`](docs/informe_modelo.md); metricas
por especie y algoritmo en `outputs/tables/metrics_all.csv`; mapas (recortados a
Sudamerica) en `outputs/maps/*_present_suitability.tif` y figuras en
`outputs/figures/`.
Los resultados de la iteracion 2 (alcance global, metricas infladas, superada) quedan
en [`docs/resultados_iter1.md`](docs/resultados_iter1.md) como referencia historica.
El **forecast a 2050 sigue diferido** como mejora pendiente.

## Licencia

MIT.
