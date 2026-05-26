# Flujo de trabajo — Versión 4 (reconstrucción modular)

> **Propósito.** Documentar, etapa por etapa, **cómo trabaja el código** para auditarlo
> uno por uno antes de reconstruirlo de forma modular. Misma lógica y mismo modelo que
> la iteración 3.1, pero (a) **alcance Sudamérica** (no solo Chile) desde la limpieza, y
> (b) estructura **modular**.
>
> - **Rama:** `version-4`
> - **Dataset original (única fuente de verdad):** `gbif_distribucion_especies.xlsx`
>   (hoja `Registros GBIF`) — 13.354 registros, 21 especies, 15 columnas.
> - **Estado de este documento:** Etapa 1 (limpieza) detallada. Las etapas 2–8 se irán
>   añadiendo a medida que se auditen.
>
> Convención: 🔴 **DECISIÓN DE AUDITORÍA** marca un punto donde tú decides antes de que
> yo escriba código.

---

## 0. Mapa general del pipeline (8 etapas)

Cada etapa consume las salidas de la anterior. El cambio de alcance (Chile → Sudamérica)
afecta sobre todo a las etapas **1** (limpieza), **4** (background/calibración) y **7**
(predicción/mapa).

```
gbif_distribucion_especies.xlsx
        │
        ▼
[01] LIMPIEZA  ───────────────►  ocurrencias_limpias.gpkg
        │   dedup · incertidumbre · coords inválidas · ★FILTRO SUDAMÉRICA★
        │   · centroides admin · océano · thinning · grupos A/B/C
        ▼
[02] CAPAS PRESENTE (WorldClim) ─►  worldclim_present/  (bioclim + elevación + máscara)
        ▼
[03] TERRENO  ──────────────────►  rasters_aligned/  (14 predictoras alineadas)
        │   slope (Horn geográfico) · aspect → northness/eastness
        ▼
[04] EXTRACCIÓN  ───────────────►  species_datasets/*.parquet (+ _predictors.json)
        │   extrae predictoras en presencias · background (área de calibración)
        │   · colinealidad (VIF/corr) · folds CV espacial adaptativo
        ▼
[05] MODELADO (ensemble) ───────►  ensemble_models/*.joblib (+ _cv_preds.parquet)
        │   GLM · GAM · RF · GBM · MaxEnt → equal-weight (TSS≥0.5)
        ▼
[06] VALIDACIÓN  ───────────────►  outputs/tables/metrics_*.csv
        │   TSS · AUC · Boyce · Brier · MESS · SD folds/algos
        ▼
[07] IDONEIDAD PRESENTE ────────►  outputs/maps/*_present_suitability.tif
        │   (recortada al área de predicción)
        │   [07_forecast_2050.py → diferido]
        ▼
[08] MAPAS / FIGURAS ───────────►  outputs/figures/*.png
```

**Lo que cambia en V4 respecto a iter. 3.1:**

| Etapa | Iter. 3.1 (Chile) | V4 (Sudamérica) |
|---|---|---|
| 01 Limpieza | sin filtro geográfico (limpia global) | **filtra a Sudamérica** según el dataset |
| 04 Extracción | background + presencias recortados a **Chile** | área de calibración = **Sudamérica** (🔴 a confirmar) |
| 07 Predicción | mapa recortado a Sudamérica | igual (Sudamérica) |

---

## 1. ETAPA 01 — Limpieza de ocurrencias

### 1.1 Contrato (entrada → salida)

| | Detalle |
|---|---|
| **Entrada** | `gbif_distribucion_especies.xlsx`, hoja `Registros GBIF` (13.354 × 15). |
| **Salida** | `data/processed/ocurrencias_limpias.gpkg`, capa `ocurrencias`, CRS EPSG:4326. |
| **Columnas mínimas de salida** | `especie, grupo, lon, lat, ano, pais, geometry` (+ metadatos: nombre_cientifico, incertidumbre_m, region, localidad, fecha, tipo_registro, institucion, dataset, catalogo, gbif_id). |

**Carga y normalización de columnas** (`utils.load_raw_occurrences`): el `.xlsx` se lee y
se renombran las columnas a snake_case ASCII estable:

```
Especie → especie · Nombre cientifico GBIF → nombre_cientifico · Latitud → lat
Longitud → lon · Incertidumbre (m) → incertidumbre_m · Pais → pais
Region / Estado → region · ... · GBIF ID → gbif_id
```

### 1.2 Flujo de pasos (orden REAL de ejecución en `main()`)

> Nota: el docstring numera los pasos 1–7 en otro orden; este es el **orden real** en que
> se ejecutan. El nº entre paréntesis es la numeración del docstring.

