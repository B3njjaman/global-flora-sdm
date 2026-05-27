# Lista de Auditoria - Proyecto de Modelado de Distribucion de Especies (SDM)

Proyecto: global-flora-sdm
Iteracion: 3
Fecha: 2026-05-26
Documento preparado para revision por jefe/revisor.

---

## 1. Resumen ejecutivo

La iteracion 2 corrigio tres defectos: bug de unidades en pendiente, sesgo de background polar, y CV espacial degenerado. La iteracion 3 acoto el alcance a Chile (calibracion) / Sudamerica (prediccion). Esta revision (3.1) corrige el reporte de metricas, que estaba inflado por dos vias: (a) el TSS se calculaba con un umbral optimizado sobre los propios datos de evaluacion, y (b) se reportaba el OOF agrupado ("pooled") en vez de la media por fold. Ademas se excluye atriplex_semibaccata (n=8) del modelado, por debajo del piso de 50 presencias.

Metricas corregidas (CV espacial, media por fold, 13 especies): AUC 0.77, TSS 0.26, Boyce 0.44. El TSS 0.707 que se reportaba antes era el numero inflado. La transferencia espacial real es floja y muy variable entre subregiones; la utilidad debe leerse por especie, no por el promedio: 4 especies confiables (Boyce ≥ 0.77), 4 buenas (0.36-0.59), 4 flojas o no confiables (≤ 0.2, dos negativas). Con metricas por fold, el ensemble no supera claramente a MaxEnt solo (TSS 0.26 vs 0.34). El producto es apto como mapa de idoneidad presente regional solo para las especies confiables y con limitaciones documentadas; NO es apto para afirmaciones de proyeccion a 2050.

---

## 2. Checklist de correcciones

- [x] Pendiente (slope): corregido bug de unidades (grados vs metros) que producia ~90 grados en el 99% del planeta. Se aplico el metodo de Horn con escala metrica por latitud. Verificado: la mediana paso de 89.8 a 0.21 grados y 0% de celdas superan 80 grados. Corregido en los datasets, en el script `03_terrain.py` y en el raster `slope.tif`.
- [x] Background (pseudo-ausencias): corregido en iteracion 2 el sesgo hacia zonas polares/glaciales (poda por |lat| <= 55, bio1 >= -5). Corregido en la fuente en iteracion 3: el sampler ahora genera puntos directamente dentro del poligono de Chile (CALIBRATION_COUNTRY="Chile"), no globalmente con poda posterior. El area accesible es coherente con el nicho de especies endemicas chilenas. Verificado: 0 puntos de background fuera de Chile.
- [x] Validacion cruzada espacial: corregida la grilla global fija que dejaba a las especies endemicas en 1-2 folds (CV degenerado, 12 de 14 especies con metricas NaN). Reemplazada por CV espacial adaptativo (clustering k-means de presencias en 5 folds). Verificado: las 14 especies pasaron a tener 5 folds con presencias y 0 de 14 con metricas NaN.
- [x] Comparacion de TSS justa: corregido el umbral. Antes el TSS del ensemble se calculaba optimizando el umbral sobre el propio OOF (mirando las etiquetas de evaluacion), lo que lo inflaba. Ahora se usa el umbral fijado en entrenamiento, y el numero de encabezado es el TSS/AUC por fold (media ± SD).
- [x] Alcance del area accesible (iteracion 3): corregido el sesgo estructural de usar background planetario para modelar endemicas chilenas. El area de calibracion se acota a Chile; la prediccion/mapa se extiende a Sudamerica. Configuracion: CALIBRATION_COUNTRY="Chile", CALIBRATION_BBOX, PREDICTION_BBOX. Las metricas corregidas (por fold) son AUC 0.77, TSS 0.26, Boyce 0.44, frente a iter. 2 global (AUC 0.944, TSS 0.822, Boyce 0.68): bajan por el acotamiento regional y por reportar por fold sin trucar el umbral.

---

## 3. Checklist de calidad de datos

| Verificacion | Estado | Resultado verificado |
|---|---|---|
| NaN en predictores | [x] | 0 NaN |
| NaN en coordenadas | [x] | 0 NaN |
| Rangos fisicos de predictores | [x] | Correctos |
| Consistencia bioclimatica bio7 = bio5 - bio6 | [x] | Exacta |
| Precipitaciones negativas | [x] | 0 valores negativos |
| Orden de temperaturas (bio5 >= bio6) | [x] | Correcto |
| Pendiente en rango fisico (mediana 0.21 grados, 0% > 80 grados) | [x] | Correcto tras correccion |
| Background acotado a Chile (CALIBRATION_COUNTRY="Chile") | [x] | Correcto en la fuente (iteracion 3) |

---

## 4. Checklist de validacion

