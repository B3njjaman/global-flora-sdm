"""
folds.py — Validación cruzada espacial adaptativa (leave-one-cluster-out).

Misma lógica que `04_extraccion.assign_spatial_folds`: agrupa las PRESENCIAS con
k-means sobre (lon corregido por cos(lat), lat) en hasta N_CV_FOLDS clústeres;
cada clúster es un fold. El background se asigna al fold del centroide de
presencias más cercano. El tamaño de fold se adapta al rango de cada especie y
cada fold contiene presencias → el CV no se degenera y es espacialmente disjunto.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import config  # noqa: E402
import utils   # noqa: E402

log = utils.get_logger("extraccion.folds")


def _proj(lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
    """lon/lat -> coords aprox. equidistantes (lon escalado por cos(lat medio))."""
    lat0 = np.deg2rad(np.mean(lats))
    return np.column_stack([lons * np.cos(lat0), lats])


def asignar_folds(
    lons: np.ndarray,
    lats: np.ndarray,
    presence: np.ndarray,
    n_folds: int = config.N_CV_FOLDS,
    seed: int = config.RANDOM_SEED,
) -> np.ndarray:
    """Devuelve un array de fold (0..k-1) por punto, por clustering de presencias."""
    from sklearn.cluster import KMeans

    lons = np.asarray(lons, dtype="float64")
    lats = np.asarray(lats, dtype="float64")
    presence = np.asarray(presence).astype(int)
    pres = presence == 1
    if pres.sum() == 0:
        return np.zeros(len(lons), dtype=int)

    Xp = _proj(lons[pres], lats[pres])
    n_distinct = len({(round(x, 4), round(y, 4)) for x, y in zip(lons[pres], lats[pres])})
    k = int(min(n_folds, n_distinct))
    if k < 1:
        return np.zeros(len(lons), dtype=int)

    km = KMeans(n_clusters=k, random_state=seed, n_init=10).fit(Xp)
    folds = np.empty(len(lons), dtype=int)
    folds[pres] = km.labels_
    bg = ~pres
    if bg.any():
        folds[bg] = km.predict(_proj(lons[bg], lats[bg]))
    log.info("CV espacial: %d folds por clustering de %d presencias.", k, int(pres.sum()))
    return folds