```
construir_geodataframe   (descarta lat/lon nulas, crea Point, EPSG:4326)
  │
  ├─[A] eliminar_duplicados        (paso 1)  dedup exacto (especie, lat, lon)
  ├─[B] filtrar_incertidumbre      (paso 2)  descarta incertidumbre_m > 10.000 m (NaN se conserva)
  ├─[C] filtrar_coords_sospechosas (paso 5)  (0,0) · |lat|>90 / |lon|>180 · decimales .0 · NaN
  ├─[★] FILTRO SUDAMÉRICA          (NUEVO)   ← se inserta aquí (ver §1.3)
  ├─[D] filtrar_centroides_admin   (paso 3)  elimina puntos a ≤1 km de centroides país/región (Natural Earth)
  ├─[E] filtrar_oceano             (paso 4)  descarta puntos fuera de la land mask (Natural Earth)
  ├─[F] thinning_espacial          (paso 6)  1 punto por celda 2.5′ (1/24°) por especie
  └─[G] asignar_grupos             (paso 7)  grupo A/B/C según conteos POST-thinning
  │
  ▼
_reportar_por_especie  →  guardar .gpkg
```

**Detalle de cada paso:**

- **[A] Duplicados** — `drop_duplicates(["especie","lat","lon"], keep="first")`.
- **[B] Incertidumbre** — descarta `incertidumbre_m > config.MAX_COORD_UNCERTAINTY_M`
  (10.000 m, coherente con la celda de ~5 km). Los registros **sin** incertidumbre (NaN)
  se conservan (son mayoría en GBIF).
- **[C] Coords sospechosas** — elimina: NaN, `(0,0)` exacto, fuera de rango, y registros
  con **lat y lon ambas con decimal .0** (truncadas a entero → baja precisión).
- **[D] Centroides administrativos** — calcula la distancia geodésica de cada punto a los
  centroides de países (admin-0, 110m) y provincias (admin-1, 10m) de Natural Earth; si
  cae a ≤ `config.CENTROID_TOLERANCE_KM` (1 km) se descarta (artefacto de geocoding). Es
  el paso **más caro** (O(n × nº centroides) geodésicas). Si no hay capas NE, se omite con
  aviso.
- **[E] Océano** — `sjoin` `within` contra la unión de polígonos de tierra de Natural
  Earth; descarta puntos en mar. Si no hay capa, se omite con aviso.
- **[F] Thinning espacial** — asigna cada punto a su celda de la grilla 2.5′ y conserva
  **1 punto por (especie, celda)**, prefiriendo el de menor incertidumbre. Reduce el sesgo
  de sobre-muestreo. **Es el paso que más reduce el n** (en iter. 1: 13.354 → 4.566 global).
- **[G] Grupos A/B/C** — `config.classify_species` sobre los conteos **después** del
  thinning: `C` si n < 50; `A` si está en la lista de cosmopolitas/introducidas con n
  suficiente; `B` resto (endémicas con datos). El grupo C se marca pero **no se descarta**
  del `.gpkg` (se excluye en modelado).

### 1.3 ★ NUEVO PASO: filtro Sudamérica

**Qué.** Conservar solo registros dentro de Sudamérica. Es el cambio central de V4 en esta
etapa.

**Dónde.** Insertado **después de [C] coords sospechosas y antes de [D] centroides**.
Motivo: reduce el dataset (13.354 → ~8.498, −36%) *antes* de los pasos caros (centroides y
océano), así que acelera el resto y todo lo posterior opera solo sobre Sudamérica.

**Cómo — dos métodos posibles (en este dataset son EQUIVALENTES):**

| Método | Criterio | Resultado en este dataset |
|---|---|---|
| **Por geometría** | punto dentro del polígono / bbox de Sudamérica (`config.PREDICTION_BBOX = (-82, -56, -34, 13)`) | 8.498 registros |
| **Por país** | columna `pais` ∈ {Chile, Colombia, Bolivia, Perú, Argentina, Brasil, Ecuador, Paraguay, …} | 8.498 registros |

> **Verificado contra el dataset:** ambos métodos seleccionan **exactamente los mismos
> 8.498 registros** (coincidencia 100%, 0 discrepancias). No hay registros "país-SA fuera
> del bbox" ni "en bbox con país no-SA".

🔴 **DECISIÓN DE AUDITORÍA 1 — método del filtro.** Como dan el mismo resultado, recomiendo
**por geometría** (polígono Natural Earth de Sudamérica, con `PREDICTION_BBOX` de respaldo),
porque: (a) el resto del pipeline es coordenada-dependiente, no usa el país; (b) es robusto
ante etiquetas de país ausentes o erróneas en descargas futuras; (c) reutiliza la lógica ya
existente de máscara en la etapa 04 (`_load_calibration_mask`). El criterio "por país" es
igual de válido hoy y literalmente "según el dataset"; queda como alternativa si lo
prefieres.

### 1.4 Implicación clave: cambia el set de especies modelables

Con alcance Sudamérica (más amplio que Chile), los conteos por especie cambian y, por tanto,
la clasificación A/B/C. **Conteos crudos por especie dentro de Sudamérica** (antes de
limpieza/thinning; el thinning los reducirá ~½):