- [x] CV espacial adaptativo (k-means de presencias en 5 folds) implementado y aplicado a las 14 especies.
- [x] 14 de 14 especies con 5 folds que contienen presencias.
- [x] 0 de 14 especies con metricas NaN (AUC / Brier / Boyce ahora calculables).
- [x] Evaluacion con umbral fijado en ENTRENAMIENTO (no re-optimizado sobre OOF) y encabezado por fold (media ± SD). Corrige el sesgo previo que inflaba el TSS.
- [ ] Validacion por hindcasting (transferencia temporal): NO realizada.
- [ ] Validacion del forecast a 2050 (CMIP6): NO ejecutada ni validada.

---

## 5. Tabla de estado del modelo

| Aspecto | Detalle | Estado |
|---|---|---|
| Algoritmos del ensemble | GLM, GAM, RF, GBM, MaxEnt (5 algoritmos) | Implementado |
| Hiperparametros | Tuneados via CV | Hecho |
| Metodo de combinacion: promedio equal-weight | elegido entre TSS^3 y stacking | Elegido |
| Resultado medio (CV espacial, por fold): TSS | 0.26 (13 sp, sin inflar) | Verificado |
| Resultado medio (CV espacial, por fold): AUC | 0.77 (13 sp, sin inflar) | Verificado |
| Resultado medio (CV espacial): Boyce | 0.44 (13 sp) | Verificado |
| Resultado medio (CV espacial): Brier | 0.04 (engaña: baja prevalencia, calib_slope ≈ 0) | Verificado |

### Comparacion ensemble vs MaxEnt (metricas por fold, mismo CV)

| Metrica (media por fold) | Ensemble | MaxEnt solo | Resultado |
|---|--:|--:|---|
| TSS | 0.26 | **0.34** | MaxEnt mejor; ensemble gana en 5/13 |
| AUC | **0.77** | 0.75 | ensemble marginal; gana en 9/13 |

Conclusion: el ensemble no supera claramente a MaxEnt; aporta robustez (menor
dependencia de un algoritmo), no un salto de desempeño. El "equal-weight supera a MaxEnt"
que se afirmaba antes se basaba en el TSS inflado.

---

## 6. Limitaciones declaradas

- [ ] Forecast a 2050 (CMIP6): NO ejecutado ni validado. No se deben presentar proyecciones de cambio climatico aun.
- [ ] Validacion por hindcasting (transferencia temporal): no realizada.
- [ ] `schinus_areira` (especie introducida, n=72 en Chile): el marco regional mejora la coherencia del background, pero la especie sigue siendo introducida y su patron no transfiere bien entre zonas. Su mapa NO debe usarse para predecir invasividad.
- [x] Sampler de background en `04_extraccion.py`: RESUELTO en iteracion 3. El sampler ahora genera puntos directamente dentro de Chile (CALIBRATION_COUNTRY="Chile"), eliminando el defecto de background global con poda posterior.
- [ ] `atriplex_semibaccata`: con acotamiento a Chile, n=8 registros, muy por debajo del umbral minimo de 50. Especie descartada del modelado individual en iteracion 3.
- [ ] Truncamiento por techo de descarga GBIF (3000 registros): afecta a `schinus` y `nolana_divaricata`. Con acotamiento a Chile `schinus` tiene n=72 (manejable); `nolana_divaricata` puede seguir siendo afectada.

---

## 7. Veredicto de aptitud para presentar

| Uso | Veredicto |
|---|---|
| Mapa de idoneidad presente de las **4 especies confiables** (Boyce ≥ 0.77: krameria, skytanthus, nolana sedifolia, nolana divaricata) con limitaciones documentadas | APTO con cautela |
| Mapa de las 4 especies "buenas" (Boyce 0.36–0.59) | APTO solo como indicativo |
| Mapa de las 4 especies flojas/no confiables (Boyce ≤ 0.2: neltuma, cumulopuntia, pleurophora, senna) | NO APTO |
| Afirmaciones o proyecciones a 2050 (cambio climatico) | NO APTO |
| Uso del mapa de `schinus_areira` para predecir invasividad | NO APTO |
| Modelado individual de `atriplex_semibaccata` (n=8) | NO APTO (excluida) |

Conclusion: el producto sirve como mapa de idoneidad presente regional por especie, no en bloque. Las metricas corregidas (por fold, 13 sp) son AUC 0.77, TSS 0.26, Boyce 0.44, con alta variabilidad entre especies: 4 confiables, 4 indicativas, 4 no usables. NO es APTO para proyeccion climatica a 2050 (calculada pero no certificada), ni para inferir invasividad de introducidas, ni para modelar atriplex (excluida por n insuficiente). El TSS 0.707 que se reportaba antes estaba inflado y queda corregido a 0.26 (por fold).
