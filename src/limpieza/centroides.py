"""
centroides.py — Paso 3: detección y eliminación de centroides administrativos.

Descarta puntos que coinciden (dentro de config.CENTROID_TOLERANCE_KM km) con
el centroide de un país (Natural Earth admin-0) o de un estado/provincia
(admin-1). Estos puntos suelen ser geocodificaciones imprecisas a nivel de
unidad administrativa, no observaciones reales.

Misma lógica que la versión previa (`01_limpieza.filtrar_centroides_admin`),
aquí operando sobre un DataFrame plano (columnas `lon`, `lat`) en vez de un
GeoDataFrame. Si alguna capa Natural Earth no está disponible, el paso se omite
con advertencia y devuelve el df sin cambios (no interrumpe el pipeline).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Geod

# config.py / utils.py viven en scripts/ — ponerlos en el path para importarlos.
_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import config  # noqa: E402
import utils  # noqa: E402

from . import natural_earth

logger = utils.get_logger("limpieza.centroides")

# Tolerancia centroide en metros (pyproj usa metros para geodésicas).
_CENTROID_TOL_M: float = config.CENTROID_TOLERANCE_KM * 1_000.0


def _distancia_minima_m(
    lon: float,
    lat: float,
    centroides_lon: np.ndarray,
    centroides_lat: np.ndarray,
    geod: Geod,
) -> float:
    """Distancia geodésica mínima (metros) entre un punto y un array de centroides."""
    if len(centroides_lon) == 0:
        return float("inf")
    lons_rep = np.full(len(centroides_lon), lon)
    lats_rep = np.full(len(centroides_lat), lat)
    _, _, dists = geod.inv(lons_rep, lats_rep, centroides_lon, centroides_lat)
    return float(np.nanmin(dists))


def filtrar_centroides_admin(df: pd.DataFrame) -> pd.DataFrame:
    """Elimina puntos que coinciden con centroides de país o región.

    Usa Natural Earth admin-0 (110m) y admin-1 (10m). Si alguna capa no está
    disponible, el paso se omite con advertencia pero no interrumpe el pipeline.

    Tolerancia: config.CENTROID_TOLERANCE_KM km.
    """
    n_antes = len(df)

    # Recopilar centroides de ambos niveles administrativos.
    centroid_lons: list[float] = []
    centroid_lats: list[float] = []

    for nombre, loader in [
        ("admin-0", natural_earth.cargar_centroides_admin0),
        ("admin-1", natural_earth.cargar_centroides_admin1),
    ]:
        capa = loader()
        if capa is None:
            logger.warning(
                "Paso 3 | Capa %s no disponible; se omite detección de centroides"
                " a ese nivel. Descargue manualmente las capas Natural Earth"
                " para activar este filtro.",
                nombre,
            )
            continue
        pts = capa["centroid_geom"]
        centroid_lons.extend(pts.x.tolist())
        centroid_lats.extend(pts.y.tolist())
        logger.debug("Paso 3 | %d centroides cargados desde %s", len(pts), nombre)

    if not centroid_lons:
        logger.warning("Paso 3 | Sin centroides disponibles; paso omitido.")
        return df.reset_index(drop=True)

    arr_lon = np.array(centroid_lons)
    arr_lat = np.array(centroid_lats)
    geod = Geod(ellps="WGS84")

    # Evaluar cada punto.
    es_centroide = np.zeros(len(df), dtype=bool)
    for i, row in enumerate(df.itertuples(index=False)):
        dist = _distancia_minima_m(row.lon, row.lat, arr_lon, arr_lat, geod)
        if dist <= _CENTROID_TOL_M:
            es_centroide[i] = True

    df = df[~es_centroide]
    n_despues = len(df)
    logger.info(
        "Paso 3 | Centroides admin eliminados: %d → %d (−%d)",
        n_antes, n_despues, n_antes - n_despues,
    )
    return df.reset_index(drop=True)
