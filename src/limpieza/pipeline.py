"""
pipeline.py — Orquestador modular de la limpieza (V4).

Crece paso a paso. Estado actual:
  0. Cargar el dataset GBIF crudo            (io.cargar_ocurrencias_crudas)
  1. Filtrar a Sudamérica                    (geo_scope.filtrar_sudamerica)
  2. Eliminar duplicados (especie+lat+lon)   (dedup.eliminar_duplicados)
  3. Coordenadas sospechosas / inválidas     (coords.filtrar_coords_sospechosas)
  4. Thinning espacial 2.5' (1 pt/celda/sp)  (thinning.thinning_espacial)
  → guarda 'Especies_sudamerica'.

Pasos siguientes a portar desde la versión previa (se integran de a uno, guiados):
incertidumbre, centroides admin, océano, grupos A/B/C.
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

from . import coords, dedup, geo_scope, io, thinning

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

    # --- Paso 1: eliminar duplicados (especie + lat + lon) ---
    n_sa = len(df_sa)
    df_dedup = dedup.eliminar_duplicados(df_sa)
    log.info(
        "Duplicados (especie+lat+lon): %d → %d (−%d)",
        n_sa, len(df_dedup), n_sa - len(df_dedup),
    )
    log.info(
        "Registros por especie (Sudamérica, sin duplicados):\n%s",
        df_dedup["especie"].value_counts().to_string(),
    )

    # --- Paso 3: coordenadas sospechosas / inválidas ---
    n_dd = len(df_dedup)
    df_coords = coords.filtrar_coords_sospechosas(df_dedup)
    log.info(
        "Coords sospechosas/inválidas: %d → %d (−%d)",
        n_dd, len(df_coords), n_dd - len(df_coords),
    )

    # --- Paso 4: thinning espacial 2.5' (1 punto por celda y especie) ---
    n_co = len(df_coords)
    df_thin = thinning.thinning_espacial(df_coords)
    log.info(
        "Thinning 2.5' (1 pt/celda/especie): %d → %d (−%d)",
        n_co, len(df_thin), n_co - len(df_thin),
    )

    # --- Guardar 'Especies_sudamerica' (estado actual de la limpieza) ---
    df_thin.to_csv(salida, index=False, encoding="utf-8")
    log.info("Guardado: %s  (%d filas × %d columnas)", salida, len(df_thin), df_thin.shape[1])
    return df_thin
