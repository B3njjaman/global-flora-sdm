"""
thinning.py — Paso 6: thinning espacial.

Retiene 1 punto por celda de la grilla 2.5 arc-min (config.WORLDCLIM_RES,
config.THINNING_PER_CELL) POR ESPECIE. La celda se calcula por floor division
de lat/lon sobre la rejilla 2.5' = 1/24 ° — sin geometría. Misma lógica de
índice de celda que la versión previa (`01_limpieza.thinning_espacial` y su
helper `_asignar_celda`), aquí adaptada a un DataFrame plano (especie, lat, lon).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# config.py / utils.py viven en scripts/ — ponerlos en el path para importarlos.
_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import config  # noqa: E402
import utils  # noqa: E402

logger = utils.get_logger("limpieza.thinning")

# 2.5 arc-min = 2.5 / 60 grados = 1/24 ° (~0.04167 °) — rejilla de thinning.
_CELL_SIZE_DEG: float = 2.5 / 60.0


def _asignar_celda(lat: pd.Series, lon: pd.Series) -> pd.Series:
    """Devuelve una clave de celda como string 'col_fila' para la grilla 2.5'."""
    col = np.floor(lon / _CELL_SIZE_DEG).astype(int)
    fila = np.floor(lat / _CELL_SIZE_DEG).astype(int)
    return pd.Series(
        [f"{c}_{f}" for c, f in zip(col, fila)], index=lat.index
    )


def thinning_espacial(df: pd.DataFrame) -> pd.DataFrame:
    """Retiene config.THINNING_PER_CELL punto(s) por celda 2.5' por especie.

    A cada registro se le asigna la celda de la grilla de 2.5' = 1/24 ° por
    floor division de lat/lon; dentro de cada (especie, celda) se conserva el
    primer registro. Opera sobre un DataFrame plano (sin geometría).
    """
    n_antes = len(df)
    df = df.copy()
    df["_celda"] = _asignar_celda(df["lat"], df["lon"])
    df = df.drop_duplicates(subset=["especie", "_celda"], keep="first")
    df = df.drop(columns=["_celda"])

    n_despues = len(df)
    logger.info(
        "Paso 6 | Thinning 2.5' (1 pt/celda/especie): %d → %d (−%d)",
        n_antes, n_despues, n_antes - n_despues,
    )
    return df.reset_index(drop=True)
