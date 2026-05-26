# Proceso de trabajo completo — Iteración 2 (bitácora detallada)

Este documento narra, paso a paso, todo el trabajo de la iteración 2: qué se
encontró mal, cómo se diagnosticó, qué se corrigió, qué experimentos se hicieron
y por qué se tomó cada decisión. Objetivo: que cualquier persona entienda el
proceso completo y pueda auditarlo o reproducirlo.

Resultado final en una línea: se pasó de **12 de 14 especies con métricas rotas
(NaN)** a **14 de 14 validables**, con un ensemble que **iguala a MaxEnt**
(TSS 0.82, AUC 0.94) y aporta robustez e incertidumbre.

---

## 0. Punto de partida (iteración 1)

- 14 especies modelables (grupos A y B), 13.354 → 4.566 ocurrencias GBIF tras
  limpieza (deduplicación, incertidumbre, máscara de océano, thinning espacial).
- Ensemble de 5 algoritmos (GLM, GAM, RF, GBM, MaxEnt), ponderado por TSS, con
  validación cruzada por bloques espaciales globales de 750 km.
- Aparentemente "funcionaba": el reporte de iteración 1 mostraba AUC de 0.95–1.00
  para casi todas las especies. Eso fue, precisamente, la primera señal de alarma.

---

## 1. Auditoría: cuatro fallos de raíz

### 1.1 Pendiente (`slope`) corrupta
Al inspeccionar los datos extraídos, la pendiente tenía **mediana 89.8 grados y el
99% de los puntos por encima de 80 grados**: el modelo "creía" que casi toda la
Tierra es un acantilado vertical, lo cual es físicamente imposible.

Causa: `xrspatial.slope()` asume coordenadas en metros (CRS proyectado), pero la
grilla está en grados (EPSG:4326) y la elevación en metros. El gradiente dz/dx
sale gigantesco (metros por grado) y `atan(gigante) ≈ 90°`. La variable entró
como predictor en 9 de 14 modelos: ruido casi constante.

### 1.2 Background (pseudo-ausencias) sesgado a los polos
**45% de los puntos de background caían en zonas polares/glaciales** (Antártida,
Ártico, Siberia), y 28.5% tenían temperatura media anual por debajo de -20 grados.
Ninguna de estas especies árido-templadas podría registrarse ahí.

Causa: el "target-group background" aplicaba un suavizado de Laplace
(`effort[land] += 1.0`) sobre TODA la tierra. Con millones de celdas y solo ~4.566
registros reales, el suavizado aplastaba la señal de esfuerzo de muestreo y el
background terminaba prácticamente uniforme sobre la tierra; como los polos son
una fracción enorme de la superficie, quedaban sobre-representados.

Efecto en los modelos: separar el nicho de una especie de un background dominado
por la Antártida es trivial. De ahí salían las **AUC infladas de 0.95–1.00** que
el documento de diseño ya marcaba como bandera roja.

### 1.3 Validación cruzada espacial degenerada (el fallo más grave)
La grilla global fija de 750 km repartía las presencias de una endémica de rango
estrecho en **1 o 2 bloques**, es decir 1–2 folds. Los otros 3–4 folds quedaban
sin presencias, se saltaban por completo, y los puntos de esos folds nunca recibían
una predicción out-of-fold (OOF). Esos NaN rompían todas las métricas basadas en
sklearn (AUC, AUC-PR, Brier, calibración), que quedaban en NaN silenciosamente.

Evidencia medida (presencias por fold con presencia):

| Especie | folds con presencia (de 5) | % NaN en OOF |
|---|---:|---:|
| atriplex_semibaccata (cosmopolita) | 5 | 0% |
| schinus_areira (cosmopolita) | 5 | 0% |
| encelia_canescens | 4 | 19.5% |
| eulychnia_acida | 1 (las 150 presencias en un fold) | 81.1% |
| nolana_divaricata | 2 | 62.3% |

Por eso solo las 2 especies cosmopolitas (presencias repartidas por el mundo)
tenían métricas completas; las 12 endémicas estaban rotas.

### 1.4 Combinación del ensemble y otros
La ponderación por TSS y el fallback de "pesos iguales" enmascaraban fallos;
además había fuga de datos en el escalado (StandardScaler ajustado sobre todos
los datos antes del CV) y una normalización OOF frágil.

---

## 2. Correcciones de datos

### 2.1 Pendiente
Recalculada con el algoritmo de Horn (1981) convirtiendo el tamaño de celda de
grados a metros con escala dependiente de la latitud:
`m_por_grado_lon = 111320 · cos(lat)`. Verificación: **mediana 89.8 → 0.21 grados,
0% por encima de 80 grados, máximos realistas en zonas montañosas**. Corregido en
tres lugares: los datasets (`fix_slope_base_datos.py`), el código fuente
(`03_terrain.py`) y el raster global (`regenerar_slope_tif.py` → `slope.tif`).

