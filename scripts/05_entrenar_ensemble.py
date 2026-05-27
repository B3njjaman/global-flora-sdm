"""
05_entrenar_ensemble.py — Entrenamiento del ensemble V4 (PA por algoritmo + CV espacial).

Para cada especie viable (>= MIN_RECORDS_TO_MODEL):
  1. Presencias + background (10k en el ÁREA ACCESIBLE de la especie: buffer
     ~300 km alrededor de sus presencias ∩ tierra-SA; módulo extraccion.background).
  2. Extrae las 14 predictoras en el background (clima + topografía + fix 60m Atacama).
  3. Filtro de colinealidad (extraccion.predictoras) y folds CV espacial (extraccion.folds).
  4. Entrena 5 algoritmos con PESOS POR ALGORITMO (Barbet-Massin + Valavi):
       GLM, GAM (escalados) · RF, GBM (sin escalar) · MaxEnt (elapid).
  5. CV espacial leave-one-cluster-out → AUC y TSS (umbral maxTSS fijado en TRAIN).
  6. Ensemble = combinación PONDERADA por TSS-CV de cada algoritmo.

Salida: outputs/tables/metricas_v4_ensemble.csv + resumen comparable con main.
Uso: python scripts/05_entrenar_ensemble.py
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
from extraccion import background as bg, predictoras, folds as foldmod  # noqa: E402

from joblib import Parallel, delayed  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.ensemble import RandomForestClassifier  # noqa: E402
from sklearn.metrics import roc_auc_score, roc_curve  # noqa: E402

BIO = ["bio1", "bio4", "bio5", "bio6", "bio7", "bio10", "bio11", "bio12", "bio15", "bio17"]
PRED = [nombres.NOMBRES_ES[c] for c in BIO] + ["elevacion", "pendiente", "exposicion_norte", "exposicion_este"]
ESCALADOS = {"glm", "gam"}
BASE = _ROOT / "rama_v4" / "data" / "processed" / "base_datos_completa.csv"
SALIDA = _ROOT / "outputs" / "tables" / "metricas_v4_ensemble.csv"


def extraer_predictoras(df: pd.DataFrame) -> pd.DataFrame:
    """Extrae las 14 predictoras en puntos lon/lat (clima + topo + fix 60m Atacama)."""
    clima = {nombres.NOMBRES_ES[c]: config.WORLDCLIM_PRESENT / f"{c}.tif" for c in BIO}
    clima[nombres.NOMBRES_ES["elevation"] if "elevation" in nombres.NOMBRES_ES else "elevacion"] = config.WORLDCLIM_PRESENT / "elevation.tif"
    # nombres reales de columna de salida = español
    ra = _ROOT / "rama_v4" / "data" / "processed" / "rasters_terreno"
    ra60 = _ROOT / "rama_v4" / "data" / "processed" / "rasters_terreno_60m"
    out = df.copy()
    # clima -> columnas español
    cap_clima = {nombres.NOMBRES_ES[c]: config.WORLDCLIM_PRESENT / f"{c}.tif" for c in BIO}
    cap_clima["elevacion"] = config.WORLDCLIM_PRESENT / "elevation.tif"
    out = muestreo.muestrear_capas(out, cap_clima)
    out = muestreo.muestrear_capas(out, {"pendiente": ra / "slope.tif",
                                         "exposicion_norte": ra / "northness.tif",
                                         "exposicion_este": ra / "eastness.tif"})
    mask = out.lon.between(-71.65, -69.98) & out.lat.between(-30.01, -25.00)
    if mask.any():
        fix = muestreo.muestrear_capas(out[mask], {"elevacion": ra60 / "elevation.tif",
                                                    "pendiente": ra60 / "slope.tif",
                                                    "exposicion_norte": ra60 / "northness.tif",
                                                    "exposicion_este": ra60 / "eastness.tif"})
        for c in ["elevacion", "pendiente", "exposicion_norte", "exposicion_este"]:
            out.loc[mask, c] = fix[c].values
    return out


def _umbral_maxtss(y, p):
    fpr, tpr, thr = roc_curve(y, p)
    return thr[np.argmax(tpr - fpr)]


def _tss(y, p, thr):
    pred = (p >= thr).astype(int)
    tp = int(((pred == 1) & (y == 1)).sum()); fn = int(((pred == 0) & (y == 1)).sum())
    tn = int(((pred == 0) & (y == 0)).sum()); fp = int(((pred == 1) & (y == 0)).sum())
    sens = tp / (tp + fn) if tp + fn else 0.0
    spec = tn / (tn + fp) if tn + fp else 0.0
    return sens + spec - 1.0


def _boyce(p_pres, p_bg, n_bins=10):
    """Continuous Boyce Index (CBI): Spearman entre ratio P/E y la idoneidad.

    P = frecuencia de presencias por bin; E = frecuencia esperada (background).
    Cercano a 1 = las zonas idóneas concentran presencias (la métrica solo-presencia).
    """
    from scipy.stats import spearmanr
    allp = np.concatenate([p_pres, p_bg])
    if len(np.unique(allp)) < 3:
        return np.nan
    edges = np.linspace(allp.min(), allp.max(), n_bins + 1)
    mids, ratios = [], []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1] + (1e-9 if i == n_bins - 1 else 0.0)
        P = float(np.mean((p_pres >= lo) & (p_pres < hi)))
        E = float(np.mean((p_bg >= lo) & (p_bg < hi)))
        if E > 0:
            mids.append((edges[i] + edges[i + 1]) / 2); ratios.append(P / E)
    if len(ratios) < 3:
        return np.nan
    r = spearmanr(mids, ratios).correlation
    return float(r) if r == r else np.nan


def _entrenar_fold(Xtr, ytr, Xte):
    """Entrena los 5 algoritmos; devuelve {algo: (pred_test, pred_train)} (prob).

    Predice sobre test (evaluación) y train (para el umbral maxTSS honesto) en un
    solo ajuste por algoritmo. Cada algoritmo va en try/except: si falla, se omite.
    """
    res = {}
    sc = StandardScaler().fit(Xtr)
    Xtr_s, Xte_s = sc.transform(Xtr), sc.transform(Xte)
    wprev = bg._pesos(ytr, "prevalencia")
    try:
        m = LogisticRegression(penalty="l1", solver="liblinear", max_iter=2000)
        m.fit(Xtr_s, ytr, sample_weight=wprev)
        res["glm"] = (m.predict_proba(Xte_s)[:, 1], m.predict_proba(Xtr_s)[:, 1])
    except Exception: pass
    try:
        from pygam import LogisticGAM
        m = LogisticGAM().fit(Xtr_s, ytr, weights=wprev)
        res["gam"] = (m.predict_proba(Xte_s), m.predict_proba(Xtr_s))
    except Exception: pass
    try:
        m = RandomForestClassifier(n_estimators=300, n_jobs=1, random_state=config.RANDOM_SEED)
        m.fit(Xtr, ytr, sample_weight=wprev)
        res["rf"] = (m.predict_proba(Xte)[:, 1], m.predict_proba(Xtr)[:, 1])
    except Exception: pass
    try:
        import lightgbm as lgb
        m = lgb.LGBMClassifier(n_estimators=400, n_jobs=1, verbose=-1, random_state=config.RANDOM_SEED)
        m.fit(Xtr, ytr, sample_weight=wprev)
        res["gbm"] = (m.predict_proba(Xte)[:, 1], m.predict_proba(Xtr)[:, 1])
    except Exception: pass
    try:
        from elapid import MaxentModel
        m = MaxentModel(); m.fit(Xtr, ytr)
        res["maxent"] = (np.asarray(m.predict(Xte)).ravel(), np.asarray(m.predict(Xtr)).ravel())
    except Exception: pass
    return res


def procesar_especie(sp, pres):
    # Background dentro del área accesible (M) de la especie: buffer alrededor de
    # SUS presencias ∩ tierra-SA (no un fondo Chile compartido). Corrige el
    # desajuste presencia(SA)/fondo(Chile).
    bgdf = extraer_predictoras(
        bg.muestrear_background_especie(pres.lon.values, pres.lat.values, seed=config.RANDOM_SEED)
    )
    preds = predictoras.seleccionar_predictoras(pres, PRED)
    dfp = pres.dropna(subset=preds).assign(presence=1)
    dfb = bgdf.dropna(subset=preds).assign(presence=0)
    full = pd.concat([dfp[["lon", "lat", "presence"] + preds],
                      dfb[["lon", "lat", "presence"] + preds]], ignore_index=True)
    fold = foldmod.asignar_folds(full.lon.values, full.lat.values, full.presence.values)
    full["fold"] = fold

    algos = ["glm", "gam", "rf", "gbm", "maxent"]
    por_fold = {a: [] for a in algos}              # AUC por fold
    tss_fold = {a: [] for a in algos}
    preds_guardadas = []                            # (fold, {algo: (y_test, pred_test)})
    for k in sorted(set(fold)):
        tr, te = full[full.fold != k], full[full.fold == k]
        if te.presence.sum() == 0 or tr.presence.sum() == 0:
            continue
        Xtr, ytr = tr[preds].values, tr.presence.values
        Xte, yte = te[preds].values, te.presence.values
        if len(np.unique(yte)) < 2:
            continue
        res = _entrenar_fold(Xtr, ytr, Xte)
        guardado = {}
        for a, (pte, ptr) in res.items():
            try:
                por_fold[a].append(roc_auc_score(yte, pte))
                thr = _umbral_maxtss(ytr, ptr)
                tss_fold[a].append(_tss(yte, pte, thr))
                guardado[a] = (yte, pte, ytr, ptr)
            except Exception:
                pass
        if guardado:
            preds_guardadas.append(guardado)

    # pesos del ensemble = TSS-CV medio (clamp >=0)
    pesos = {a: max(np.mean(tss_fold[a]), 0.0) if tss_fold[a] else 0.0 for a in algos}
    if sum(pesos.values()) == 0:
        pesos = {a: 1.0 for a in algos}
    ens_auc, ens_tss = [], []
    oof_pe, oof_y = [], []
    for g in preds_guardadas:
        ys = next(iter(g.values()))[0]
        ytr_f = next(iter(g.values()))[2]
        num_te = np.zeros(len(ys)); num_tr = np.zeros(len(ytr_f)); den = 0.0
        for a, (yte_, pte_, ytr_, ptr_) in g.items():
            num_te += pesos[a] * pte_; num_tr += pesos[a] * ptr_; den += pesos[a]
        if den == 0:
            continue
        pe, pe_tr = num_te / den, num_tr / den
        oof_pe.append(pe); oof_y.append(ys)
        if len(np.unique(ys)) == 2:
            ens_auc.append(roc_auc_score(ys, pe))
            # umbral HONESTO: del ensemble en TRAIN, aplicado al test (no se truca con el test)
            ens_tss.append(_tss(ys, pe, _umbral_maxtss(ytr_f, pe_tr)))
    boyce = np.nan
    if oof_pe:
        op, oy = np.concatenate(oof_pe), np.concatenate(oof_y)
        boyce = _boyce(op[oy == 1], op[oy == 0])

    fila = {"especie": sp, "n_pres": int(pres.shape[0]), "n_pred": len(preds), "n_folds": len(preds_guardadas)}
    for a in algos:
        fila[f"auc_{a}"] = float(np.mean(por_fold[a])) if por_fold[a] else np.nan
        fila[f"tss_{a}"] = float(np.mean(tss_fold[a])) if tss_fold[a] else np.nan
    fila["auc_ensemble"] = float(np.mean(ens_auc)) if ens_auc else np.nan
    fila["tss_ensemble"] = float(np.mean(ens_tss)) if ens_tss else np.nan
    fila["boyce_ensemble"] = float(boyce) if boyce == boyce else np.nan
    print(f"  {sp:24s} pred={len(preds)} AUC ens={fila['auc_ensemble']:.3f} maxent={fila['auc_maxent']:.3f} | TSS ens={fila['tss_ensemble']:.3f} | Boyce={fila['boyce_ensemble']:.3f}")
    return fila


def _filtrar_sudamerica(base: pd.DataFrame) -> pd.DataFrame:
    """Descarta coordenadas fuera del bbox de Sudamérica (PREDICTION_BBOX).

    base_datos_completa.csv arrastra unos pocos puntos corruptos (p. ej. una
    'Argentina' en lat 29 N / lon 0.5 E y dos en el Atlántico) que el filtro SA
    documentado no había aplicado. Aquí se eliminan antes de modelar.
    """
    minx, miny, maxx, maxy = config.PREDICTION_BBOX
    m = base.lon.between(minx, maxx) & base.lat.between(miny, maxy)
    n_out = int((~m).sum())
    if n_out:
        print(f"Filtro Sudamérica: descarto {n_out} registros fuera del bbox SA.")
    return base[m].copy()


def main():
    base = _filtrar_sudamerica(pd.read_csv(BASE))
    vc = base["especie"].value_counts()
    viables = vc[vc >= config.MIN_RECORDS_TO_MODEL].index.tolist()
    print(f"Entrenando ensemble V4 en {len(viables)} especies viables (paralelo)...")
    print("Background = area accesible por especie (buffer "
          f"{bg.BUFFER_KM:.0f} km alrededor de presencias, en tierra-SA).")
    filas = Parallel(n_jobs=4)(
        delayed(procesar_especie)(sp, base[base.especie == sp].copy())
        for sp in viables
    )
    df = pd.DataFrame(filas)
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    df.round(4).to_csv(SALIDA, index=False, encoding="utf-8")
    print(f"\nGuardado: {SALIDA}")
    print("\n=== RESUMEN V4 (media sobre especies) ===")
    for a in ["glm", "gam", "rf", "gbm", "maxent", "ensemble"]:
        print(f"  {a:9s} AUC={df[f'auc_{a}'].mean():.3f}  TSS={df[f'tss_{a}'].mean():.3f}")
    print(f"  ensemble Boyce/CBI medio = {df['boyce_ensemble'].mean():.3f}")


if __name__ == "__main__":
    main()
