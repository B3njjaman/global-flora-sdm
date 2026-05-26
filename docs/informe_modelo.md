# Informe: cómo funciona el modelo (iteración 2)

Modelo de distribución de especies (SDM) **ensemble**, escala global, para 14
especies de flora. Predice **idoneidad de hábitat** (0–1) en cada celda de ~5 km
a partir de clima y topografía. Esta iteración corrige tres fallos de raíz
(pendiente, background, validación cruzada), ajusta la combinación del ensemble,
y deja las 14 especies con métricas completas y honestas.

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
GLM (regresión logística L1), GAM (pyGAM), Random Forest, LightGBM, MaxEnt (elapid).

**Combinación:** promedio **equal-weight** de los modelos que superan el umbral
mínimo de calidad (TSS≥0.5 en CV). Se probaron alternativas (ponderación por TSS³
y stacking): el promedio equal-weight resultó el mejor (ver §6).

**Umbrales** para binarizar: maxTSS, p10 (conservador, ~10% omisión), min_train.

## 3. Cómo se valida — CV espacial adaptativo

Validación por **leave-one-spatial-cluster-out**: las presencias se agrupan en 5
clústeres espaciales (k-means sobre lon/lat); cada clúster es un fold; se entrena
en 4 y se evalúa en el que queda fuera. El tamaño de fold **se adapta al rango de
la especie**, de modo que cada fold contiene presencias — esto rescató a las 12
endémicas que antes caían en 1–2 bloques y daban métricas no calculables (NaN).

Métricas: TSS y AUC (discriminación), Boyce/CBI (solo-presencia), Brier
(calibración), SD entre folds (robustez), MESS (extrapolación). El TSS se reporta
al umbral maxTSS sobre OOF, el **mismo criterio para el ensemble y para cada
algoritmo**, de modo que las comparaciones son justas.

## 4. Resultados (CV espacial, ensemble)

| Especie | n | TSS | AUC | CBI | Lectura |
|---|---:|---:|---:|---:|---|
| miqueliopuntia_miquelii | 129 | 0.98 | 0.99 | 0.78 | sólido |
| skytanthus_acutus | 84 | 0.98 | 0.99 | 0.65 | sólido |
| encelia_canescens | 271 | 0.95 | 0.99 | 0.97 | sólido |
| krameria_cistoidea | 223 | 0.94 | 0.99 | 0.90 | sólido |
| nolana_divaricata | 45 | 0.93 | 0.99 | 0.69 | sólido |
| oxalis_gigantea | 68 | 0.88 | 0.97 | 0.69 | bueno |
| nolana_sedifolia | 37 | 0.85 | 0.95 | 0.67 | bueno |
| eulychnia_acida | 150 | 0.81 | 0.97 | 0.81 | bueno |
| cumulopuntia_sphaerica | 135 | 0.79 | 0.95 | 0.93 | bueno |
| senna_cumingii | 92 | 0.78 | 0.95 | 0.74 | bueno |
| atriplex_semibaccata | 1054 | 0.77 | 0.95 | 0.88 | bueno |
| neltuma_chilensis | 184 | 0.69 | 0.87 | 0.94 | moderado |
| schinus_areira | 1309 | 0.62 | 0.84 | −0.31 | débil (ver nota) |
| pleurophora_pungens | 56 | 0.54 | 0.81 | 0.16 | moderado |

**Media: TSS 0.82 · AUC 0.94 · CBI 0.68 · Brier 0.04.** Las 14 especies tienen
métricas completas (antes 12/14 eran NaN).

## 5. Comparación con MaxEnt (modelo de referencia en SDM)

Pregunta clave: ¿aporta algo el ensemble frente a usar solo MaxEnt? Comparación
**justa** (mismo umbral y mismo CV espacial para ambos):

| | Ensemble equal-weight | MaxEnt solo |
|---|---:|---:|
| TSS medio | **0.822** | 0.826 |
| AUC medio | **0.944** | 0.939 |
| Especies donde ensemble ≥ MaxEnt (TSS) | **8 / 14** | — |

**Lectura honesta:** el ensemble **iguala a MaxEnt** en TSS (diferencia 0.004,
dentro del ruido) y lo **supera levemente en AUC**, ganándole en 8 de 14 especies.
No lo supera de forma decisiva. El valor real del ensemble no es un TSS más alto,
sino: (a) **robustez** — no depende de la elección de un solo algoritmo; (b)
**incertidumbre** — el acuerdo/desacuerdo entre los 5 modelos da una medida de
confianza por píxel que un modelo único no entrega.

## 6. Lectura honesta de los resultados

- **12 de 14 especies** discriminan bien (AUC 0.81–0.99) bajo validación espacial
  estricta, con buena calibración (Brier ≤0.10).
- **`schinus_areira`** es la excepción: TSS 0.62 y AUC 0.84 razonables, pero su
  Boyce es negativo (−0.31) → en regiones no vistas, las zonas de alta idoneidad
  no concentran más presencias. Es **introducida** y su distribución responde a la
  historia de invasión, no solo al clima → **no transfiere bien entre regiones**.
  Para predecir invasividad requiere un marco específico.
- **`pleurophora_pungens`**: la endémica más floja (TSS 0.54, Boyce 0.16).
- **SD entre folds alta** en endémicas de pocos registros: el modelo funciona muy
  bien en unas subregiones y peor en otras. Interpretar el TSS medio con esa
  varianza en mente.

## 7. Qué se corrigió en esta iteración

1. **Pendiente (`slope`)**: estaba en ~90° en el 99% del planeta (unidades grados
   vs. metros). Recalculada con Horn y escala métrica por latitud (datos y raster).
2. **Background**: ~45% caía en zonas polares/glaciales. Podado al rango plausible.
3. **Validación cruzada**: de grilla global fija (endémicas degeneradas, NaN) a
   **clustering espacial adaptativo** (todas las especies validables).
4. **Combinación del ensemble**: se descartó la ponderación TSS³ (bajaba el TSS de
   ensamble a 0.73) y el stacking (sobreajuste, 0.77); se adoptó **equal-weight**
   de los modelos que superan el umbral (TSS 0.82, empata a MaxEnt).
5. **Tuning** de hiperparámetros de los 5 algoritmos.
6. **Escalado dentro de cada fold** (sin fuga) y comparación de TSS con umbral
   consistente entre ensemble y algoritmos individuales.

## 8. Limitaciones y siguientes pasos

- Forecast 2050 (CMIP6) aún no ejecutado/validado: no presentar proyecciones de
  cambio climático todavía.
- Sin validación por hindcasting (transferencia temporal).
- El sampler de background en `04_extraccion.py` sigue siendo global; el
  saneamiento fue por poda. Conviene un target-group correcto.
- *schinus_areira*: marco específico de especies introducidas.
