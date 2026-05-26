# Modelos de distribución de especies (SDM) — Python, alcance regional (Chile/Sudamérica), con forecasting

## Objetivo

Modelar la distribución potencial de especies de flora chilena con registros en GBIF a **escala regional**, calibrando dentro de **Chile** como área accesible y proyectando/visualizando los mapas de idoneidad a **Sudamérica**, en **stack Python**, usando un **enfoque ensemble** que combina múltiples algoritmos (más allá de MaxEnt), y **proyectar a 2050** bajo escenarios CMIP6.

Pipeline diseñado para **crecer**: las primeras iteraciones usan bioclim + topografía; iteraciones siguientes incorporan groundwater (pozos, ríos), índices temporales (NDVI/EVI), y eventualmente deep learning sobre series temporales para forecasting nativo.

## Decisiones de diseño

| Decisión | Elegida | Por qué |
|---|---|---|
| Alcance espacial | Regional: calibración en Chile, predicción a Sudamérica | Área accesible coherente con especies endémicas chilenas; evita inflar AUC con background planetario |
| Resolución | 2.5 arc-min (~5 km) | Punto dulce para SDM regional; 30 arc-sec global son 3–10 GB por variable, inmanejable |
| Stack | Python | Flexibilidad para extender a deep learning, modelos temporales, integración hidrológica |
| Modelos | Ensemble: GLM, GAM, RF, GBM, MaxEnt | Robustez frente a sesgo de un solo algoritmo |
| Horizonte temporal | Presente + 2050 (CMIP6) | Decisiones estratégicas a 30 años son defendibles; más allá la incertidumbre se dispara (Brodie et al. 2022) |
| Variables iter. 1 | Bioclim (8) + topografía (4) | Empezar simple; extensiones documentadas en roadmap |

## Datos de ocurrencia

- **Fuente:** GBIF (13.354 registros, 21 especies)
- **Distribución geográfica:** Chile (6.990), Australia (3.076), EE.UU. (975), Colombia (552), Bolivia (342), Sudáfrica (342), Perú (314), Argentina (291), España (254), México (98), otros
- **Especies dominantes:** *Atriplex semibaccata*, *Nolana divaricata*, *Schinus areira*, *Encelia canescens*, *Nolana sedifolia*, *Krameria cistoidea*, *Eulychnia acida*, entre otras
- **Sesgo conocido:** tres especies (*Atriplex semibaccata*, *Nolana divaricata*, *Schinus areira*) tienen exactamente 3.000 registros — techo de descarga GBIF. Para análisis riguroso, rehacer descarga particionada vía API GBIF

## Estrategia por tipo de especie

| Grupo | Especies | Estrategia |
|---|---|---|
| **A. Introducidas con suficientes registros en Chile** | *Schinus areira* (n=72 en Chile) | Modelar con marco regional; predice nicho climático dentro de Chile/Sudamérica. Cuidado con interpretación de invasividad: la especie ocupa sitios antropogénicos |
| **B. Endémicas con datos suficientes** | *Nolana divaricata*, *N. sedifolia*, *Encelia canescens*, *Eulychnia acida*, *Krameria cistoidea*, otras | Modelar regional (calibración Chile, proyección Sudamérica); predicción + análogos climáticos dentro del subcontinente |
| **C. Pocos registros en Chile (<50)** | *Atriplex semibaccata* (n=8 en Chile, insuficiente), *Centaurea chilensis*, *Aloysia salviifolia*, *Caesalpinia angulata*, *Atriplex deserticola* | **No modelar individualmente.** Con acotamiento a Chile, n=8 queda muy por debajo del umbral mínimo de 50 registros. Considerar descarte, pooling taxonómico, o métodos especializados para datos escasos |

## Variables predictoras — Iteración 1

### Climáticas (WorldClim v2.1, bioclim, 2.5 arc-min)

