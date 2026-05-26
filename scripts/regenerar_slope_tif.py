"""
regenerar_slope_tif.py — Regenera el raster global slope.tif corregido.

El slope.tif de rasters_aligned tenía el bug de unidades (≈90° en casi todo el
planeta). Lo recalcula con Horn (1981) y escala métrica dependiente de la latitud,
a partir de la elevación alineada (misma fuente que usaron los datasets). Respalda
el raster viejo como slope_bug.tif.

Necesario para que 07b (proyección de idoneidad) reciba pendiente correcta en
TODO el mapa, no solo en los puntos de entrenamiento.
"""
from __future__ import annotations

import numpy as np
import rasterio

import config
import utils

logger = utils.get_logger("regen_slope")
M_PER_DEG = 111_320.0


def main() -> None:
    elev_path = config.RASTERS_ALIGNED / "elevation.tif"
    slope_path = config.RASTERS_ALIGNED / "slope.tif"
    backup = config.RASTERS_ALIGNED / "slope_bug.tif"

    with rasterio.open(elev_path) as src:
        elev = src.read(1).astype(np.float32)
        profile = src.profile.copy()
        transform = src.transform
        nodata = src.nodata

    if nodata is not None and not np.isnan(nodata):
        elev = np.where(elev == nodata, np.nan, elev)

    ny, nx = elev.shape
    res_x_deg = abs(transform.a)
    res_y_deg = abs(transform.e)
    # latitud del centro de cada fila
    lat = transform.f + (np.arange(ny) + 0.5) * transform.e  # transform.e < 0
    logger.info("Elevación %s  res=%.5f°  → calculando Horn geográfico...", elev.shape, res_x_deg)

    pad = np.pad(elev, 1, mode="edge")
    a = pad[0:ny, 0:nx];     b = pad[0:ny, 1:nx + 1];     c = pad[0:ny, 2:nx + 2]
    d = pad[1:ny + 1, 0:nx]; center = pad[1:ny + 1, 1:nx + 1]; f = pad[1:ny + 1, 2:nx + 2]
    g = pad[2:ny + 2, 0:nx]; h = pad[2:ny + 2, 1:nx + 1]; i = pad[2:ny + 2, 2:nx + 2]
    del pad

    def fill(x):
        return np.where(np.isnan(x), center, x)

    a, b, c, d, f, g, h, i = (fill(v) for v in (a, b, c, d, f, g, h, i))

    csx = (res_x_deg * M_PER_DEG * np.cos(np.deg2rad(lat)))[:, None]
    csx = np.where(np.abs(csx) < 1.0, np.nan, csx)
    csy = res_y_deg * M_PER_DEG

    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * csx)
    dzdy = ((g + 2 * h + i) - (a + 2 * b + c)) / (8 * csy)
    slope = np.degrees(np.arctan(np.sqrt(dzdx ** 2 + dzdy ** 2)))
    slope = np.where(np.isnan(center), np.nan, slope).astype(np.float32)

    # Respaldar el viejo y escribir el nuevo
    if slope_path.exists() and not backup.exists():
        slope_path.replace(backup)
        logger.info("slope.tif viejo respaldado como slope_bug.tif")

    profile.update(dtype="float32", count=1, nodata=np.float32(np.nan), compress="lzw")
    with rasterio.open(slope_path, "w", **profile) as dst:
        dst.write(slope, 1)

    finite = slope[np.isfinite(slope)]
    logger.info("slope.tif regenerado: mediana=%.2f° max=%.2f° (>80°: %.1f%%)",
                float(np.median(finite)), float(np.max(finite)),
                100 * np.mean(finite > 80))


if __name__ == "__main__":
    main()
