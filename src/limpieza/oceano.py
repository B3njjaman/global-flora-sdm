"""
oceano.py — Paso 4: eliminación de coordenadas en el océano.

Descarta puntos que no intersectan con la máscara de tierra de Natural Earth
(land mask 110m): observaciones cuyas coordenadas caen en el mar suelen ser
errores de geocodificación. La geometría se construye internamente a partir de
las columnas `lon`/`lat` del DataFrame plano.

Misma lógica que la versión previa (`01_limpieza.filtrar_oceano`), aquí operando
sobre un DataFrame plano en vez de un GeoDataFrame. Si la land mask no está
disponible, el paso se omite con advertencia y devuelve el df sin cambios (no
interrumpe el pipeline).
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

# config.py / utils.py viven en scripts/ — ponerlos en el path para importarlos.
_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import config  # noqa: E402
import utils  # noqa: E402

from . import natural_earth

logger = utils.get_logger("limpieza.oceano")


def filtrar_oceano(df: pd.DataFrame) -> pd.DataFrame:
    """Descarta puntos que no intersectan con la land mask Natural Earth.

    La geometría se construye internamente desde `df.lon` / `df.lat`. Si la capa
    no está disponible, el paso se omite con advertencia y devuelve el df intacto.
    """
    n_antes = len(df)
    land = natural_earth.cargar_land_mask()
    if land is None:
        logger.warning(
            "Paso 4 | Land mask no disponible; paso de filtrado oceánico omitido."
        )
        return df.reset_index(drop=True)

    # Asegurar mismo CRS y unir polígonos de tierra para un sjoin eficiente.
    land = land.to_crs(config.CRS_GEO)
    land_union = land.dissolve()

    # Construir geometría de los puntos a partir de lon/lat.
    puntos_gdf = gpd.GeoDataFrame(
        df[["lon", "lat"]].reset_index(drop=True),
        geometry=gpd.points_from_xy(df["lon"], df["lat"]),
        crs=config.CRS_GEO,
    )

    en_tierra = gpd.sjoin(
        puntos_gdf.reset_index(),
        land_union[["geometry"]],
        how="left",
        predicate="within",
    )
    idx_tierra = en_tierra[en_tierra["index_right"].notna()]["index"].values

    df = df.iloc[idx_tierra]
    n_despues = len(df)
    logger.info(
        "Paso 4 | Coords en océano eliminadas: %d → %d (−%d)",
        n_antes, n_despues, n_antes - n_despues,
    )
    return df.reset_index(drop=True)
