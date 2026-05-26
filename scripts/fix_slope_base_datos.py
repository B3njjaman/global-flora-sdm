"""
fix_slope_base_datos.py — Corrección del bug de `slope` y export auditable.

Problema corregido
-------------------
03_terrain.py derivaba la pendiente con xrspatial.slope() sobre una grilla
geográfica (EPSG:4326, coordenadas en GRADOS) mientras la elevación está en
METROS. xrspatial asume coordenadas proyectadas (metros), de modo que el
gradiente dz/dx queda gigantesco (metros / grado) y atan(gigante) ≈ 90°.
Resultado: ~99% del planeta con pendiente ~90° (acantilado vertical), físicamente
imposible.

Solución
--------
Recalcula la pendiente con el algoritmo de Horn (1981) — el mismo de gdaldem —
pero convirtiendo el tamaño de celda de grados a metros con escala dependiente
de la latitud:

    m_por_grado_lat ≈ 111320
    m_por_grado_lon ≈ 111320 · cos(lat)

    dz/dx = ((c+2f+i) − (a+2d+g)) / (8 · csx)
    dz/dy = ((g+2h+i) − (a+2b+c)) / (8 · csy)
    slope = atan( sqrt( (dz/dx)² + (dz/dy)² ) )  [grados]

Qué hace este script
---------------------
1. Carga la elevación alineada (la misma fuente que ya está en los datasets).
2. Recalcula `slope` (grados) en cada punto de cada dataset por especie.
3. Sobrescribe la columna `slope` en cada {slug}.parquet (esquema intacto).
4. Exporta TODAS las especies apiladas a outputs/tables/base_de_datos_completa.csv,
   conservando la columna `slope_bug_original` para que la corrección sea auditable.

NOTA: este script NO regenera el raster slope.tif global ni reentrena los modelos
(.joblib). Eso es un paso aparte (ver mensaje final). Aquí solo se corrige la
base de datos modelable.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import rowcol

import config
import utils

logger = utils.get_logger("fix_slope")

M_PER_DEG = 111_320.0  # metros por grado de latitud (aprox. esférica)


def load_elevation() -> tuple[np.ndarray, rasterio.Affine, float, float]:
    """Carga la elevación alineada a memoria con nodata → NaN."""
    path = config.RASTERS_ALIGNED / "elevation.tif"
    with rasterio.open(path) as src:
        elev = src.read(1).astype(np.float32)
        if src.nodata is not None and not np.isnan(src.nodata):
            elev = np.where(elev == src.nodata, np.nan, elev)
        transform = src.transform
    res_x_deg = abs(transform.a)
    res_y_deg = abs(transform.e)
    logger.info("Elevación cargada: %s  res=%.6f°", elev.shape, res_x_deg)
    return elev, transform, res_x_deg, res_y_deg


def horn_slope_at_points(
    lons: np.ndarray,
    lats: np.ndarray,
    elev: np.ndarray,
    transform: rasterio.Affine,
    res_x_deg: float,
    res_y_deg: float,
) -> np.ndarray:
    """Calcula la pendiente (grados) de Horn en cada (lon, lat).

    Toma la ventana 3×3 de elevación alrededor de cada punto; los vecinos NaN
    (costa, borde) se rellenan con el valor central para no propagar NaN.
    """
    ny, nx = elev.shape
    rows, cols = rowcol(transform, lons, lats)
    r = np.clip(np.asarray(rows, dtype=int), 0, ny - 1)
    c = np.clip(np.asarray(cols, dtype=int), 0, nx - 1)

    def nb(di: int, dj: int) -> np.ndarray:
        rr = np.clip(r + di, 0, ny - 1)
        cc = np.clip(c + dj, 0, nx - 1)
        return elev[rr, cc]

    center = nb(0, 0)

    def fill(x: np.ndarray) -> np.ndarray:
        return np.where(np.isnan(x), center, x)

    a = fill(nb(-1, -1)); b = fill(nb(-1, 0)); cc = fill(nb(-1, 1))
    d = fill(nb(0, -1));                        f = fill(nb(0, 1))
    g = fill(nb(1, -1));  h = fill(nb(1, 0));   i = fill(nb(1, 1))

    csx = res_x_deg * M_PER_DEG * np.cos(np.deg2rad(lats))
    csx = np.where(np.abs(csx) < 1.0, np.nan, csx)  # cerca de los polos: indefinido
    csy = res_y_deg * M_PER_DEG

    dzdx = ((cc + 2 * f + i) - (a + 2 * d + g)) / (8 * csx)
    dzdy = ((g + 2 * h + i) - (a + 2 * b + cc)) / (8 * csy)

    slope_deg = np.degrees(np.arctan(np.sqrt(dzdx ** 2 + dzdy ** 2)))
    slope_deg = np.where(np.isnan(center), np.nan, slope_deg)
    return slope_deg.astype(np.float32)


def main() -> None:
    elev, transform, res_x_deg, res_y_deg = load_elevation()

    combined: list[pd.DataFrame] = []
    parquets = sorted(
        p for p in config.SPECIES_DATASETS.glob("*.parquet")
        if not p.stem.endswith("_cv_preds")
    )
    logger.info("Datasets a corregir: %d", len(parquets))

    for pq in parquets:
        slug = pq.stem
        df = pd.read_parquet(pq)

        slope_old = df["slope"].to_numpy(dtype=np.float32, copy=True)
        slope_new = horn_slope_at_points(
            df["lon"].to_numpy(float), df["lat"].to_numpy(float),
            elev, transform, res_x_deg, res_y_deg,
        )

        df["slope"] = slope_new              # corregir esquema canónico
        df.to_parquet(pq, index=False)       # sobrescribir dataset

        logger.info(
            "%-26s slope: antes mediana=%.2f° (>%d°: %.0f%%)  →  ahora mediana=%.2f° max=%.2f°",
            slug, float(np.nanmedian(slope_old)), 80,
            100 * np.nanmean(slope_old > 80), float(np.nanmedian(slope_new)),
            float(np.nanmax(slope_new)),
        )

        export = df.copy()
        export.insert(0, "slug", slug)
        export["slope_bug_original"] = slope_old  # para auditar la corrección
        combined.append(export)

    all_df = pd.concat(combined, ignore_index=True)
    out = config.TABLES / "base_de_datos_completa.csv"
    utils.ensure_dirs(config.TABLES)
    all_df.to_csv(out, index=False)
    logger.info("=" * 70)
    logger.info("Base de datos completa exportada: %s  (%d filas, %d columnas)",
                out, len(all_df), all_df.shape[1])
    logger.info("Especies: %d  |  presencias: %d  |  background: %d",
                all_df["slug"].nunique(),
                int((all_df["presence"] == 1).sum()),
                int((all_df["presence"] == 0).sum()))


if __name__ == "__main__":
    main()
