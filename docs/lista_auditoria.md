# Lista de Auditoria - Proyecto de Modelado de Distribucion de Especies (SDM)

Proyecto: global-flora-sdm
Iteracion: 3
Fecha: 2026-05-26
Documento preparado para revision por jefe/revisor.

---

## 1. Resumen ejecutivo

En la iteracion 2 se corrigieron tres defectos criticos: bug de unidades en pendiente, sesgo de background hacia zonas polares/glaciales, y CV espacial degenerado. En la iteracion 3 se corrigio el defecto de fondo: el alcance global del background. El proyecto paso de SDM global a SDM regional acotado a Chile: presencias y background se calibran dentro del poligono de Chile (Natural Earth admin-0, respaldo bbox CALIBRATION_BBOX); la idoneidad presente se proyecta y recorta a Sudamerica (PREDICTION_BBOX). Esto elimino la inflacion artificial de metricas que ocurria cuando el background planetario hacia trivial distinguir el clima de una endemica chilena del de un polo o desierto remoto. Las metricas medias (CV espacial) pasaron a AUC 0.884, TSS 0.707, Boyce 0.42, Brier 0.04. La caida respecto a iteracion 2 (AUC 0.944, TSS 0.822, Boyce 0.68) es esperada y saludable: refleja un problema de discriminacion mas honesto. El producto es apto para presentarse como mapa de idoneidad presente regional (iteracion 3) con limitaciones documentadas; NO es apto para afirmaciones de proyeccion a 2050.

---

## 2. Checklist de correcciones

- [x] Pendiente (slope): corregido bug de unidades (grados vs metros) que producia ~90 grados en el 99% del planeta. Se aplico el metodo de Horn con escala metrica por latitud. Verificado: la mediana paso de 89.8 a 0.21 grados y 0% de celdas superan 80 grados. Corregido en los datasets, en el script `03_terrain.py` y en el raster `slope.tif`.
- [x] Background (pseudo-ausencias): corregido en iteracion 2 el sesgo hacia zonas polares/glaciales (poda por |lat| <= 55, bio1 >= -5). Corregido en la fuente en iteracion 3: el sampler ahora genera puntos directamente dentro del poligono de Chile (CALIBRATION_COUNTRY="Chile"), no globalmente con poda posterior. El area accesible es coherente con el nicho de especies endemicas chilenas. Verificado: 0 puntos de background fuera de Chile.
- [x] Validacion cruzada espacial: corregida la grilla global fija que dejaba a las especies endemicas en 1-2 folds (CV degenerado, 12 de 14 especies con metricas NaN). Reemplazada por CV espacial adaptativo (clustering k-means de presencias en 5 folds). Verificado: las 14 especies pasaron a tener 5 folds con presencias y 0 de 14 con metricas NaN.
- [x] Comparacion de TSS justa: el ensemble y cada algoritmo se evaluan ahora al mismo umbral (maxTSS sobre las predicciones out-of-fold / OOF).
- [x] Alcance del area accesible (iteracion 3): corregido el sesgo estructural de usar background planetario para modelar endemicas chilenas. El area de calibracion se acota a Chile; la prediccion/mapa se extiende a Sudamerica. Configuracion: CALIBRATION_COUNTRY="Chile", CALIBRATION_BBOX, PREDICTION_BBOX. Las metricas bajaron (AUC 0.944 -> 0.884, TSS 0.822 -> 0.707, Boyce 0.68 -> 0.42) porque el problema de discriminacion es ahora mas exigente y honesto.

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
- [x] Evaluacion al mismo umbral (maxTSS sobre OOF) para ensemble y para cada algoritmo individual, garantizando comparacion justa.
- [ ] Validacion por hindcasting (transferencia temporal): NO realizada.
- [ ] Validacion del forecast a 2050 (CMIP6): NO ejecutada ni validada.

---

## 5. Tabla de estado del modelo

| Aspecto | Detalle | Estado |
|---|---|---|
| Algoritmos del ensemble | GLM, GAM, RF, GBM, MaxEnt (5 algoritmos) | Implementado |
| Hiperparametros | Tuneados via CV | Hecho |
| Metodo de combinacion: ponderacion por TSS^3 | TSS 0.73 (peor) | Descartado |
| Metodo de combinacion: stacking | TSS 0.77 (sobreajuste) | Descartado |
| Metodo de combinacion: promedio equal-weight | TSS 0.82 (iter. 2; mejor entre las 3 opciones) | Elegido |
| Resultado medio (CV espacial): TSS | 0.707 (iter. 3, regional) | Verificado |
| Resultado medio (CV espacial): AUC | 0.884 (iter. 3, regional) | Verificado |
| Resultado medio (CV espacial): Boyce | 0.42 (iter. 3, regional) | Verificado |
| Resultado medio (CV espacial): Brier | 0.04 | Verificado |

### Comparacion ensemble vs MaxEnt (mismo umbral y CV)

| Metrica | Ensemble | MaxEnt | Resultado |
|---|---|---|---|
| TSS | 0.707 | -- | Ver resultados iter. 3 |
| AUC | 0.884 | -- | Ver resultados iter. 3 |
| Especies con ensemble >= MaxEnt | -- | - | Ver resultados iter. 3 |

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
| Mapa de idoneidad presente regional (iteracion 3, calibracion Chile, proyeccion Sudamerica) con limitaciones documentadas | APTO |
| Afirmaciones o proyecciones a 2050 (cambio climatico) | NO APTO |
| Uso del mapa de `schinus_areira` para predecir invasividad | NO APTO |
| Modelado individual de `atriplex_semibaccata` (n=8 en Chile) | NO APTO |

Conclusion: el producto es APTO para presentarse como "idoneidad presente regional iteracion 3 con limitaciones documentadas". El alcance fue corregido de global a regional (calibracion en Chile, prediccion a Sudamerica), eliminando la inflacion de metricas por background planetario. Las metricas medias son AUC 0.884, TSS 0.707, Boyce 0.42 bajo CV espacial. El producto NO es APTO para sustentar afirmaciones de proyeccion climatica a 2050, ni para inferir invasividad de especies introducidas, ni para modelar `atriplex_semibaccata` individualmente (n insuficiente).
