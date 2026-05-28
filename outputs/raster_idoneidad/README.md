# raster_idoneidad — Mapas de idoneidad de hábitat (21 especies)

GeoTIFF de idoneidad de hábitat proyectados a **Sudamérica**, listos para abrir en
QGIS / ArcGIS (arrástralos al lienzo). Un archivo por especie:
`{especie}_idoneidad_sa.tif`.

## Formato del raster

| Propiedad | Valor |
|---|---|
| CRS | EPSG:4326 (lon/lat) |
| Dimensiones | 1656 × 1152 píxeles |
| Resolución | ~0.0417° (2.5 arc-min, ≈5 km) |
| Extensión | lon [-82, -34], lat [-56, 13] (Sudamérica) |
| Tipo | Float32, banda única |
| Rango de valores | 0–1 (mayor = más idóneo) |
| NoData | NaN |

Todos los archivos comparten exactamente la misma grilla, así que se superponen
celda a celda.

## Las 21 especies y su método

Se modelan según el número de registros disponibles en Sudamérica (n). El umbral
de modelado individual es n ≥ 50.

**16 especies viables (n ≥ 50) — ensemble SDM completo** (GLM, GAM, RF, GBM,
MaxEnt; combinación ponderada por TSS-CV espacial):
Atriplex semibaccata, Caesalpinia angulata, Centaurea chilensis,
Cumulopuntia sphaerica, Encelia canescens, Eulychnia acida, Krameria cistoidea,
Miqueliopuntia miquelii, Neltuma chilensis, Nolana divaricata, Nolana sedifolia,
Oxalis gigantea, Pleurophora pungens, Schinus areira, Senna cumingii,
Skytanthus acutus.

**4 especies con pocos registros (n 25–49) — MaxEnt regularizado, BAJA CONFIANZA**
(features linear+product, beta=3.0, para evitar sobreajuste):
Aloysia salviifolia (n=27), Atriplex deserticola (n=33), Dinemagonum gayanum
(n=46), Nolana rostrata (n=47).

**1 especie con muy pocos registros (n < 25) — extensión de ocurrencia (NO es un
SDM):** Nolana albescens (n=16). Superficie = 1.0 dentro del polígono convexo de
las presencias, con decaimiento gaussiano (escala 50 km) hacia afuera. Es un mapa
de **rango**, no de idoneidad ambiental: con tan pocos puntos no hay base para
ajustar un nicho.

El detalle por especie (método y nivel de confianza) está en
`confianza_idoneidad_por_especie.csv`.

## Cómo se regeneran

- 16 viables: `python scripts/07_predecir_sudamerica.py`
- 5 restantes (low-n + EOO): `python scripts/07c_low_n_eoo.py`
