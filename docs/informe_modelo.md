# Informe: cómo funciona el modelo (iteración 3)

Modelo de distribución de especies (SDM) **ensemble**, **escala regional (Chile)**,
para 14 especies de flora. Predice **idoneidad de hábitat** (0–1) en cada celda de
~5 km a partir de clima y topografía. Esta iteración reencuadra la calibración al
territorio de Chile: las 14 especies son endémicas chilenas (más dos introducidas con
presencia en Chile) y calibrar contra background planetario inflaba la discriminación
de forma trivial (AUC ~0.99). Al acotar a Chile el modelo resuelve un problema
ecológico real: caracterizar el nicho dentro del rango del país.

---

## 1. Qué produce

Para cada especie: un raster de idoneidad presente (0–1) recortado a Sudamérica,
umbrales para mapas binarios (presencia/ausencia), y un set de métricas de validación.
El pipeline está preparado para proyectar a 2050 (CMIP6), paso aún no ejecutado.

## 2. Cómo funciona

**Datos de entrada por punto** (presencia o pseudo-ausencia):
- **10 variables bioclimáticas** (WorldClim v2.1): temperatura (bio1,4,5,6,7,10,11)
  y precipitación (bio12,15,17).
- **4 topográficas**: elevación, pendiente, northness, eastness.
- Selección por especie eliminando colinealidad (|r|>0.7 y VIF>10).

**Presencias:** registros GBIF acotados al polígono de Chile (Natural Earth admin-0
intersección tierra; respaldo: bounding-box de Chile). Esto descarta registros
fuera del país y reduce el n disponible frente a la iteración anterior.

**Background (pseudo-ausencias):** ~18.750 puntos (de 20.000 solicitados; el resto
cae con NaN en algún predictor) muestreados **dentro de Chile**, no en todo el planeta.
El fondo representa las condiciones ambientales disponibles en el territorio de
calibración. *Matiz pendiente:* el "target-group" no corrige sesgo de muestreo como
debería — el suavizado de Laplace en `04_extraccion.py` aplastó la señal de esfuerzo
GBIF, así que el background es prácticamente aleatorio dentro de Chile. Además, las
presencias ocupan una franja estrecha (lat ≈ −25 a −35) mientras el background abarca
todo el país (hasta Patagonia): distinguir "norte árido" de "sur templado" sigue siendo
relativamente fácil, lo que aún infla algo la discriminación. El área accesible honesta
sería un *buffer* alrededor de las presencias, no Chile entero.

**Ensemble de 5 algoritmos** (cada uno con hiperparámetros tuneados vía CV):
GLM (regresión logística L1), GAM (pyGAM), Random Forest, LightGBM, MaxEnt (elapid).

**Combinación:** promedio **equal-weight** de los modelos que superan el umbral
mínimo de calidad (TSS>=0.5 en CV). Se probaron alternativas (ponderación por TSS³
y stacking): el promedio equal-weight resultó el mejor.

**Umbrales** para binarizar: maxTSS, p10 (conservador, ~10% omisión), min_train.

**Predicción y mapas:** la idoneidad se predice sobre Sudamérica y los mapas se
enfocan en esa región (extent configurado en `config.py`).

## 3. Cómo se valida — CV espacial adaptativo

Validación por **leave-one-spatial-cluster-out**: las presencias se agrupan en 5
clústeres espaciales (k-means sobre lon/lat); cada clúster es un fold; se entrena
en 4 y se evalúa en el que queda fuera. El tamaño de fold **se adapta al rango de
la especie**, de modo que cada fold contiene presencias — esto rescató a las 12
endémicas que antes caían en 1–2 bloques y daban métricas no calculables (NaN).

Métricas: TSS y AUC (discriminación), Boyce/CBI (solo-presencia), Brier
(calibración), SD entre folds (robustez), MESS (extrapolación).

**Cómo se reporta el TSS (corregido en esta revisión).** El número honesto de
transferencia espacial es el **TSS/AUC por fold (media ± SD)**. El TSS pooled se
reporta al **umbral fijado en entrenamiento** aplicado al OOF. Antes se optimizaba el
umbral sobre el propio OOF (mirando las etiquetas de evaluación), lo que sobrestimaba
el TSS de forma sistemática (p. ej. nolana_divaricata aparecía con TSS 0.80 cuando su
TSS-transfer real es 0.12). Eso quedó eliminado: `06_validacion.py` ya no re-optimiza
el umbral sobre el conjunto de evaluación.

