"""
07_forecast_2050.py — Etapa 6: Proyección a 2050 bajo escenarios CMIP6.

Pipeline:
    1. Descarga bioclim CMIP6 desde WorldClim Future (Fick & Hijmans) para cada
       combinación config.GCMS × config.SSPS en resolución 2.5 arc-min.
    2. Alinea capas futuras al grid de referencia; reutiliza topografía constante
       de config.RASTERS_ALIGNED.
    3. Para cada especie × GCM × SSP aplica el ensemble cargado desde joblib,
       genera GeoTIFF de idoneidad.
    4. Ensemble de ensembles: promedio (mean) y SD entre los 8 escenarios;
       Δidoneidad = futuro_mean − presente.
    5. MESS futuro: detecta celdas fuera del espacio ambiental de entrenamiento
       (Elith et al. 2010).
    6. Áreas km²: reproyecta a Mollweide (config.CRS_EQUAL_AREA) y calcula área
       idónea presente vs. futuro bajo umbrales maxTSS y p10.
    7. Hindcasting (opción --hindcast): estructura para entrenar con 1970-2000 y
       proyectar a 2000-2020, comparando con registros GBIF post-2000 (Cavanaugh
       et al. 2022).

Dependencias de otras etapas
-----------------------------
- Etapa 5 (05_modelado.py): modelos en config.ENSEMBLE_MODELS/{slug}.joblib.
  Cada joblib contiene un dict con claves:
    'models'      : dict[str, estimator]  — un sklearn/elapid estimator por algoritmo
    'scaler'      : sklearn scaler        — ajustado en entrenamiento
    'tss_weights' : dict[str, float]      — TSS de CV espacial por algoritmo
    'thresholds'  : dict[str, float]      — {'maxTSS': float, 'p10': float, 'min_train': float}
    'feature_names': list[str]            — orden de predictores
    'train_env'   : np.ndarray (N×P)      — matriz ambiental de entrenamiento (para MESS)
- Etapa 6 (06_validacion.py): expone `def mess(...)` si está disponible.
  Se importa con importlib (nombre comienza con dígito). Si no existe, la función
  MESS se implementa localmente (ver _compute_mess_array).

URLs WorldClim CMIP6
---------------------
Patrón base:
    https://geodata.ucdavis.edu/cmip6/2.5m/{GCM}/{ssp}/
    wc2.1_2.5m_bioc_{GCM}_{ssp}_{period}.tif
Ejemplo completo:
    https://geodata.ucdavis.edu/cmip6/2.5m/GFDL-ESM4/ssp245/
    wc2.1_2.5m_bioc_GFDL-ESM4_ssp245_2041-2060.tif
El archivo descargado es un GeoTIFF multibanda (19 variables bioclim por orden).

Supuestos
----------
- El GeoTIFF CMIP6 multibanda tiene bandas en el orden estándar WorldClim
  (bio1…bio19); se seleccionan las bandas correspondientes a config.BIOCLIM_VARS.
- La topografía (elevation, slope, northness, eastness) es estacionaria —
  se reutilizan los archivos de config.RASTERS_ALIGNED para todos los escenarios.
- Los modelos cargados desde joblib incluyen scaler y pesos TSS ya calculados.
- El procesamiento chunked con rioxarray/dask gestiona memoria a 2.5 arc-min global.
- Para hindcasting se asume que el usuario dispone de capas históricas WorldClim
  (p. ej. wc2.1_2.5m_bioc_{GCM}_historical_1970-2000.tif en un directorio ad-hoc).

Uso
----
    python 07_forecast_2050.py
    python 07_forecast_2050.py --species "Nolana divaricata"
    python 07_forecast_2050.py --species "Schinus areira" --gcm GFDL-ESM4 --ssp ssp245
    python 07_forecast_2050.py --hindcast
    python 07_forecast_2050.py --species "Nolana divaricata" --hindcast
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Stdlib
# ---------------------------------------------------------------------------
import argparse
import importlib
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Terceros (todos disponibles en el entorno SDM; ver pyproject.toml)
# ---------------------------------------------------------------------------
import joblib
import numpy as np
import pandas as pd
import requests
import rasterio
import rioxarray as rxr       # noqa: F401  — activa el accessor .rio
import xarray as xr
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.warp import calculate_default_transform, reproject
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Módulos locales del pipeline
# ---------------------------------------------------------------------------
import config
import utils

logger = utils.get_logger("07_forecast_2050")

# ---------------------------------------------------------------------------
# Índice WorldClim estándar de bandas CMIP6 (1-indexed)
# El archivo multibanda tiene 19 variables en orden bio1…bio19.
# ---------------------------------------------------------------------------
_WC_BAND_INDEX: dict[str, int] = {
    "bio1": 1,  "bio2": 2,  "bio3": 3,  "bio4": 4,  "bio5": 5,
    "bio6": 6,  "bio7": 7,  "bio8": 8,  "bio9": 9,  "bio10": 10,
    "bio11": 11, "bio12": 12, "bio13": 13, "bio14": 14, "bio15": 15,
    "bio16": 16, "bio17": 17, "bio18": 18, "bio19": 19,
}

# Nombre corto del archivo de topografía esperado en config.RASTERS_ALIGNED
_TOPO_FILES: dict[str, str] = {
    "elevation": "elevation.tif",
    "slope":     "slope.tif",
    "northness": "northness.tif",
    "eastness":  "eastness.tif",
}

# Tamaño del chunk en píxeles para procesamiento con dask/rioxarray
_CHUNK_SIZE = 1024  # 1024×1024 px ≈ 64 MB/banda a float32


# ===========================================================================
# 1. DESCARGA Y PREPARACIÓN DE CAPAS FUTURAS
# ===========================================================================

def _worldclim_future_url(gcm: str, ssp: str, period: str) -> str:
    """Construye la URL de descarga WorldClim CMIP6 (multibanda, 19 bioclim).

    Patrón:
        https://geodata.ucdavis.edu/cmip6/2.5m/{GCM}/{ssp}/
        wc2.1_2.5m_bioc_{GCM}_{ssp}_{period}.tif

    Parámetros
    ----------
    gcm : str
        Identificador del GCM, p. ej. "GFDL-ESM4".
    ssp : str
        Escenario SSP, p. ej. "ssp245".
    period : str
        Período temporal, p. ej. "2041-2060".

    Returns
    -------
    str
        URL completa del GeoTIFF.
    """
    base = "https://geodata.ucdavis.edu/cmip6/2.5m"
    fname = f"wc2.1_2.5m_bioc_{gcm}_{ssp}_{period}.tif"
    return f"{base}/{gcm}/{ssp}/{fname}"


def download_future_layers(
    gcm: str,
    ssp: str,
    period: str = config.FUTURE_PERIOD,
    dest_dir: Path | None = None,
    chunk_bytes: int = 1 << 20,
) -> Path:
    """Descarga el GeoTIFF bioclim CMIP6 para un escenario GCM×SSP (idempotente).

    El archivo se almacena en::

        config.WORLDCLIM_FUTURE/{gcm}_{ssp}/wc2.1_2.5m_bioc_{gcm}_{ssp}_{period}.tif

    Si el archivo ya existe y tiene tamaño > 0, no se vuelve a descargar.

    Parámetros
    ----------
    gcm : str
        Modelo de circulación general, p. ej. "GFDL-ESM4".
    ssp : str
        Escenario de emisiones, p. ej. "ssp245" o "ssp585".
    period : str
        Período futuro; por defecto config.FUTURE_PERIOD ("2041-2060").
    dest_dir : Path, opcional
        Directorio destino. Si None, usa config.WORLDCLIM_FUTURE/{gcm}_{ssp}.
    chunk_bytes : int
        Tamaño de fragmento de descarga (bytes).

    Returns
    -------
    Path
        Ruta local del archivo descargado.

    Raises
    ------
    requests.HTTPError
        Si el servidor devuelve un código de error HTTP.
    """
    if dest_dir is None:
        dest_dir = config.WORLDCLIM_FUTURE / f"{gcm}_{ssp}"
    dest_dir.mkdir(parents=True, exist_ok=True)

    fname = f"wc2.1_2.5m_bioc_{gcm}_{ssp}_{period}.tif"
    dest = dest_dir / fname

    if dest.exists() and dest.stat().st_size > 0:
        logger.info("Ya existe (skip): %s", dest)
        return dest

    url = _worldclim_future_url(gcm, ssp, period)
    logger.info("Descargando %s → %s", url, dest)

    with requests.get(url, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        with (
            open(dest, "wb") as fh,
            tqdm(
                total=total,
                unit="B",
                unit_scale=True,
                desc=f"{gcm}_{ssp}",
                leave=False,
            ) as pbar,
        ):
            for chunk in resp.iter_content(chunk_size=chunk_bytes):
                fh.write(chunk)
                pbar.update(len(chunk))

    logger.info("Descarga completada: %s (%.1f MB)", dest, dest.stat().st_size / 1e6)
    return dest


def align_future_to_reference(
    future_tif: Path,
    reference_tif: Path,
    bioclim_vars: list[str] = config.BIOCLIM_VARS,
    chunk: int = _CHUNK_SIZE,
) -> dict[str, xr.DataArray]:
    """Extrae y alinea las variables bioclim futuras al grid de referencia.

    Lee las bandas correspondientes a ``bioclim_vars`` desde el GeoTIFF CMIP6
    multibanda, y usa ``reproject_match`` de rioxarray para garantizar que el
    extent, resolución y CRS coincidan exactamente con el raster de referencia.

    Parámetros
    ----------
    future_tif : Path
        GeoTIFF CMIP6 multibanda (19 bioclim).
    reference_tif : Path
        Raster de referencia (cualquier capa de config.RASTERS_ALIGNED).
    bioclim_vars : list[str]
        Subconjunto de variables bioclim a conservar (p. ej. config.BIOCLIM_VARS).
    chunk : int
        Tamaño de chunk dask en píxeles para gestión de memoria.

    Returns
    -------
    dict[str, xr.DataArray]
        Diccionario {nombre_var: DataArray alineado} con nodata → np.nan.
    """
    ref = rxr.open_rasterio(reference_tif, chunks={"x": chunk, "y": chunk}).squeeze()

    aligned: dict[str, xr.DataArray] = {}
    for var in bioclim_vars:
        band_idx = _WC_BAND_INDEX[var]  # 1-indexed
        da = rxr.open_rasterio(
            future_tif,
            chunks={"band": 1, "x": chunk, "y": chunk},
        ).sel(band=band_idx)
        da = da.rio.write_crs(config.CRS_GEO)
        da = da.rio.reproject_match(ref, resampling=Resampling.bilinear)
        # Reemplazar nodata por NaN
        nodata = da.rio.nodata
        if nodata is not None:
            da = da.where(da != nodata, other=np.nan)
        aligned[var] = da.astype(np.float32)

    return aligned


def load_topo_layers(
    rasters_dir: Path = config.RASTERS_ALIGNED,
    topo_vars: list[str] = config.TOPO_VARS,
    chunk: int = _CHUNK_SIZE,
) -> dict[str, xr.DataArray]:
    """Carga las capas topográficas constantes desde config.RASTERS_ALIGNED.

    La topografía no cambia en 30 años: elevation, slope, northness, eastness
    se reutilizan directamente del procesamiento de presente.

    Parámetros
    ----------
    rasters_dir : Path
        Directorio donde residen los rasters alineados del presente.
    topo_vars : list[str]
        Variables topográficas a cargar.
    chunk : int
        Tamaño de chunk dask.

    Returns
    -------
    dict[str, xr.DataArray]
        Diccionario {nombre_var: DataArray}.
    """
    topo: dict[str, xr.DataArray] = {}
    for var in topo_vars:
        fname = _TOPO_FILES.get(var, f"{var}.tif")
        path = rasters_dir / fname
        if not path.exists():
            logger.warning("Capa topo no encontrada: %s — se omite.", path)
            continue
        da = rxr.open_rasterio(path, chunks={"x": chunk, "y": chunk}).squeeze()
        nodata = da.rio.nodata
        if nodata is not None:
            da = da.where(da != nodata, other=np.nan)
        topo[var] = da.astype(np.float32)
    return topo


def load_present_bioclim(
    rasters_dir: Path = config.RASTERS_ALIGNED,
    bioclim_vars: list[str] = config.BIOCLIM_VARS,
    chunk: int = _CHUNK_SIZE,
) -> dict[str, xr.DataArray]:
    """Carga las capas bioclim presentes (WorldClim v2.1) desde rasters alineados.

    Parámetros
    ----------
    rasters_dir : Path
        Directorio de rasters alineados.
    bioclim_vars : list[str]
        Variables a cargar.
    chunk : int
        Tamaño de chunk dask.

    Returns
    -------
    dict[str, xr.DataArray]
        Diccionario {nombre_var: DataArray}.
    """
    layers: dict[str, xr.DataArray] = {}
    for var in bioclim_vars:
        path = rasters_dir / f"{var}.tif"
        if not path.exists():
            logger.warning("Bioclim presente no encontrado: %s", path)
            continue
        da = rxr.open_rasterio(path, chunks={"x": chunk, "y": chunk}).squeeze()
        nodata = da.rio.nodata
        if nodata is not None:
            da = da.where(da != nodata, other=np.nan)
        layers[var] = da.astype(np.float32)
    return layers


# ===========================================================================
# 2. CARGA DEL MODELO ENSEMBLE
# ===========================================================================

def load_ensemble(slug: str) -> dict[str, Any]:
    """Carga el bundle joblib del ensemble para una especie.

    Espera encontrar config.ENSEMBLE_MODELS/{slug}.joblib.
    El dict debe tener las claves:
      - 'models'       : dict[str, estimator]
      - 'scaler'       : sklearn scaler ajustado
      - 'tss_weights'  : dict[str, float] — TSS de CV por algoritmo
      - 'thresholds'   : dict[str, float] — umbrales binarios
      - 'feature_names': list[str]
      - 'train_env'    : np.ndarray (N×P) — datos de entrenamiento (para MESS)

    Parámetros
    ----------
    slug : str
        Slug de la especie, p. ej. "nolana_divaricata".

    Returns
    -------
    dict[str, Any]
        Bundle del ensemble.

    Raises
    ------
    FileNotFoundError
        Si el archivo joblib no existe.
    """
    path = config.ENSEMBLE_MODELS / f"{slug}.joblib"
    if not path.exists():
        raise FileNotFoundError(
            f"Modelo no encontrado: {path}\n"
            "Ejecuta 05_modelado.py antes de este script."
        )
    bundle = joblib.load(path)
    logger.info("Ensemble cargado: %s (%d algoritmos)", slug, len(bundle["models"]))
    return bundle


# ===========================================================================
# 3. CONSTRUCCIÓN DEL STACK DE PREDICTORES
# ===========================================================================

def build_predictor_stack(
    bioclim_layers: dict[str, xr.DataArray],
    topo_layers: dict[str, xr.DataArray],
    feature_names: list[str],
) -> tuple[np.ndarray, np.ndarray, Any]:
    """Apila los predictores en una matriz 2D (N_pixels × P_features).

    Aplana el raster a píxeles, conservando un mapa de píxeles válidos
    (sin NaN en ningún predictor) para reconstruir el output raster.

    Parámetros
    ----------
    bioclim_layers : dict[str, xr.DataArray]
        Capas bioclim (presentes o futuras), alineadas al grid de referencia.
    topo_layers : dict[str, xr.DataArray]
        Capas topográficas constantes.
    feature_names : list[str]
        Orden exacto de columnas que espera el modelo (del joblib).

    Returns
    -------
    X : np.ndarray, shape (N_valid, P)
        Matriz de predictores para píxeles válidos.
    valid_mask : np.ndarray, shape (height, width), dtype bool
        Máscara de píxeles con datos completos.
    ref_da : xr.DataArray
        DataArray de referencia para reconstruir el raster de salida.

    Notes
    -----
    Usa .compute() de dask para materializar los chunks antes de apilar.
    En sistemas con poca RAM, se puede sustituir por procesamiento por bloques
    (ver _predict_chunked para la variante con ventanas rasterio).
    """
    all_layers = {**bioclim_layers, **topo_layers}

    # DataArray de referencia (cualquier capa para obtener shape/transform)
    ref_da = next(iter(all_layers.values()))

    # Apilar y materializar
    arrays: list[np.ndarray] = []
    for feat in feature_names:
        da = all_layers.get(feat)
        if da is None:
            raise KeyError(
                f"Predictor '{feat}' no encontrado en el stack. "
                f"Disponibles: {list(all_layers.keys())}"
            )
        arr = da.values if not hasattr(da.data, "compute") else da.compute().values
        arrays.append(arr.astype(np.float32).ravel())

    stacked = np.column_stack(arrays)  # (N_total, P)

    # Máscara de píxeles completamente válidos
    valid_mask = ~np.any(~np.isfinite(stacked), axis=1)
    valid_mask_2d = valid_mask.reshape(ref_da.shape[-2:])

    X = stacked[valid_mask]
    return X, valid_mask_2d, ref_da


# ===========================================================================
# 4. PREDICCIÓN ENSEMBLE CON PESOS TSS
# ===========================================================================

def predict_ensemble(
    X: np.ndarray,
    bundle: dict[str, Any],
) -> np.ndarray:
    """Aplica el ensemble ponderado por TSS sobre la matriz de predictores.

    Escala X con el scaler del bundle, predice probabilidades con cada
    algoritmo cuyo TSS ≥ config.TSS_MIN_ENSEMBLE, pondera por TSS y promedia.

    Parámetros
    ----------
    X : np.ndarray, shape (N_valid, P)
        Predictores ya filtrados (sin NaN).
    bundle : dict[str, Any]
        Bundle cargado por load_ensemble.

    Returns
    -------
    np.ndarray, shape (N_valid,)
        Idoneidad ensemble (0–1).
    """
    scaler = bundle["scaler"]
    models = bundle["models"]
    tss_weights = bundle["tss_weights"]

    X_scaled = scaler.transform(X)

    weighted_sum = np.zeros(len(X), dtype=np.float64)
    weight_total = 0.0

    for algo, model in models.items():
        tss = tss_weights.get(algo, 0.0)
        if tss < config.TSS_MIN_ENSEMBLE:
            logger.debug("Algoritmo %s excluido (TSS=%.3f < %.2f)", algo, tss, config.TSS_MIN_ENSEMBLE)
            continue

        if hasattr(model, "predict_proba"):
            prob = model.predict_proba(X_scaled)[:, 1]
        elif hasattr(model, "predict"):
            # MaxEnt de elapid devuelve directamente probabilidad
            prob = model.predict(X_scaled)
        else:
            logger.warning("Modelo %s sin predict_proba ni predict; se omite.", algo)
            continue

        weighted_sum += prob * tss
        weight_total += tss
        logger.debug("  %s TSS=%.3f contribuye al ensemble.", algo, tss)

    if weight_total == 0:
        logger.warning("Ningún algoritmo pasó el umbral TSS; retornando zeros.")
        return np.zeros(len(X), dtype=np.float32)

    return (weighted_sum / weight_total).astype(np.float32)


def reconstruct_raster(
    values_1d: np.ndarray,
    valid_mask_2d: np.ndarray,
    ref_da: xr.DataArray,
    nodata: float = -9999.0,
) -> xr.DataArray:
    """Reconstruye un DataArray 2D a partir de los valores de píxeles válidos.

    Parámetros
    ----------
    values_1d : np.ndarray, shape (N_valid,)
        Valores para los píxeles válidos.
    valid_mask_2d : np.ndarray, shape (height, width), dtype bool
        Máscara de píxeles válidos.
    ref_da : xr.DataArray
        DataArray de referencia (provee coords, transform, CRS).
    nodata : float
        Valor de nodata para píxeles no válidos.

    Returns
    -------
    xr.DataArray
        Raster reconstruido con CRS y nodata configurados.
    """
    out = np.full(valid_mask_2d.shape, fill_value=nodata, dtype=np.float32)
    out[valid_mask_2d] = values_1d.astype(np.float32)

    # Crear DataArray con las mismas coordenadas
    da_out = xr.DataArray(
        out,
        dims=ref_da.dims[-2:],
        coords={k: ref_da.coords[k] for k in ref_da.dims[-2:]},
    )
    da_out = da_out.expand_dims("band").assign_coords(band=[1])
    da_out = da_out.rio.write_crs(config.CRS_GEO)
    da_out = da_out.rio.write_nodata(nodata)
    return da_out


def save_geotiff(da: xr.DataArray, path: Path) -> None:
    """Guarda un DataArray como GeoTIFF comprimido con LZW.

    Parámetros
    ----------
    da : xr.DataArray
        DataArray con CRS y nodata configurados (via rioxarray).
    path : Path
        Ruta de salida.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    da.rio.to_raster(str(path), compress="LZW", dtype="float32")
    logger.info("Guardado: %s", path)


