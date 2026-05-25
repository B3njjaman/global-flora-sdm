"""
04_extraccion.py — Etapa 3: Dataset modelable por especie.

Para cada especie modelable (grupos A y B según config.classify_species):
  1. Extrae valores de los rasters alineados en los puntos de presencia.
  2. Genera puntos de background en tierra (con o sin target-group background).
  3. Filtra predictores colineales (|r| > config.CORR_THRESHOLD o VIF > config.VIF_THRESHOLD).
  4. Asigna bloques espaciales para validación cruzada (spatial block CV).
  5. Guarda {slug}.parquet y {slug}_predictors.json en config.SPECIES_DATASETS.

Uso
---
    python 04_extraccion.py               # procesa todas las especies modelables
    python 04_extraccion.py --species "Nolana divaricata"

Dependencias externas
---------------------
    geopandas, rasterio, numpy, pandas, pyarrow (parquet), scipy, statsmodels

Referencias metodológicas
--------------------------
    - Phillips et al. (2009) — target-group background para corregir sesgo GBIF.
    - Roberts et al. (2017) — spatial block CV.
    - Valavi et al. (2019) — blockCV.
    - VIF (O'Brien 2007) iterativo: eliminar la variable con VIF más alto hasta que
      todas estén por debajo del umbral.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import rowcol

import config
import utils

logger = utils.get_logger("04_extraccion")


# ---------------------------------------------------------------------------
# Extracción de valores raster en puntos
# ---------------------------------------------------------------------------

def _open_rasters(variables: list[str]) -> dict[str, rasterio.DatasetReader]:
    """Abre los rasters alineados para cada predictor.

    Devuelve un dict {nombre_var: DatasetReader}. Los DatasetReader deben
    cerrarse explícitamente por el llamador.
    """
    rasters: dict[str, rasterio.DatasetReader] = {}
    for var in variables:
        tif_path = config.RASTERS_ALIGNED / f"{var}.tif"
        if not tif_path.exists():
            raise FileNotFoundError(
                f"Raster alineado no encontrado: {tif_path}. "
                "Ejecuta primero 02_capas_presente.py y 03_terrain.py."
            )
        rasters[var] = rasterio.open(tif_path)
    return rasters


def _close_rasters(rasters: dict[str, rasterio.DatasetReader]) -> None:
    """Cierra todos los DatasetReader abiertos."""
    for ds in rasters.values():
        ds.close()


def extract_raster_values(
    lons: np.ndarray,
    lats: np.ndarray,
    rasters: dict[str, rasterio.DatasetReader],
) -> pd.DataFrame:
    """Extrae valores de cada raster en las coordenadas (lon, lat) dadas.

    Parámetros
    ----------
    lons, lats : arrays 1-D de float con coordenadas en EPSG:4326.
    rasters    : dict {nombre_var: rasterio.DatasetReader}.

    Retorna
    -------
    DataFrame con una columna por variable; NaN donde la lectura falla o
    cae fuera de la extensión/máscara del raster.
    """
    n = len(lons)
    data: dict[str, np.ndarray] = {}

    for var, ds in rasters.items():
        values = np.full(n, np.nan, dtype=np.float32)
        band = ds.read(1, masked=True)  # masked array: nodata → mask=True
        transform = ds.transform

        rows, cols = rowcol(transform, lons, lats)
        rows = np.asarray(rows)
        cols = np.asarray(cols)

        # Filtrar índices dentro de la extensión del raster
        valid = (
            (rows >= 0) & (rows < ds.height)
            & (cols >= 0) & (cols < ds.width)
        )
        r_valid = rows[valid]
        c_valid = cols[valid]

        extracted = band[r_valid, c_valid]
        # Si es masked array, convertir máscara a NaN
        if np.ma.is_masked(extracted):
            vals = np.where(extracted.mask, np.nan, extracted.data).astype(np.float32)
        else:
            vals = extracted.astype(np.float32)

        values[valid] = vals
        data[var] = values

    return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# Máscara de tierra y muestreo de background
# ---------------------------------------------------------------------------

def _load_land_mask() -> tuple[np.ndarray, rasterio.DatasetReader]:
    """Carga la máscara de tierra (land_mask.tif) en config.WORLDCLIM_PRESENT.

    Retorna (array_bool_tierra, DatasetReader).
    El DatasetReader debe cerrarse por el llamador.
    """
    mask_path = config.WORLDCLIM_PRESENT / "land_mask.tif"
    if not mask_path.exists():
        raise FileNotFoundError(
            f"Máscara de tierra no encontrada: {mask_path}. "
            "Genera land_mask.tif en la etapa 02_capas_presente.py."
        )
    ds = rasterio.open(mask_path)
    arr = ds.read(1)
    # Tierra = valores > 0 (convenio: 1 = tierra, 0/nodata = mar)
    land = (arr > 0)
    return land, ds


def _raster_land_coords(
    land: np.ndarray,
    ds: rasterio.DatasetReader,
) -> tuple[np.ndarray, np.ndarray]:
    """Convierte los píxeles de tierra de la máscara a coordenadas (lon, lat).

    Retorna (lons_tierra, lats_tierra) como arrays 1-D.
    """
    rows, cols = np.where(land)
    # Centro de píxel
    lons, lats = ds.xy(rows, cols)
    return np.array(lons), np.array(lats)


def sample_random_background(
    n: int,
    land: np.ndarray,
    land_ds: rasterio.DatasetReader,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """Muestrea n puntos aleatorios uniformes en tierra.

    Parámetros
    ----------
    n       : número de puntos deseados.
    land    : array booleano (tierra=True) del land_mask.
    land_ds : DatasetReader de land_mask.tif (para transformación).
    rng     : generador numpy para reproducibilidad.

    Retorna
    -------
    (lons, lats) como arrays 1-D de longitud min(n, n_celdas_tierra).
    """
    land_rows, land_cols = np.where(land)
    n_available = len(land_rows)
    if n > n_available:
        logger.warning(
            "Se pidieron %d puntos background pero solo hay %d celdas de tierra; "
            "se usarán todas.", n, n_available
        )
        n = n_available

    idx = rng.choice(n_available, size=n, replace=False)
    rows = land_rows[idx]
    cols = land_cols[idx]
    lons, lats = land_ds.xy(rows, cols)

    # Añadir jitter sub-píxel para que los puntos no queden exactamente en
    # el centro de celda (evita duplicación perfecta con presencias thinneadas)
    res_x = land_ds.transform.a   # ancho de píxel en grados
    res_y = abs(land_ds.transform.e)  # alto de píxel en grados
    lons = np.array(lons) + rng.uniform(-res_x / 2, res_x / 2, size=n)
    lats = np.array(lats) + rng.uniform(-res_y / 2, res_y / 2, size=n)

    return lons, lats


def sample_target_group_background(
    n: int,
    land: np.ndarray,
    land_ds: rasterio.DatasetReader,
    all_presences: gpd.GeoDataFrame,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """Muestrea background con probabilidad proporcional al esfuerzo de muestreo GBIF.

    Aproximación (Phillips et al. 2009, Ecol. Appl.)
    ------------------------------------------------
    El "esfuerzo de muestreo" se aproxima con la densidad de registros del
    dataset completo de GBIF (todas las especies cargadas en
    config.OCCURRENCES_CLEAN) por celda raster. Esta es una proxy razonable
    cuando no se dispone de una lista separada de Plantae de GBIF: asume que
    el sesgo espacial del dataset es representativo del sesgo general del
    colector.

    Supuesto clave: las regiones con más registros en cualquier especie del
    dataset tienen mayor probabilidad de que un observador haya visitado esa
    celda. Por tanto, los puntos de background deben muestrearse con mayor
    densidad en esas regiones para reflejar dónde se habría registrado una
    especie *si estuviera presente*.

    Algoritmo
    ---------
    1. Contar registros de todas las presencias por celda del raster (row, col).
    2. Normalizar a distribución de probabilidad (suma = 1).
    3. Muestrear n celdas con esa distribución.
    4. Añadir jitter sub-píxel.

    Si una celda no cae en tierra (land_mask), se redibuja hasta completar n
    puntos (con un límite de iteraciones para evitar bucle infinito).

    Parámetros
    ----------
    n              : número de puntos deseados.
    land           : array booleano tierra del land_mask.
    land_ds        : DatasetReader de land_mask.tif.
    all_presences  : GeoDataFrame con TODAS las presencias del dataset (proxy
                     de esfuerzo). Debe tener columnas 'lon' y 'lat'.
    rng            : generador numpy para reproducibilidad.

    Retorna
    -------
    (lons, lats) como arrays 1-D.
    """
    logger.info(
        "Target-group background: calculando densidad de esfuerzo sobre %d registros.",
        len(all_presences),
    )

    transform = land_ds.transform
    height = land_ds.height
    width = land_ds.width

    # Convertir todas las presencias a índices de píxel
    all_lons = all_presences["lon"].values
    all_lats = all_presences["lat"].values
    p_rows, p_cols = rowcol(transform, all_lons, all_lats)
    p_rows = np.clip(np.asarray(p_rows), 0, height - 1)
    p_cols = np.clip(np.asarray(p_cols), 0, width - 1)

    # Contar registros por celda en tierra
    # Usamos un array 2-D de conteos
    effort = np.zeros((height, width), dtype=np.float64)
    for r, c in zip(p_rows, p_cols):
        if land[r, c]:
            effort[r, c] += 1.0

    # Suavizar con una pequeña constante para que celdas con 0 registros
    # también tengan probabilidad no nula (evitar sobre-concentración)
    effort[land] += 1.0  # Laplace smoothing solo en tierra

    # Distribución de probabilidad sobre celdas de tierra
    land_rows, land_cols = np.where(land)
    probs = effort[land_rows, land_cols]
    probs /= probs.sum()

    n_available = len(land_rows)
    if n > n_available:
        logger.warning(
            "Se pidieron %d puntos background; solo hay %d celdas en tierra.",
            n, n_available,
        )
        n = n_available

    idx = rng.choice(n_available, size=n, replace=True, p=probs)
    rows = land_rows[idx]
    cols = land_cols[idx]
    lons_bg, lats_bg = land_ds.xy(rows, cols)

    res_x = land_ds.transform.a
    res_y = abs(land_ds.transform.e)
    lons_bg = np.array(lons_bg) + rng.uniform(-res_x / 2, res_x / 2, size=n)
    lats_bg = np.array(lats_bg) + rng.uniform(-res_y / 2, res_y / 2, size=n)

    return lons_bg, lats_bg


# ---------------------------------------------------------------------------
# Selección de predictores: correlación + VIF iterativo
# ---------------------------------------------------------------------------

def _pearson_corr_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula la matriz de correlación de Pearson sobre columnas numéricas."""
    return df.corr(method="pearson")