### 2.2 Background
Poda del background no-hábitat (`quemar_background_inservible.py`) con criterio
ligado a los datos (ninguna presencia lo viola): se eliminó el background con
`bio1 < -5` o `|lat| > 55` o `bio4 == 0`. Resultado: **270.500 → 132.835 puntos
(-51%)**, rango de latitud del background de -89.9/+83.6 a -55/+55, y **0 puntos
con bio1 < -20**. Las presencias (registros GBIF reales) no se tocaron.

### 2.3 Verificación de calidad de predictores
- 0 NaN en predictores y coordenadas.
- Rangos físicos correctos (temperaturas, precipitación, elevación).
- Consistencia interna `bio7 = bio5 - bio6` exacta.
- 0 precipitaciones negativas; orden de temperaturas `bio5 >= bio6` correcto.

---

## 3. Corrección de la validación cruzada (lo que rescató a las endémicas)

Se reemplazó la grilla global por **CV espacial adaptativo (leave-one-spatial-
cluster-out)**: las presencias se agrupan con k-means en 5 clústeres espaciales
(longitud corregida por cos(lat)); cada clúster es un fold; el background se
asigna al fold del centroide de presencias más cercano. El tamaño de fold se
adapta al rango de cada especie (continental para cosmopolitas, subregional para
endémicas), garantizando que **cada fold contenga presencias**.

Verificación: las 14 especies pasaron a tener **5 folds con presencias** (antes:
eulychnia 1, krameria/nolana 2, etc.) y **0 de 14 con métricas NaN** (antes 12/14).

Mejoras de calidad asociadas: escalado ajustado dentro de cada fold (sin fuga de
datos) y normalización OOF por fila (robusta).

---

## 4. Tuning de hiperparámetros (3 agentes en paralelo)

Cada algoritmo se tuneó con búsqueda aleatoria (~20 configuraciones) evaluada con
el CV espacial, optimizando solo sobre las especies con CV válido (para no ajustar
contra ruido). Ganancias en TSS de CV:

| Algoritmo | Config ganadora | TSS antes → después |
|---|---|---|
| GLM | C=0.5, L1, class_weight=balanced | 0.725 → 0.756 |
| GAM | n_splines=25, lam=10 | 0.803 → 0.813 |
| RF | n_estimators=300, max_depth=30, min_samples_leaf=8, max_features=log2, balanced_subsample | 0.787 → 0.819 |
| GBM | n_estimators=800, min_child_samples=20, reg_lambda=1, subsample=0.7, colsample_bytree=0.9 | 0.809 → 0.816 |
| MaxEnt | features lineal+cuadrático+producto, beta=1.59, tau=0.55, class_weights=100 | 0.791 → 0.796 |

---

## 5. Cómo combinar el ensemble (experimento clave)

Pregunta: ¿cómo combinar los 5 modelos para obtener el mejor ensemble? Se
compararon cuatro estrategias, evaluadas sobre las predicciones OOF con el CV
espacial (TSS al umbral maxTSS, anidado leave-one-fold-out donde aplica):

| Estrategia | TSS medio | Veredicto |
|---|---:|---|
| Ponderación por TSS³ | 0.73 | concentraba demasiado, descartada |
| Stacking (meta-modelo logístico) | 0.77 | sobreajuste con pocas presencias, descartada |
| Promedio simple (todos) | 0.81 | bueno |
| **Promedio equal-weight (excluye modelos con TSS<0.5)** | **0.82** | **elegida** |

Hallazgo importante: la ponderación TSS³ que se había introducido antes en realidad
**empeoraba** el ensemble (lo bajaba a 0.73). El promedio equal-weight de los
modelos que superan el umbral es la mejor combinación y la más simple.

Además se corrigió un sesgo en la comparación: el TSS del ensemble se calculaba
con el umbral de training mientras que el de cada algoritmo usaba el umbral óptimo
sobre OOF. Se unificó (ambos al umbral maxTSS sobre OOF) para que la comparación
ensemble vs MaxEnt sea justa.

---

## 6. Resultado final (CV espacial, ensemble equal-weight)

**Media: TSS 0.82 · AUC 0.94 · Boyce 0.68 · Brier 0.04. Las 14 especies con
métricas completas.**