| Código | Variable | Justificación |
|---|---|---|
| BIO1 | Temperatura media anual | Driver fundamental |
| BIO4 | Estacionalidad de temperatura | **Crítica en el gradiente regional**: distingue zonas áridas del norte de Chile de las zonas templadas del sur |
| BIO5 | Temp. máx. mes más cálido | Tolerancia a calor extremo |
| BIO6 | Temp. mín. mes más frío | Tolerancia a frío extremo |
| BIO7 | Rango anual de temperatura | BIO5 - BIO6 |
| BIO10 | Temp. media trimestre cálido | Período de crecimiento |
| BIO11 | Temp. media trimestre frío | Dormancia |
| BIO12 | Precipitación anual | Restricción hídrica básica |
| BIO15 | Estacionalidad de precipitación | Patrón mediterráneo vs. tropical vs. monzónico |
| BIO17 | Precipitación trimestre seco | Relevante para flora árida |

### Topográficas (derivadas del DEM de WorldClim)

| Variable | Fuente / método |
|---|---|
| **Elevación** | WorldClim elevation (SRTM-derivado), directo |
| **Pendiente** | Derivada con `richdem` o `xarray-spatial` |
| **Northness** | `cos(aspect)` |
| **Eastness** | `sin(aspect)` |

> **Por qué descomponer el aspecto:** el aspecto en grados (0–360°) es circular: 359° y 1° son casi idénticos pero numéricamente opuestos. Descomponer en `sin/cos` lo convierte en dos variables continuas y monótonas.

> **Nota sobre northness:** Chile y Sudamérica están en el hemisferio sur, donde las laderas más cálidas miran al norte. Al proyectar a Sudamérica completa no hay cruce de hemisferios relevante, pero si se extiende a zonas tropicales ecuatoriales conviene revisar `northness * sign(latitud)`.

## Stack Python — librerías por etapa

| Etapa | Librerías |
|---|---|
| Limpieza ocurrencias | `pandas`, `geopandas`, `pyproj`, `coordinatecleaner-py` (port parcial) o reimplementar reglas de `CoordinateCleaner` |
| Raster I/O y procesamiento | `rasterio`, `rioxarray`, `xarray`, `dask` (para chunked processing) |
| Análisis terrain | `richdem`, `xarray-spatial` (TIN, slope, aspect, curvature) |
| Descarga WorldClim | `requests` directo, o `pyimpute` |
| Descarga CMIP6 | `cdsapi` (Copernicus Climate Data Store), `xclim` (cálculo de bioclim derivadas) |
| Extracción puntos-raster | `rasterstats`, `rioxarray.sel` |
| Background / pseudo-ausencias | Implementación propia o `elapid.geo.sample_raster` |
| Modelos clásicos | `scikit-learn` (GLM, RF), `lightgbm` o `xgboost` (GBM), `pyGAM` (GAM) |
| MaxEnt en Python | `elapid` (implementación moderna de MaxEnt) |
| BART (opcional) | `pymc-bart` |
| Spatial CV | `spacv` o implementación propia con `sklearn.model_selection` |
| Métricas SDM-específicas | `elapid.evaluate` (Boyce index), implementación propia |
| MESS / extrapolación | `dismo` no existe en Python — implementar o portar |
| Visualización | `matplotlib`, `contextily`, `folium`, `cartopy` |
| Reproducibilidad | `conda env` + `requirements.txt`, `pyproject.toml` con `uv` o `poetry` |

## Pipeline de procesamiento

### Etapa 1 — Limpieza de ocurrencias (`01_limpieza.py`)
- Eliminar duplicados exactos
- Filtrar incertidumbre > 5 km (a 2.5 arc-min, > 10 km es razonable)
- Detectar centroides administrativos: comparar coords contra centroides conocidos de país/región (Natural Earth admin boundaries) con tolerancia de ~1 km
- Eliminar coords en océano (overlay con land mask)
- Eliminar (0,0), coords con muchos decimales en cero, patrones sospechosos
- **Thinning espacial:** 1 punto por celda raster usando `xarray.groupby` sobre celda grid
- Por grupo de especies (A/B/C), aplicar reglas distintas