# ===========================================================================
# 5. MESS FUTURO
# ===========================================================================

def _try_import_mess_from_validacion() -> Any | None:
    """Intenta importar la función mess del módulo 06_validacion.

    Usa importlib porque el nombre del módulo comienza con un dígito.
    Si no está disponible, retorna None y se usa la implementación local.

    Returns
    -------
    callable o None
    """
    try:
        mod = importlib.import_module("06_validacion")
        fn = getattr(mod, "mess", None)
        if fn is not None:
            logger.info("Función mess importada desde 06_validacion.")
        return fn
    except ModuleNotFoundError:
        logger.debug("06_validacion no disponible; se usa implementación local de MESS.")
        return None


def _compute_mess_array(
    train_env: np.ndarray,
    pred_env: np.ndarray,
) -> np.ndarray:
    """Calcula MESS (Multivariate Environmental Similarity Surface) localmente.

    Implementación directa de Elith et al. (2010, Methods Ecol. Evol.):
    para cada variable j y cada sitio i de proyección:

        si  p_ij ≤ min_j  →  f_ij = 0
        si  min_j < p_ij ≤ median_j  →  f_ij = 2*(p_ij - min_j)/(median_j - min_j) * 100 / 2
        si  median_j < p_ij < max_j  →  f_ij = 2*(max_j - p_ij)/(max_j - median_j) * 100 / 2
        si  p_ij ≥ max_j  →  f_ij = 0

    El MESS de cada sitio = min_j(f_ij). Negativo → extrapolación.

    Parámetros
    ----------
    train_env : np.ndarray, shape (N_train, P)
        Valores de las variables en los sitios de entrenamiento.
    pred_env : np.ndarray, shape (N_pred, P)
        Valores de las variables en los sitios de proyección.

    Returns
    -------
    np.ndarray, shape (N_pred,)
        Scores MESS. Valores negativos indican extrapolación ambiental.
    """
    min_ref = train_env.min(axis=0)       # (P,)
    max_ref = train_env.max(axis=0)       # (P,)
    med_ref = np.median(train_env, axis=0)  # (P,)

    range_ref = max_ref - min_ref
    range_ref[range_ref == 0] = 1e-9      # evitar división por cero

    # (N_pred, P)
    p = pred_env.astype(np.float64)
    f = np.empty_like(p)

    for j in range(p.shape[1]):
        pj = p[:, j]
        lo, hi, med = min_ref[j], max_ref[j], med_ref[j]
        rng = range_ref[j]

        below = pj <= lo
        above = pj >= hi
        in_lo = (~below) & (pj <= med)
        in_hi = (~above) & (pj > med)

        f[below, j] = 0.0
        f[above, j] = 0.0
        f[in_lo, j] = 2.0 * (pj[in_lo] - lo) / (med - lo + 1e-9) * 50.0
        # Clip [0, 100]
        f[in_lo, j] = np.clip(f[in_lo, j], 0, 100)
        f[in_hi, j] = 2.0 * (hi - pj[in_hi]) / (hi - med + 1e-9) * 50.0
        f[in_hi, j] = np.clip(f[in_hi, j], 0, 100)

    mess_scores = f.min(axis=1).astype(np.float32)
    return mess_scores