def _vif_series(df: pd.DataFrame) -> pd.Series:
    """Calcula el VIF de cada columna de df usando regresión OLS múltiple.

    VIF_j = 1 / (1 − R²_j), donde R²_j es el coeficiente de determinación
    de la regresión de la variable j sobre el resto.

    Implementación propia para evitar dependencia de statsmodels.variance_inflation_factor
    (aunque se usa statsmodels.OLS para el ajuste).
    """
    from statsmodels.regression.linear_model import OLS
    from statsmodels.tools import add_constant

    cols = df.columns.tolist()
    vifs: dict[str, float] = {}
    for col in cols:
        y = df[col].values
        X = df.drop(columns=[col]).values
        X = add_constant(X)
        try:
            res = OLS(y, X).fit()
            r2 = res.rsquared
            vifs[col] = 1.0 / (1.0 - r2) if r2 < 1.0 else np.inf
        except Exception:
            vifs[col] = np.inf
    return pd.Series(vifs, name="VIF")


def select_predictors(
    presence_df: pd.DataFrame,
    candidates: list[str],
    corr_threshold: float = config.CORR_THRESHOLD,
    vif_threshold: float = config.VIF_THRESHOLD,
) -> list[str]:
    """Selecciona predictores no colineales por correlación y VIF iterativo.

    Estrategia de selección: **global/compartida entre especies**
    ------------------------------------------------------------
    La selección se realiza sobre el subconjunto de presencias de la especie
    en cuestión. Esto garantiza que la selección refleja la variabilidad del
    espacio ambiental muestreado por esa especie. Sin embargo, para maximizar
    la comparabilidad entre especies en el ensemble final, se recomienda
    revisar si el subconjunto final converge hacia un conjunto común.

    Procedimiento
    -------------
    1. Eliminar variables con |r| > corr_threshold con cualquier otra de mayor
       varianza (heurística: retener la del par con mayor varianza, así se
       mantiene información); en caso de empate se descarta la segunda (orden
       en la lista).
    2. Calcular VIF sobre el subconjunto restante; eliminar la variable con
       VIF más alto si supera vif_threshold. Repetir hasta convergencia.

    Parámetros
    ----------
    presence_df    : DataFrame con columnas = candidates (solo presencias).
    candidates     : lista inicial de predictores.
    corr_threshold : umbral |r|.
    vif_threshold  : umbral VIF.

    Retorna
    -------
    Lista de predictores seleccionados.
    """
    subset = presence_df[candidates].dropna()
    if len(subset) == 0:
        logger.warning("Sin datos para selección de predictores; se retorna la lista completa.")
        return candidates[:]

    retained = candidates[:]

    # --- Paso 1: filtro por correlación ---
    corr = _pearson_corr_matrix(subset[retained])
    to_drop: set[str] = set()
    for i, col_i in enumerate(retained):
        if col_i in to_drop:
            continue
        for j in range(i + 1, len(retained)):
            col_j = retained[j]
            if col_j in to_drop:
                continue
            if abs(corr.loc[col_i, col_j]) > corr_threshold:
                # Descartar la variable con menor varianza (retener más informativa)
                var_i = subset[col_i].var()
                var_j = subset[col_j].var()
                drop_col = col_j if var_i >= var_j else col_i
                to_drop.add(drop_col)
                logger.debug(
                    "Correlación |r|=%.3f entre %s y %s → descartando %s",
                    abs(corr.loc[col_i, col_j]), col_i, col_j, drop_col,
                )

    retained = [c for c in retained if c not in to_drop]
    logger.info(
        "Tras filtro de correlación (|r|>%.2f): %d predictores retenidos de %d.",
        corr_threshold, len(retained), len(candidates),
    )

    # --- Paso 2: eliminación iterativa por VIF ---
    max_iter = len(retained)  # cota superior de iteraciones
    for _ in range(max_iter):
        if len(retained) <= 1:
            break
        data_subset = subset[retained].dropna()
        if len(data_subset) < len(retained) + 1:
            logger.warning("Muy pocos datos para calcular VIF; deteniendo eliminación.")
            break
        vif = _vif_series(data_subset)
        max_vif = vif.max()
        if max_vif <= vif_threshold:
            break
        worst = vif.idxmax()
        logger.debug("VIF máximo: %.2f en '%s' → descartando.", max_vif, worst)
        retained.remove(worst)

    logger.info(
        "Tras eliminación VIF (umbral=%.1f): %d predictores finales: %s",
        vif_threshold, len(retained), retained,
    )
    return retained