### Etapa 2 — Descarga y procesamiento de capas (`02_capas.py` + `03_terrain.py`)

**Presente:**
- Descargar WorldClim v2.1 bioclim 2.5 arc-min (10 capas) y elevation
- Derivar pendiente y aspecto con `richdem`
- Descomponer aspecto en northness/eastness
- Generar máscara de tierra (Natural Earth)
- Verificar alineación entre capas (mismo extent, resolución, CRS)

**Futuro (CMIP6):**
- Descargar bioclim CMIP6 desde **WorldClim Future** (Fick & Hijmans) o calcularlas desde CMIP6 raw con `xclim`
- **GCMs múltiples** (mínimo 4): p. ej. GFDL-ESM4, IPSL-CM6A-LR, MPI-ESM1-2-HR, MRI-ESM2-0
- **SSPs múltiples** (mínimo 2): SSP2-4.5 (medio) y SSP5-8.5 (alto)
- **Período:** 2041-2060 (centrado en 2050)
- Las topográficas se mantienen constantes (la topografía no cambia en 30 años)

### Etapa 3 — Dataset modelable (`04_extraccion.py`)

- Extraer valores de raster en cada punto de presencia (`rasterstats` o `rioxarray`)
- Generar **background points**: 10.000–50.000 puntos muestreados **dentro de Chile** (polígono Natural Earth admin-0 intersección tierra; respaldo: bbox `CALIBRATION_BBOX`). Configuración: `CALIBRATION_COUNTRY="Chile"`. Esto define el área accesible y evita inflar la discriminación con background planetario
- **Target-group background** (recomendado): sacar puntos con probabilidad proporcional a densidad de registros del grupo taxonómico dentro del área de calibración (Chile)
- **Recorte de presencias:** igualmente se filtran al polígono de Chile antes de modelar
- **Colinealidad:** VIF y matriz de correlación; eliminar |r| > 0.7 o VIF > 10
- **Split espacial:** spatial block CV con bloques de ~100–300 km (escala apropiada para Chile; `spacv` o implementación propia)

### Etapa 4 — Ensemble (`05_modelado.py`)

Cinco algoritmos en paralelo, todos en Python:

```python
# Pseudocódigo del ensemble
from sklearn.linear_model import LogisticRegression  # GLM
from pygam import LogisticGAM                          # GAM
from sklearn.ensemble import RandomForestClassifier   # RF
import lightgbm as lgb                                 # GBM
from elapid import MaxentModel                         # MaxEnt

models = {
    'glm': LogisticRegression(penalty='l2'),
    'gam': LogisticGAM(),
    'rf': RandomForestClassifier(n_estimators=500, class_weight='balanced'),
    'gbm': lgb.LGBMClassifier(num_leaves=31, learning_rate=0.05),
    'maxent': MaxentModel()
}

# Ensemble ponderado por TSS de spatial CV
ensemble_pred = weighted_average(predictions, weights=tss_scores)
```

**Ensemble:** promedio ponderado por TSS de spatial CV, con umbral mínimo (modelos con TSS < 0.5 excluidos).

### Etapa 5 — Evaluación y métricas (`06_validacion.py`)

Ninguna métrica sola es suficiente. Reportar al menos una de cada categoría:

#### 5.1 Discriminación

| Métrica | Rango | Interpretación |
|---|---|---|
| **TSS** | -1 a 1 | Sensibilidad + especificidad − 1; >0.5 aceptable, >0.7 bueno |
| **AUC-ROC** | 0–1 | Estándar histórico; criticada porque depende de extensión |
| **AUC-PR** | 0–1 | Más informativa cuando presencias son raras |
| **F1-score** | 0–1 | Balance precision/recall |

#### 5.2 Calibración

| Métrica | Qué evalúa |
|---|---|
| **Brier score** | Error cuadrático medio de probabilidades; menor = mejor |
| **Curva de calibración** | Probabilidad predicha vs. observada por bins |
| **Calibration slope/intercept** | Ideal slope=1, intercept=0 |