def compute_mess(
    train_env: np.ndarray,
    X_pred: np.ndarray,
    valid_mask_2d: np.ndarray,
    ref_da: xr.DataArray,
) -> xr.DataArray:
    """Calcula el mapa MESS y lo devuelve como DataArray.

    Intenta usar mess() de 06_validacion si está disponible; si no, usa
    la implementación local _compute_mess_array.

    Parámetros
    ----------
    train_env : np.ndarray, shape (N_train, P)
        Espacio ambiental de entrenamiento.
    X_pred : np.ndarray, shape (N_valid, P)
        Predictores de los píxeles de proyección (ya filtrados).
    valid_mask_2d : np.ndarray, shape (H, W), dtype bool
    ref_da : xr.DataArray
        DataArray de referencia.

    Returns
    -------
    xr.DataArray
        Mapa MESS (negativo = extrapolación).
    """
    ext_fn = _try_import_mess_from_validacion()
    if ext_fn is not None:
        try:
            scores = ext_fn(train_env, X_pred)
        except Exception as exc:
            logger.warning("mess() externo falló (%s); usando implementación local.", exc)
            scores = _compute_mess_array(train_env, X_pred)
    else:
        scores = _compute_mess_array(train_env, X_pred)

    return reconstruct_raster(scores, valid_mask_2d, ref_da, nodata=-9999.0)


