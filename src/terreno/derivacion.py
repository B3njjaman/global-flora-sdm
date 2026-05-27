"""
derivacion.py — Pendiente y aspecto desde la elevación (Horn geográfico).

POR QUÉ no xrspatial/richdem directos: asumen coordenadas en METROS. Aquí la
grilla está en grados (EPSG:4326) y la elevación en metros, así que el gradiente
sale gigantesco y atan(·) ≈ 90° casi en todo el planeta (bug histórico de la
iter. 1). La corrección convierte el tamaño de celda a metros con escala
dependiente de la latitud:  m_por_grado_lon ≈ 111320·cos(lat).

Misma lógica que `03_terrain._derive_terrain_geographic`.
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

log = utils.get_logger("terreno.derivacion")

_M_POR_GRADO = 111_320.0  # metros por grado de latitud (aprox.)


def derivar_terreno(elevation: xr.DataArray) -> tuple[xr.DataArray, xr.DataArray]:
    """Devuelve (slope_grados, aspect_grados) con Horn corregido por latitud.

    slope ∈ [0, 90]; aspect ∈ [0, 360) (norte=0, horario); terreno plano → aspect NaN.
    """
    elev = elevation.values.astype(np.float32)
    ny, nx = elev.shape
    ys = np.asarray(elevation["y"].values, dtype=np.float64)
    xs = np.asarray(elevation["x"].values, dtype=np.float64)
    res_x_deg = abs(float(xs[1] - xs[0])) if nx > 1 else 2.5 / 60
    res_y_deg = abs(float(ys[1] - ys[0])) if ny > 1 else 2.5 / 60

    pad = np.pad(elev, 1, mode="edge")
    a = pad[0:ny, 0:nx];     b = pad[0:ny, 1:nx + 1];     c = pad[0:ny, 2:nx + 2]
    d = pad[1:ny + 1, 0:nx]; center = pad[1:ny + 1, 1:nx + 1]; f = pad[1:ny + 1, 2:nx + 2]
    g = pad[2:ny + 2, 0:nx]; h = pad[2:ny + 2, 1:nx + 1]; i = pad[2:ny + 2, 2:nx + 2]
    del pad

    def fill(x: np.ndarray) -> np.ndarray:
        return np.where(np.isnan(x), center, x)

    a, b, c, d, f, g, h, i = (fill(v) for v in (a, b, c, d, f, g, h, i))

    csx = (res_x_deg * _M_POR_GRADO * np.cos(np.deg2rad(ys)))[:, None]
    csx = np.where(np.abs(csx) < 1.0, np.nan, csx)  # polos: indefinido
    csy = res_y_deg * _M_POR_GRADO

    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * csx)
    dzdy = ((g + 2 * h + i) - (a + 2 * b + c)) / (8 * csy)

    slope_np = np.degrees(np.arctan(np.sqrt(dzdx ** 2 + dzdy ** 2)))
    flat = (dzdx == 0) & (dzdy == 0)
    slope_np = np.where(np.isnan(center), np.nan, slope_np).astype(np.float32)

    aspect_np = np.degrees(np.arctan2(dzdy, -dzdx))
    aspect_np = np.where(aspect_np < 0, 90.0 - aspect_np,
                         np.where(aspect_np > 90.0, 450.0 - aspect_np, 90.0 - aspect_np))
    aspect_np = np.where(flat | np.isnan(center), np.nan, aspect_np).astype(np.float32)

    slope = elevation.copy(data=slope_np); slope.name = "slope"
    aspect = elevation.copy(data=aspect_np); aspect.name = "aspect"
    log.info("Terreno derivado (Horn geográfico, escala métrica por latitud).")
    return slope, aspect