# ---------------------------------------------------------------------------
# Spatial block CV
# ---------------------------------------------------------------------------

def assign_spatial_blocks(
    lons: np.ndarray,
    lats: np.ndarray,
    block_size_km: float = config.SPATIAL_BLOCK_KM,
    n_folds: int = config.N_CV_FOLDS,
    rng: Optional[np.random.Generator] = None,
) -> np.ndarray:
    """Asigna cada punto a un fold de validación cruzada espacial por bloques.

    Método: grilla regular en grados (aproximada a block_size_km usando la
    conversión 1° ≈ 111 km) sobre el rango lon/lat del conjunto de puntos.
    Cada celda de la grilla recibe un bloque_id; los bloques se asignan
    aleatoriamente (sin remplazo, con repetición cíclica si hay más bloques
    que folds) a uno de los n_folds folds.

    Diseño
    ------
    - No requiere dependencias externas (spacv) pero produce bloques
      equivalentes: grupos espacialmente contiguos de tamaño ~block_size_km.
    - La asignación aleatoria de bloques a folds garantiza que cada fold
      contenga bloques dispersos geográficamente → evita gradientes sistemáticos.
    - Puntos fuera del rango global (lon ∈ [-180,180], lat ∈ [-90,90]) se
      asignan al fold 0 por defecto.

    Parámetros
    ----------
    lons, lats     : arrays 1-D de coordenadas.
    block_size_km  : tamaño aproximado del bloque en km.
    n_folds        : número de folds.
    rng            : generador numpy; si es None se crea uno con RANDOM_SEED.

    Retorna
    -------
    Array entero 1-D (misma longitud que lons/lats) con valores en [0, n_folds-1].
    """
    if rng is None:
        rng = np.random.default_rng(config.RANDOM_SEED)

    # Tamaño de bloque en grados (1° ≈ 111.32 km)
    block_deg = block_size_km / 111.32

    lon_min, lon_max = -180.0, 180.0
    lat_min, lat_max = -90.0, 90.0

    n_lon_blocks = max(1, int(np.ceil((lon_max - lon_min) / block_deg)))
    n_lat_blocks = max(1, int(np.ceil((lat_max - lat_min) / block_deg)))
    n_blocks_total = n_lon_blocks * n_lat_blocks

    logger.info(
        "Block CV: grilla %.0f km → %d×%d = %d bloques, %d folds.",
        block_size_km, n_lon_blocks, n_lat_blocks, n_blocks_total, n_folds,
    )

    # Asignación aleatoria de bloque_id → fold_id
    block_ids = np.arange(n_blocks_total)
    shuffled = rng.permutation(block_ids)
    block_to_fold = np.empty(n_blocks_total, dtype=int)
    for i, bid in enumerate(shuffled):
        block_to_fold[bid] = i % n_folds

    # Calcular índice de bloque para cada punto
    lon_idx = np.clip(
        ((np.asarray(lons) - lon_min) / block_deg).astype(int),
        0, n_lon_blocks - 1,
    )
    lat_idx = np.clip(
        ((np.asarray(lats) - lat_min) / block_deg).astype(int),
        0, n_lat_blocks - 1,
    )
    flat_block_id = lat_idx * n_lon_blocks + lon_idx

    cv_folds = block_to_fold[flat_block_id]
    return cv_folds