# ===========================================================================
# 6. CÁLCULO DE ÁREAS EN PROYECCIÓN EQUIÁREA
# ===========================================================================

def _raster_to_binary(
    suitability_da: xr.DataArray,
    threshold: float,
) -> np.ndarray:
    """Binariza un raster de idoneidad con un umbral dado.

    Parámetros
    ----------
    suitability_da : xr.DataArray
        Raster de idoneidad (0–1).
    threshold : float
        Umbral de corte. Píxeles >= threshold → 1 (idóneo).

    Returns
    -------
    np.ndarray, shape (H, W), dtype uint8
        0 = no idóneo, 1 = idóneo, 255 = nodata.
    """
    vals = suitability_da.values
    nodata = suitability_da.rio.nodata or -9999.0
    valid = vals != nodata
    binary = np.where(valid, (vals >= threshold).astype(np.uint8), 255)
    return binary


def _pixel_area_km2_mollweide(src_da: xr.DataArray) -> float:
    """Calcula el área en km² de un píxel reproyectado a Mollweide.

    Parámetros
    ----------
    src_da : xr.DataArray
        DataArray fuente con CRS geográfico (EPSG:4326).

    Returns
    -------
    float
        Área aproximada en km² por píxel en proyección Mollweide (ESRI:54009).
        Para 2.5 arc-min ≈ 5×5 km ≈ 25 km² (varía con latitud; aquí se estima
        reprojectando un píxel de referencia centrado).

    Notes
    -----
    En la proyección de Mollweide (igual-área), todos los píxeles tienen la
    misma área real, por lo que basta calcularla una vez y multiplicar por
    el conteo de píxeles idóneos.
    """
    # Dimensiones del DataArray en grados
    lats = src_da.coords.get("y") or src_da.coords.get("latitude")
    lons = src_da.coords.get("x") or src_da.coords.get("longitude")

    if lats is None or lons is None:
        # Fallback: usar resolución nominal 2.5 arc-min → ~5 km de lado
        return 25.0

    res_deg_y = abs(float(lats[1] - lats[0])) if len(lats) > 1 else 2.5 / 60
    res_deg_x = abs(float(lons[1] - lons[0])) if len(lons) > 1 else 2.5 / 60

    # Reprojecto un píxel centrado al ecuador a Mollweide para obtener resolución real
    from pyproj import Transformer
    t = Transformer.from_crs(config.CRS_GEO, config.CRS_EQUAL_AREA, always_xy=True)
    cx, cy = t.transform(0.0, 0.0)
    ex, _ = t.transform(res_deg_x, 0.0)
    _, ey = t.transform(0.0, res_deg_y)
    return abs((ex - cx) * (ey - cy)) / 1e6  # m² → km²


