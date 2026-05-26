"""Auditoria de variables por especie: importancia por permutacion (model-agnostica,
los 5 algoritmos) + redundancia (correlacion entre predictores seleccionados).
Solo lectura: NO toca modelos ni datasets. Imprime un informe."""
from __future__ import annotations
import json, warnings
import numpy as np, pandas as pd, joblib
from pathlib import Path
from sklearn.metrics import roc_auc_score

warnings.simplefilter("ignore")
rng = np.random.default_rng(42)
ROOT = Path(__file__).resolve().parents[1]
SD = ROOT / "data/processed/species_datasets"
EM = ROOT / "data/modeling/ensemble_models"
NEEDS_SCALING = {"glm", "gam", "maxent"}
N_PERM = 8

def predict_proba(model, X, algo):
    if algo == "gam":
        return np.asarray(model.predict_proba(X)).ravel()
    if algo == "maxent":
        try:
            return np.asarray(model.predict(X)).ravel()
        except Exception:
            p = model.predict_proba(X)
            return p[:, 1] if np.ndim(p) == 2 else np.asarray(p).ravel()
    return model.predict_proba(X)[:, 1]

def perm_importance_model(model, algo, X, Xs, y, feats):
    """Importancia por permutacion = caida de AUC al barajar cada feature."""
    Xuse = Xs if algo in NEEDS_SCALING else X
    base = roc_auc_score(y, predict_proba(model, Xuse, algo))
    imp = np.zeros(len(feats))
    for j in range(len(feats)):
        drops = []
        for _ in range(N_PERM):
            Xp = Xuse.copy()
            Xp[:, j] = rng.permutation(Xp[:, j])
            drops.append(base - roc_auc_score(y, predict_proba(model, Xp, algo)))
        imp[j] = max(np.mean(drops), 0.0)
    return imp  # AUC-drop por feature

slugs = sorted(p.stem for p in EM.glob("*.joblib"))
summary_rows = []
for slug in slugs:
    b = joblib.load(EM / f"{slug}.joblib")
    feats = b["selected_predictors"]
    df = pd.read_parquet(SD / f"{slug}.parquet")
    X = df[feats].values.astype(float)
    y = df["presence"].values.astype(int)
    scaler = b.get("scaler")
    Xs = scaler.transform(X) if scaler is not None else X
    weights = b["tss_weights"]

    # Importancia ensemble = promedio ponderado (por peso del ensamble) de la
    # importancia por permutacion de cada modelo activo. Normalizada a % dentro de sp.
    agg = np.zeros(len(feats)); wtot = 0.0
    for algo, model in b["models"].items():
        w = weights.get(algo, 0.0)
        if w <= 0:
            continue
        agg += w * perm_importance_model(model, algo, X, Xs, y, feats)
        wtot += w
    agg = agg / wtot if wtot > 0 else agg
    pct = 100 * agg / agg.sum() if agg.sum() > 0 else agg

    order = np.argsort(-pct)
    print("=" * 78)
    print(f"{slug}   (n_pres={int(y.sum())}, predictores={len(feats)})")
    print("  Importancia ensemble (AUC-drop por permutacion, % dentro de la especie):")
    for j in order:
        bar = "#" * int(round(pct[j] / 2))
        print(f"    {feats[j]:<10} {pct[j]:6.1f}%  {bar}")

    # Redundancia: correlacion entre predictores seleccionados
    C = np.corrcoef(X, rowvar=False)
    red = []
    for i in range(len(feats)):
        for k in range(i + 1, len(feats)):
            if abs(C[i, k]) >= 0.7:
                red.append((feats[i], feats[k], C[i, k]))
    if red:
        print("  Pares correlacionados |r|>=0.7 entre seleccionados:")
        for a, c, r in red:
            print(f"    {a:<10} ~ {c:<10} r={r:+.2f}")
    else:
        print("  Sin pares |r|>=0.7 entre seleccionados.")

    for j in order:
        summary_rows.append((slug, feats[j], round(pct[j], 1)))

# Tabla resumen: frecuencia y importancia media de cada predictor a traves de especies
print("\n" + "=" * 78)
print("RESUMEN GLOBAL por predictor (sobre las especies que lo usan):")
sdf = pd.DataFrame(summary_rows, columns=["slug", "var", "pct"])
g = sdf.groupby("var")["pct"].agg(["count", "mean", "median", "max"]).sort_values("mean", ascending=False)
print(g.round(1).to_string())
