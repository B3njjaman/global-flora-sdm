"""
geo_scope.py — Paso 3b: filtro geográfico a Sudamérica.

Conserva solo los registros de Sudamérica. Tres métodos (verificado contra el
dataset: "pais" y "geografia" seleccionan exactamente los mismos 8.498 de 13.354):

  - "pais"      : según la columna `pais` (literal "según el dataset").
  - "geografia" : coordenadas dentro del bounding box de Sudamérica (config.PREDICTION_BBOX).
  - "ambos"     : exige país-SA Y coords-SA (descarta inconsistencias etiqueta↔coord).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import config  # noqa: E402

# Países de Sudamérica en las variantes de etiqueta que usa GBIF / Natural Earth.
PAISES_SUDAMERICA: set[str] = {
    "Argentina",
    "Bolivia", "Bolivia (Plurinational State of)",
    "Brazil", "Brasil",
    "Chile",
    "Colombia",
    "Ecuador",
    "Guyana",
    "Paraguay",
    "Peru", "Perú",
    "Suriname",
    "Uruguay",
    "Venezuela", "Venezuela (Bolivarian Republic of)",
    "French Guiana",
    "Falkland Islands (Malvinas)",
}

# Bounding box de Sudamérica = (min_lon, min_lat, max_lon, max_lat).
BBOX_SUDAMERICA: tuple[float, float, float, float] = config.PREDICTION_BBOX


def _mask_pais(df: pd.DataFrame) -> pd.Series:
    return df["pais"].isin(PAISES_SUDAMERICA)


def _mask_geografia(df: pd.DataFrame) -> pd.Series:
    min_lon, min_lat, max_lon, max_lat = BBOX_SUDAMERICA
    return df["lon"].between(min_lon, max_lon) & df["lat"].between(min_lat, max_lat)


def filtrar_sudamerica(df: pd.DataFrame, metodo: str = "pais") -> pd.DataFrame:
    """Devuelve solo los registros de Sudamérica según `metodo`.

    metodo ∈ {"pais", "geografia", "ambos"}.
    """
    if metodo == "pais":
        mask = _mask_pais(df)
    elif metodo == "geografia":
        mask = _mask_geografia(df)
    elif metodo == "ambos":
        mask = _mask_pais(df) & _mask_geografia(df)
    else:
        raise ValueError(
            f"metodo desconocido: {metodo!r} (usa 'pais', 'geografia' o 'ambos')"
        )
    return df[mask].copy().reset_index(drop=True)