def compute_area_table(
    present_da: xr.DataArray,
    future_das: dict[str, xr.DataArray],
    thresholds: dict[str, float],
    slug: str,
) -> pd.DataFrame:
    """Calcula tabla de áreas idóneas km² presente vs. futuro por umbral.

    La idoneidad se binariza con cada umbral disponible, se estima el área
    multiplicando el conteo de píxeles idóneos por el área por píxel en
    Mollweide.

    Parámetros
    ----------
    present_da : xr.DataArray
        Raster de idoneidad presente.
    future_das : dict[str, xr.DataArray]
        Diccionario {escenario_label: DataArray idoneidad futura}.
    thresholds : dict[str, float]
        Umbrales del joblib, p. ej. {'maxTSS': 0.45, 'p10': 0.32}.
    slug : str
        Slug de la especie (para la tabla).

    Returns
    -------
    pd.DataFrame
        Columnas: especie, escenario, umbral, area_km2, delta_km2.
    """
    pixel_km2 = _pixel_area_km2_mollweide(present_da)
    logger.info("Área por píxel en Mollweide: %.2f km²", pixel_km2)

    rows: list[dict[str, Any]] = []

    for thr_name, thr_val in thresholds.items():
        if thr_name not in ("maxTSS", "p10", "min_train"):
            continue

        pres_bin = _raster_to_binary(present_da, thr_val)
        pres_area = float(np.sum(pres_bin == 1)) * pixel_km2

        rows.append({
            "especie": slug,
            "escenario": "presente",
            "umbral": thr_name,
            "umbral_valor": round(thr_val, 4),
            "area_km2": round(pres_area, 1),
            "delta_km2": 0.0,
        })

        for scenario, fut_da in future_das.items():
            fut_bin = _raster_to_binary(fut_da, thr_val)
            fut_area = float(np.sum(fut_bin == 1)) * pixel_km2
            rows.append({
                "especie": slug,
                "escenario": scenario,
                "umbral": thr_name,
                "umbral_valor": round(thr_val, 4),
                "area_km2": round(fut_area, 1),
                "delta_km2": round(fut_area - pres_area, 1),
            })

    return pd.DataFrame(rows)


# ===========================================================================
# 7. HINDCASTING (Cavanaugh et al. 2022)
# ===========================================================================