# ---------------------------------------------------------------------------
# Procesamiento por especie
# ---------------------------------------------------------------------------

def process_species(
    species: str,
    gdf_all: gpd.GeoDataFrame,
    rasters: dict[str, rasterio.DatasetReader],
    land: np.ndarray,
    land_ds: rasterio.DatasetReader,
    rng: np.random.Generator,
) -> None:
    """Construye y guarda el dataset modelable de una especie.

    Parámetros
    ----------
    species   : nombre científico exacto de la especie.
    gdf_all   : GeoDataFrame con TODAS las ocurrencias limpias.
    rasters   : dict {var: DatasetReader} de los predictores alineados.
    land      : array booleano tierra de land_mask.
    land_ds   : DatasetReader de land_mask.tif.
    rng       : generador numpy compartido (para reproducibilidad de semilla global).
    """
    slug = utils.slugify_species(species)
    out_parquet = config.SPECIES_DATASETS / f"{slug}.parquet"
    out_json = config.SPECIES_DATASETS / f"{slug}_predictors.json"

    logger.info("=" * 60)
    logger.info("Procesando: %s  (slug=%s)", species, slug)

    # ------------------------------------------------------------------
    # 1. Filtrar presencias de la especie
    # ------------------------------------------------------------------
    gdf_sp = gdf_all[gdf_all["especie"] == species].copy()
    logger.info("  Presencias cargadas: %d", len(gdf_sp))

    pres_lons = gdf_sp["lon"].values.astype(np.float64)
    pres_lats = gdf_sp["lat"].values.astype(np.float64)

    # ------------------------------------------------------------------
    # 2. Extraer valores de raster en presencias
    # ------------------------------------------------------------------
    logger.info("  Extrayendo predictores en presencias…")
    pres_vals = extract_raster_values(pres_lons, pres_lats, rasters)

    # Construir DataFrame de presencias
    df_pres = pd.DataFrame({
        "especie": species,
        "presence": 1,
        "lon": pres_lons,
        "lat": pres_lats,
    })
    df_pres = pd.concat([df_pres, pres_vals], axis=1)

    # Descartar presencias con NaN en cualquier predictor
    complete_mask = df_pres[config.PREDICTORS].notna().all(axis=1)
    n_before = len(df_pres)
    df_pres = df_pres[complete_mask].reset_index(drop=True)
    n_dropped = n_before - len(df_pres)
    if n_dropped > 0:
        logger.warning(
            "  Descartadas %d presencias con NaN en predictores (%d restantes).",
            n_dropped, len(df_pres),
        )
    logger.info("  Presencias con datos completos: %d", len(df_pres))

    if len(df_pres) == 0:
        logger.error(
            "  Sin presencias válidas para %s. Se omite esta especie.", species
        )
        return

    # ------------------------------------------------------------------
    # 3. Generar puntos de background
    # ------------------------------------------------------------------
    logger.info(
        "  Generando %d puntos background (target_group=%s)…",
        config.N_BACKGROUND, config.TARGET_GROUP_BACKGROUND,
    )

    if config.TARGET_GROUP_BACKGROUND:
        bg_lons, bg_lats = sample_target_group_background(
            n=config.N_BACKGROUND,
            land=land,
            land_ds=land_ds,
            all_presences=gdf_all,
            rng=rng,
        )
    else:
        bg_lons, bg_lats = sample_random_background(
            n=config.N_BACKGROUND,
            land=land,
            land_ds=land_ds,
            rng=rng,
        )

    # Extraer predictores en background
    logger.info("  Extrayendo predictores en background…")
    bg_vals = extract_raster_values(bg_lons, bg_lats, rasters)

    df_bg = pd.DataFrame({
        "especie": species,
        "presence": 0,
        "lon": bg_lons,
        "lat": bg_lats,
    })
    df_bg = pd.concat([df_bg, bg_vals], axis=1)

    # Descartar background con NaN (puntos en orillas, islas sin datos, etc.)
    bg_complete = df_bg[config.PREDICTORS].notna().all(axis=1)
    n_bg_before = len(df_bg)
    df_bg = df_bg[bg_complete].reset_index(drop=True)
    logger.info(
        "  Background con datos completos: %d (descartados %d con NaN).",
        len(df_bg), n_bg_before - len(df_bg),
    )

    # ------------------------------------------------------------------
    # 4. Selección de predictores por colinealidad (sobre presencias)
    # ------------------------------------------------------------------
    logger.info("  Seleccionando predictores no colineales…")
    selected_predictors = select_predictors(
        presence_df=df_pres,
        candidates=config.PREDICTORS,
        corr_threshold=config.CORR_THRESHOLD,
        vif_threshold=config.VIF_THRESHOLD,
    )

    # ------------------------------------------------------------------
    # 5. Combinar presencias + background
    # ------------------------------------------------------------------
    df_full = pd.concat([df_pres, df_bg], ignore_index=True)

    # ------------------------------------------------------------------
    # 6. Spatial block CV
    # ------------------------------------------------------------------
    logger.info("  Asignando bloques espaciales (block_size=%d km, folds=%d)…",
                config.SPATIAL_BLOCK_KM, config.N_CV_FOLDS)

    cv_folds = assign_spatial_blocks(
        lons=df_full["lon"].values,
        lats=df_full["lat"].values,
        block_size_km=config.SPATIAL_BLOCK_KM,
        n_folds=config.N_CV_FOLDS,
        rng=rng,
    )
    df_full["cv_fold"] = cv_folds.astype(np.int32)

    # Estadísticas de distribución entre folds
    fold_counts = df_full.groupby("cv_fold")["presence"].agg(["sum", "count"])
    fold_counts.columns = ["n_presencias", "n_total"]
    logger.info("  Distribución por fold:\n%s", fold_counts.to_string())

    # ------------------------------------------------------------------
    # 7. Orden de columnas y guardado
    # ------------------------------------------------------------------
    cols_out = (
        ["especie", "presence", "lon", "lat"]
        + config.PREDICTORS   # todas las variables (incluso no seleccionadas)
        + ["cv_fold"]
    )
    # Asegurar que todas las columnas existen
    for col in cols_out:
        if col not in df_full.columns:
            df_full[col] = np.nan

    df_out = df_full[cols_out].copy()

    utils.ensure_dirs(config.SPECIES_DATASETS)

    df_out.to_parquet(out_parquet, index=False, engine="pyarrow")
    logger.info("  Guardado parquet: %s  (%d filas)", out_parquet, len(df_out))

    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(selected_predictors, f, indent=2, ensure_ascii=False)
    logger.info("  Guardado JSON predictores: %s  (%d vars)", out_json, len(selected_predictors))


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

