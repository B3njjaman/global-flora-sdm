"""
pipeline.py — Orquestador modular de la limpieza (V4).

Crece paso a paso. Estado actual:
  0. Cargar el dataset GBIF crudo        (io.cargar_ocurrencias_crudas)
  3b. Filtrar a Sudamérica → 'Especies_sudamerica'  (geo_scope.filtrar_sudamerica)

Pasos siguientes a portar desde la versión previa (en orden): duplicados,
incertidumbre, coords sospechosas, centroides admin, océano, thinning, grupos A/B/C.
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
import utils   # noqa: E402

from . import geo_scope, io

log = utils.get_logger("limpieza.pipeline")

# Salida del filtro de Sudamérica (nombre solicitado: 'Especies_sudamerica').
ESPECIES_SUDAMERICA: Path = config.PROCESSED / "Especies_sudamerica.csv"


def run(metodo_sa: str = "pais", salida: Path | None = None) -> pd.DataFrame:
    """Ejecuta la limpieza modular (por ahora: carga + filtro Sudamérica).

    Parámetros
    ----------
    metodo_sa : método del filtro Sudamérica — "pais" | "geografia" | "ambos".
    salida    : ruta del CSV de salida (defecto: ESPECIES_SUDAMERICA).
    """
    salida = Path(salida) if salida else ESPECIES_SUDAMERICA
    utils.ensure_dirs(config.PROCESSED)

    # --- Paso 0: cargar dataset crudo ---
    df = io.cargar_ocurrencias_crudas()
    log.info("Cargados %d registros crudos desde %s", len(df), config.OCCURRENCES_XLSX.name)

    # --- Paso 3b: filtro Sudamérica ---
    n0 = len(df)
    df_sa = geo_scope.filtrar_sudamerica(df, metodo=metodo_sa)
    log.info(
        "Filtro Sudamérica (metodo=%s): %d → %d (−%d, %.1f%% retenido)",
        metodo_sa, n0, len(df_sa), n0 - len(df_sa), 100.0 * len(df_sa) / max(n0, 1),
    )
    log.info("Países retenidos:\n%s", df_sa["pais"].value_counts().to_string())
    log.info(
        "Registros por especie en Sudamérica:\n%s",
        df_sa["especie"].value_counts().to_string(),
    )

    # --- Guardar 'Especies_sudamerica' ---
    df_sa.to_csv(salida, index=False, encoding="utf-8")
    log.info("Guardado: %s  (%d filas × %d columnas)", salida, len(df_sa), df_sa.shape[1])
    return df_sa