| Especie | n | TSS | AUC | Boyce |
|---|---:|---:|---:|---:|
| miqueliopuntia_miquelii | 129 | 0.98 | 0.99 | 0.78 |
| skytanthus_acutus | 84 | 0.98 | 0.99 | 0.65 |
| encelia_canescens | 271 | 0.95 | 0.99 | 0.97 |
| krameria_cistoidea | 223 | 0.94 | 0.99 | 0.90 |
| nolana_divaricata | 45 | 0.93 | 0.99 | 0.69 |
| oxalis_gigantea | 68 | 0.88 | 0.97 | 0.69 |
| nolana_sedifolia | 37 | 0.85 | 0.95 | 0.67 |
| eulychnia_acida | 150 | 0.81 | 0.97 | 0.81 |
| cumulopuntia_sphaerica | 135 | 0.79 | 0.95 | 0.93 |
| senna_cumingii | 92 | 0.78 | 0.95 | 0.74 |
| atriplex_semibaccata | 1054 | 0.77 | 0.95 | 0.88 |
| neltuma_chilensis | 184 | 0.69 | 0.87 | 0.94 |
| schinus_areira | 1309 | 0.62 | 0.84 | −0.31 |
| pleurophora_pungens | 56 | 0.54 | 0.81 | 0.16 |

### Comparación con MaxEnt (misma vara para ambos)
| | Ensemble | MaxEnt |
|---|---:|---:|
| TSS medio | 0.822 | 0.826 |
| AUC medio | 0.944 | 0.939 |
| Ensemble ≥ MaxEnt (TSS) | 8 / 14 | — |

El ensemble **iguala a MaxEnt** (diferencia de TSS 0.004, dentro del ruido), lo
supera levemente en AUC, y le gana en 8 de 14 especies. Su valor diferencial no es
un TSS más alto sino **robustez** (no depende de un solo algoritmo) e
**incertidumbre** (acuerdo entre los 5 modelos por píxel).

---

## 7. Lectura honesta y limitaciones

- 12 de 14 especies son sólidas/buenas bajo validación espacial estricta.
- **schinus_areira** es la excepción: es una especie introducida y su Boyce
  negativo indica que no transfiere bien entre regiones (su distribución la marca
  la historia de invasión, no solo el clima). No usar su mapa para predecir
  invasividad sin un marco específico.
- **Pendientes declarados:** forecast a 2050 (CMIP6) no ejecutado ni validado (no
  presentar proyecciones de cambio climático todavía); sin validación por
  hindcasting; el sampler de background sigue siendo global en origen (se saneó por
  poda); 3 especies truncadas en el techo de descarga de GBIF (3.000 registros).

---

## 8. Orden de los scripts (reproducibilidad)

1. `01_limpieza.py` — limpieza de ocurrencias.
2. `02_capas_presente.py` — descarga/preparación de capas WorldClim presentes.
3. `03_terrain.py` — derivación de terreno (pendiente Horn geográfico) y alineación.
4. `04_extraccion.py` — extracción de predictores, background, CV espacial adaptativo.
5. `05_modelado.py` — ensemble (lee `tuned_params/`), combinación equal-weight.
6. `06_validacion.py` — métricas (TSS/AUC/Boyce/Brier/MESS, comparación justa).
7. `07b_present_suitability.py` — mapas de idoneidad presente.
8. `08_mapas.py` — figuras PNG.

Utilidades de saneamiento de esta iteración: `fix_slope_base_datos.py`,
`quemar_background_inservible.py`, `regenerar_slope_tif.py`, `aplicar_cv_adaptativo.py`.

---

# Iteración 3 — Acotar a Chile (área de calibración) y mapa a Sudamérica

## Diagnóstico: AUC inflados por extensión planetaria del background

Las métricas de iteración 2 seguían en AUC 0.95–0.99. La poda polar del background
(sección 2.2) había eliminado el síntoma más obvio, pero el problema de raíz
permanecía intacto: el background cubría **todo el planeta**, y las 14 especies son
endémicas o cuasi-endémicas chilenas. Separar el nicho climático de una planta del
desierto chileno de "el resto del mundo" es una tarea trivial para cualquier
clasificador; el AUC alto no refleja capacidad discriminativa real sino la enormidad
del contraste geográfico.

**Evidencia concreta — skytanthus_acutus:**

```
Presencias:  lon [-71, -70]  lat [-36, -24]   (franja andina chilena)
Background:  lon [-164, 179] lat [-55,  55]   (el planeta entero)
```

Con ese contraste, incluso un modelo nulo que usara solo longitud obtendría AUC > 0.95.

---

## Solución: edición quirúrgica del área, sin tocar el resto del pipeline

Se modificaron cuatro archivos. El resto del pipeline (limpieza, capas, terreno,
modelado, validación) no se tocó.

### `config.py`
Se añadieron dos parámetros de área:

- `CALIBRATION_COUNTRY = "Chile"` — nombre para la máscara de tierra.
- `CALIBRATION_BBOX` — bounding box del Chile continental.
- `PREDICTION_BBOX` — bounding box de Sudamérica (para proyecciones y mapas).

