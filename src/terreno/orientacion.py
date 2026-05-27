"""
orientacion.py — Descompone el aspecto circular en northness / eastness.

El aspecto en grados es circular (1° ≈ 359° pero opuestos numéricamente); meterlo
así a un modelo lineal crea una discontinuidad artificial. Se descompone en:
    northness = cos(aspect)  → +1 cara norte, −1 cara sur
    eastness  = sin(aspect)  → +1 cara este,  −1 cara oeste

CLAVE: el terreno plano (aspect NaN) se codifica como northness=eastness=0 (sin
orientación), NO NaN — si no, se perderían ~95% de las presencias de endémicas del
Atacama en terreno llano. El océano/borde se re-enmascara con la land mask aguas abajo.

Misma lógica que `03_terrain.compute_northness_eastness`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import xarray as xr

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import utils  # noqa: E402

log = utils.get_logger("terreno.orientacion")


def northness_eastness(
    aspect_deg: xr.DataArray,
    ajustar_hemisferio: bool = False,
) -> tuple[xr.DataArray, xr.DataArray]:
    """Devuelve (northness, eastness) en [−1, 1]. Terreno plano → 0."""
    aspect_rad = np.deg2rad(aspect_deg)
    northness = np.cos(aspect_rad).astype(np.float32)
    eastness = np.sin(aspect_rad).astype(np.float32)

    # Terreno plano (aspect NaN) → sin componente direccional (0), no NaN.
    northness = northness.fillna(np.float32(0.0))
    eastness = eastness.fillna(np.float32(0.0))
    northness.name = "northness"
    eastness.name = "eastness"

    if ajustar_hemisferio:
        # northness *= sign(lat): +1 = cara cálida en ambos hemisferios.
        lats = aspect_deg.y
        sign_lat = np.sign(xr.ones_like(aspect_deg) * lats).astype(np.float32)
        sign_lat = sign_lat.where(sign_lat != 0, other=np.float32(1.0))
        northness = (northness * sign_lat).astype(np.float32)
        northness.name = "northness"
        log.info("Northness ajustada por hemisferio (northness * sign(lat)).")
    else:
        log.info("Northness cruda (cos(aspect)).")

    return northness, eastness
