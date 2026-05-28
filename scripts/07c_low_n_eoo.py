"""
07c_low_n_eoo.py — Idoneidad de las especies con pocos registros (n < 50).

Completa las 21 especies del proyecto. Las 16 viables (n >= MIN_RECORDS_TO_MODEL)
ya tienen su {slug}_idoneidad_sa.tif desde 07_predecir_sudamerica.py. Aquí se
generan las 5 restantes con el método apropiado a su tamaño muestral, sobre la
MISMA grilla de Sudamérica que [07] (se reutiliza su construir_grilla):

  A) MaxEnt regularizado — BAJA CONFIANZA (n en 25-49):
       Aloysia salviifolia, Atriplex deserticola, Dinemagonum gayanum, Nolana rostrata
     MaxEnt (elapid) con features simples (linear + product) y beta_multiplier alto
     para evitar el sobreajuste propio de n bajo; background = área accesible de la
     especie (mismo criterio que [05]/[07]). Salida en idoneidad cloglog [0,1].

  B) Extensión de ocurrencia (EOO — NO es un SDM, n < 25):
       Nolana albescens
     Superficie = 1.0 dentro del polígono convexo (mínimo) de las presencias, con
     decaimiento gaussiano (escala 50 km) hacia afuera. Es un mapa de RANGO, no de
     idoneidad ambiental: con n muy bajo no hay base para ajustar un nicho.

Salidas:
  outputs/maps/{slug}_idoneidad_sa.tif        (mismo formato/CRS que las 16 viables)
  outputs/tables/confianza_idoneidad_por_especie.csv   (método y confianza por especie)

Uso: python scripts/07c_low_n_eoo.py
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

import config  # noqa: E402

# Reutilizar el script de predicción [07]: nos da construir_grilla (grilla SA
# idéntica), PRED (orden de predictoras), el módulo de entrenamiento ENT, y los
# helpers de background/predictoras. Así los 5 raster nuevos quedan alineados
# celda a celda con los 16 existentes.
_spec = importlib.util.spec_from_file_location(
    "prd", _ROOT / "scripts" / "07_predecir_sudamerica.py"
)
PRD = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PRD)

PRED = PRD.PRED
ENT = PRD.ENT
bg = PRD.bg
predictoras = PRD.predictoras
RUTA_MAPS = PRD.RUTA_MAPS
RUTA_TABLES = _ROOT / "outputs" / "tables"

# Reparto por tamaño muestral (conteos V4 post-filtro Sudamérica).
ESPECIES_MAXENT_LOWN = [
    "Aloysia salviifolia",
    "Atriplex deserticola",
    "Dinemagonum gayanum",
    "Nolana rostrata",
]
ESPECIES_EOO = [
    "Nolana albescens",
]

# Hiperparámetros MaxEnt low-n: features simples + regularización fuerte.
MAXENT_FEATURES = ["linear", "product"]
MAXENT_BETA = 3.0
# Escala de decaimiento del mapa EOO fuera del polígono convexo (km).
EOO_DECAY_KM = 50.0


def _slug(sp: str) -> str:
    return sp.strip().lower().replace(" ", "_").replace(".", "")


def _escribir_tif(suit_valid, valid, shape, perfil, destino: Path):
    """Coloca el vector de celdas válidas en la grilla completa y escribe el GeoTIFF."""
    H, W = shape
    full = np.full(H * W, np.nan, dtype="float32")
    full[np.where(valid)[0]] = suit_valid.astype("float32")
    full = full.reshape(H, W)
    RUTA_MAPS.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        destino, "w", driver="GTiff", height=H, width=W, count=1,
        dtype="float32", crs=perfil["crs"], transform=perfil["transform"],
        nodata=np.nan, compress="lzw",
    ) as dst:
        dst.write(full, 1)


def maxent_lown(sp, pres, Xvalid, valid, shape, perfil) -> dict:
    """Idoneidad por MaxEnt regularizado (baja confianza) sobre la grilla SA."""
    from elapid import MaxentModel

    t0 = time.time()
    slug = _slug(sp)
    # Background en el área accesible de la especie (mismo criterio que [05]/[07]).
    bgdf = ENT.extraer_predictoras(
        bg.muestrear_background_especie(pres.lon.values, pres.lat.values, seed=config.RANDOM_SEED)
    )
    preds = predictoras.seleccionar_predictoras(pres, PRED)
    sel_idx = [PRED.index(p) for p in preds]
    dfp = pres.dropna(subset=preds)
    dfb = bgdf.dropna(subset=preds)
    Xtr = np.vstack([dfp[preds].values, dfb[preds].values])
    ytr = np.r_[np.ones(len(dfp), int), np.zeros(len(dfb), int)]

    m = MaxentModel(
        feature_types=MAXENT_FEATURES,
        beta_multiplier=MAXENT_BETA,
        clamp=True,
        transform="cloglog",
    )
    m.fit(Xtr, ytr)
    suit = np.asarray(m.predict(Xvalid[:, sel_idx])).ravel()

    destino = RUTA_MAPS / f"{slug}_idoneidad_sa.tif"
    _escribir_tif(suit, valid, shape, perfil, destino)
    t = (time.time() - t0) / 60
    print(f"  {sp:24s} MaxEnt low-n  n={len(dfp):3d}  {len(preds)} pred  {t:.2f} min -> {destino.name}")
    return {"especie": sp, "slug": slug, "n_pres": int(len(dfp)), "n_pred": int(len(preds)),
            "metodo": "MaxEnt regularizado (linear+product, beta=%.1f)" % MAXENT_BETA,
            "confianza": "BAJA", "archivo": destino.name}


def eoo_map(sp, pres, valid, shape, perfil) -> dict:
    """Mapa de extensión de ocurrencia (polígono convexo + decaimiento gaussiano)."""
    from scipy.spatial import Delaunay
    from sklearn.neighbors import BallTree

    t0 = time.time()
    slug = _slug(sp)
    H, W = shape
    flat_idx = np.where(valid)[0]
    rows, cols = np.unravel_index(flat_idx, (H, W))
    # Centros de celda (lon, lat) de las celdas de tierra-SA válidas.
    xs, ys = rasterio.transform.xy(perfil["transform"], rows, cols)
    xs = np.asarray(xs, dtype="float64")
    ys = np.asarray(ys, dtype="float64")

    pts = np.column_stack([pres.lon.values, pres.lat.values]).astype("float64")
    # Dentro del polígono convexo (Delaunay.find_simplex >= 0 ⇔ dentro del hull).
    try:
        dela = Delaunay(pts)
        inside = dela.find_simplex(np.column_stack([xs, ys])) >= 0
    except Exception:
        inside = np.zeros(len(xs), dtype=bool)

    # Distancia geodésica a la presencia más cercana (BallTree-haversine).
    tree = BallTree(np.radians(np.column_stack([pres.lat.values, pres.lon.values])),
                    metric="haversine")
    d, _ = tree.query(np.radians(np.column_stack([ys, xs])), k=1)
    dist_km = d[:, 0] * 6371.0088

    suit = np.where(inside, 1.0, np.exp(-(dist_km / EOO_DECAY_KM) ** 2)).astype("float32")

    destino = RUTA_MAPS / f"{slug}_idoneidad_sa.tif"
    _escribir_tif(suit, valid, shape, perfil, destino)
    t = (time.time() - t0) / 60
    print(f"  {sp:24s} EOO (hull+decay {EOO_DECAY_KM:.0f}km)  n={len(pts):3d}  {t:.2f} min -> {destino.name}")
    return {"especie": sp, "slug": slug, "n_pres": int(len(pts)), "n_pred": 0,
            "metodo": "EOO: polígono convexo + decaimiento gaussiano %.0f km" % EOO_DECAY_KM,
            "confianza": "RANGO (no SDM)", "archivo": destino.name}


def main():
    base = ENT._filtrar_sudamerica(pd.read_csv(PRD.BASE))
    print("Construyendo grilla de predictoras (Sudamérica)...")
    Xvalid, valid, shape, perfil = PRD.construir_grilla()
    print(f"Grilla: {shape[0]}x{shape[1]}  |  {Xvalid.shape[0]} celdas válidas")

    filas = []
    print("\n== MaxEnt regularizado (BAJA CONFIANZA, n 25-49) ==")
    for sp in ESPECIES_MAXENT_LOWN:
        pres = base[base.especie == sp].copy()
        if pres.empty:
            print(f"  {sp:24s} SIN registros en la base — omitida.")
            continue
        filas.append(maxent_lown(sp, pres, Xvalid, valid, shape, perfil))

    print("\n== Extensión de ocurrencia (EOO, n < 25) ==")
    for sp in ESPECIES_EOO:
        pres = base[base.especie == sp].copy()
        if pres.empty:
            print(f"  {sp:24s} SIN registros en la base — omitida.")
            continue
        filas.append(eoo_map(sp, pres, valid, shape, perfil))

    RUTA_TABLES.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(filas)
    out = RUTA_TABLES / "confianza_idoneidad_por_especie.csv"
    df.to_csv(out, index=False, encoding="utf-8")
    print(f"\nGenerados {len(filas)} raster nuevos.")
    print(f"Tabla de método/confianza: {out}")


if __name__ == "__main__":
    main()
