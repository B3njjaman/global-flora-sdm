"""
dedup.py — Paso 1: eliminación de registros duplicados.

Elimina filas duplicadas por (especie, lat, lon), conservando la primera
ocurrencia. Misma lógica que la versión previa
(`01_limpieza.eliminar_duplicados`).
"""
from __future__ import annotations

import pandas as pd

# Un mismo punto (misma especie y mismas coordenadas) es el mismo registro.
CLAVES_DUPLICADO: list[str] = ["especie", "lat", "lon"]


def eliminar_duplicados(
    df: pd.DataFrame,
    claves: list[str] | None = None,
) -> pd.DataFrame:
    """Devuelve el DataFrame sin duplicados por `claves` (conserva el primero).

    Por defecto la clave de duplicado es (especie, lat, lon).
    """
    claves = claves or CLAVES_DUPLICADO
    return df.drop_duplicates(subset=claves, keep="first").reset_index(drop=True)
