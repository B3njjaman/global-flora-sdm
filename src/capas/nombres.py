"""
nombres.py — Nombres en español de las variables predictoras.

Traduce los códigos WorldClim/terreno (`bioN`, `slope`, …) a nombres
descriptivos en español (snake_case, aptos como nombre de columna) y a una
descripción legible con unidades. Así la base de datos se lee por lo que las
variables de verdad significan, no por un código.
"""
from __future__ import annotations

import pandas as pd

# código → (nombre_es snake_case, descripción legible, unidad)
VARIABLES: dict[str, tuple[str, str, str]] = {
    # --- Clima (WorldClim v2.1 bioclim) ---
    "bio1":  ("temp_media_anual",        "Temperatura media anual",                         "°C"),
    "bio4":  ("estacionalidad_temp",     "Estacionalidad de temperatura (desv. est. ×100)", "°C×100"),
    "bio5":  ("temp_max_mes_calido",     "Temperatura máxima del mes más cálido",           "°C"),
    "bio6":  ("temp_min_mes_frio",       "Temperatura mínima del mes más frío",             "°C"),
    "bio7":  ("rango_anual_temp",        "Rango anual de temperatura (bio5 − bio6)",        "°C"),
    "bio10": ("temp_media_trim_calido",  "Temperatura media del trimestre más cálido",      "°C"),
    "bio11": ("temp_media_trim_frio",    "Temperatura media del trimestre más frío",        "°C"),
    "bio12": ("precip_anual",            "Precipitación anual",                             "mm"),
    "bio15": ("estacionalidad_precip",   "Estacionalidad de precipitación (coef. variación)", "%"),
    "bio17": ("precip_trim_seco",        "Precipitación del trimestre más seco",            "mm"),
    # --- Terreno ---
    "elevation": ("elevacion",        "Elevación sobre el nivel del mar",                   "m"),
    "slope":     ("pendiente",        "Pendiente del terreno",                              "grados"),
    "northness": ("exposicion_norte", "Orientación N–S de la ladera (1=N, −1=S)",           "índice [-1,1]"),
    "eastness":  ("exposicion_este",  "Orientación E–O de la ladera (1=E, −1=O)",           "índice [-1,1]"),
}

# código → nombre_es (para renombrar columnas)
NOMBRES_ES: dict[str, str] = {cod: v[0] for cod, v in VARIABLES.items()}


def renombrar(df: pd.DataFrame) -> pd.DataFrame:
    """Devuelve `df` con las columnas de predictoras renombradas al español."""
    return df.rename(columns=NOMBRES_ES)


def diccionario() -> pd.DataFrame:
    """Tabla código → nombre en español → descripción → unidad."""
    filas = [
        {"codigo": cod, "nombre_es": nom, "descripcion": desc, "unidad": uni}
        for cod, (nom, desc, uni) in VARIABLES.items()
    ]
    return pd.DataFrame(filas)
