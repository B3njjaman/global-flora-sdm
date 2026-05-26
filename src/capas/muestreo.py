"""
muestreo.py — Extrae los valores de las capas en puntos (lon, lat).

Dado un DataFrame con columnas lon/lat y un dict {nombre: ruta_raster}, agrega
una columna por capa con el valor del píxel que contiene cada punto. Los puntos
sin dato (nodata, o fuera del raster) quedan como NaN.

Es el puente entre las ocurrencias limpias (etapa 1) y las capas (etapa 2):
permite "ver" cómo se enriquece el dataset con el clima antes de la extracción
formal de la etapa 4.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import rasterio

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import utils  # noqa: E402

log = utils.get_logger("capas.muestreo")


def muestrear_capas(
    df: pd.DataFrame,
    capas: dict[str, Path],
    lon_col: str = "lon",
    lat_col: str = "lat",
) -> pd.DataFrame:
    """Devuelve `df` con una columna por capa (valor del píxel en cada punto).

    Los puntos sobre nodata o fuera del raster quedan como NaN.
    """
    coords = list(zip(df[lon_col].astype(float), df[lat_col].astype(float)))
    out = df.copy()
    for nombre, ruta in capas.items():
        with rasterio.open(ruta) as src:
            vals = [v[0] for v in src.sample(coords)]
            s = pd.Series(vals, index=df.index, dtype="float64")
            if src.nodata is not None:
                s = s.mask(s == src.nodata)
        out[nombre] = s
        n_nan = int(s.isna().sum())
        log.info("Capa %-10s muestreada (%d puntos, %d sin dato)", nombre, len(s), n_nan)
    return out
