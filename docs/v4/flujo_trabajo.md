# Flujo de trabajo — Versión 4 (reconstrucción modular)

> **Propósito.** Inventario de **los pasos que ejecuta cada script**, en el orden real del
> código. Esta es la unidad sobre la que iremos trabajando: **cada paso es algo que podemos
> mantener, modificar o eliminar** al reconstruir de forma modular. Misma lógica y mismo
> modelo que la iteración 3.1, con dos cambios de fondo: **alcance Sudamérica** (no solo
> Chile) y **estructura modular**.
>
> - **Rama:** `version-4`
> - **Dataset original (única fuente de verdad):** `gbif_distribucion_especies.xlsx`
>   (hoja `Registros GBIF`) — 13.354 registros, 21 especies, 15 columnas.
>
> **Leyenda de cada paso:**
> - (sin marca) = se mantiene igual que en 3.1.
> - ⟳ **V4** = paso que cambia en esta versión.
> - ★ **NUEVO** = paso que no existe en 3.1 y se agrega.
> - 🔴 = decisión de auditoría pendiente (tú decides antes de tocar código).
>
> **Estado:** Etapa 01 lista para implementar; 02–08 inventariadas para auditar en orden.

---

## Secuencia y artefactos (resumen)

```
xlsx → [01] → ocurrencias_limpias.gpkg → [04] → species_datasets/*.parquet → [05] → ensemble_models/*.joblib
              [02] → worldclim_present/ → [03] → rasters_aligned/  ┘                          │
                                                                                              ├→ [06] → tables/metrics_*.csv
                                                                                              ├→ [07b] → maps/*_present.tif → [08] → figures/*.png
                                                                                              └→ [07] → forecast 2050 (DIFERIDO)
```

---

## [01] `01_limpieza.py` — limpieza de ocurrencias

**Entrada:** `gbif_distribucion_especies.xlsx` (hoja `Registros GBIF`) · **Salida:**
`data/processed/ocurrencias_limpias.gpkg` (capa `ocurrencias`, EPSG:4326).
Pasos en el orden real de `main()`:

| # | Paso (función) | Qué hace | V4 |
|---|---|---|---|
| 0 | `construir_geodataframe` | Carga el xlsx (`utils.load_raw_occurrences`), renombra columnas a snake_case, descarta lat/lon nulas, crea geometría `Point` EPSG:4326. | |
| 1 | `eliminar_duplicados` | dedup exacto por `(especie, lat, lon)`, conserva el primero. | |
| 2 | `filtrar_incertidumbre` | descarta `incertidumbre_m > 10.000` m; los NaN se conservan. | |
| 3 | `filtrar_coords_sospechosas` | elimina NaN, `(0,0)`, fuera de rango (\|lat\|>90/\|lon\|>180) y coords con lat **y** lon de decimal `.0` (truncadas). | |
| **3b** | **`filtrar_sudamerica`** | **conserva solo registros dentro de Sudamérica** (13.354 → ~8.498). Se inserta aquí, antes de los pasos caros. | ★ **NUEVO** |
| 4 | `filtrar_centroides_admin` | descarta puntos a ≤1 km de centroides de país/provincia (Natural Earth admin-0/1). Paso más caro. | |
| 5 | `filtrar_oceano` | descarta puntos fuera de la land mask (Natural Earth), `sjoin within`. | |
| 6 | `thinning_espacial` | 1 punto por celda 2.5′ (1/24°) por especie; prefiere menor incertidumbre. (El paso que más reduce el n.) | |
| 7 | `asignar_grupos` | clasifica A/B/C (`config.classify_species`) sobre conteos **post-thinning**; C se marca pero no se descarta. | ⟳ **V4**: recalcular sobre conteos de Sudamérica (cambia el set modelable). |
| 8 | `_reportar_por_especie` + guardar | tabla inicial→final por especie y escribe el `.gpkg`. | |

**Detalle del paso ★ 3b (filtro Sudamérica).** Dos métodos posibles — verifiqué contra el
dataset que dan **exactamente los mismos 8.498 registros** (coincidencia 100%, 0
discrepancias):
- **por geometría:** punto dentro del polígono / bbox de Sudamérica (`config.PREDICTION_BBOX`).
- **por país:** columna `pais` ∈ {Chile, Colombia, Bolivia, Perú, Argentina, Brasil, Ecuador, Paraguay, …}.

🔴 **Decisión A — método del filtro 3b.** Recomiendo **por geometría** (el resto del
pipeline es coordenada-dependiente y es robusto ante etiquetas de país ausentes/erróneas).
"Por país" es igual de válido hoy. → *Determina cómo escribo el paso 3b.*