RF y GBM discriminan bien pero calibran mal; MaxEnt y GLM al revés. Importante si el output se usará para priorización.

#### 5.3 Solo-presencia (las más apropiadas para GBIF)

| Métrica | Qué hace |
|---|---|
| **Boyce index / CBI** | -1 a 1; mide si áreas de alta idoneidad contienen más presencias. La métrica más honesta para datos solo-presencia |
| **OR10** | Omission rate al 10% percentile training; debe estar cerca del esperado teórico |

#### 5.4 Validación cruzada espacial

- ❌ k-fold aleatorio: inflado por autocorrelación, no usar
- ✅ Spatial block CV con bloques de 500–1000 km
- ✅ Continental CV: dejar fuera un continente entero
- Reportar **media ± SD entre folds**: SD alta indica mala transferencia entre regiones

#### 5.5 Extrapolación

| Métrica | Qué muestra |
|---|---|
| **MESS** | Mapa: dónde el modelo extrapola vs. interpola |
| **MoD** | Variable más responsable de la extrapolación |
| **ExDet (NT1/NT2)** | Distingue extrapolación univariada de combinaciones novedosas |

Al proyectar desde Chile a Sudamérica y especialmente en forecasting, reportar MESS es **obligatorio**.

#### 5.6 Evaluación del ensemble (no solo modelos individuales)

- **SD entre algoritmos** por píxel — mapa de incertidumbre
- **Acuerdo binario:** cuántos de los 5 modelos predicen presencia tras umbralizar (5/5 = alta confianza)
- **Ensemble vs. mejor modelo individual:** el ensemble debe igualar o superar

#### 5.7 Umbrales para mapas binarios

Reportar al menos 2 umbrales para mostrar sensibilidad:

| Método | Comportamiento |
|---|---|
| **maxTSS** | Maximiza TSS; balance estándar |
| **10th percentile training presence** | Conservador; acepta ~10% omisión |
| **Minimum training presence** | Liberal; sitio tan idóneo como el peor de entrenamiento |

#### 5.8 Tabla resumen — qué reportar mínimo

| Categoría | Métrica mínima | Complementaria |
|---|---|---|
| Discriminación | TSS | AUC, AUC-PR |
| Calibración | Brier score | Curva de calibración |
| Solo-presencia | Boyce / CBI | OR10 |
| Robustez espacial | Block CV (media ± SD) | Variabilidad por continente |
| Extrapolación | MESS (mapa) | % área extrapolada |
| Ensemble | SD entre algoritmos | Acuerdo binario |
| Umbral | maxTSS + 10th percentile | — |

### Etapa 6 — Proyección a 2050 (`07_forecast.py`)

- Aplicar el ensemble entrenado en presente sobre las capas CMIP6 futuras **recortadas a Sudamérica** (`PREDICTION_BBOX`)
- **Una proyección por combinación GCM × SSP** = 4 GCMs × 2 SSPs = 8 mapas por especie
- **Ensemble de ensembles:** promediar las 8 proyecciones, reportar SD
- **MESS futuro:** mostrar dónde 2050 tiene combinaciones climáticas fuera del rango de entrenamiento (especialmente relevante al proyectar fuera de Chile)
- **Cambio relativo:** mapa de Δidoneidad (futuro − presente), enfocado en Sudamérica
- **Cálculo de áreas:** reproyectar a equiárea (Mollweide o Equal Earth) antes de calcular km²

#### Validación de forecast con hindcasting (recomendado)

Idealmente, antes de confiar en el forecast a 2050:
1. Entrenar el modelo con clima de 1970-2000 (WorldClim histórico)
2. Proyectar al período 2000-2020
3. Comparar contra registros GBIF posteriores a 2000 no usados en entrenamiento
4. Si el hindcast acierta, el forecast 2050 es más defendible