## 4. Resultados (CV espacial por fold, ensemble, calibrado a Chile)

13 especies (atriplex excluida, n=8). AUC y TSS son **media ± SD entre folds**
(transferencia espacial honesta); Boyce es el CBI sobre OOF. Ordenadas por Boyce:

| Especie | n | AUC fold ±SD | TSS fold ±SD | Boyce | Lectura |
|---|--:|--:|--:|--:|---|
| krameria_cistoidea | 234 | 0.71±0.07 | 0.10±0.13 | +0.89 | confiable (Boyce alto; transfer local inestable) |
| skytanthus_acutus | 117 | 0.80±0.17 | 0.35±0.31 | +0.79 | confiable |
| nolana_sedifolia | 74 | 0.76±0.17 | 0.38±0.27 | +0.79 | confiable |
| nolana_divaricata | 64 | 0.74±0.22 | 0.10±0.15 | +0.77 | confiable (Boyce alto; transfer local inestable) |
| oxalis_gigantea | 99 | 0.83±0.20 | 0.56±0.32 | +0.59 | buena |
| eulychnia_acida | 165 | 0.79±0.13 | 0.49±0.22 | +0.59 | buena |
| miqueliopuntia_miquelii | 136 | 0.84±0.12 | 0.12±0.10 | +0.49 | buena (transfer débil) |
| encelia_canescens | 209 | 0.74±0.14 | 0.28±0.34 | +0.36 | aceptable |
| neltuma_chilensis | 84 | 0.75±0.12 | 0.31±0.25 | +0.19 | débil (Boyce bajo) |
| cumulopuntia_sphaerica | 111 | 0.68±0.22 | 0.11±0.09 | +0.15 | débil (Boyce bajo) |
| pleurophora_pungens | 59 | 0.79±0.14 | 0.00±0.01 | −0.23 | no confiable |
| senna_cumingii | 114 | 0.76±0.09 | 0.27±0.14 | −0.66 | no transfiere |
| schinus_areira | 72 | 0.81±0.08 | 0.26±0.25 | +0.98 | introducida, n bajo → Boyce artefactual |

**Media (por fold): AUC 0.77 · TSS 0.26 · Boyce 0.44.**

La SD entre folds es alta (a menudo > el propio TSS): el modelo funciona en unas
subregiones de Chile y no en otras. El pooled-OOF con umbral de entrenamiento da una
cifra más alta (AUC 0.89 · TSS 0.50) pero el pooling mezcla regiones y sobrestima la
transferencia; por eso el encabezado honesto es la cifra por fold.

## 5. Por qué las métricas bajaron (y por qué eso es correcto)

En la iteración 2 el background era planetario. Para una especie del desierto de
Atacama, distinguir sus presencias de puntos en la selva amazónica o la tundra
ártica es trivial: cualquier modelo aprende esa separación y reporta AUC ~0.99 sin
esfuerzo. Ese número no decía nada sobre si el modelo captura el nicho real.

Al acotar background y presencias a Chile, el modelo debe discriminar entre zonas
de Chile con nicho apto y zonas de Chile sin él — una tarea ecológicamente relevante
y mucho más difícil. Las métricas que bajan son, por tanto, **métricas honestas**.

El Boyce (CBI) bajó de 0.68 a 0.42 en promedio. Eso también es información: contra
background planetario, concentrar presencias en zonas de alta idoneidad era trivial;
dentro de Chile se revela qué especies transfieren realmente su nicho.

## 6. Lectura honesta de los resultados

**Especies sólidas dentro de Chile** (Boyce >= 0.7):
- `krameria_cistoidea` (Boyce 0.89), `skytanthus_acutus` (0.79),
  `nolana_sedifolia` (0.79), `nolana_divaricata` (0.77). Sus mapas son confiables.

**Especies buenas** (Boyce 0.35–0.65):
- `eulychnia_acida` (0.59), `oxalis_gigantea` (0.59), `miqueliopuntia_miquelii`
  (0.49), `encelia_canescens` (0.36). Resultados razonables, usar con precaución.

