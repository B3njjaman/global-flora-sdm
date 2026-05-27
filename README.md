# global-flora-sdm

**Modelos de distribución de especies (SDM) para flora endemica de Chile — alcance regional, enfoque ensemble, con proyeccion a 2050 (CMIP6). (Versión 4 — modelo canónico vigente)**

> **Modelo vigente: Versión 4** (alcance Sudamérica, **background por área accesible
> por especie**). Métricas canónicas en `outputs/tables/metricas_v4_ensemble.csv`.
> La iteración 3 (Chile, equal-weight, `metrics_all.csv`) queda como referencia histórica
> más abajo. Ver sección [Resultados — Versión 4](#resultados--versión-4-modelo-canónico).

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
> capas alineadas, **13 modelos ensemble calibrados regionalmente** (atriplex excluida por n insuficiente), **validacion completa**
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

## Resultados — Versión 4 (modelo canónico)

**16 modelos** ensemble (GLM·GAM·RF·GBM·MaxEnt) entrenados con CV espacial adaptativo
(leave-one-cluster-out, 5 folds), alcance **Sudamérica**, ensemble **ponderado por
TSS-CV** y **background por área accesible por especie** (buffer 300 km alrededor de las
presencias de cada especie, en tierra-SA, distancia geodésica exacta). Esto corrige el
desajuste presencia(SA)/fondo(Chile) de versiones previas.

### Encabezado honesto (CV espacial por fold, media 16 especies)

| Modelo | AUC | TSS | Boyce/CBI |
|---|--:|--:|--:|
| **Ensemble (canónico)** | **0.826** | 0.473 | **0.863** |
| MaxEnt solo | 0.824 | **0.478** | — |

Con un background ecológicamente correcto, **ensemble y MaxEnt empatan**: el ensemble
gana en AUC (9/16 especies) y aporta el mejor Boyce (0.86 — concentración de presencias en
lo idóneo, la métrica más honesta para datos solo-presencia); MaxEnt gana TSS por 0.005
(8/16). El valor del ensemble es **robustez + Boyce**, no un salto de discriminación.
Frente a la iteración 3 (Chile, equal-weight): AUC 0.77→0.83, TSS 0.26→0.47, Boyce 0.44→0.86.

### Resultados por especie (ensemble, ordenado por Boyce)

| Especie | n | AUC | TSS | Boyce |
|---|--:|--:|--:|--:|
| Krameria cistoidea | 254 | 0.89 | 0.54 | +1.00 |
| Encelia canescens | 387 | 0.93 | 0.62 | +0.99 |
| Schinus areira | 323 | 0.75 | 0.22 | +0.99 |
| Oxalis gigantea | 123 | 0.95 | 0.69 | +0.99 |
| Nolana divaricata | 116 | 0.97 | 0.80 | +0.99 |
| Senna cumingii | 138 | 0.91 | 0.60 | +0.98 |
| Eulychnia acida | 199 | 0.89 | 0.53 | +0.98 |
| Cumulopuntia sphaerica | 175 | 0.89 | 0.57 | +0.96 |
| Neltuma chilensis | 304 | 0.69 | 0.19 | +0.92 |
| Skytanthus acutus | 162 | 0.86 | 0.58 | +0.92 |
| Pleurophora pungens | 69 | 0.74 | 0.31 | +0.91 |
| Nolana sedifolia | 122 | 0.78 | 0.56 | +0.90 |
| Caesalpinia angulata | 114 | 0.69 | 0.17 | +0.79 |
| Miqueliopuntia miquelii | 153 | 0.87 | 0.45 | +0.77 |
| Centaurea chilensis | 129 | 0.85 | 0.63 | +0.72 |
| Atriplex semibaccata | 83 | 0.55 | 0.11 | +0.02 |

Boyce ≥ 0.7 en 15/16 especies (mapas ecológicamente interpretables). *Atriplex semibaccata*
(introducida, Boyce 0.02) sigue siendo el caso débil. *Neltuma* y *Caesalpinia* tienen Boyce
alto pero TSS-transfer bajo (coherentes en agregado, inestables localmente).

> Métricas completas en `outputs/tables/metricas_v4_ensemble.csv`. Respaldo de la versión
> previa (background=Chile) en `outputs/_v4_bg-chile_backup/`. Mapas de idoneidad (SA) en
> `outputs/maps/*_idoneidad_sa.tif`.

---

## Resultados (iteracion 3 — calibracion regional Chile)

**13 modelos** ensemble entrenados y validados con CV espacial adaptativo, calibrados
dentro de Chile continental (ensemble equal-weight). *(atriplex_semibaccata, n=8, queda
excluida: por debajo del piso de 50 presencias — ver nota al final.)*

### Como se reporta el desempeño (lee esto antes de la tabla)

Existen tres formas de resumir el mismo CV espacial, y dan numeros muy distintos:

| Forma | AUC | TSS | Que es | Uso |
|---|---|---|---|---|
| **Por fold (media ± SD)** | **0.77** | **0.26** | promedio de las metricas calculadas fold-a-fold | **el numero honesto de transferencia espacial** |
| Pooled @ umbral de entrenamiento | 0.89 | 0.50 | metricas sobre el OOF agrupado, umbral fijado en entrenamiento | optimista (el pooling mezcla regiones) |
| ~~Pooled @ umbral optimizado sobre OOF~~ | ~~0.88~~ | ~~0.71~~ | umbral elegido mirando las etiquetas de evaluacion | **inflado — ya no se reporta** |

La iteracion 3 reportaba antes la fila tachada (TSS 0.71): el umbral se optimizaba
sobre los mismos datos de evaluacion, lo que sobrestimaba el TSS de forma sistematica.
Corregido. **El encabezado honesto es: AUC 0.77 · TSS 0.26 · Boyce 0.44** (CV espacial
por fold, 13 especies). El **Boyce (CBI)** es la metrica mas honesta para datos
solo-presencia y la que mas pesa en la interpretacion.

> Comparacion iter. 2 (global) vs iter. 3 (Chile): el alcance global producia AUC ~0.94
> porque distinguir el clima de una endemica chilena del de Siberia es trivial. Acotar a
> Chile (y reportar por fold sin trucar el umbral) baja los numeros: eso es honestidad,
> no regresion.

### Resultados por especie (CV espacial por fold + Boyce)

Ordenadas por Boyce (reliabilidad solo-presencia):

| Especie | n | AUC fold ±SD | TSS fold ±SD | Boyce | Lectura |
|---|--:|--:|--:|--:|---|
| krameria_cistoidea | 234 | 0.71±0.07 | 0.10±0.13 | +0.89 | confiable (Boyce alto; OJO TSS-transfer bajo) |
| skytanthus_acutus | 117 | 0.80±0.17 | 0.35±0.31 | +0.79 | confiable |
| nolana_sedifolia | 74 | 0.76±0.17 | 0.38±0.27 | +0.79 | confiable |
| nolana_divaricata | 64 | 0.74±0.22 | 0.10±0.15 | +0.77 | confiable (Boyce alto; OJO TSS-transfer bajo) |
| oxalis_gigantea | 99 | 0.83±0.20 | 0.56±0.32 | +0.59 | buena |
| eulychnia_acida | 165 | 0.79±0.13 | 0.49±0.22 | +0.59 | buena |
| miqueliopuntia_miquelii | 136 | 0.84±0.12 | 0.12±0.10 | +0.49 | buena (transfer debil) |
| encelia_canescens | 209 | 0.74±0.14 | 0.28±0.34 | +0.36 | aceptable |
| neltuma_chilensis | 84 | 0.75±0.12 | 0.31±0.25 | +0.19 | floja (mapa poco confiable) |
| cumulopuntia_sphaerica | 111 | 0.68±0.22 | 0.11±0.09 | +0.15 | floja (mapa poco confiable) |
| pleurophora_pungens | 59 | 0.79±0.14 | 0.00±0.01 | −0.23 | NO confiable |
| senna_cumingii | 114 | 0.76±0.09 | 0.27±0.14 | −0.66 | NO confiable (no transfiere) |
| schinus_areira* | 72 | 0.81±0.08 | 0.26±0.25 | +0.98 | introducida, n bajo → Boyce artefactual |

### Lectura honesta del ensemble

- **Confiables (Boyce ≥ 0.7):** krameria, skytanthus, nolana sedifolia, nolana
  divaricata. Mapas ecologicamente interpretables. *Caveat:* krameria y nolana
  divaricata tienen Boyce alto pero TSS-transfer por fold ~0.10 (alta SD entre
  subregiones): el mapa es coherente en agregado pero inestable localmente.
- **Buenas (Boyce 0.35–0.65):** oxalis, eulychnia, miqueliopuntia, encelia. Utiles
  con cautela normal.
- **Flojas / no confiables (Boyce ≤ 0.2 o negativo):** neltuma (0.19),
  cumulopuntia (0.15), pleurophora (−0.23), senna (−0.66). Sus mapas **no** deben
  usarse para toma de decisiones sin mejora de datos.
- **Introducida con n bajo:** schinus (n=72): Boyce 0.98 es artefacto de n pequeño,
  no señal real. Requiere marco de especie invasora.

### Ensemble vs MaxEnt — ¿valió la pena el ensemble?

Con las metricas honestas por fold, **el ensemble no le gana claramente a MaxEnt solo**:

| Metrica (media por fold) | Ensemble | MaxEnt solo | Gana ensemble en |
|---|--:|--:|--:|
| TSS | 0.26 | **0.34** | 5/13 especies |
| AUC | **0.77** | 0.75 | 9/13 especies |

MaxEnt solo es **mejor en TSS**; el ensemble empata/gana marginalmente en AUC. El valor
del ensemble aqui es de **robustez** (no depender de un solo algoritmo, menor varianza
entre especies), no de un salto de desempeño. El comentario previo de que el ensemble
equal-weight "supera a MaxEnt" se basaba en el TSS inflado y queda corregido.

> **Sobre las probabilidades:** la calibracion es pobre (`calib_slope` ≈ 0, ver
> `metrics_all.csv`). Las salidas deben leerse como **idoneidad relativa**, no como
> probabilidades; el Brier bajo (~0.04) es artefacto de la baja prevalencia, no señal
> de buena calibracion.

Informe completo en [`docs/informe_modelo.md`](docs/informe_modelo.md); metricas
por especie y algoritmo en `outputs/tables/metrics_all.csv`; mapas (recortados a
Sudamerica) en `outputs/maps/*_present_suitability.tif` y figuras en
`outputs/figures/`.
Los resultados de la iteracion 2 (alcance global, metricas infladas, superada) quedan
en [`docs/resultados_iter1.md`](docs/resultados_iter1.md) como referencia historica.

\* *atriplex_semibaccata (n=8 en Chile) está por debajo del piso de 50 presencias y
**no se modela** (excluida en `config.classify_species` y en el reporte). schinus_areira
(n=72), introducida, se reporta pero su mapa no debe usarse para inferir invasividad.*

El **forecast a 2050 está calculado** (`outputs/maps/_forecast_deferred/`) pero **no
certificado**: falta el MESS de proyeccion Chile→Sudamerica y la validacion por
hindcasting. No presentar proyecciones de cambio climatico hasta cerrar eso.

## Licencia

MIT.