Esto sigue la estrategia de Cavanaugh et al. (2022) — única forma honesta de validar proyecciones temporales.

## Estructura de carpetas

```
proyecto_sdm/
├── data/
│   ├── raw/
│   │   ├── gbif_distribucion_especies.xlsx
│   │   ├── worldclim_present/        # 2.5 arc-min bioclim + elevation
│   │   └── worldclim_future/         # CMIP6 por GCM × SSP
│   ├── processed/
│   │   ├── ocurrencias_limpias.gpkg
│   │   ├── rasters_aligned/          # bioclim + topo, alineados
│   │   └── species_datasets/         # un .parquet por especie con valores extraídos
│   └── modeling/
│       └── ensemble_models/          # pickle/joblib por especie
├── scripts/
│   ├── 01_limpieza.py
│   ├── 02_capas_presente.py
│   ├── 03_terrain.py
│   ├── 04_extraccion.py
│   ├── 05_modelado.py
│   ├── 06_validacion.py
│   ├── 07_forecast_2050.py
│   └── 08_mapas.py
├── outputs/
│   ├── figures/
│   ├── maps/                         # GeoTIFF de idoneidad
│   └── tables/                       # métricas por especie/algoritmo
├── docs/
│   └── proyecto_sdm.md               # este archivo
├── pyproject.toml                    # deps con uv/poetry
└── README.md
```

## Roadmap de extensiones (iteraciones 2 y siguientes)

Una vez funcionando la iteración 1, el pipeline está diseñado para crecer en estas direcciones:

### Iteración 2 — Variables hidrogeológicas
- **Pozos:** profundidad de tabla freática (IGRAC global wells DB, o datos nacionales chilenos DGA)
- **Ríos/quebradas:** distancia a cauces y orden de Strahler (HydroSHEDS)
- **Cuerpos de agua:** distancia a lagos/lagunas
- Especialmente relevante para flora árida (Wang et al. 2023; Li et al. 2025 muestran que groundwater depth mejora SDMs en zonas áridas)

### Iteración 3 — Variables temporales (series, no promedios)
- **MODIS NDVI/EVI** mensual 2000-presente
- **TerraClimate** mensual (temperatura, precipitación, ET) 1958-presente
- En vez de "BIO1 = promedio anual", el modelo recibe la serie temporal completa
- Habilita capturar variabilidad interanual, anomalías, tendencias

### Iteración 4 — Deep learning sobre series temporales
- Arquitectura: LSTM/Transformer sobre series temporales + CNN sobre patches espaciales
- Reemplaza el ensemble clásico cuando la pregunta requiere dinámica temporal explícita
- Implementación: PyTorch
- Referencia metodológica: Deneu et al. (2022) — "Predicting species distributions with environmental time series data and deep learning"

### Iteración 5 — Forecasting nativo (no solo proyección)
- En vez de "entrenar con promedio histórico → proyectar con promedio futuro", el modelo aprende dinámica temporal y proyecta secuencias
- Permite responder: "¿cuándo será no apto?" no solo "¿es apto en 2050?"
- Requiere arquitecturas seq2seq o forecasting bayesiano

### Iteración 6 — Integración hidrológica-ecológica
- Acoplar SDM con modelos de balance hídrico (PCR-GLOBWB, WaterGAP)
- Predecir no solo idoneidad climática sino disponibilidad de agua subterránea futura
- Referencia: Li et al. (2025) framework SDM + hidrogeología en regiones áridas

## Notas metodológicas relevantes

1. **MaxEnt no es "malo", solo es uno.** Ensemble reduce sesgo. RF y GBM suelen ganarle a MaxEnt en discriminación, pero MaxEnt suele calibrar mejor.

2. **El thinning espacial importa más que el algoritmo.** Especialmente cuando el background está acotado a Chile, donde el sesgo GBIF concentrado en zonas accesibles puede distorsionar el nicho aprendido.

3. **Cross-validation espacial no es opcional.** AUC con CV aleatorio en SDM moderno está desacreditado. Métricas de 0.95+ son bandera roja, no verde.