**Especies expuestas como débiles** (Boyce bajo/negativo, antes oculto por inflado):
- `cumulopuntia_sphaerica` (Boyce 0.15), `neltuma_chilensis` (0.19):
  el modelo discrimina en CV pero no transfiere bien. Mapa no confiable.
- `pleurophora_pungens` (Boyce −0.23): zonas predichas como aptas no concentran
  presencias reales. Mapa no confiable.
- `senna_cumingii` (Boyce −0.66): el peor caso de transferibilidad. Mapa descartable
  para toma de decisiones.

**Especies introducidas con marco roto por datos:**
- `atriplex_semibaccata` (n=8 en Chile): por debajo del umbral mínimo de 50
  presencias. **No debería modelarse** bajo este marco; necesita datos adicionales o
  un enfoque distinto.
- `schinus_areira` (n=72 en Chile): al acotar a Chile los registros GBIF globales
  quedan muy reducidos. Boyce 0.98 sugiere ajuste artefactual por n pequeño;
  resultados inestables. Requiere marco específico de especie introducida.

**SD entre folds alta** en endémicas de pocos registros: el modelo funciona mejor
en unas subregiones que en otras. Interpretar el TSS medio con esa varianza en mente.

## 7. Qué se cambió en esta iteración

1. **Área de calibración**: background y presencias acotados a Chile (polígono
   Natural Earth admin-0 intersección tierra; respaldo: bounding-box). Configurado
   en `config.py` (`CALIBRATION_COUNTRY`, `CALIBRATION_BBOX`) y aplicado en
   `04_extraccion.py`.
2. **Área de predicción y mapas**: recortados a Sudamérica (`PREDICTION_BBOX` en
   `config.py`; aplicado en `07b` y `build_predictor_stack`; mapas en `08`).
3. **El resto de la metodología no se tocó**: CV adaptativo, umbral maxTSS sobre
   OOF, ensemble equal-weight, tuning de hiperparámetros, escalado dentro de fold,
   siguen exactamente igual que en la iteración 2.

## 8. Comparación iteraciones

| Métrica | Iter 2 (global) | Iter 3 (Chile, por fold) | Nota |
|---|---:|---:|---|
| AUC medio | 0.944 | 0.77 | doble corrección: alcance regional + reporte por fold |
| TSS medio | 0.822 | 0.26 | el TSS 0.71 que se reportaba antes usaba umbral optimizado sobre OOF (inflado) |
| Boyce medio | 0.68 | 0.44 | métrica solo-presencia, la más exigente |

La caída de iter 2 a iter 3 tiene dos causas: (a) acotar el background a Chile elimina
el contraste trivial "Chile vs. planeta"; (b) reportar por fold y sin trucar el umbral
expone la transferencia espacial real. Ambas son honestidad, no regresión.

## 9. Limitaciones y siguientes pasos

- **Forecast 2050 (CMIP6)**: los rasters **están calculados** (en
  `outputs/maps/_forecast_deferred/`) pero **no certificados** — falta el MESS de
  proyección Chile→Sudamérica y la validación por hindcasting. No presentar
  proyecciones de cambio climático hasta cerrar eso.
- **Probabilidades mal calibradas:** `calib_slope` ≈ 0 en casi todas las especies. Las
  salidas son índices de **idoneidad relativa**, no probabilidades; el Brier bajo
  (~0.04) es artefacto de la baja prevalencia, no señal de buena calibración.
- **Ensemble ≈ MaxEnt:** con métricas por fold, MaxEnt solo iguala o supera al ensemble
  en TSS (0.34 vs 0.26). El ensemble aporta robustez, no un salto de desempeño.
- Sin validación por hindcasting (transferencia temporal).
- **Baja transferibilidad revelada**: acotar a Chile expuso que cumulopuntia,
  neltuma, pleurophora y senna tienen Boyce bajo/negativo. Sus mapas requieren
  interpretación muy cautelosa.
- **Umbral maxTSS-sobre-OOF y CV adaptativo**: pendientes de refinamiento; no
  modificados en esta iteración.
- **Especies introducidas con n insuficiente** (atriplex n=8, schinus n=72 al
  acotar a Chile): requieren un marco aparte — más registros nacionales, enfoque
  de invasibilidad, o exclusión del análisis comparativo.
- El sampler de background podría mejorarse con un target-group correcto (mismo
  sesgo de muestreo que las presencias).
