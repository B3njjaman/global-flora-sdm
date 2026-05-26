"""
incertidumbre.py — Paso 2: filtro por incertidumbre de coordenada.

Descarta registros cuya incertidumbre supera config.MAX_COORD_UNCERTAINTY_M
(10.000 m). Los registros SIN incertidumbre (NaN) se conservan: son mayoría en
GBIF y su ausencia de metadato no implica mala calidad. Misma lógica que la
versión previa (`01_limpieza.filtrar_incertidumbre`), aquí operando sobre un
DataFrame plano (sin geometría).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

# config.py / utils.py viven en scripts/ — ponerlos en el path para importarlos.
_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import config  # noqa: E402
import utils  # noqa: E402

logger = utils.get_logger("limpieza.incertidumbre")


def filtrar_incertidumbre(df: pd.DataFrame) -> pd.DataFrame:
    """Descarta registros con incertidumbre_m > config.MAX_COORD_UNCERTAINTY_M.

    Registros sin incertidumbre (NaN) se conservan: son mayoría en GBIF y
    su ausencia de metadato no implica mala calidad.
    """
    n_antes = len(df)
    mascara_mala = (
        df["incertidumbre_m"].notna()
        & (df["incertidumbre_m"] > config.MAX_COORD_UNCERTAINTY_M)
    )
    df = df[~mascara_mala]
    n_despues = len(df)
    logger.info(
        "Paso 2 | Incertidumbre > %d m eliminados: %d → %d (−%d)",
        config.MAX_COORD_UNCERTAINTY_M, n_antes, n_despues, n_antes - n_despues,
    )
    return df.reset_index(drop=True)
