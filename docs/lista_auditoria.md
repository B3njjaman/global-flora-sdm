# Lista de Auditoria - Proyecto de Modelado de Distribucion de Especies (SDM)

Proyecto: global-flora-sdm
Iteracion: 2
Fecha: 2026-05-25
Documento preparado para revision por jefe/revisor.

---

## 1. Resumen ejecutivo

En la iteracion 2 se corrigieron tres defectos criticos identificados en la iteracion 1: un bug de unidades en la pendiente (slope), un sesgo de muestreo en el background hacia zonas polares/glaciales, y una validacion cruzada espacial degenerada que dejaba a la mayoria de las especies sin metricas calculables. Tras las correcciones, la calidad de los predictores quedo verificada (0 NaN, rangos fisicos correctos, consistencia bioclimatica exacta), las 14 especies pasaron a tener validacion cruzada espacial valida (5 folds con presencias cada una, 0 con metricas NaN) y el ensemble de 5 algoritmos con promedio equal-weight alcanzo un TSS medio de 0.82, AUC 0.94, Boyce 0.68 y Brier 0.04 bajo CV espacial. El producto es apto para presentarse como mapa de idoneidad presente (iteracion 2) con limitaciones documentadas; NO es apto para afirmaciones de proyeccion a 2050, ya que el forecast CMIP6 no fue ejecutado ni validado.

---

## 2. Checklist de correcciones

- [x] Pendiente (slope): corregido bug de unidades (grados vs metros) que producia ~90 grados en el 99% del planeta. Se aplico el metodo de Horn con escala metrica por latitud. Verificado: la mediana paso de 89.8 a 0.21 grados y 0% de celdas superan 80 grados. Corregido en los datasets, en el script `03_terrain.py` y en el raster `slope.tif`.
- [x] Background (pseudo-ausencias): corregido el sesgo que ubicaba ~45% de los puntos en zonas polares/glaciales (Antartida, Artico), no representativas de ausencias plausibles. Se podo al rango climatico/latitudinal plausible (|lat| <= 55, bio1 >= -5). Verificado: 0 puntos con bio1 < -20 tras la poda.
- [x] Validacion cruzada espacial: corregida la grilla global fija que dejaba a las especies endemicas en 1-2 folds (CV degenerado, 12 de 14 especies con metricas NaN). Reemplazada por CV espacial adaptativo (clustering k-means de presencias en 5 folds). Verificado: las 14 especies pasaron a tener 5 folds con presencias y 0 de 14 con metricas NaN.
- [x] Comparacion de TSS justa: el ensemble y cada algoritmo se evaluan ahora al mismo umbral (maxTSS sobre las predicciones out-of-fold / OOF).

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
| Background dentro de rango plausible (0 puntos con bio1 < -20) | [x] | Correcto tras poda |

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
| Metodo de combinacion: promedio equal-weight | TSS 0.82 | Elegido |
| Resultado medio (CV espacial): TSS | 0.82 | Verificado |
| Resultado medio (CV espacial): AUC | 0.94 | Verificado |
| Resultado medio (CV espacial): Boyce | 0.68 | Verificado |
| Resultado medio (CV espacial): Brier | 0.04 | Verificado |

### Comparacion ensemble vs MaxEnt (mismo umbral y CV)

| Metrica | Ensemble | MaxEnt | Resultado |
|---|---|---|---|
| TSS | 0.822 | 0.826 | Empate |
| AUC | 0.944 | 0.939 | Ensemble mejor |
| Especies con ensemble >= MaxEnt | 8 de 14 | - | Ensemble mayoritario |

---

## 6. Limitaciones declaradas

- [ ] Forecast a 2050 (CMIP6): NO ejecutado ni validado. No se deben presentar proyecciones de cambio climatico aun.
- [ ] Validacion por hindcasting (transferencia temporal): no realizada.
- [ ] `schinus_areira` (especie introducida): Boyce negativo y mala transferencia entre regiones. Requiere un marco metodologico de especies invasoras; su mapa NO debe usarse para predecir invasividad.
- [ ] Sampler de background en `04_extraccion.py`: sigue siendo global en origen. Se saneo mediante poda posterior, pero no se corrigio en la fuente del muestreo.
- [ ] Truncamiento por techo de descarga GBIF (3000 registros): afecta a 3 especies (`atriplex`, `schinus`, `nolana_divaricata`).

---

## 7. Veredicto de aptitud para presentar

| Uso | Veredicto |
|---|---|
| Mapa de idoneidad presente (iteracion 2) con limitaciones documentadas | APTO |
| Afirmaciones o proyecciones a 2050 (cambio climatico) | NO APTO |
| Uso del mapa de `schinus_areira` para predecir invasividad | NO APTO |

Conclusion: el producto es APTO para presentarse como "idoneidad presente iteracion 2 con limitaciones documentadas". Las correcciones de datos y de validacion estan verificadas y el desempeno del ensemble es solido bajo CV espacial. El producto NO es APTO para sustentar afirmaciones de proyeccion climatica a 2050, ya que el forecast CMIP6 no fue ejecutado ni validado, ni para inferir invasividad de la especie introducida.