**Efecto del paso 7 en V4 (set de especies modelables).** Ampliar a Sudamérica suma
candidatas que con "solo Chile" quedaban fuera: **Nolana albescens** (365 reg. en SA),
posiblemente **Dinemagonum gayanum** (65) y **Nolana rostrata** (60); y da más datos a
*Schinus areira* (72 → 1.135 crudos). *Atriplex semibaccata* sigue bajo el piso de 50 (48 en
SA). El grupo A/B/C debe recalcularse sobre SA post-thinning, no heredarse.

**Verificación de salida (checklist de auditoría):** 0 registros fuera de SA · 0 lat/lon NaN
· 0 en océano · ≤1 punto por celda/especie · grupos recalculados · tabla inicial→final.

---

## [02] `02_capas_presente.py` — capas WorldClim (presente)

**Salida:** `data/raw/worldclim_present/` (10 bioclim + elevación + `land_mask.tif`).

| # | Paso | Qué hace | V4 |
|---|---|---|---|
| 1 | Descargar ZIPs | WorldClim v2.1 bioclim 2.5′ + elevación, con reanudación; extrae TIFs. | |
| 2 | Seleccionar bioclim | extrae/renombra las 10 de `config.BIOCLIM_VARS` (`wc2.1_2.5m_bio_N.tif → bioN.tif`). | |
| 3 | Preparar elevación | renombra a `elevation.tif`. | |
| 4 | Máscara de tierra | rasteriza Natural Earth sobre la grilla de `bio1` → `land_mask.tif`. | |
| 5 | Verificar alineación | mismo extent/resolución/CRS en todas las capas. | |

> V4: WorldClim se descarga global; el recorte a Sudamérica ocurre aguas abajo. Sin cambio
> funcional (opcional: recortar al bbox SA para acelerar 03/04).

---

## [03] `03_terrain.py` — terreno y alineación de predictoras

**Salida:** `data/processed/rasters_aligned/` (14 predictoras alineadas).

| # | Paso | Qué hace | V4 |
|---|---|---|---|
| 1 | Verificar entradas | exige elevación, mask y las 10 bioclim. | |
| 2 | Cargar elevación | `elevation.tif`. | |
| 3 | Derivar slope + aspect | xarray-spatial / richdem / **Horn geográfico** (escala metros por latitud — corrige el bug histórico). | |
| 4 | Descomponer aspecto | `northness = cos(aspect)`, `eastness = sin(aspect)`. | |
| 5 | Alinear bioclim | las 10 al grid de `bio1` (grilla canónica). | |
| 6 | Alinear topográficas | elevación, slope, northness, eastness al mismo grid. | |
| 7 | Aplicar land mask | enmascara todas las capas. | |
| 8 | Verificar + escribir | verifica alineación y escribe los 14 GeoTIFF. | |

> V4: sin cambio funcional. La topografía no depende del alcance geográfico.

---

## [04] `04_extraccion.py` — dataset modelable por especie

**Salida:** `data/processed/species_datasets/{slug}.parquet` (+ `_predictors.json`).
`main()` prepara el contexto y `process_species()` arma el dataset de cada especie.

**`main()`:**

| # | Paso | Qué hace | V4 |
|---|---|---|---|
| m1 | Especies modelables | lee columna `grupo` (A/B) del gpkg limpio (post-thinning). | hereda el set ampliado de [01]. |
| m2 | Cargar ocurrencias + abrir rasters + land_mask | prepara entradas. | |
| m3 | **`_load_calibration_mask`** | intersecta tierra ∩ **Chile** (polígono NE; respaldo `CALIBRATION_BBOX`). | ⟳ **V4**: cambiar área de calibración a **Sudamérica**. |

**`process_species()`:**

| # | Paso | Qué hace | V4 |
|---|---|---|---|
| 1 | Filtrar presencias | registros de la especie. | |
| 2 | Extraer predictoras en presencias | + descarta NaN + **recorta presencias al área de calibración**. | ⟳ **V4**: recorta a SA (no a Chile). |
| 3 | Generar background | `N_BACKGROUND=20.000` puntos dentro del área de calibración (target-group o aleatorio). | ⟳ **V4**: background dentro de SA. |
| 4 | Selección de predictoras | elimina colineales: \|r\|>0.7 y VIF>10 (sobre presencias). | |
| 5 | Combinar | presencias + background. | |
| 6 | Folds CV espacial | clustering adaptativo k-means (`N_CV_FOLDS=5`) garantizando presencias por fold. | |
| 7 | Guardar | ordena columnas → `.parquet` + `_predictors.json`. | |

🔴 **Decisión B — área de calibración (pasos m3/2/3).** Recomiendo **calibrar en toda
Sudamérica** (coherente con el alcance). Alternativa: seguir en Chile (pero entonces limpiar
a SA aporta poco). → *No bloquea [01]; condiciona [04].*

---

## [05] `05_modelado.py` — ensemble (GLM·GAM·RF·GBM·MaxEnt)

