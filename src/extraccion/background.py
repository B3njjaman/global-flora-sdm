"""
background.py — Pseudo-ausencias / background por algoritmo (etapa 4, V4).

Esquema acordado: Barbet-Massin et al. (2012) con el ajuste de Valavi et al. (2022)
para un ensemble de combinación ponderada. Para nuestros 5 algoritmos:

    GLM, GAM   -> 10.000 background random + pesos (prevalencia 0.5)
    RF, GBM    -> ajuste Valavi: MISMO background grande, down-weighted (pesos)
    MaxEnt     -> 10.000 background random (presence-background, sin pesos)

Implementación: UN background de 10.000 puntos random DENTRO DE CHILE (área de
calibración, especies endémicas) compartido por todos los algoritmos; lo que
cambia por algoritmo son los PESOS de caso. Así, RF/GBM usan background grande
down-weighted (Valavi) en vez de n=presencias (Barbet-Massin puro).

Referencias:
  - Barbet-Massin et al. 2012, Methods Ecol. Evol. 3(2):327-338.
  - Valavi et al. 2022, Ecological Monographs 92(1).
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import config  # noqa: E402
import utils   # noqa: E402

log = utils.get_logger("extraccion.background")

# Nº de pseudo-ausencias/background (Barbet-Massin: 10.000 para los métodos de
# regresión / MaxEnt; Valavi: el mismo background grande, ponderado, para RF/GBM).
N_PA = 10_000

# Esquema por algoritmo: cuántos puntos y cómo se ponderan.
ESQUEMA_PA: dict[str, dict[str, object]] = {
    "glm":    {"n": N_PA, "pesos": "prevalencia"},
    "gam":    {"n": N_PA, "pesos": "prevalencia"},
    "rf":     {"n": N_PA, "pesos": "prevalencia"},   # Valavi: down-weighting
    "gbm":    {"n": N_PA, "pesos": "prevalencia"},   # Valavi: down-weighting
    "maxent": {"n": N_PA, "pesos": "ninguno"},        # presence-background
}


# ---------------------------------------------------------------------------
# Máscara de calibración (tierra ∩ Chile)
# ---------------------------------------------------------------------------

def _cargar_land_mask() -> tuple[np.ndarray, rasterio.DatasetReader]:
    """Carga land_mask.tif (1=tierra). Devuelve (bool_tierra, DatasetReader)."""
    mask_path = config.WORLDCLIM_PRESENT / "land_mask.tif"
    if not mask_path.exists():
        raise FileNotFoundError(
            f"Máscara de tierra no encontrada: {mask_path} (generar en etapa 2)."
        )
    ds = rasterio.open(mask_path)
    return (ds.read(1) > 0), ds


def cargar_mascara_calibracion() -> tuple[np.ndarray, rasterio.DatasetReader]:
    """Máscara booleana tierra ∩ Chile (área de calibración) + DatasetReader.

    Calibrar contra background planetario infla la discriminación (AUC ~0.99
    triviales); por eso el background se restringe a Chile (especies endémicas).
    Usa el polígono Natural Earth de Chile; si falta, cae a CALIBRATION_BBOX.
    El llamador debe cerrar el DatasetReader.
    """
    from rasterio.features import rasterize

    land, ds = _cargar_land_mask()
    try:
        ne_path = config.RAW / "natural_earth" / "ne_110m_admin0_countries.gpkg"
        gdf = gpd.read_file(ne_path)
        name_col = next(
            (c for c in ("ADMIN", "NAME", "SOVEREIGNT", "GEOUNIT")
             if c in gdf.columns
             and (gdf[c].astype(str) == config.CALIBRATION_COUNTRY).any()),
            None,
        )
        if name_col is None:
            raise ValueError(f"'{config.CALIBRATION_COUNTRY}' no está en {ne_path.name}")
        geom = gdf[gdf[name_col].astype(str) == config.CALIBRATION_COUNTRY].geometry
        pais = rasterize([(g, 1) for g in geom], out_shape=land.shape,
                         transform=ds.transform, fill=0, dtype="uint8").astype(bool)
        calib = land & pais
        log.info("Calibración = tierra ∩ %s: %d celdas (de %d en tierra).",
                 config.CALIBRATION_COUNTRY, int(calib.sum()), int(land.sum()))
    except Exception as exc:  # noqa: BLE001
        log.warning("Sin polígono de %s (%s); uso CALIBRATION_BBOX.",
                    config.CALIBRATION_COUNTRY, exc)
        minx, miny, maxx, maxy = config.CALIBRATION_BBOX
        xs, _ = ds.xy(np.zeros(land.shape[1], int), np.arange(land.shape[1]))
        _, ys = ds.xy(np.arange(land.shape[0]), np.zeros(land.shape[0], int))
        calib = land & np.outer((np.array(ys) >= miny) & (np.array(ys) <= maxy),
                                (np.array(xs) >= minx) & (np.array(xs) <= maxx))

    if calib.sum() == 0:
        log.error("Área de calibración vacía; revierto a toda la tierra.")
        return land, ds
    return calib, ds


# ---------------------------------------------------------------------------
# Muestreo de background
# ---------------------------------------------------------------------------

def muestrear_background(n: int = N_PA, seed: int = config.RANDOM_SEED) -> pd.DataFrame:
    """Devuelve n puntos background random uniformes dentro de Chile (lon, lat).

    Añade jitter sub-píxel para que no caigan exactamente en el centro de celda.
    Si n supera las celdas disponibles, usa todas.

    NOTA: muestreo histórico restringido a Chile. Para presencias de Sudamérica
    usar `muestrear_background_especie` (área accesible por especie), que evita el
    desajuste presencia(SA)/fondo(Chile).
    """
    rng = np.random.default_rng(seed)
    calib, ds = cargar_mascara_calibracion()
    try:
        rows, cols = np.where(calib)
        disp = len(rows)
        if n > disp:
            log.warning("Se pidieron %d puntos pero hay %d celdas; uso todas.", n, disp)
            n = disp
        idx = rng.choice(disp, size=n, replace=False)
        lons, lats = ds.xy(rows[idx], cols[idx])
        rx, ry = ds.transform.a, abs(ds.transform.e)
        lons = np.array(lons) + rng.uniform(-rx / 2, rx / 2, size=n)
        lats = np.array(lats) + rng.uniform(-ry / 2, ry / 2, size=n)
    finally:
        ds.close()
    log.info("Background: %d puntos random en %s.", len(lons), config.CALIBRATION_COUNTRY)
    return pd.DataFrame({"lon": lons, "lat": lats, "presence": 0})


# Grilla de terreno de Sudamérica (ya enmascarada a tierra): sirve de máscara
# tierra∩SA y de grilla de muestreo, coherente con la grilla de predicción de [07].
_GRILLA_SA = _ROOT / "rama_v4" / "data" / "processed" / "rasters_terreno" / "slope.tif"

# Radio del área accesible (M) alrededor de las presencias. 300 km es un valor
# estándar para definir el fondo de calibración a partir de las presencias.
BUFFER_KM = 300.0


def muestrear_background_especie(
    pres_lon: np.ndarray,
    pres_lat: np.ndarray,
    n: int = N_PA,
    buffer_km: float = BUFFER_KM,
    seed: int = config.RANDOM_SEED,
) -> pd.DataFrame:
    """Background dentro del área accesible (M) de UNA especie.

    Área accesible = celdas de tierra de Sudamérica a <= `buffer_km` de alguna
    presencia (distancia geodésica exacta, BallTree-haversine). Así el fondo
    representa la región que la especie podría ocupar: ni restringido a Chile
    cuando las presencias se extienden por Sudamérica (desajuste), ni todo el
    continente cuando son endémicas estrictas (inflación trivial de la
    discriminación). Devuelve n puntos (lon, lat, presence=0) con jitter sub-píxel.
    """
    from sklearn.neighbors import BallTree

    with rasterio.open(_GRILLA_SA) as ds:
        arr = ds.read(1).astype("float32")
        valid = ~np.isnan(arr)
        nod = ds.nodata
        if nod is not None and not np.isnan(nod):
            valid &= arr != nod
        rows, cols = np.where(valid)
        xs, ys = ds.xy(rows, cols)            # lon, lat de centros de celda tierra-SA
        rx, ry = ds.transform.a, abs(ds.transform.e)

    xs = np.asarray(xs, dtype="float64")
    ys = np.asarray(ys, dtype="float64")

    # Celdas a <= buffer_km de alguna presencia (great-circle exacta).
    tree = BallTree(np.radians(np.column_stack([pres_lat, pres_lon])), metric="haversine")
    dist, _ = tree.query(np.radians(np.column_stack([ys, xs])), k=1)
    within = (dist[:, 0] * 6371.0088) <= buffer_km
    xs, ys = xs[within], ys[within]
    disp = len(xs)
    if disp == 0:
        raise ValueError("Área accesible vacía: ninguna celda de tierra dentro del buffer.")

    rng = np.random.default_rng(seed)
    n_use = min(n, disp)
    if n_use < n:
        log.warning("Área accesible con %d celdas (< %d pedidas); uso todas.", disp, n)
    idx = rng.choice(disp, size=n_use, replace=False)
    lon = xs[idx] + rng.uniform(-rx / 2, rx / 2, size=n_use)
    lat = ys[idx] + rng.uniform(-ry / 2, ry / 2, size=n_use)
    log.info("Background especie: %d puntos en buffer %.0f km (%d celdas accesibles).",
             n_use, buffer_km, disp)
    return pd.DataFrame({"lon": lon, "lat": lat, "presence": 0})


# ---------------------------------------------------------------------------
# Pesos por algoritmo
# ---------------------------------------------------------------------------

def _pesos(presence: np.ndarray, modo: str) -> np.ndarray:
    """Pesos de caso para un array presence (1/0) según `modo`.

    'prevalencia' : presencia=1; background = n_pres/n_bg  → Σpres = Σbg (prev 0.5).
    'ninguno'     : todos 1 (MaxEnt, presence-background).
    """
    presence = np.asarray(presence).astype(int)
    w = np.ones(len(presence), dtype="float64")
    if modo == "ninguno":
        return w
    if modo == "prevalencia":
        n_pres = int((presence == 1).sum())
        n_bg = int((presence == 0).sum())
        if n_bg > 0:
            w[presence == 0] = n_pres / n_bg
        return w
    raise ValueError(f"modo de pesos desconocido: {modo}")


def pesos_por_algoritmo(presence: np.ndarray) -> dict[str, np.ndarray]:
    """Devuelve {algoritmo: array_de_pesos} según ESQUEMA_PA.

    GLM/GAM/RF/GBM usan pesos de prevalencia 0.5 (RF/GBM = ajuste Valavi:
    background grande ponderado). MaxEnt usa pesos uniformes (presence-background).
    """
    return {algo: _pesos(presence, cfg["pesos"]) for algo, cfg in ESQUEMA_PA.items()}
