"""
io.py — Carga y escritura de rasters para el terreno (V4).

Misma lógica que la versión previa (`03_terrain._load_raster` / `_write_raster`),
aislada como módulo del paquete `terreno`. nodata ↔ NaN, float32, LZW.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import rioxarray  # noqa: F401  (registra el accessor .rio)
import xarray as xr

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import utils  # noqa: E402

log = utils.get_logger("terreno.io")


def cargar_raster(path: Path, name: str) -> xr.DataArray:
    """Carga un GeoTIFF como DataArray (nodata → NaN, float32)."""
    da = rioxarray.open_rasterio(path, masked=True).squeeze("band", drop=True)
    da = da.astype(np.float32)
    da.name = name
    return da


def escribir_raster(da: xr.DataArray, path: Path) -> None:
    """Escribe un DataArray como GeoTIFF float32 comprimido (LZW)."""
    da_out = da.astype(np.float32)
    da_out.rio.write_nodata(np.float32(np.nan), inplace=True, encoded=False)
    da_out.rio.to_raster(str(path), dtype="float32", compress="lzw", driver="GTiff")
    log.info("Escrito: %s  %s", path.name, tuple(da_out.shape))
