"""
08_figuras_v4.py — Figuras PNG de los mapas de idoneidad V4 (revisión visual).

Para cada raster outputs/maps/{slug}_idoneidad_sa.tif:
  - idoneidad (0..1) sobre Sudamérica, colormap viridis + colorbar,
  - bordes de países (Natural Earth) para contexto geográfico,
  - presencias de la especie superpuestas (puntos),
  - título con AUC / TSS / Boyce / n (de metricas_v4_completa.csv).
Además, un panel 4×4 con las 16 especies para revisión rápida.

Salida: outputs/figures/{slug}_idoneidad_sa.png + outputs/figures/_panel_idoneidad_v4.png
Uso: python scripts/08_figuras_v4.py
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio

warnings.filterwarnings("ignore")
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))
import config  # noqa: E402

MAPS = _ROOT / "outputs" / "maps"
FIGS = _ROOT / "outputs" / "figures"
BASE = _ROOT / "rama_v4" / "data" / "processed" / "base_datos_completa.csv"
METRICAS = _ROOT / "outputs" / "tables" / "metricas_v4_completa.csv"
BBOX = config.PREDICTION_BBOX  # (min_lon, min_lat, max_lon, max_lat)


def _slug(sp: str) -> str:
    return sp.strip().lower().replace(" ", "_").replace(".", "")


def _cargar_bordes():
    """GeoSeries de bordes de países de Sudamérica (o None si no está Natural Earth)."""
    try:
        import geopandas as gpd
        gdf = gpd.read_file(config.RAW / "natural_earth" / "ne_110m_admin0_countries.gpkg")
        col = next((c for c in gdf.columns if c.upper() == "CONTINENT"), None)
        if col is not None:
            gdf = gdf[gdf[col] == "South America"]
        return gdf.boundary
    except Exception as exc:  # noqa: BLE001
        print(f"  (sin bordes de paises: {exc})")
        return None


def _leer_raster(path: Path):
    with rasterio.open(path) as ds:
        arr = ds.read(1)
        b = ds.bounds
    return arr, (b.left, b.right, b.bottom, b.top)


def _titulo(sp: str, met: pd.DataFrame) -> str:
    r = met[met["especie"] == sp]
    if r.empty:
        return sp
    r = r.iloc[0]
    return (f"{sp}\nAUC {r.auc_ensemble:.2f} · TSS {r.tss_ensemble:.2f} · "
            f"Boyce {r.boyce_ensemble:.2f} · n={int(r.n_pres)}")


def figura_especie(sp, arr, extent, pres, bordes, met):
    fig, ax = plt.subplots(figsize=(6.2, 7.6))
    im = ax.imshow(arr, extent=extent, origin="upper", cmap="viridis",
                   vmin=0.0, vmax=1.0, interpolation="nearest")
    if bordes is not None:
        bordes.plot(ax=ax, color="white", linewidth=0.4, alpha=0.6)
    if len(pres):
        ax.scatter(pres.lon, pres.lat, s=6, c="red", edgecolors="black",
                   linewidths=0.2, alpha=0.7, label=f"presencias ({len(pres)})")
        ax.legend(loc="lower left", fontsize=8, framealpha=0.8)
    ax.set_xlim(extent[0], extent[1]); ax.set_ylim(extent[2], extent[3])
    ax.set_xlabel("lon"); ax.set_ylabel("lat")
    ax.set_title(_titulo(sp, met), fontsize=10)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label="idoneidad")
    fig.tight_layout()
    out = FIGS / f"{_slug(sp)}_idoneidad_sa.png"
    fig.savefig(out, dpi=130); plt.close(fig)
    return out


def panel(items, bordes, met):
    """items: lista de (sp, arr, extent, pres). Panel 4x4."""
    n = len(items)
    ncol = 4
    nrow = int(np.ceil(n / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(ncol * 3.2, nrow * 4.0))
    axes = np.atleast_1d(axes).ravel()
    for ax in axes[n:]:
        ax.axis("off")
    for ax, (sp, arr, extent, pres) in zip(axes, items):
        im = ax.imshow(arr, extent=extent, origin="upper", cmap="viridis",
                       vmin=0.0, vmax=1.0, interpolation="nearest")
        if bordes is not None:
            bordes.plot(ax=ax, color="white", linewidth=0.3, alpha=0.5)
        if len(pres):
            ax.scatter(pres.lon, pres.lat, s=2, c="red", alpha=0.5)
        ax.set_xlim(extent[0], extent[1]); ax.set_ylim(extent[2], extent[3])
        ax.set_xticks([]); ax.set_yticks([])
        r = met[met["especie"] == sp]
        b = f" B{r.iloc[0].boyce_ensemble:.2f}" if not r.empty else ""
        ax.set_title(f"{sp}{b}", fontsize=8)
    fig.suptitle("Idoneidad V4 (background por área accesible) — Sudamérica", fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.98])
    out = FIGS / "_panel_idoneidad_v4.png"
    fig.savefig(out, dpi=120); plt.close(fig)
    return out


def main():
    FIGS.mkdir(parents=True, exist_ok=True)
    met = pd.read_csv(METRICAS)
    base = pd.read_csv(BASE)
    base = base[base.lon.between(BBOX[0], BBOX[2]) & base.lat.between(BBOX[1], BBOX[3])]
    bordes = _cargar_bordes()

    tifs = sorted(MAPS.glob("*_idoneidad_sa.tif"))
    if not tifs:
        print("No hay rasters *_idoneidad_sa.tif en outputs/maps.")
        return
    print(f"Renderizando {len(tifs)} mapas...")

    # orden por Boyce desc (mejores primero) si hay métricas
    orden = {row.especie: row.boyce_ensemble for _, row in met.iterrows()}
    items = []
    for tif in tifs:
        slug = tif.stem.replace("_idoneidad_sa", "")
        sp_match = met[met["especie"].map(_slug) == slug]
        sp = sp_match.iloc[0]["especie"] if not sp_match.empty else slug.replace("_", " ").title()
        arr, extent = _leer_raster(tif)
        pres = base[base.especie == sp][["lon", "lat"]]
        out = figura_especie(sp, arr, extent, pres, bordes, met)
        print(f"  OK {out.name}")
        items.append((sp, arr, extent, pres))

    items.sort(key=lambda it: orden.get(it[0], -1), reverse=True)
    out = panel(items, bordes, met)
    print(f"\nPanel resumen: {out}")
    print(f"Figuras en: {FIGS}")


if __name__ == "__main__":
    main()