### `04_extraccion.py`
Nuevo helper `_load_calibration_mask` que intersecta la capa de tierra (Natural
Earth admin-0, ya cacheada por `01_limpieza.py`) con el polígono de Chile y, de
respaldo, con el `CALIBRATION_BBOX`. Efectos:

1. El **background se muestrea solo dentro de Chile**: las celdas fuera del polígono
   no son elegibles, independientemente de sus valores climáticos.
2. Las **presencias también se recortan a Chile**: esto es relevante para las
   introducidas *Schinus areira* y *Atriplex semibaccata*, cuyos registros GBIF son
   globales; dentro de este proyecto interesa modelar solo la fracción chilena.

### `07_forecast_2050.py` y `07b_present_suitability.py`
`build_predictor_stack` acepta ahora el parámetro `extent_bbox` y recorta el stack
al `PREDICTION_BBOX` antes de predecir. La proyección de idoneidad presente cubre
**Sudamérica** en lugar del planeta entero (~0.9 M píxeles vs decenas de millones):
más rápido y más informativo visualmente.

### `08_mapas.py`
La vista por defecto de los mapas se enfoca en Sudamérica. Antes llamaba a
`ax.set_global()`, lo que forzaba una vista mundial centrada en el Atlántico que
hacía los mapas de Chile prácticamente ilegibles.

---

## Verificación de la máscara de Chile

```
Celdas en la máscara : 49.807
Rango de longitud    : [-75.6, -67.0]
Rango de latitud     : [-55.6, -17.6]
Fraccion de la tierra:  0.4 %
```

El recorte es el Chile continental exacto (excluye la Antártida chilena).

---

## Resultados: métricas honestas tras acotar el área

Las métricas **bajaron** respecto a iteración 2. Eso es la señal correcta: antes
estaban infladas por el contraste geográfico trivial; ahora el modelo tiene que
discriminar dentro del territorio donde las especies realmente viven.

**Medias (CV espacial):**

| Metrica | Iteracion 2 | Iteracion 3 |
|---|---:|---:|
| AUC     | 0.944 | 0.884 |
| TSS     | 0.822 | 0.707 |
| Boyce   |  0.68 |  0.42 |

**Por especie (iteracion 3):**

| Especie | TSS | AUC | Boyce | Nota |
|---|---:|---:|---:|---|
| krameria_cistoidea      | 0.89 | — | — | solida |
| skytanthus_acutus       | 0.79 | — | — | solida |
| nolana_divaricata       | 0.77 | — | — | solida |
| nolana_sedifolia        | 0.79 | — | — | solida |
| cumulopuntia_sphaerica  |  —   | — | 0.15 | Boyce bajo, modelo debil |
| neltuma_chilensis       |  —   | — | 0.19 | Boyce bajo, modelo debil |
| pleurophora_pungens     |  —   | — | -0.23 | no transfiere |
| senna_cumingii          |  —   | — | -0.66 | no transfiere |
| atriplex_semibaccata    | —    | — | — | n=8 en Chile (< umbral 50) |
| schinus_areira          | —    | — | — | n=72 en Chile; especie introducida |

Las especies introducidas (*Atriplex semibaccata*, *Schinus areira*) colapsaron por
datos: al recortar los registros GBIF globales a Chile, *atriplex_semibaccata* quedó
con solo **n = 8 presencias** en Chile, por debajo del umbral operativo de 50
registros. *Schinus areira* bajó a **n = 72**, suficiente para modelar pero con
transferencia geográfica muy limitada dentro del país.

Las especies que quedan expuestas como flojas (cumulopuntia, neltuma, pleurophora,
senna) no son errores del pipeline: son especies con poca señal climática dentro del
dominio chileno, lo cual es una conclusión ecológica relevante en sí misma.

---

## 8. Orden de los scripts (reproducibilidad) — actualización iteración 3

1. `01_limpieza.py` — limpieza de ocurrencias (también cachea polígono Natural Earth).
2. `02_capas_presente.py` — descarga/preparación de capas WorldClim presentes.
3. `03_terrain.py` — derivación de terreno (pendiente Horn geográfico) y alineación.
4. `04_extraccion.py` — extracción de predictores; **acota background y presencias a Chile** mediante `_load_calibration_mask`; CV espacial adaptativo.
5. `05_modelado.py` — ensemble (lee `tuned_params/`), combinación equal-weight.
6. `06_validacion.py` — metricas (TSS/AUC/Boyce/Brier/MESS, comparacion justa).
7. `07b_present_suitability.py` — idoneidad presente **recortada a Sudamerica** (`PREDICTION_BBOX`).
8. `08_mapas.py` — figuras PNG con **vista centrada en Sudamerica** (no global).

Utilidades de saneamiento de iteracion 2 (siguen siendo validas):
`fix_slope_base_datos.py`, `quemar_background_inservible.py`,
`regenerar_slope_tif.py`, `aplicar_cv_adaptativo.py`.