4. **Reportar MESS siempre.** Al proyectar desde Chile a toda Sudamérica y especialmente en forecast, partes del mapa estarán fuera del espacio de entrenamiento.

5. **Forecasting honesto requiere hindcasting.** Validar con período pasado conocido antes de confiar en proyecciones futuras (Cavanaugh et al. 2022).

6. **Incertidumbre del forecast crece con el tiempo.** Brodie et al. (2022): primeros 30 años el poder predictivo se mantiene; más allá la incertidumbre se dispara y depende de qué tan novedoso es el clima futuro vs. entrenamiento.

7. **Endémicas en modelos regionales.** Para *Nolana* chilenas y otras endémicas, calibrar en Chile es lo metodológicamente correcto: el background regional representa el área accesible real y evita la inflación artificial de AUC que ocurría con background planetario. El modelo puede predecir análogos climáticos en Sudamérica fuera de Chile, pero esa proyección debe interpretarse con el MESS como guía.

8. **Las 3 especies truncadas en 3.000 registros.** Si son foco real, rehacer descarga particionada vía API GBIF.

## Papers de referencia

### SDM general y validación
- Hijmans et al. (2005), Fick & Hijmans (2017). WorldClim. *Int. J. Climatology*
- Norberg et al. (2019). A comprehensive evaluation of 33 SDMs. *Ecological Monographs* — el benchmark de referencia
- Thuiller et al. (2009). BIOMOD ensemble forecasting. *Ecography*
- Hao et al. (2019). Review of ensemble SDMs. *Diversity and Distributions*
- Valavi et al. (2022). Predictive performance of presence-only SDMs. *Ecological Monographs*
- Zurell et al. (2020). ODMAP — standard protocol for SDM reporting. *Ecography*
- Aiello-Lammens et al. (2015). spThin. *Ecography*

### Limpieza de datos y sesgo
- Zizka et al. (2019). CoordinateCleaner. *Methods Ecol. Evol.*
- Phillips et al. (2009). Sample selection bias — target-group background. *Ecol. Appl.*

### Validación espacial
- Roberts et al. (2017). Cross-validation strategies for spatial data. *Ecography*
- Valavi et al. (2019). blockCV. *Methods Ecol. Evol.*

### Métricas
- Allouche et al. (2006). TSS. *J. Appl. Ecol.*
- Hirzel et al. (2006). Boyce index. *Ecol. Modell.*
- Liu et al. (2013). Selecting thresholds with presence-only data. *J. Biogeography*

### Extrapolación
- Elith et al. (2010). MESS — art of modelling range-shifting species. *Methods Ecol. Evol.*
- Mesgaran et al. (2014). ExDet — quantifying novelty. *Diversity & Distributions*

### Forecasting y proyecciones climáticas
- **Brodie et al. (2022). Quantifying and reducing uncertainty in climate projections of SDMs.** *Global Change Biology* — calibrar expectativas de forecast
- **Cavanaugh et al. (2022). Hindcast-validated SDMs reveal future vulnerabilities.** *Ecology and Evolution* — técnica de validación de forecasts
- **Sridhar et al. (2017). Ensemble forecasting of distributional shifts.** *Ecology and Evolution* — template práctico

### Series temporales y deep learning (iteraciones futuras)
- **Deneu et al. (2022). Predicting species distributions with environmental time series data and deep learning.** bioRxiv — el paper estrella para forecasting con DL
- Kellenberger et al. (2026). Performance of deep learning for SDMs. *Global Ecology and Biogeography* — realismo sobre DL en SDM

### Variables hidrogeológicas (iteración 2)
- **Li et al. (2025). Mapping shallow groundwater solute footprints using hydrologically enhanced SDM.** *Water Resources Research* — framework SDM + hidrogeología
- **Wang et al. (2023). Simulation of potential vegetation in arid areas at regional scale.** — groundwater depth como predictor concreto
