"""
utils.py — Utilidades compartidas del pipeline SDM (global-flora-sdm).

Funciones que usan múltiples etapas: logging consistente, carga de ocurrencias
crudas con manejo de codificación, y helpers menores. Mantener ligero.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

import config


def get_logger(name: str) -> logging.Logger:
    """Logger con formato consistente para todas las etapas."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(name)-18s | %(levelname)-7s | %(message)s",
                              datefmt="%H:%M:%S")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def ensure_dirs(*paths: Path) -> None:
    """Crea directorios si no existen (idempotente)."""
    for p in paths:
        Path(p).mkdir(parents=True, exist_ok=True)


def load_raw_occurrences() -> pd.DataFrame:
    """Carga el .xlsx de GBIF y normaliza nombres de columnas a snake_case ASCII.

    Renombra a un esquema estable que el resto del pipeline asume:
      especie, nombre_cientifico, lat, lon, incertidumbre_m, pais, region,
      localidad, fecha, ano, tipo_registro, institucion, dataset, catalogo, gbif_id
    """
    df = pd.read_excel(config.OCCURRENCES_XLSX, sheet_name=config.OCCURRENCES_SHEET)
    rename = {
        "Especie": "especie",
        "Nombre cientifico GBIF": "nombre_cientifico",
        "Latitud": "lat",
        "Longitud": "lon",
        "Incertidumbre (m)": "incertidumbre_m",
        "Pais": "pais",
        "Region / Estado": "region",
        "Localidad": "localidad",
        "Fecha": "fecha",
        "Ano": "ano",
        "Tipo de registro": "tipo_registro",
        "Institucion": "institucion",
        "Dataset": "dataset",
        "Catalogo": "catalogo",
        "GBIF ID": "gbif_id",
    }
    df = df.rename(columns=rename)
    return df


def species_counts() -> dict[str, int]:
    """Conteo de registros crudos por especie (para clasificación A/B/C)."""
    df = load_raw_occurrences()
    return df["especie"].value_counts().to_dict()


def slugify_species(name: str) -> str:
    """'Schinus areira' -> 'schinus_areira' (para nombres de archivo)."""
    return name.strip().lower().replace(" ", "_").replace(".", "")
