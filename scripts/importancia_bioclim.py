"""
importancia_bioclim.py — Importancia de las variables BIOCLIMÁTICAS por especie.

Para cada especie modelable (>= MIN_RECORDS_TO_MODEL presencias) ajusta un Random
Forest sobre presencias vs. background (pseudo-ausencias del módulo
`extraccion.background`) usando SOLO las 10 variables bioclimáticas, y calcula la
importancia por PERMUTACIÓN (caída de AUC al permutar cada variable). El terreno
queda fuera de este análisis (decisión del proyecto): se incluye siempre en el modelo.

PARALELO: cada especie se procesa en un proceso aparte (joblib), porque son
independientes. El RF y la permutación internos corren en 1 hilo (n_jobs=1) para
no sobre-suscribir los núcleos (paralelismo solo a nivel de especie).

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

from joblib import Parallel, delayed  # noqa: E402
from sklearn.ensemble import RandomForestClassifier  # noqa: E402
from sklearn.inspection import permutation_importance  # noqa: E402

BIO_COD = ["bio1", "bio4", "bio5", "bio6", "bio7", "bio10", "bio11", "bio12", "bio15", "bio17"]
BIO_ES = [nombres.NOMBRES_ES[c] for c in BIO_COD]
BASE = _ROOT / "rama_v4" / "data" / "processed" / "base_datos_completa.csv"
SALIDA = _ROOT / "rama_v4" / "data" / "processed" / "importancia_bioclim_por_especie.csv"


def _importancia_una(sp: str, pres_X: np.ndarray, X_bg: np.ndarray, seed: int):
    """Importancia por permutación (AUC) de las 10 bioclim para UNA especie.

    Corre en un proceso aparte; RF y permutación en 1 hilo (n_jobs=1).
    Devuelve (especie, array_importancias) alineado con BIO_ES.
    """
    X = np.vstack([pres_X, X_bg])
    y = np.r_[np.ones(len(pres_X), int), np.zeros(len(X_bg), int)]
    w = bg._pesos(y, "prevalencia")  # prevalencia 0.5 (criterio Valavi del RF)
    rf = RandomForestClassifier(n_estimators=300, n_jobs=1, random_state=seed)
    rf.fit(X, y, sample_weight=w)
    imp = permutation_importance(rf, X, y, scoring="roc_auc", n_repeats=8,
                                 random_state=seed, n_jobs=1)
    return sp, imp.importances_mean


def main() -> None:
    base = pd.read_csv(BASE)

    # Background + sus valores bioclim (una sola vez, secuencial)
    bgdf = bg.muestrear_background()
    capas = {nombres.NOMBRES_ES[c]: config.WORLDCLIM_PRESENT / f"{c}.tif" for c in BIO_COD}
    bgdf = muestreo.muestrear_capas(bgdf, capas).dropna(subset=BIO_ES)
    X_bg = bgdf[BIO_ES].to_numpy()

    vc = base["especie"].value_counts()
    modelables = vc[vc >= config.MIN_RECORDS_TO_MODEL].index.tolist()

    tareas = []
    for sp in modelables:
        pres = base[base["especie"] == sp].dropna(subset=BIO_ES)
        if len(pres) >= config.MIN_RECORDS_TO_MODEL:
            tareas.append((sp, pres[BIO_ES].to_numpy()))

    print(f"Calculando importancia para {len(tareas)} especies en paralelo "
          f"(background={len(X_bg)})...")
    resultados = Parallel(n_jobs=-1, verbose=5)(
        delayed(_importancia_una)(sp, pres_X, X_bg, config.RANDOM_SEED)
        for sp, pres_X in tareas
    )

    filas = {sp: pd.Series(imp, index=BIO_ES) for sp, imp in resultados}
    for sp in [t[0] for t in tareas]:
        top = filas[sp].sort_values(ascending=False)
        top3 = ", ".join(f"{v} ({i:.3f})" for v, i in top.head(3).items())
        print(f"{sp:26s} top3: {top3}")

    tabla = pd.DataFrame(filas).T
    tabla.index.name = "especie"
    tabla.round(4).to_csv(SALIDA, encoding="utf-8")
    print(f"\nTabla guardada en: {SALIDA}")
    print("\nImportancia media sobre especies (ranking global de las bioclim):")
    for v, val in tabla.mean().sort_values(ascending=False).items():
        print(f"  {v:24s} {val:.4f}")


if __name__ == "__main__":
    main()
