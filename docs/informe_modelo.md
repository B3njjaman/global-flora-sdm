# Informe: cómo funciona el modelo (iteración 2)

Modelo de distribución de especies (SDM) **ensemble**, escala global, para 14
especies de flora. Predice **idoneidad de hábitat** (0–1) en cada celda de ~5 km
a partir de clima y topografía. Esta iteración corrige tres fallos de raíz
(pendiente, background, validación cruzada) y deja las 14 especies con métricas
completas y honestas.

---

## 1. Qué produce

Para cada especie: un raster de idoneidad presente (0–1), umbrales para mapas
binarios (presencia/ausencia), y un set de métricas de validación. El pipeline
está preparado para proyectar a 2050 (CMIP6), paso aún no ejecutado.

## 2. Cómo funciona

**Datos de entrada por punto** (presencia o pseudo-ausencia):
- **10 variables bioclimáticas** (WorldClim v2.1): temperatura (bio1,4,5,6,7,10,11)
  y precipitación (bio12,15,17).
- **4 topográficas**: elevación, pendiente, northness, eastness.
- Selección por especie eliminando colinealidad (|r|>0.7 y VIF>10).

**Background (pseudo-ausencias):** ~9.500 puntos por especie sobre tierra,
restringidos a latitudes plausibles (se descartó el background polar/glacial que
no representa ausencias reales para flora árido-templada).

**Ensemble de 5 algoritmos** (cada uno con hiperparámetros tuneados vía CV):
| Algoritmo | Rol |
|---|---|
| GLM (regresión logística L1) | lineal, calibrado |
| GAM (pyGAM) | no lineal suave |
| Random Forest | interacciones, no lineal |
| LightGBM (GBM) | boosting, no lineal |
| MaxEnt (elapid) | estándar SDM solo-presencia |

**Combinación:** promedio ponderado por **TSS³** del CV espacial (los modelos
mejores por especie pesan más; se excluyen los de TSS<0.5). El resultado es la
idoneidad ensemble 0–1.

**Umbrales** para binarizar: maxTSS (balance), p10 (conservador, ~10% omisión),
min_train (liberal).

## 3. Cómo se valida — CV espacial adaptativo

La validación NO usa k-fold aleatorio (inflado por autocorrelación). Usa
**leave-one-spatial-cluster-out**: las presencias se agrupan en 5 clústeres
espaciales (k-means sobre lon/lat); cada clúster es un fold; el modelo se entrena
en 4 y se evalúa en el que quedó fuera. El tamaño de fold **se adapta al rango de
la especie** (continental para cosmopolitas, subregional para endémicas), de modo
que **cada fold contiene presencias** — esto rescató a las 12 endémicas que antes
caían en 1–2 bloques y daban métricas no calculables (NaN).

Métricas reportadas: TSS y AUC (discriminación), Boyce/CBI (solo-presencia),
Brier (calibración), SD entre folds (robustez espacial), MESS (extrapolación).

## 4. Resultados (CV espacial, ensemble)

| Especie | n | TSS | AUC | CBI | Brier | Lectura |
|---|---:|---:|---:|---:|---:|---|
| miqueliopuntia_miquelii | 129 | 0.98 | 0.99 | 0.79 | 0.00 | sólido |
| skytanthus_acutus | 84 | 0.96 | 0.99 | 0.63 | 0.01 | sólido |
| krameria_cistoidea | 223 | 0.94 | 0.99 | 0.89 | 0.01 | sólido |
| encelia_canescens | 271 | 0.90 | 0.99 | 0.95 | 0.01 | sólido |
| nolana_divaricata | 45 | 0.87 | 0.99 | 0.71 | 0.07 | sólido |
| oxalis_gigantea | 68 | 0.87 | 0.97 | 0.49 | 0.01 | bueno |
| cumulopuntia_sphaerica | 135 | 0.75 | 0.95 | 0.96 | 0.02 | bueno |
| senna_cumingii | 92 | 0.75 | 0.95 | 0.63 | 0.04 | bueno |
| eulychnia_acida | 150 | 0.73 | 0.97 | 0.77 | 0.07 | bueno |
| atriplex_semibaccata | 1054 | 0.70 | 0.96 | 0.91 | 0.05 | bueno |
| nolana_sedifolia | 37 | 0.66 | 0.95 | 0.49 | 0.02 | moderado |
| neltuma_chilensis | 184 | 0.60 | 0.87 | 0.94 | 0.03 | moderado |
| pleurophora_pungens | 56 | 0.44 | 0.81 | 0.26 | 0.10 | moderado |
| schinus_areira | 1309 | 0.09 | 0.84 | −0.28 | 0.10 | débil (ver nota) |

**Media: TSS 0.73 · AUC 0.94 · CBI 0.65 · Brier 0.04.** Las 14 especies tienen
métricas completas (antes 12/14 eran NaN).

## 5. Lectura honesta

- **13 de 14 especies** discriminan bien (AUC 0.81–0.99) bajo validación espacial
  estricta. Calibración buena (Brier ≤0.10).
- **schinus_areira es la excepción real.** Tiene AUC 0.84 (discrimina) pero TSS y
  Boyce colapsan: es una especie **introducida** cuya distribución global responde
  a la historia de introducción, no solo al clima, por lo que **no transfiere
  entre regiones**. El CV espacial lo revela; el CV aleatorio anterior lo ocultaba
  (AUC inflada 0.97). Es un resultado correcto, no un bug.
- **SD entre folds alta** en endémicas de pocos registros (eulychnia, oxalis,
  miqueliopuntia, krameria: SD 0.40–0.48): el modelo funciona muy bien en unas
  subregiones y peor en otras. Esperable con muestras pequeñas repartidas en 5
  folds; interpretar el TSS medio con esa varianza en mente.

## 6. Qué se corrigió en esta iteración

1. **Pendiente (`slope`)**: estaba en ~90° en el 99% del planeta (unidades grados
   vs. metros). Recalculada con Horn y escala métrica por latitud.
2. **Background**: ~45% caía en zonas polares/glaciales (sesgo del muestreo).
   Podado al rango climático plausible.
3. **Validación cruzada**: de grilla global fija (endémicas degeneradas, métricas
   NaN) a **clustering espacial adaptativo** (todas las especies validables).
4. **Tuning** de hiperparámetros de los 5 algoritmos y ponderación TSS³ del
   ensamble.

## 7. Limitaciones y siguientes pasos

- `slope.tif` (raster global) y los mapas/forecast aún usan la pendiente vieja:
  regenerar antes de proyectar a 2050.
- El sampler de background en `04_extraccion.py` aún genera puntos globales; el
  saneamiento fue por poda. Conviene un target-group correcto (peso por esfuerzo
  GBIF sin el suavizado uniforme).
- MESS en validación usa solo presencias como referencia → %extrapolación inflado;
  unificar con la referencia entrenamiento completo.
- *schinus_areira*: para predecir invasividad conviene un marco específico de
  especies introducidas (no asumir equilibrio nicho-distribución).
