"""
coords.py — Paso 5: coordenadas sospechosas / inválidas.

Elimina coordenadas geográficamente imposibles o estadísticamente sospechosas:
(0, 0), fuera de rango (|lat| > 90, |lon| > 180), pares con parte decimal == 0
(registro truncado a entero) y lat/lon nulas. Misma lógica que la versión previa
(`01_limpieza.filtrar_coords_sospechosas`), aquí operando sobre un DataFrame
plano (sin geometría).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd

# utils.py vive en scripts/ — ponerlo en el path para importarlo.
_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import utils  # noqa: E402

logger = utils.get_logger("limpieza.coords")

# Patrón sospechoso: parte decimal exactamente .0 (coordenada truncada a entero).
_DECIMAL_ZERO_THRESH: float = 1e-9


def _tiene_decimal_cero(valor: float) -> bool:
    """True si la parte decimal del número es exactamente 0.

    NaN devuelve False (las filas con lat/lon nulas se descartan por su propia
    máscara, no por ésta).
    """
    if math.isnan(valor):
        return False
    parte_decimal = abs(valor) - math.floor(abs(valor))
    return parte_decimal < _DECIMAL_ZERO_THRESH


def filtrar_coords_sospechosas(df: pd.DataFrame) -> pd.DataFrame:
    """Elimina coordenadas geográficamente inválidas o estadísticamente sospechosas.

    Reglas aplicadas:
    - (lat, lon) = (0, 0) exacto: coordenada nula por defecto.
    - lat o lon fuera de rango válido (|lat| > 90, |lon| > 180).
    - lat y lon ambas con parte decimal == 0: registro truncado a entero.
    - lat NaN o lon NaN: no utilizables.
    """
    n_antes = len(df)

    mascara_nula = df["lat"].isna() | df["lon"].isna()

    mascara_cero = (
        (df["lat"].abs() < _DECIMAL_ZERO_THRESH)
        & (df["lon"].abs() < _DECIMAL_ZERO_THRESH)
    )

    mascara_rango = (df["lat"].abs() > 90) | (df["lon"].abs() > 180)

    # Ambas coordenadas truncadas a entero (decimal == 0)
    mascara_truncada = df.apply(
        lambda r: _tiene_decimal_cero(r["lat"]) and _tiene_decimal_cero(r["lon"]),
        axis=1,
    )

    mascara_mala = mascara_nula | mascara_cero | mascara_rango | mascara_truncada

    n_nula = mascara_nula.sum()
    n_cero = mascara_cero.sum()
    n_rango = mascara_rango.sum()
    n_trunc = (mascara_truncada & ~(mascara_nula | mascara_cero | mascara_rango)).sum()

    df = df[~mascara_mala]
    n_despues = len(df)

    logger.info(
        "Paso 5 | Coords sospechosas eliminadas: %d → %d (−%d) "
        "[NaN=%d, (0,0)=%d, rango=%d, truncadas=%d]",
        n_antes, n_despues, n_antes - n_despues,
        n_nula, n_cero, n_rango, n_trunc,
    )
    return df.reset_index(drop=True)