| Especie | total | en Sudamérica | ¿modelable en Chile (3.1)? | ¿candidata en V4? |
|---|--:|--:|:--:|:--:|
| Nolana divaricata | 3000 | 3000 | sí | sí |
| Schinus areira | 3000 | 1135 | sí (n=72 Chile) | sí (más datos) |
| Encelia canescens | 708 | 708 | sí | sí |
| Nolana sedifolia | 431 | 431 | sí | sí |
| Krameria cistoidea | 409 | 408 | sí | sí |
| Eulychnia acida | 375 | 375 | sí | sí |
| **Nolana albescens** | 365 | 365 | **no (fuera de scope)** | **sí (NUEVA)** |
| Cumulopuntia sphaerica | 302 | 302 | sí | sí |
| Neltuma chilensis | 296 | 262 | sí | sí |
| Oxalis gigantea | 294 | 293 | sí | sí |
| Skytanthus acutus | 294 | 294 | sí | sí |
| Miqueliopuntia miquelii | 288 | 287 | sí | sí |
| Senna cumingii | 204 | 204 | sí | sí |
| Pleurophora pungens | 144 | 144 | sí | sí |
| **Dinemagonum gayanum** | 65 | 65 | **no** | **sí (NUEVA, revisar tras thinning)** |
| **Nolana rostrata** | 62 | 60 | **no** | **posible (cerca del piso de 50)** |
| **Atriplex semibaccata** | 3000 | **48** | no (n=8 Chile) | **no (sigue < 50)** |
| Atriplex deserticola | 44 | 44 | no | no (< 50) |
| Aloysia salviifolia | 39 | 39 | no | no |
| Caesalpinia angulata | 28 | 28 | no | no |
| Centaurea chilensis | 6 | 6 | no | no |

**Lectura:** ampliar a Sudamérica **suma especies modelables** (al menos *Nolana
albescens*; posiblemente *Dinemagonum gayanum* y *Nolana rostrata* según queden tras el
thinning) y **da más datos** a *Schinus areira*. *Atriplex semibaccata* sigue por debajo
del piso de 50 incluso en Sudamérica (48 crudos → quedará excluida igual). La clasificación
A/B/C debe **recalcularse sobre los conteos de Sudamérica post-thinning**, no heredarse de
3.1.

🔴 **DECISIÓN DE AUDITORÍA 2 — área de calibración.** En 3.1 la etapa 04 recorta el
background y las presencias a **Chile**. Si el alcance es ahora Sudamérica, hay que decidir:
- **(a) Calibrar en toda Sudamérica:** usar todas las presencias SA + background en SA.
  Aprovecha Colombia/Bolivia/Perú/Argentina/Brasil; cambia el "área accesible".
- **(b) Mantener calibración en Chile**, solo limpiar a SA: las presencias fuera de Chile
  se descartarían en la etapa 04 (poco aporta limpiar a SA).

Recomiendo **(a)** para que el cambio de alcance sea coherente de punta a punta. Esto NO se
toca en la etapa 01 (solo limpieza); lo registro aquí porque condiciona la etapa 04 y la
clasificación de especies. *(No bloquea el inicio de la etapa 01.)*

### 1.5 Estructura modular propuesta para la etapa 01

Misma lógica, separada en módulos pequeños y testeables (en vez de un único script de 600
líneas). Propuesta a auditar:

```
src/limpieza/
  io.py            # cargar xlsx + normalizar columnas (hoy en utils.load_raw_occurrences)
  geo_scope.py     # ★ filtro Sudamérica (geometría o país)  ← NUEVO
  dedup.py         # duplicados exactos
  uncertainty.py   # filtro de incertidumbre
  coords.py        # coords sospechosas/inválidas
  centroids.py     # centroides admin (Natural Earth)
  ocean.py         # máscara de tierra/océano
  thinning.py      # thinning 2.5′ por especie
  grouping.py      # clasificación A/B/C
  pipeline.py      # orquesta el orden y reporta
scripts/01_limpieza.py   # CLI delgado que llama a src.limpieza.pipeline
```

Cada módulo: una función pura `gdf → gdf` con su log de "n antes → n después", igual que hoy.

### 1.6 Cómo auditar/verificar la salida de esta etapa

Checks reproducibles sobre `ocurrencias_limpias.gpkg`:

- [ ] **0 registros fuera de Sudamérica** (todos los `pais` ∈ lista SA **y** todas las
      coords dentro de `PREDICTION_BBOX`).
- [ ] **0 lat/lon NaN**, 0 fuera de rango, 0 en `(0,0)`.
- [ ] **0 puntos en océano** (intersección con land mask).
- [ ] **1 punto máx. por celda 2.5′ por especie** (thinning correcto).
- [ ] Conteo final por especie y **grupo A/B/C recalculado** sobre SA post-thinning.
- [ ] Tabla "inicial → final, % retenido" por especie en el log.

---

## Etapas 2–8

*(pendientes de auditar — se documentarán aquí en orden a medida que avancemos)*