def hindcast_structure(
    slug: str,
    bundle: dict[str, Any],
    hindcast_dir: Path | None = None,
) -> None:
    """Estructura de validación por hindcasting (Cavanaugh et al. 2022).

    Estrategia:
    -----------
    1. ENTRENAR con clima histórico 1970-2000 (WorldClim v2.1 historical):
       - Descargar wc2.1_2.5m_bioc_{GCM}_historical_1970-2000.tif desde
         https://geodata.ucdavis.edu/cmip6/2.5m/{GCM}/historical/...
       - O usar directamente los rasters presentes de WorldClim v2.1 como
         aproximación (representan el período 1970-2000).
       - Re-entrenar el ensemble completo con estos datos climáticos
         (llamar a la lógica de 05_modelado.py con las capas históricas).

    2. PROYECTAR al período 2000-2020:
       - Descargar capas CMIP6 del período histórico reciente (si disponible)
         o usar CHELSA/ERA5 para 2000-2020.
       - Aplicar predict_ensemble con las capas 2000-2020.

    3. COMPARAR con GBIF post-2000:
       - Cargar ocurrencias_limpias.gpkg, filtrar año > 2000.
       - Estos registros NO se usaron en entrenamiento (entrenado en 1970-2000).
       - Calcular métricas de discriminación (AUC, TSS) sobre las presencias
         post-2000 vs. el raster proyectado a 2000-2020.
       - Si AUC_hindcast > 0.7 → el modelo transfiere temporalmente → el
         forecast a 2050 es más defendible.

    Referencia
    ----------
    Cavanaugh et al. (2022). Hindcast-validated SDMs reveal future
    vulnerabilities in bird distributions. Ecology and Evolution.

    Parámetros
    ----------
    slug : str
        Slug de la especie.
    bundle : dict[str, Any]
        Bundle del ensemble (del presente).
    hindcast_dir : Path, opcional
        Directorio con capas climáticas 2000-2020. Si None, usa
        config.WORLDCLIM_FUTURE / "historical".

    Notes
    -----
    Esta función está DOCUMENTADA pero no ejecuta el re-entrenamiento
    completo, ya que requeriría re-invocar 05_modelado.py con capas
    alternativas — operación costosa. El usuario puede activarla pasando
    --hindcast y proveyendo las capas en hindcast_dir.

    Para activar completamente: implementar o invocar la función de
    entrenamiento de 05_modelado.py con parámetro climate_dir=hindcast_dir.
    """
    if hindcast_dir is None:
        hindcast_dir = config.WORLDCLIM_FUTURE / "historical"

    logger.info(
        "[HINDCAST] Especie: %s | Directorio climático histórico: %s",
        slug,
        hindcast_dir,
    )

    # Paso 1: verificar disponibilidad de capas históricas
    hist_layers_available = hindcast_dir.exists() and any(hindcast_dir.glob("*.tif"))
    if not hist_layers_available:
        logger.warning(
            "[HINDCAST] Paso 1: No se encontraron capas históricas en %s.\n"
            "  → Descargar WorldClim v2.1 historical (1970-2000) desde:\n"
            "    https://worldclim.org/data/worldclim21.html\n"
            "  → O capas CMIP6 historical por GCM desde:\n"
            "    https://geodata.ucdavis.edu/cmip6/2.5m/{GCM}/historical/",
            hindcast_dir,
        )
    else:
        logger.info("[HINDCAST] Paso 1: Capas históricas detectadas en %s.", hindcast_dir)

    # Paso 2: proyección 2000-2020 (estructura)
    logger.info(
        "[HINDCAST] Paso 2: Para proyectar 2000-2020:\n"
        "  a) Cargar capas climáticas 2000-2020 (CHELSA v2.1, ERA5-Land, o\n"
        "     WorldClim historical si se usa como proxy).\n"
        "  b) build_predictor_stack(bioclim_2000_2020, topo_layers, feature_names)\n"
        "  c) predict_ensemble(X, bundle_historical)\n"
        "  d) Guardar como {slug}_hindcast_2000-2020_suitability.tif en config.MAPS"
    )

    # Paso 3: comparación con GBIF post-2000
    logger.info(
        "[HINDCAST] Paso 3: Validación con GBIF post-2000:\n"
        "  a) Cargar config.OCCURRENCES_CLEAN, filtrar columna 'ano' > 2000.\n"
        "  b) Extraer valores de idoneidad del raster hindcast en esas coordenadas.\n"
        "  c) Calcular AUC-ROC, TSS vs. background (nuevos puntos aleatorios).\n"
        "  d) Si AUC_hindcast > 0.7, el modelo transfiere temporalmente y el\n"
        "     forecast a 2050 es metodológicamente más robusto (Cavanaugh et al. 2022)."
    )

    logger.info(
        "[HINDCAST] Resumen: La validación por hindcasting no está completamente\n"
        "implementada en este script porque requiere re-entrenamiento con capas\n"
        "históricas (responsabilidad de 05_modelado.py). La estructura está\n"
        "documentada para guiar la implementación completa."
    )


# ===========================================================================
# 8. FLUJO PRINCIPAL POR ESPECIE
# ===========================================================================