**Salida:** `data/modeling/ensemble_models/{slug}.joblib` (+ `{slug}_cv_preds.parquet`).
Pasos de `process_species()`:

| # | Paso | Qué hace | V4 |
|---|---|---|---|
| 1 | Cargar datos | `.parquet` + `_predictors.json`. | |
| 2 | Ajustar scaler | `StandardScaler` sobre los predictores. | |
| 3 | Construir modelos | 5 algoritmos con hiperparámetros de `tuned_params/*.json`. | |
| 4 | Spatial CV | leave-one-block-out → TSS y AUC por algoritmo y por fold. | |
| 5 | Pesos del ensemble | **equal-weight**; excluye algoritmos con TSS<0.5. | |
| 6 | Reentrenar | todos los modelos con el 100% de los datos. | |
| 7 | Umbrales | maxTSS, p10, min_train sobre las predicciones de training. | |
| 8 | OOF del ensemble | predicción out-of-fold ponderada (norm. por fila) → `cv_preds.parquet`. | |
| 9 | Serializar | guarda el artefacto `joblib` (incluye `train_env` para MESS). | |

> V4: sin cambio de lógica. Los números cambiarán al cambiar el área de calibración en [04].

---

## [06] `06_validacion.py` — métricas

**Salida:** `outputs/tables/metrics_{slug}.csv` + `metrics_all.csv`.
Pasos de `validate_species()`:

| # | Paso | Qué hace | V4 |
|---|---|---|---|
| 1 | Cargar datos + artefacto | parquet, predictoras, `joblib` (umbrales guardados). | |
| 2 | Cargar OOF (`cv_preds`) | base de las métricas; si no hay, usa training con **warning**. | |
| 3 | Umbral | usa el **maxTSS fijado en entrenamiento** (no se re-optimiza sobre OOF). | ⟳ (corrección 3.1 ya aplicada — mantener). |
| 4 | Discriminación | TSS, AUC-ROC, AUC-PR, F1. | |
| 5 | Calibración | Brier, slope/intercept + figura `calib_*.png`. | |
| 6 | Solo-presencia | **Boyce/CBI**, OR10. | |
| 7 | Robustez espacial | TSS y AUC **media ± SD por fold** ← número de encabezado honesto. | |
| 8 | Ensemble | SD entre algoritmos, acuerdo binario. | |
| 9 | Extrapolación | MESS, % de área extrapolada. | |
| 10 | Exportar | `metrics_{slug}.csv` + consolidado `metrics_all.csv`. | |

---

## [07b] `07b_present_suitability.py` — idoneidad presente

**Salida:** `outputs/maps/{slug}_present_suitability.tif`.

| # | Paso | Qué hace | V4 |
|---|---|---|---|
| 1 | Especies modelables | grupo A/B del gpkg. | |
| 2 | Cargar bioclim presente + topo | entradas de predicción. | |
| 3 | `project_present` por especie | `build_predictor_stack` (recorta a `PREDICTION_BBOX` = Sudamérica) → `predict_ensemble` → `reconstruct_raster` → `save_geotiff`. | `PREDICTION_BBOX` ya es Sudamérica. |

---

## [07] `07_forecast_2050.py` — proyección 2050 (DIFERIDO, no certificado)

Pasos de `process_species()`: (1) cargar ensemble · (2) descargar CMIP6 (4 GCM × 2 SSP) ·
(3) proyectar presente · (4) proyectar cada GCM×SSP · (5) ensemble de ensembles (mean+SD) +
Δidoneidad · (6) MESS futuro · (7) tabla de áreas (Mollweide).

> Estado: rasters calculados en `outputs/maps/_forecast_deferred/`, **no certificados**
> (falta MESS Chile→SA y validación por hindcasting). Fuera del alcance inmediato de V4.

---

## [08] `08_mapas.py` — figuras

**Salida:** `outputs/figures/*.png`. Por especie: idoneidad presente, mapas binarios
(maxTSS/p10/min_train), panel comparativo presente vs 2050, incertidumbre del ensemble, MESS,
y curva de calibración. **Vista centrada en Sudamérica** (no global).

---

## Decisiones de auditoría abiertas

- 🔴 **A — método del filtro Sudamérica (paso [01].3b):** geometría (recomendado) vs país.
- ✅ **B — área de calibración / background → RESUELTA.** El background NO es ni Chile fijo
  ni todo Sudamérica, sino el **área accesible (M) por especie**: buffer de 300 km alrededor
  de las presencias de cada especie ∩ tierra-SA (distancia geodésica exacta, BallTree-haversine,
  `src/extraccion/background.py::muestrear_background_especie`). Evita el desajuste
  presencia(SA)/fondo(Chile) sin caer en la inflación trivial de un fondo continental para
  endémicas estrictas. Implementado en `05_entrenar_ensemble.py` y `07_predecir_sudamerica.py`.
