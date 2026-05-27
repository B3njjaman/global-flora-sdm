"""
07_predecir_sudamerica.py — Idoneidad del ensemble V4 proyectada sobre Sudamérica.

Para cada especie viable: ajusta los 5 algoritmos (PA por algoritmo, pesos de
prevalencia) con TODOS los datos y predice la idoneidad en cada celda de la grilla
de Sudamérica, combinando PONDERADO por el TSS-CV de cada algoritmo (tabla de
métricas). Escribe un raster por especie en outputs/maps/.

Paraleliza POR ESPECIE (joblib), con timing por especie y un resumen en
outputs/logs/resumen_prediccion.csv (misma lógica del entrenamiento: loop por
especie, t_total, resumen CSV).

Grilla = rasters_terreno (SA, slope.tif como referencia). Las 10 bioclim + elevación
se alinean a esa grilla (reproject_match); pendiente/orientación ya están en ella.

Uso: python scripts/07_predecir_sudamerica.py
"""
from __future__ import annotations

import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import rioxarray  # noqa: F401
import rasterio

warnings.filterwarnings("ignore")
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

import config  # noqa: E402
from capas import nombres  # noqa: E402
from extraccion import background as bg, predictoras  # noqa: E402

from joblib import Parallel, delayed  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.ensemble import RandomForestClassifier  # noqa: E402

BIO = ["bio1", "bio4", "bio5", "bio6", "bio7", "bio10", "bio11", "bio12", "bio15", "bio17"]
PRED = [nombres.NOMBRES_ES[c] for c in BIO] + ["elevacion", "pendiente", "exposicion_norte", "exposicion_este"]
ALGOS = ["glm", "gam", "rf", "gbm", "maxent"]

RA = _ROOT / "rama_v4" / "data" / "processed" / "rasters_terreno"
RA60 = _ROOT / "rama_v4" / "data" / "processed" / "rasters_terreno_60m"
BASE = _ROOT / "rama_v4" / "data" / "processed" / "base_datos_completa.csv"
METRICAS = _ROOT / "outputs" / "tables" / "metricas_v4_ensemble.csv"
RUTA_MAPS = _ROOT / "outputs" / "maps"
RUTA_LOGS = _ROOT / "outputs" / "logs"

# Reusar extraer_predictoras / _filtrar_sudamerica del script de entrenamiento
# (mismo background por especie). Se carga a nivel de módulo para que los workers
# de joblib lo tengan disponible dentro de predecir_especie.
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("ent", _ROOT / "scripts" / "05_entrenar_ensemble.py")
ENT = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(ENT)


def construir_grilla():
    """Apila las 14 predictoras alineadas a la grilla SA. Devuelve (Xvalid, valid, (H,W), perfil)."""
    ref = rioxarray.open_rasterio(RA / "slope.tif", masked=True).squeeze("band", drop=True)
    capas = {}
    for c in BIO:
        da = rioxarray.open_rasterio(config.WORLDCLIM_PRESENT / f"{c}.tif", masked=True).squeeze("band", drop=True)
        capas[nombres.NOMBRES_ES[c]] = da.rio.reproject_match(ref)
    el = rioxarray.open_rasterio(config.WORLDCLIM_PRESENT / "elevation.tif", masked=True).squeeze("band", drop=True)
    capas["elevacion"] = el.rio.reproject_match(ref)
    capas["pendiente"] = ref
    capas["exposicion_norte"] = rioxarray.open_rasterio(RA / "northness.tif", masked=True).squeeze("band", drop=True)
    capas["exposicion_este"] = rioxarray.open_rasterio(RA / "eastness.tif", masked=True).squeeze("band", drop=True)

    arrays = [np.asarray(capas[c].values, dtype="float32") for c in PRED]
    H, W = arrays[0].shape
    flat = np.stack(arrays, axis=-1).reshape(-1, len(PRED))
    valid = ~np.isnan(flat).any(axis=1)
    perfil = {"crs": ref.rio.crs, "transform": ref.rio.transform(), "height": H, "width": W}
    return flat[valid], valid, (H, W), perfil


def _fit_predict(Xtr, ytr, w, Xgrid_sel):
    """Ajusta los 5 algoritmos en datos completos y predice sobre la grilla."""
    out = {}
    sc = StandardScaler().fit(Xtr)
    Xtr_s, Xg_s = sc.transform(Xtr), sc.transform(Xgrid_sel)
    try:
        m = LogisticRegression(penalty="l1", solver="liblinear", max_iter=2000).fit(Xtr_s, ytr, sample_weight=w)
        out["glm"] = m.predict_proba(Xg_s)[:, 1]
    except Exception: pass
    try:
        from pygam import LogisticGAM
        m = LogisticGAM().fit(Xtr_s, ytr, weights=w); out["gam"] = m.predict_proba(Xg_s)
    except Exception: pass
    try:
        m = RandomForestClassifier(n_estimators=300, n_jobs=1, random_state=config.RANDOM_SEED).fit(Xtr, ytr, sample_weight=w)
        out["rf"] = m.predict_proba(Xgrid_sel)[:, 1]
    except Exception: pass
    try:
        import lightgbm as lgb
        m = lgb.LGBMClassifier(n_estimators=400, n_jobs=1, verbose=-1, random_state=config.RANDOM_SEED).fit(Xtr, ytr, sample_weight=w)
        out["gbm"] = m.predict_proba(Xgrid_sel)[:, 1]
    except Exception: pass
    try:
        from elapid import MaxentModel
        m = MaxentModel().fit(Xtr, ytr); out["maxent"] = np.asarray(m.predict(Xgrid_sel)).ravel()
    except Exception: pass
    return out


