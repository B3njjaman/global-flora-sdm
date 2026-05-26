"""
aplicar_cv_adaptativo.py — Reasigna cv_fold con el CV espacial adaptativo.

Aplica `04_extraccion.assign_spatial_folds` (clustering espacial de presencias)
a la columna `cv_fold` de cada dataset ya saneado (slope corregido + background
podado), SIN re-extraer (preserva los datos limpios). Reporta cuántos folds
contienen presencias antes y después: el arreglo debe llevar a casi todas las
especies a 5 folds con presencias (antes muchas endémicas tenían 1–2).
"""
from __future__ import annotations

import importlib
import numpy as np
import pandas as pd

import config
import utils

logger = utils.get_logger("cv_adaptativo")

_mod = importlib.import_module("04_extraccion")
assign_spatial_folds = _mod.assign_spatial_folds


def _folds_con_presencia(df: pd.DataFrame) -> int:
    fc = df.groupby("cv_fold")["presence"].sum()
    return int((fc > 0).sum())


def main() -> None:
    parquets = sorted(
        p for p in config.SPECIES_DATASETS.glob("*.parquet")
        if not p.stem.endswith("_cv_preds")
    )
    logger.info("%-26s %12s %12s", "especie", "folds_antes", "folds_despues")
    for pq in parquets:
        df = pd.read_parquet(pq)
        antes = _folds_con_presencia(df)

        folds = assign_spatial_folds(
            lons=df["lon"].values,
            lats=df["lat"].values,
            presence=df["presence"].values,
            n_folds=config.N_CV_FOLDS,
            seed=config.RANDOM_SEED,
        )
        df["cv_fold"] = folds.astype(np.int32)
        df.to_parquet(pq, index=False)

        despues = _folds_con_presencia(df)
        logger.info("%-26s %12d %12d", pq.stem, antes, despues)


if __name__ == "__main__":
    main()