def process_species(
    species_name: str,
    gcms: list[str],
    ssps: list[str],
    skip_download: bool = False,
) -> None:
    """Ejecuta el pipeline completo de forecast para una especie.

    1. Carga el ensemble desde joblib.
    2. Descarga capas CMIP6 (idempotente).
    3. Proyecta presente.
    4. Proyecta cada GCM×SSP.
    5. Ensemble de ensembles (mean + SD) y Δidoneidad.
    6. MESS futuro.
    7. Tabla de áreas.

    Parámetros
    ----------
    species_name : str
        Nombre científico de la especie, p. ej. "Nolana divaricata".
    gcms : list[str]
        Lista de GCMs a procesar.
    ssps : list[str]
        Lista de SSPs a procesar.
    skip_download : bool
        Si True, omite la descarga (asume que los archivos ya existen).
    """
    slug = utils.slugify_species(species_name)
    logger.info("=== Iniciando forecast: %s (slug=%s) ===", species_name, slug)

    # ---- Cargar ensemble ----
    bundle = load_ensemble(slug)
    feature_names: list[str] = bundle["feature_names"]
    thresholds: dict[str, float] = bundle.get("thresholds", {})
    train_env: np.ndarray | None = bundle.get("train_env")

    # ---- Capas topográficas constantes ----
    logger.info("Cargando capas topográficas (constantes)...")
    topo_layers = load_topo_layers()

    # ---- Capas bioclim presentes ----
    logger.info("Cargando bioclim presente para proyección base...")
    present_bioclim = load_present_bioclim()

    if not present_bioclim:
        logger.error(
            "No se encontraron capas bioclim presentes en %s. "
            "Verifica que 02_capas_presente.py se ejecutó.",
            config.RASTERS_ALIGNED,
        )
        return

    # ---- Proyección presente ----
    logger.info("Proyectando ensemble sobre rasters presentes...")
    X_pres, mask_pres, ref_da_pres = build_predictor_stack(
        present_bioclim, topo_layers, feature_names
    )
    suit_pres_1d = predict_ensemble(X_pres, bundle)
    suit_pres_da = reconstruct_raster(suit_pres_1d, mask_pres, ref_da_pres)

    present_tif = config.MAPS / f"{slug}_present_suitability.tif"
    save_geotiff(suit_pres_da, present_tif)

    # ---- Proyecciones futuras GCM×SSP ----
    future_das: dict[str, xr.DataArray] = {}
    scenarios = [(gcm, ssp) for gcm in gcms for ssp in ssps]
    logger.info("Total escenarios a procesar: %d", len(scenarios))

    # Usar el primer raster presente como referencia de grid
    reference_tif = config.RASTERS_ALIGNED / f"{config.BIOCLIM_VARS[0]}.tif"
    if not reference_tif.exists():
        # fallback: cualquier tif en rasters_aligned
        candidates = list(config.RASTERS_ALIGNED.glob("*.tif"))
        if not candidates:
            logger.error("No hay rasters de referencia en %s.", config.RASTERS_ALIGNED)
            return
        reference_tif = candidates[0]
        logger.warning("Raster de referencia: %s (fallback)", reference_tif)

    for gcm, ssp in scenarios:
        scenario_label = f"{gcm}_{ssp}"
        logger.info("--- Escenario: %s ---", scenario_label)

        # Descarga idempotente
        if not skip_download:
            try:
                future_tif = download_future_layers(gcm, ssp)
            except Exception as exc:
                logger.error(
                    "Error descargando %s: %s — escenario omitido.", scenario_label, exc
                )
                continue
        else:
            future_tif = (
                config.WORLDCLIM_FUTURE
                / f"{gcm}_{ssp}"
                / f"wc2.1_2.5m_bioc_{gcm}_{ssp}_{config.FUTURE_PERIOD}.tif"
            )
            if not future_tif.exists():
                logger.error(
                    "Archivo futuro no encontrado (skip_download=True): %s", future_tif
                )
                continue

        # Alinear al grid de referencia
        logger.info("Alineando capas futuras al grid de referencia...")
        try:
            future_bioclim = align_future_to_reference(future_tif, reference_tif)
        except Exception as exc:
            logger.error("Error alineando %s: %s", scenario_label, exc)
            continue

        # Construir stack y predecir
        X_fut, mask_fut, ref_da_fut = build_predictor_stack(
            future_bioclim, topo_layers, feature_names
        )
        suit_fut_1d = predict_ensemble(X_fut, bundle)
        suit_fut_da = reconstruct_raster(suit_fut_1d, mask_fut, ref_da_fut)

        # Guardar GeoTIFF por escenario
        out_tif = config.MAPS / f"{slug}_{gcm}_{ssp}_suitability.tif"
        save_geotiff(suit_fut_da, out_tif)

        future_das[scenario_label] = suit_fut_da

    if not future_das:
        logger.error("No se generó ninguna proyección futura para %s.", slug)
        return

    # ---- Ensemble de ensembles: mean + SD ----
    logger.info("Calculando ensemble de ensembles (mean y SD)...")
    # Alinear todos los futuros al mismo grid que el presente
    nodata_val = -9999.0

    # Construir cubo numpy de idoneidades futuras
    fut_arrays: list[np.ndarray] = []
    for da in future_das.values():
        arr = da.values.astype(np.float32).squeeze()
        arr[arr == nodata_val] = np.nan
        fut_arrays.append(arr)

    cube = np.stack(fut_arrays, axis=0)  # (N_scenarios, H, W)

    mean_arr = np.nanmean(cube, axis=0).astype(np.float32)
    sd_arr = np.nanstd(cube, axis=0).astype(np.float32)

    # Rellenar NaN con nodata
    mean_arr = np.where(np.isnan(mean_arr), nodata_val, mean_arr)
    sd_arr = np.where(np.isnan(sd_arr), nodata_val, sd_arr)

    # Usar el primer DataArray como referencia de forma
    ref_fut = next(iter(future_das.values()))

    def _array_to_da(arr: np.ndarray, ref: xr.DataArray) -> xr.DataArray:
        da = xr.DataArray(
            arr,
            dims=ref.dims[-2:],
            coords={k: ref.coords[k] for k in ref.dims[-2:]},
        )
        da = da.expand_dims("band").assign_coords(band=[1])
        da = da.rio.write_crs(config.CRS_GEO)
        da = da.rio.write_nodata(nodata_val)
        return da

    mean_da = _array_to_da(mean_arr, ref_fut)
    sd_da = _array_to_da(sd_arr, ref_fut)

    save_geotiff(mean_da, config.MAPS / f"{slug}_future_mean_suitability.tif")
    save_geotiff(sd_da, config.MAPS / f"{slug}_future_sd_suitability.tif")

    # ---- Δidoneidad = futuro_mean − presente ----
    logger.info("Calculando Δidoneidad (futuro_mean − presente)...")
    pres_arr = suit_pres_da.values.astype(np.float32).squeeze()
    pres_arr[pres_arr == nodata_val] = np.nan

    # Asegurar que mean_arr esté alineado con pres_arr
    # (deben ser del mismo shape si todos los rasters están alineados)
    if pres_arr.shape == mean_arr.shape:
        delta_arr = mean_arr - pres_arr
    else:
        logger.warning(
            "Shapes de presente (%s) y futuro_mean (%s) no coinciden. "
            "No se calcula delta.",
            pres_arr.shape,
            mean_arr.shape,
        )
        delta_arr = None

    if delta_arr is not None:
        delta_arr = np.where(
            np.isnan(delta_arr) | np.isnan(pres_arr), nodata_val, delta_arr
        ).astype(np.float32)
        delta_da = _array_to_da(delta_arr, suit_pres_da)
        save_geotiff(delta_da, config.MAPS / f"{slug}_delta_suitability.tif")

    # ---- MESS futuro ----
    if train_env is not None:
        logger.info("Calculando MESS futuro...")
        # Usar el stack del primer escenario como representante del espacio ambiental futuro
        first_scenario = list(scenarios)[0]
        first_gcm, first_ssp = first_scenario
        first_label = f"{first_gcm}_{first_ssp}"
        if first_label in future_das:
            # Reconstruir X_fut del primer escenario
            first_fut_tif = (
                config.WORLDCLIM_FUTURE
                / f"{first_gcm}_{first_ssp}"
                / f"wc2.1_2.5m_bioc_{first_gcm}_{first_ssp}_{config.FUTURE_PERIOD}.tif"
            )
            if first_fut_tif.exists():
                try:
                    first_bio = align_future_to_reference(first_fut_tif, reference_tif)
                    X_mess, mask_mess, ref_da_mess = build_predictor_stack(
                        first_bio, topo_layers, feature_names
                    )
                    mess_da = compute_mess(train_env, X_mess, mask_mess, ref_da_mess)
                    save_geotiff(mess_da, config.MAPS / f"{slug}_future_mess.tif")
                    pct_extrap = 100.0 * float(np.sum(
                        (mess_da.values < 0) & (mess_da.values != nodata_val)
                    )) / max(1, float(np.sum(mess_da.values != nodata_val)))
                    logger.info(
                        "MESS futuro: %.1f%% del área cae fuera del espacio de entrenamiento.",
                        pct_extrap,
                    )
                except Exception as exc:
                    logger.warning("Error calculando MESS futuro: %s", exc)
    else:
        logger.warning(
            "train_env no disponible en el bundle. "
            "MESS futuro omitido. "
            "Asegúrate de que 05_modelado.py guarde 'train_env' en el joblib."
        )

    # ---- Tabla de áreas ----
    logger.info("Calculando tabla de áreas (km²) presente vs. futuro...")
    if thresholds:
        area_df = compute_area_table(
            present_da=suit_pres_da,
            future_das=future_das,
            thresholds=thresholds,
            slug=slug,
        )
        # Agregar filas para futuro_mean
        mean_fut_das = {"futuro_mean": mean_da}
        area_mean_df = compute_area_table(
            present_da=suit_pres_da,
            future_das=mean_fut_das,
            thresholds=thresholds,
            slug=slug,
        )
        area_df = pd.concat(
            [area_df, area_mean_df[area_mean_df["escenario"] != "presente"]],
            ignore_index=True,
        )

        tables_dir = config.TABLES
        tables_dir.mkdir(parents=True, exist_ok=True)
        area_csv = tables_dir / f"areas_{slug}.csv"
        area_df.to_csv(area_csv, index=False)
        logger.info("Tabla de áreas guardada: %s", area_csv)
    else:
        logger.warning(
            "No se encontraron umbrales ('thresholds') en el bundle de %s. "
            "Tabla de áreas omitida. Asegúrate de que 05_modelado.py los guarde.",
            slug,
        )

    logger.info("=== Forecast completado: %s ===", slug)


