"""
importancia_bioclim.py — Importancia de las variables BIOCLIMÁTICAS por especie.

Para cada especie modelable (>= MIN_RECORDS_TO_MODEL presencias) ajusta un Random
Forest sobre presencias vs. background (pseudo-ausencias del módulo
`extraccion.background`) usando SOLO las 10 variables bioclimáticas, y calcula la
importancia por PERMUTACIÓN (caída de AUC al permutar cada variable). El terreno
queda fuera de este análisis (decisión del proyecto): se incluye siempre en el modelo.

Salida: tabla especie × 10 bioclim (importancia) en rama_v4 + ranking por especie.

Uso: python scripts/importancia_bioclim.py
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

import config  # noqa: E402
from capas import muestreo, nombres  # noqa: E402
from extraccion import background as bg  # noqa: E402

from sklearn.ensemble import RandomForestClassifier  # noqa: E402
from sklearn.inspection import permutation_importance  # noqa: E402

BIO_COD = ["bio1", "bio4", "bio5", "bio6", "bio7", "bio10", "bio11", "bio12", "bio15", "bio17"]
BIO_ES = [nombres.NOMBRES_ES[c] for c in BIO_COD]
BASE = config.PROCESSED.parent.parent / "rama_v4" / "data" / "processed" / "base_datos_completa.csv"
SALIDA = _ROOT / "rama_v4" / "data" / "processed" / "importancia_bioclim_por_especie.csv"


def main() -> None:
    base = pd.read_csv(BASE)

    # Background + sus valores bioclim (mismo origen WorldClim que las presencias)
    bgdf = bg.muestrear_background()
    capas = {nombres.NOMBRES_ES[c]: config.WORLDCLIM_PRESENT / f"{c}.tif" for c in BIO_COD}
    bgdf = muestreo.muestrear_capas(bgdf, capas).dropna(subset=BIO_ES)

    vc = base["especie"].value_counts()
    modelables = vc[vc >= config.MIN_RECORDS_TO_MODEL].index.tolist()

    filas = {}
    for sp in modelables:
        pres = base[base["especie"] == sp].dropna(subset=BIO_ES)
        if len(pres) < config.MIN_RECORDS_TO_MODEL:
            continue
        X = np.vstack([pres[BIO_ES].values, bgdf[BIO_ES].values])
        y = np.r_[np.ones(len(pres), int), np.zeros(len(bgdf), int)]
        # peso de prevalencia 0.5 (mismo criterio que el RF del ensemble, Valavi)
        w = bg._pesos(y, "prevalencia")
        rf = RandomForestClassifier(n_estimators=300, n_jobs=-1,
                                    random_state=config.RANDOM_SEED)
        rf.fit(X, y, sample_weight=w)
        imp = permutation_importance(rf, X, y, scoring="roc_auc", n_repeats=8,
                                     random_state=config.RANDOM_SEED, n_jobs=-1)
        filas[sp] = pd.Series(imp.importances_mean, index=BIO_ES)
        top = filas[sp].sort_values(ascending=False)
        top3 = ", ".join(f"{v} ({i:.3f})" for v, i in top.head(3).items())
        print(f"{sp:26s} n={len(pres):4d} | top3: {top3}")

    tabla = pd.DataFrame(filas).T  # especie x bioclim
    tabla.index.name = "especie"
    tabla.round(4).to_csv(SALIDA, encoding="utf-8")
    print(f"\nTabla completa (importancia por permutacion, caida de AUC): {SALIDA}")

    # Resumen: variable mas importante globalmente (promedio sobre especies)
    print("\nImportancia media sobre especies (ranking global de las bioclim):")
    for v, val in tabla.mean().sort_values(ascending=False).items():
        print(f"  {v:24s} {val:.4f}")


if __name__ == "__main__":
    main()
