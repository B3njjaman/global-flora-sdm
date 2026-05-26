"""
pipeline.py — Orquestador modular de las capas presentes (V4).

Crece paso a paso. Estado actual:
  1. Adquisición de las capas WorldClim presentes  (descarga.capas_presentes)
     → reusa lo descargado; no re-baja los ~628 MB si ya existen.

Pasos siguientes a portar desde la versión previa (se integran de a uno, guiados):
máscara de tierra (Natural Earth) y verificación de alineación entre capas.
"""
from __future__ import annotations

import sys
from pathlib import Path

import rasterio

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import utils  # noqa: E402

from . import descarga

log = utils.get_logger("capas.pipeline")


def run(overwrite: bool = False) -> dict[str, Path]:
    """Ejecuta la etapa 2 (por ahora: adquisición de capas presentes).

    Devuelve {nombre: ruta} de las capas disponibles y reporta su inventario
    (CRS, resolución, dimensiones) para verificar que están listas.
    """
    capas = descarga.capas_presentes(overwrite=overwrite)

    log.info("=== Inventario de capas presentes (%d) ===", len(capas))
    for nombre, ruta in capas.items():
        with rasterio.open(ruta) as src:
            res_x = abs(src.transform.a)
            log.info(
                "  %-10s %5dx%-5d  res=%.5f°  CRS=%s",
                nombre, src.width, src.height, res_x, src.crs,
            )
    return capas