# ===========================================================================
# 9. ARGPARSE Y MAIN
# ===========================================================================

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parsea los argumentos de línea de comandos.

    Parámetros
    ----------
    argv : list[str] o None
        Lista de argumentos (None usa sys.argv).

    Returns
    -------
    argparse.Namespace
        Namespace con atributos: species, gcm, ssp, hindcast, skip_download.
    """
    parser = argparse.ArgumentParser(
        prog="07_forecast_2050.py",
        description=(
            "Proyección SDM a 2050 bajo escenarios CMIP6 (Etapa 6).\n"
            "Genera GeoTIFFs de idoneidad futura, Δidoneidad, MESS y tabla de áreas."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python 07_forecast_2050.py\n"
            "  python 07_forecast_2050.py --species 'Nolana divaricata'\n"
            "  python 07_forecast_2050.py --species 'Schinus areira' --gcm GFDL-ESM4 --ssp ssp245\n"
            "  python 07_forecast_2050.py --hindcast\n"
        ),
    )

    parser.add_argument(
        "--species",
        type=str,
        default=None,
        metavar="NOMBRE",
        help=(
            "Nombre científico de la especie a procesar, p. ej. 'Nolana divaricata'. "
            "Si se omite, procesa todas las especies con modelo disponible en "
            "config.ENSEMBLE_MODELS."
        ),
    )
    parser.add_argument(
        "--gcm",
        type=str,
        default=None,
        choices=config.GCMS,
        metavar="GCM",
        help=(
            f"GCM a procesar. Opciones: {config.GCMS}. "
            "Si se omite, procesa todos los GCMs definidos en config.GCMS."
        ),
    )
    parser.add_argument(
        "--ssp",
        type=str,
        default=None,
        choices=config.SSPS,
        metavar="SSP",
        help=(
            f"SSP a procesar. Opciones: {config.SSPS}. "
            "Si se omite, procesa todos los SSPs en config.SSPS."
        ),
    )
    parser.add_argument(
        "--hindcast",
        action="store_true",
        default=False,
        help=(
            "Activar modo de validación por hindcasting (Cavanaugh et al. 2022). "
            "Documenta los pasos para entrenar con 1970-2000 y validar contra "
            "registros GBIF post-2000. Ver docstring de hindcast_structure() "
            "para los pasos de implementación completa."
        ),
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        default=False,
        help=(
            "Omitir la descarga de capas CMIP6 (asume que ya existen en "
            "config.WORLDCLIM_FUTURE/{gcm}_{ssp}/). Útil para reruns."
        ),
    )

    return parser.parse_args(argv)


def _collect_species_to_process(species_arg: str | None) -> list[str]:
    """Determina la lista de especies a procesar.

    Si se especifica --species, retorna esa sola especie.
    Si no, busca todos los .joblib en config.ENSEMBLE_MODELS y deriva
    los nombres de especie a partir de los slugs de archivo.

    Parámetros
    ----------
    species_arg : str o None
        Valor del argumento --species.

    Returns
    -------
    list[str]
        Lista de nombres científicos (o slugs si --species no se pasa).
    """
    if species_arg is not None:
        return [species_arg]

    if not config.ENSEMBLE_MODELS.exists():
        logger.warning(
            "Directorio de modelos no existe: %s. "
            "Ejecuta 05_modelado.py primero.",
            config.ENSEMBLE_MODELS,
        )
        return []

    jobl_files = sorted(config.ENSEMBLE_MODELS.glob("*.joblib"))
    if not jobl_files:
        logger.warning("No se encontraron modelos en %s.", config.ENSEMBLE_MODELS)
        return []

    # Reconstruir nombre a partir del slug (ej: "nolana_divaricata" → slug directo)
    slugs = [f.stem for f in jobl_files]
    logger.info("Especies a procesar (%d): %s", len(slugs), slugs)
    return slugs


def main(argv: list[str] | None = None) -> None:
    """Punto de entrada principal del script de forecast.

    Parsea argumentos, asegura directorios de salida, y ejecuta el pipeline
    por especie.
    """
    args = parse_args(argv)

    # Asegurar que existen los directorios de salida
    utils.ensure_dirs(config.MAPS, config.TABLES, config.WORLDCLIM_FUTURE)

    # Determinar GCMs y SSPs a procesar
    gcms: list[str] = [args.gcm] if args.gcm else config.GCMS
    ssps: list[str] = [args.ssp] if args.ssp else config.SSPS
    logger.info(
        "GCMs: %s | SSPs: %s | Período: %s", gcms, ssps, config.FUTURE_PERIOD
    )

    # Determinar especies
    species_list = _collect_species_to_process(args.species)
    if not species_list:
        logger.error("No hay especies para procesar. Abortando.")
        sys.exit(1)

    # Modo hindcast
    if args.hindcast:
        logger.info("=== MODO HINDCASTING (Cavanaugh et al. 2022) ===")
        for sp in species_list:
            slug = utils.slugify_species(sp) if " " in sp else sp
            try:
                bundle = load_ensemble(slug)
                hindcast_structure(slug, bundle)
            except FileNotFoundError as exc:
                logger.error("[HINDCAST] %s", exc)
        return

    # Pipeline de forecast estándar
    for sp in species_list:
        # Aceptar tanto nombre completo como slug (si se llamó con slug directo)
        slug_test = utils.slugify_species(sp) if " " in sp else sp
        model_path = config.ENSEMBLE_MODELS / f"{slug_test}.joblib"
        if not model_path.exists():
            logger.warning(
                "Modelo no encontrado para '%s' (%s). Saltando.",
                sp,
                model_path,
            )
            continue

        # Reconstruir nombre legible para logging (slug → nombre aproximado)
        species_display = sp if " " in sp else sp.replace("_", " ").title()

        try:
            process_species(
                species_name=species_display,
                gcms=gcms,
                ssps=ssps,
                skip_download=args.skip_download,
            )
        except Exception as exc:
            logger.exception(
                "Error procesando '%s': %s. Continuando con siguientes.", sp, exc
            )

    logger.info("=== Pipeline de forecast completado para %d especie(s). ===", len(species_list))


if __name__ == "__main__":
    main()