def main() -> None:
    """Punto de entrada principal del script de extracción."""
    parser = argparse.ArgumentParser(
        description="Etapa 3 SDM: extrae predictores, genera background, "
                    "selecciona variables y asigna folds espaciales.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--species",
        type=str,
        default=None,
        metavar="NOMBRE",
        help="Procesar solo esta especie (ej: 'Nolana divaricata'). "
             "Sin argumento: procesa todas las especies modelables.",
    )
    args = parser.parse_args()

    logger.info("Iniciando 04_extraccion.py")
    logger.info("  PREDICTORS         : %s", config.PREDICTORS)
    logger.info("  N_BACKGROUND       : %d", config.N_BACKGROUND)
    logger.info("  TARGET_GROUP_BG    : %s", config.TARGET_GROUP_BACKGROUND)
    logger.info("  CORR_THRESHOLD     : %.2f", config.CORR_THRESHOLD)
    logger.info("  VIF_THRESHOLD      : %.1f", config.VIF_THRESHOLD)
    logger.info("  SPATIAL_BLOCK_KM   : %d", config.SPATIAL_BLOCK_KM)
    logger.info("  N_CV_FOLDS         : %d", config.N_CV_FOLDS)
    logger.info("  RANDOM_SEED        : %d", config.RANDOM_SEED)

    # -- Reproducibilidad global --
    rng = np.random.default_rng(config.RANDOM_SEED)

    # -- Determinar especies a procesar --
    counts = utils.species_counts()
    modelable = config.modelable_species(counts)

    if args.species is not None:
        if args.species not in modelable:
            logger.error(
                "La especie '%s' no es modelable (grupo C o no encontrada). "
                "Especies modelables: %s",
                args.species, modelable,
            )
            sys.exit(1)
        species_list = [args.species]
    else:
        species_list = modelable

    logger.info("Especies a procesar (%d): %s", len(species_list), species_list)

    # -- Cargar ocurrencias limpias --
    if not config.OCCURRENCES_CLEAN.exists():
        logger.error(
            "Archivo de ocurrencias limpias no encontrado: %s. "
            "Ejecuta primero 01_limpieza.py.",
            config.OCCURRENCES_CLEAN,
        )
        sys.exit(1)

    logger.info("Cargando ocurrencias limpias desde %s…", config.OCCURRENCES_CLEAN)
    gdf_all = gpd.read_file(config.OCCURRENCES_CLEAN)
    logger.info("  Total registros cargados: %d", len(gdf_all))

    # Validar columnas esenciales
    required_cols = {"especie", "lon", "lat"}
    missing = required_cols - set(gdf_all.columns)
    if missing:
        logger.error("Columnas faltantes en ocurrencias limpias: %s", missing)
        sys.exit(1)

    # -- Abrir rasters de predictores --
    logger.info("Abriendo rasters alineados en %s…", config.RASTERS_ALIGNED)
    try:
        rasters = _open_rasters(config.PREDICTORS)
    except FileNotFoundError as exc:
        logger.error("Error al abrir rasters: %s", exc)
        sys.exit(1)

    # -- Cargar máscara de tierra --
    logger.info("Cargando land_mask.tif desde %s…", config.WORLDCLIM_PRESENT)
    try:
        land, land_ds = _load_land_mask()
    except FileNotFoundError as exc:
        logger.error("Error al cargar land_mask: %s", exc)
        _close_rasters(rasters)
        sys.exit(1)

    logger.info(
        "  Celdas de tierra en land_mask: %d (%.1f%% del total).",
        land.sum(), 100.0 * land.sum() / land.size,
    )

    # -- Procesar cada especie --
    errors: list[str] = []
    try:
        for sp in species_list:
            try:
                process_species(
                    species=sp,
                    gdf_all=gdf_all,
                    rasters=rasters,
                    land=land,
                    land_ds=land_ds,
                    rng=rng,
                )
            except Exception as exc:
                logger.exception("Error procesando '%s': %s", sp, exc)
                errors.append(sp)
    finally:
        _close_rasters(rasters)
        land_ds.close()

    # -- Resumen final --
    logger.info("=" * 60)
    n_ok = len(species_list) - len(errors)
    logger.info("Extracción completada: %d/%d especies procesadas.", n_ok, len(species_list))
    if errors:
        logger.warning("Errores en: %s", errors)
        sys.exit(1)
    logger.info("Datasets guardados en: %s", config.SPECIES_DATASETS)


if __name__ == "__main__":
    main()
