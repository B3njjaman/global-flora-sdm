"""
natural_earth.py — Carga (con cacheo) de las capas Natural Earth compartidas.

Módulo de soporte para los pasos de limpieza que dependen de Natural Earth:
centroides administrativos (`centroides.py`) y máscara de océano (`oceano.py`).
Centraliza la descarga bajo demanda y el cacheo local en
`data/raw/natural_earth/` (ruta derivada de `config.RAW`).

Interfaz pública (la consumen los pasos que la importan vía
`from . import natural_earth`):

    cargar_land_mask()        -> GeoDataFrame | None   (polígono de tierra 110m)
    cargar_centroides_admin0() -> GeoDataFrame | None  (centroides de países 110m)
    cargar_centroides_admin1() -> GeoDataFrame | None  (centroides de estados 10m)

Las funciones devuelven ``None`` (no lanzan) si la capa no está en caché ni se
puede descargar; los pasos que las usan deben omitirse en ese caso. Misma lógica
que la versión previa (`01_limpieza._load_ne_layer` y derivados).
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path
from typing import Optional

import geopandas as gpd

# config.py / utils.py viven en scripts/ — ponerlos en el path para importarlos.
_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import config  # noqa: E402
import utils  # noqa: E402

logger = utils.get_logger("limpieza.natural_earth")

# URLs Natural Earth (solo se descargan si no se puede leer localmente).
_NE_LAND_URL = (
    "https://naturalearth.s3.amazonaws.com/110m_physical/ne_110m_land.zip"
)
_NE_ADMIN0_URL = (
    "https://naturalearth.s3.amazonaws.com/110m_cultural/ne_110m_admin_0_countries.zip"
)
_NE_ADMIN1_URL = (
    "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_1_states_provinces.zip"
)

# Caché local para las capas Natural Earth (dentro de data/raw para reproducibilidad).
_NE_CACHE_DIR: Path = config.RAW / "natural_earth"


def _load_ne_layer(url: str, cache_name: str) -> Optional[gpd.GeoDataFrame]:
    """Carga una capa Natural Earth desde caché local o la descarga.

    Parámetros
    ----------
    url:
        URL del archivo .zip en el servidor de Natural Earth.
    cache_name:
        Nombre de archivo local (sin extensión) para caché en _NE_CACHE_DIR.

    Devuelve
    -------
    GeoDataFrame o None si no hay caché ni acceso a red.
    """
    cache_path = _NE_CACHE_DIR / f"{cache_name}.gpkg"

    # 1. Intentar caché local
    if cache_path.exists():
        logger.debug("Cargando capa NE desde caché: %s", cache_path)
        try:
            return gpd.read_file(cache_path)
        except Exception as exc:
            logger.warning("Caché dañada (%s), intentando descarga: %s", cache_path, exc)

    # 2. Intentar descarga
    logger.info("Descargando capa Natural Earth: %s", url)
    try:
        import requests  # solo se usa aquí; no es dep dura del módulo

        resp = requests.get(url, timeout=60)
        resp.raise_for_status()

        utils.ensure_dirs(_NE_CACHE_DIR)
        with zipfile.ZipFile(io.BytesIO(resp.content)):
            # geopandas puede leer el zip directamente vía /vsiz/
            pass  # solo verificamos que sea zip válido

        gdf = gpd.read_file(f"/vsizip/vsicurl/{url}")
        gdf.to_file(cache_path, driver="GPKG")
        logger.info("Capa guardada en caché: %s", cache_path)
        return gdf

    except Exception as exc:
        logger.warning(
            "No se pudo descargar la capa Natural Earth '%s': %s. "
            "Descárguela manualmente desde %s y guárdela en %s.",
            cache_name, exc, url, _NE_CACHE_DIR,
        )
        return None


def cargar_land_mask() -> Optional[gpd.GeoDataFrame]:
    """Carga el polígono de tierra Natural Earth 110m (o None si no disponible)."""
    return _load_ne_layer(_NE_LAND_URL, "ne_110m_land")


def cargar_centroides_admin0() -> Optional[gpd.GeoDataFrame]:
    """Carga países Natural Earth 110m y añade su centroide (columna 'centroid_geom').

    Devuelve None si la capa no está disponible.
    """
    gdf = _load_ne_layer(_NE_ADMIN0_URL, "ne_110m_admin0_countries")
    if gdf is None:
        return None
    gdf = gdf[["geometry"]].copy()
    gdf["centroid_geom"] = gdf.geometry.centroid
    return gdf


def cargar_centroides_admin1() -> Optional[gpd.GeoDataFrame]:
    """Carga provincias/estados Natural Earth 10m y añade su centroide.

    Nota: la capa 10m es detallada (~4 MB zip); si la red es lenta puede tardar.
    Devuelve None si la capa no está disponible.
    """
    gdf = _load_ne_layer(_NE_ADMIN1_URL, "ne_10m_admin1_states_provinces")
    if gdf is None:
        return None
    gdf = gdf[["geometry"]].copy()
    gdf["centroid_geom"] = gdf.geometry.centroid
    return gdf
