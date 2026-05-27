"""
predictoras.py — Filtro de colinealidad por especie (correlación + VIF iterativo).

Misma lógica que `04_extraccion.select_predictors`: 1) descarta variables con
|r| > CORR_THRESHOLD (conserva la de mayor varianza del par); 2) elimina
iterativamente la de mayor VIF mientras supere VIF_THRESHOLD. Se calcula sobre
las presencias de la especie.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import config  # noqa: E402
import utils   # noqa: E402

log = utils.get_logger("extraccion.predictoras")


def _vif(df: pd.DataFrame) -> pd.Series:
    """VIF_j = 1/(1-R²_j) por regresión OLS de cada columna sobre las demás."""
    X = df.to_numpy(dtype="float64")
    vifs = {}
    for j, col in enumerate(df.columns):
        y = X[:, j]
        Xo = np.column_stack([np.ones(len(X)), np.delete(X, j, axis=1)])
        beta, *_ = np.linalg.lstsq(Xo, y, rcond=None)
        ss_res = float(((y - Xo @ beta) ** 2).sum())
        ss_tot = float(((y - y.mean()) ** 2).sum())
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        vifs[col] = np.inf if r2 >= 1.0 else 1.0 / (1.0 - r2)
    return pd.Series(vifs)


def seleccionar_predictoras(
    presencias: pd.DataFrame,
    candidatas: list[str],
    corr_threshold: float = config.CORR_THRESHOLD,
    vif_threshold: float = config.VIF_THRESHOLD,
) -> list[str]:
    """Devuelve las predictoras no colineales (correlación + VIF) de una especie."""
    sub = presencias[candidatas].dropna()
    if len(sub) == 0:
        return candidatas[:]

    retenidas = candidatas[:]
    corr = sub[retenidas].corr().abs()
    drop: set[str] = set()
    for i, ci in enumerate(retenidas):
        if ci in drop:
            continue
        for cj in retenidas[i + 1:]:
            if cj in drop:
                continue
            if corr.loc[ci, cj] > corr_threshold:
                drop.add(cj if sub[ci].var() >= sub[cj].var() else ci)
    retenidas = [c for c in retenidas if c not in drop]

    for _ in range(len(retenidas)):
        if len(retenidas) <= 1:
            break
        d = sub[retenidas].dropna()
        if len(d) < len(retenidas) + 1:
            break
        vif = _vif(d)
        if vif.max() <= vif_threshold:
            break
        retenidas.remove(vif.idxmax())

    log.info("%d predictoras tras colinealidad: %s", len(retenidas), retenidas)
    return retenidas