def predecir_especie(sp, pres, pesos, Xvalid, valid, shape, perfil):
    """Entrena el ensemble y escribe el raster de idoneidad de una especie."""
    t0 = time.time()
    slug = sp.strip().lower().replace(" ", "_").replace(".", "")
    try:
        # Background dentro del área accesible de la especie (mismo criterio que [05]).
        bgdf = ENT.extraer_predictoras(
            bg.muestrear_background_especie(pres.lon.values, pres.lat.values, seed=config.RANDOM_SEED)
        )
        preds = predictoras.seleccionar_predictoras(pres, PRED)
        sel_idx = [PRED.index(p) for p in preds]
        dfp = pres.dropna(subset=preds); dfb = bgdf.dropna(subset=preds)
        Xtr = np.vstack([dfp[preds].values, dfb[preds].values])
        ytr = np.r_[np.ones(len(dfp), int), np.zeros(len(dfb), int)]
        w = bg._pesos(ytr, "prevalencia")
        gpred = _fit_predict(Xtr, ytr, w, Xvalid[:, sel_idx])

        # combinación ponderada por TSS-CV (clamp >=0); equal si no hay pesos
        num = np.zeros(Xvalid.shape[0]); den = 0.0
        for a, p in gpred.items():
            peso = max(pesos.get(a, 0.0), 0.0)
            num += peso * p; den += peso
        if den == 0:
            for a, p in gpred.items():
                num += p; den += 1
        ens = num / den if den else num

        H, W = shape
        full = np.full(H * W, np.nan, dtype="float32")
        full[np.where(valid)[0]] = ens
        full = full.reshape(H, W)
        RUTA_MAPS.mkdir(parents=True, exist_ok=True)
        destino = RUTA_MAPS / f"{slug}_idoneidad_sa.tif"
        with rasterio.open(destino, "w", driver="GTiff", height=H, width=W, count=1,
                           dtype="float32", crs=perfil["crs"], transform=perfil["transform"],
                           nodata=np.nan, compress="lzw") as dst:
            dst.write(full, 1)
        t = (time.time() - t0) / 60
        print(f"  {sp:24s} OK  {len(preds)} pred  {t:.2f} min  -> {destino.name}")
        return {"especie": sp, "status": "OK", "n_pred": len(preds), "min": round(t, 2), "archivo": destino.name}
    except Exception as e:  # noqa: BLE001
        print(f"  {sp:24s} ERROR: {e}")
        return {"especie": sp, "status": f"ERROR: {e}", "n_pred": 0, "min": round((time.time()-t0)/60, 2)}


def main():
    t_ini = time.time()
    base = ENT._filtrar_sudamerica(pd.read_csv(BASE))
    met = pd.read_csv(METRICAS).set_index("especie")
    print("Construyendo grilla de predictoras (Sudamérica)...")
    Xvalid, valid, shape, perfil = construir_grilla()

    vc = base["especie"].value_counts()
    viables = [s for s in vc[vc >= config.MIN_RECORDS_TO_MODEL].index if s in met.index]
    print(f"Prediciendo {len(viables)} especies en paralelo ({Xvalid.shape[0]} celdas válidas)...")
    print("Background = área accesible por especie (buffer 300 km, en tierra-SA).")

    def pesos_de(sp):
        return {a: float(met.loc[sp, f"tss_{a}"]) if f"tss_{a}" in met.columns and pd.notna(met.loc[sp, f"tss_{a}"]) else 0.0 for a in ALGOS}

    resultados = Parallel(n_jobs=4)(
        delayed(predecir_especie)(sp, base[base.especie == sp].copy(),
                                  pesos_de(sp), Xvalid, valid, shape, perfil)
        for sp in viables
    )
    t_total = (time.time() - t_ini) / 60
    print(f"\n{'='*50}\nPredicción terminada en {t_total:.2f} minutos\n{'='*50}")
    RUTA_LOGS.mkdir(parents=True, exist_ok=True)
    resumen = pd.DataFrame(resultados)
    archivo = RUTA_LOGS / "resumen_prediccion.csv"
    resumen.to_csv(archivo, index=False)
    print(resumen.to_string(index=False))
    print(f"\nResumen guardado en: {archivo}")


if __name__ == "__main__":
    main()
