"""
pipeline.py — Orquestador modular del terreno (V4).

Crece paso a paso. Estado actual:
  1. Cargar elevación (WorldClim) y recortar a Sudamérica (PREDICTION_BBOX).
  2. Derivar pendiente y aspecto (derivacion, Horn geográfico).
  3. Aspecto → northness / eastness (orientacion).
  → escribe elevation/slope/northness/eastness recortados a la grilla común.

Pasos siguientes a portar (se integran de a uno, guiados): alinear las 10 bioclim
a esta misma grilla (reproject_match) y aplicar la máscara de tierra.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import config  # noqa: E402
import utils   # noqa: E402

from . import derivacion, io, orientacion

log = utils.get_logger("terreno.pipeline")


def run(
    salida_dir: Path | None = None,
    recortar_sudamerica: bool = True,
    ajustar_hemisferio: bool = False,
) -> dict[str, Path]:
    """Deriva el terreno (elevación, pendiente, orientación) sobre Sudamérica.

    Devuelve {nombre: ruta} de las 4 capas de terreno escritas.
    """
    salida_dir = Path(salida_dir) if salida_dir else config.RASTERS_ALIGNED
    utils.ensure_dirs(salida_dir)

    elev = io.cargar_raster(config.WORLDCLIM_PRESENT / "elevation.tif", "elevation")
    log.info("Elevación cargada: %s", tuple(elev.shape))

    if recortar_sudamerica:
        lon0, lat0, lon1, lat1 = config.PREDICTION_BBOX
        elev = elev.rio.clip_box(lon0, lat0, lon1, lat1)
        log.info("Recortada a Sudamérica %s → %s", config.PREDICTION_BBOX, tuple(elev.shape))

    slope, aspect = derivacion.derivar_terreno(elev)
    north, east = orientacion.northness_eastness(aspect, ajustar_hemisferio=ajustar_hemisferio)

    capas = {"elevation": elev, "slope": slope, "northness": north, "eastness": east}
    rutas: dict[str, Path] = {}
    for nombre, da in capas.items():
        destino = salida_dir / f"{nombre}.tif"
        io.escribir_raster(da, destino)
        rutas[nombre] = destino
    return rutas
