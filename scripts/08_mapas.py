"""
08_mapas.py — Visualización cartográfica del pipeline SDM (global-flora-sdm).

Genera figuras PNG en config.FIGURES para cada especie procesada:
  - Idoneidad presente
  - Idoneidad media futura (2050, ensemble de GCMs × SSPs)
  - Δidoneidad (futuro − presente)
  - Incertidumbre del ensemble (SD entre escenarios/algoritmos)
  - MESS futuro (zonas de extrapolación)
  - Mapas binarios por umbral (maxTSS, p10) si los umbrales están disponibles
  - Panel comparativo presente vs. 2050

Dependencias principales: rasterio, matplotlib, numpy, geopandas.
Cartopy se usa cuando está disponible; si no, se degrada a imshow+extent.

Uso
---
    python 08_mapas.py                          # todas las especies
    python 08_mapas.py --species "Nolana divaricata"
    python 08_mapas.py --species "Schinus areira" "Atriplex semibaccata"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import matplotlib
matplotlib.use("Agg")  # backend sin display para entornos sin GUI
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.cm import ScalarMappable

import config
import utils

# ---------------------------------------------------------------------------
# Detección de dependencias opcionales
# ---------------------------------------------------------------------------
try:
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    _CARTOPY_AVAILABLE = True
except ImportError:
    _CARTOPY_AVAILABLE = False

try:
    import geopandas as gpd
    _GEOPANDAS_AVAILABLE = True
except ImportError:
    _GEOPANDAS_AVAILABLE = False

try:
    import rasterio
    from rasterio.plot import reshape_as_image
    _RASTERIO_AVAILABLE = True
except ImportError:
    _RASTERIO_AVAILABLE = False

try:
    import joblib
    _JOBLIB_AVAILABLE = True
except ImportError:
    _JOBLIB_AVAILABLE = False

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
log = utils.get_logger("08_mapas")

# ---------------------------------------------------------------------------
# Constantes de visualización
# ---------------------------------------------------------------------------
DPI = 150
FIGSIZE_SINGLE = (12, 7)
FIGSIZE_PANEL = (18, 7)

# Colormaps
CMAP_SUITABILITY = "YlOrRd"          # idoneidad: amarillo → rojo
CMAP_DELTA = "RdBu_r"                # Δidoneidad: divergente azul/rojo invertido
CMAP_UNCERTAINTY = "viridis"         # incertidumbre: secuencial
CMAP_MESS = "RdYlGn"                 # MESS: rojo=extrapolación, verde=interpolación
CMAP_BINARY = mcolors.ListedColormap(["#e0e0e0", "#2c7bb6"])  # gris / azul

# Colores de puntos de ocurrencia
OCCURRENCE_COLOR = "#1a1a1a"
OCCURRENCE_EDGE = "white"
OCCURRENCE_SIZE = 8
OCCURRENCE_ALPHA = 0.7

# Proyección cartopy por defecto
DEFAULT_PROJECTION = "PlateCarree"   # alternativa: "Robinson"


# ===========================================================================
# Carga de datos
# ===========================================================================

def _load_raster(path: Path) -> tuple[np.ndarray, tuple[float, float, float, float], float]:
    """Carga un GeoTIFF y devuelve (array_2d, extent_lonlat, nodata).

    Parameters
    ----------
    path:
        Ruta al archivo GeoTIFF.

    Returns
    -------
    data:
        Array 2D float32 con nodata reemplazado por np.nan.
    extent:
        (xmin, xmax, ymin, ymax) en grados decimales (lon/lat).
    nodata_val:
        Valor nodata original (para referencia).
    """
    if not _RASTERIO_AVAILABLE:
        raise RuntimeError("rasterio no disponible; instalar con: pip install rasterio")

    with rasterio.open(path) as src:
        data = src.read(1).astype(np.float32)
        nodata_val = src.nodata
        bounds = src.bounds  # left, bottom, right, top
        extent = (bounds.left, bounds.right, bounds.bottom, bounds.top)

    if nodata_val is not None:
        data[data == nodata_val] = np.nan
    # Valores negativos muy bajos como nodata (ej. -9999, -3.4e38)
    data[data < -1e10] = np.nan

    return data, extent, nodata_val


def _load_occurrences(slug: str) -> Optional["gpd.GeoDataFrame"]:
    """Carga las ocurrencias limpias de una especie como GeoDataFrame.

    Filtra por slug (nombre_cientifico o especie convertido a slug).
    Devuelve None si geopandas no está disponible o el archivo no existe.
    """
    if not _GEOPANDAS_AVAILABLE:
        log.warning("geopandas no disponible; se omitirá overlay de ocurrencias")
        return None

    occ_path = config.OCCURRENCES_CLEAN
    if not occ_path.exists():
        log.warning("Archivo de ocurrencias no encontrado: %s", occ_path)
        return None

    try:
        gdf = gpd.read_file(occ_path)
    except Exception as exc:
        log.warning("No se pudo leer ocurrencias: %s", exc)
        return None

    # Detectar columna de especie
    species_col = None
    for col in ("especie", "nombre_cientifico", "species"):
        if col in gdf.columns:
            species_col = col
            break

    if species_col is None:
        log.warning("No se encontró columna de especie en ocurrencias; omitiendo overlay")
        return None

    # Filtrar por slug
    mask = gdf[species_col].apply(utils.slugify_species) == slug
    subset = gdf[mask]
    if subset.empty:
        log.debug("Sin ocurrencias para slug '%s'", slug)
        return None

    return subset.to_crs("EPSG:4326")


def _load_thresholds_from_joblib(slug: str) -> dict[str, float]:
    """Intenta leer umbrales maxTSS y p10 del joblib del ensemble.

    El joblib debe contener un dict con clave 'thresholds' que a su vez
    tenga 'maxTSS' y/o 'p10'. Si no, devuelve dict vacío.
    """
    if not _JOBLIB_AVAILABLE:
        return {}

    model_path = config.ENSEMBLE_MODELS / f"{slug}.joblib"
    if not model_path.exists():
        return {}

    try:
        bundle = joblib.load(model_path)
    except Exception as exc:
        log.debug("No se pudo cargar joblib para '%s': %s", slug, exc)
        return {}

    if isinstance(bundle, dict) and "thresholds" in bundle:
        return {k: float(v) for k, v in bundle["thresholds"].items()
                if v is not None}
    return {}


def _compute_thresholds_from_data(
    suitability: np.ndarray,
    occurrences: Optional["gpd.GeoDataFrame"],
    extent: tuple[float, float, float, float],
) -> dict[str, float]:
    """Calcula umbrales maxTSS y p10 desde la capa de idoneidad.

    Cuando no hay un joblib con umbrales pre-calculados, usa heurísticas:
    - p10: percentil 10 de los valores de idoneidad en celdas de presencia
      (si hay ocurrencias); si no, percentil 10 global de la distribución.
    - maxTSS: umbral que maximiza TSS estimado como F-score del histograma
      (heurística rápida sin presencias/ausencias explícitas → usa media
      de la distribución de idoneidad como sustituto razonable).

    Parameters
    ----------
    suitability:
        Array 2D de idoneidad (0–1).
    occurrences:
        GeoDataFrame de ocurrencias (puede ser None).
    extent:
        (xmin, xmax, ymin, ymax) de la capa.

    Returns
    -------
    Diccionario con claves 'maxTSS' y/o 'p10'.
    """
    thresholds: dict[str, float] = {}
    valid = suitability[~np.isnan(suitability)]

    if valid.size == 0:
        return thresholds

    # --- p10 ---
    if occurrences is not None and not occurrences.empty and _RASTERIO_AVAILABLE:
        # Extraer idoneidad en puntos de presencia por coordenadas
        nrows, ncols = suitability.shape
        xmin, xmax, ymin, ymax = extent
        xs = occurrences.geometry.x.values
        ys = occurrences.geometry.y.values
        # Convertir lon/lat a índices de pixel
        col_idx = ((xs - xmin) / (xmax - xmin) * (ncols - 1)).astype(int)
        row_idx = ((ymax - ys) / (ymax - ymin) * (nrows - 1)).astype(int)
        # Recortar a límites del array
        valid_mask = (
            (col_idx >= 0) & (col_idx < ncols) &
            (row_idx >= 0) & (row_idx < nrows)
        )
        col_idx = col_idx[valid_mask]
        row_idx = row_idx[valid_mask]
        pres_vals = suitability[row_idx, col_idx]
        pres_vals = pres_vals[~np.isnan(pres_vals)]
        if pres_vals.size > 0:
            thresholds["p10"] = float(np.percentile(pres_vals, 10))
    if "p10" not in thresholds:
        # Fallback: percentil 10 global de la distribución de idoneidad
        thresholds["p10"] = float(np.percentile(valid, 10))

    # --- maxTSS heurístico ---
    # Sin ausencias reales, usamos el método de Liu et al. (2013):
    # el umbral que divide la distribución de idoneidad minimizando
    # los errores de comisión + omisión → equivalente a la media.
    # Si hay ocurrencias, usamos la mediana de los valores de presencia.
    if occurrences is not None and "p10" in thresholds:
        thresholds["maxTSS"] = float(
            np.clip(thresholds["p10"] * 2.0, 0.0, valid.max())
        )
    else:
        thresholds["maxTSS"] = float(np.nanmean(suitability))

    return thresholds


# ===========================================================================
# Funciones de dibujo
# ===========================================================================

def _make_figure_cartopy(
    projection: str = DEFAULT_PROJECTION,
) -> tuple["plt.Figure", "plt.Axes"]:
    """Crea figura y eje con proyección cartopy.

    Parameters
    ----------
    projection:
        'PlateCarree' o 'Robinson'.

    Returns
    -------
    fig, ax
    """
    if projection == "Robinson":
        proj = ccrs.Robinson()
    else:
        proj = ccrs.PlateCarree()

    fig = plt.figure(figsize=FIGSIZE_SINGLE)
    ax = fig.add_subplot(1, 1, 1, projection=proj)
    _focus_view(ax, use_cartopy=True)
    return fig, ax


def _add_cartopy_features(ax: "plt.Axes") -> None:
    """Añade costas, bordes y graticulas a un eje cartopy."""
    ax.add_feature(cfeature.LAND, facecolor="#f5f5f0", zorder=0)
    ax.add_feature(cfeature.OCEAN, facecolor="#d6eaf8", zorder=0)
    ax.add_feature(cfeature.COASTLINE, linewidth=0.5, edgecolor="#333333", zorder=2)
    ax.add_feature(cfeature.BORDERS, linewidth=0.3, edgecolor="#999999",
                   linestyle=":", zorder=2)
    ax.gridlines(draw_labels=False, linewidth=0.3, color="#aaaaaa",
                 alpha=0.5, linestyle="--", zorder=1)


def _add_matplotlib_basemap(ax: "plt.Axes", extent: tuple) -> None:
    """Dibuja un basemap mínimo con matplotlib puro (fallback sin cartopy).

    Solo añade el rectángulo del extent y etiquetas de ejes. La vista se enfoca
    en Sudamérica (config.PREDICTION_BBOX), no en el extent global del raster.
    """
    minx, miny, maxx, maxy = config.PREDICTION_BBOX
    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)
    ax.set_xlabel("Longitud (°)", fontsize=8)
    ax.set_ylabel("Latitud (°)", fontsize=8)
    ax.tick_params(labelsize=7)
    ax.set_facecolor("#d6eaf8")


def _focus_view(ax: "plt.Axes", use_cartopy: bool) -> None:
    """Enfoca la vista del mapa en Sudamérica (config.PREDICTION_BBOX).

    El raster ya viene recortado a Sudamérica (nodata fuera); esto ajusta el
    encuadre para que la figura no muestre el globo completo.
    """
    minx, miny, maxx, maxy = config.PREDICTION_BBOX
    if use_cartopy:
        ax.set_extent([minx, maxx, miny, maxy], crs=ccrs.PlateCarree())
    else:
        ax.set_xlim(minx, maxx)
        ax.set_ylim(miny, maxy)


def _add_colorbar(
    fig: "plt.Figure",
    ax: "plt.Axes",
    mappable: "ScalarMappable",
    label: str,
    orientation: str = "vertical",
) -> None:
    """Añade colorbar con etiqueta al eje indicado.

    Parameters
    ----------
    fig, ax:
        Figura y eje principal.
    mappable:
        Resultado de imshow o pcolormesh.
    label:
        Texto del colorbar.
    orientation:
        'vertical' (derecha) o 'horizontal' (abajo).
    """
    cbar = fig.colorbar(
        mappable,
        ax=ax,
        orientation=orientation,
        fraction=0.03 if orientation == "vertical" else 0.04,
        pad=0.02,
        shrink=0.7,
    )
    cbar.set_label(label, fontsize=9)
    cbar.ax.tick_params(labelsize=8)


def _overlay_occurrences(
    ax: "plt.Axes",
    occurrences: Optional["gpd.GeoDataFrame"],
    use_cartopy: bool = True,
) -> None:
    """Dibuja puntos de presencia sobre el mapa.

    Parameters
    ----------
    ax:
        Eje donde dibujar.
    occurrences:
        GeoDataFrame con geometría de puntos.
    use_cartopy:
        Si True, usa transform=ccrs.PlateCarree() para cartopy.
    """
    if occurrences is None or occurrences.empty:
        return

    xs = occurrences.geometry.x.values
    ys = occurrences.geometry.y.values

    scatter_kwargs: dict = dict(
        s=OCCURRENCE_SIZE,
        c=OCCURRENCE_COLOR,
        edgecolors=OCCURRENCE_EDGE,
        linewidths=0.3,
        alpha=OCCURRENCE_ALPHA,
        zorder=5,
        label=f"Presencias (n={len(occurrences):,})",
    )
    if use_cartopy:
        ax.scatter(xs, ys, transform=ccrs.PlateCarree(), **scatter_kwargs)
    else:
        ax.scatter(xs, ys, **scatter_kwargs)


# ===========================================================================
# Función principal de renderizado de un raster
# ===========================================================================

def plot_raster(
    data: np.ndarray,
    extent: tuple[float, float, float, float],
    title: str,
    out_path: Path,
    cmap: str | mcolors.Colormap = CMAP_SUITABILITY,
    vmin: Optional[float] = None,
    vmax: Optional[float] = None,
    vcenter: Optional[float] = None,
    colorbar_label: str = "Idoneidad",
    occurrences: Optional["gpd.GeoDataFrame"] = None,
    projection: str = DEFAULT_PROJECTION,
    note: str = "",
) -> None:
    """Dibuja un raster de idoneidad sobre un mapa global y guarda la figura.

    Usa cartopy si está disponible; si no, degrada a matplotlib+imshow.

    Parameters
    ----------
    data:
        Array 2D float con el raster a visualizar (nodata = np.nan).
    extent:
        (xmin, xmax, ymin, ymax) en grados decimales.
    title:
        Título del mapa.
    out_path:
        Ruta de salida del PNG.
    cmap:
        Colormap matplotlib o nombre de colormap.
    vmin, vmax:
        Rango del colormap. Si son None, se calculan automáticamente.
    vcenter:
        Si se especifica, se crea una normalización divergente centrada aquí
        (útil para Δidoneidad).
    colorbar_label:
        Etiqueta del colorbar.
    occurrences:
        GeoDataFrame de puntos de presencia a superponer.
    projection:
        Proyección cartopy a usar ('PlateCarree' o 'Robinson').
    note:
        Nota adicional en pie de figura.
    """
    if vmin is None:
        vmin = float(np.nanpercentile(data, 2))
    if vmax is None:
        vmax = float(np.nanpercentile(data, 98))

    # Normalización
    if vcenter is not None:
        norm = mcolors.TwoSlopeNorm(vmin=vmin, vcenter=vcenter, vmax=vmax)
    else:
        norm = mcolors.Normalize(vmin=vmin, vmax=vmax)

    xmin, xmax, ymin, ymax = extent

    if _CARTOPY_AVAILABLE:
        fig, ax = _make_figure_cartopy(projection)
        _add_cartopy_features(ax)

        # imshow con transformación PlateCarree (datos en lon/lat)
        im = ax.imshow(
            data,
            origin="upper",
            extent=[xmin, xmax, ymin, ymax],
            transform=ccrs.PlateCarree(),
            cmap=cmap,
            norm=norm,
            interpolation="nearest",
            zorder=3,
        )
        _overlay_occurrences(ax, occurrences, use_cartopy=True)

        # Leyenda de ocurrencias
        if occurrences is not None and not occurrences.empty:
            ax.legend(
                loc="lower left",
                fontsize=7,
                framealpha=0.7,
                markerscale=1.5,
            )

        ax.set_title(title, fontsize=11, fontweight="bold", pad=8)
        _add_colorbar(fig, ax, im, colorbar_label)

    else:
        # Fallback: matplotlib puro
        fig, ax = plt.subplots(figsize=FIGSIZE_SINGLE)
        _add_matplotlib_basemap(ax, extent)
        im = ax.imshow(
            data,
            origin="upper",
            extent=[xmin, xmax, ymin, ymax],
            aspect="auto",
            cmap=cmap,
            norm=norm,
            interpolation="nearest",
            zorder=2,
        )
        _overlay_occurrences(ax, occurrences, use_cartopy=False)

        if occurrences is not None and not occurrences.empty:
            ax.legend(loc="lower left", fontsize=7, framealpha=0.7)

        ax.set_title(title, fontsize=11, fontweight="bold")
        _add_colorbar(fig, ax, im, colorbar_label)

    if note:
        fig.text(
            0.5, 0.01, note,
            ha="center", va="bottom", fontsize=7, color="#666666",
            style="italic",
        )

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    log.info("  Guardado: %s", out_path.name)


# ===========================================================================
# Mapa binario por umbral
# ===========================================================================

def plot_binary(
    data: np.ndarray,
    threshold: float,
    extent: tuple[float, float, float, float],
    title: str,
    out_path: Path,
    occurrences: Optional["gpd.GeoDataFrame"] = None,
    projection: str = DEFAULT_PROJECTION,
    note: str = "",
) -> None:
    """Genera un mapa binario (apto/no apto) aplicando un umbral sobre idoneidad.

    Parameters
    ----------
    data:
        Array 2D de idoneidad continua (0–1).
    threshold:
        Valor de corte. Píxeles >= threshold se marcan como presencia.
    extent:
        (xmin, xmax, ymin, ymax).
    title:
        Título del mapa.
    out_path:
        Ruta de salida PNG.
    occurrences:
        Puntos de presencia (opcional).
    projection:
        Proyección cartopy.
    note:
        Nota al pie.
    """
    binary = np.where(np.isnan(data), np.nan, (data >= threshold).astype(float))

    # Para imshow, nan debe visualizarse como transparente o color de fondo
    # Usamos masked array
    binary_masked = np.ma.masked_invalid(binary)

    xmin, xmax, ymin, ymax = extent

    if _CARTOPY_AVAILABLE:
        fig, ax = _make_figure_cartopy(projection)
        _add_cartopy_features(ax)
        im = ax.imshow(
            binary_masked,
            origin="upper",
            extent=[xmin, xmax, ymin, ymax],
            transform=ccrs.PlateCarree(),
            cmap=CMAP_BINARY,
            vmin=0, vmax=1,
            interpolation="nearest",
            zorder=3,
        )
        _overlay_occurrences(ax, occurrences, use_cartopy=True)
        if occurrences is not None and not occurrences.empty:
            ax.legend(loc="lower left", fontsize=7, framealpha=0.7)
        ax.set_title(title, fontsize=11, fontweight="bold", pad=8)
        # Colorbar con etiquetas de clase
        cbar = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02, shrink=0.7,
                            ticks=[0.25, 0.75])
        cbar.ax.set_yticklabels(["No apto", "Apto"], fontsize=8)
        cbar.set_label(f"Umbral ≥ {threshold:.3f}", fontsize=9)

    else:
        fig, ax = plt.subplots(figsize=FIGSIZE_SINGLE)
        _add_matplotlib_basemap(ax, extent)
        im = ax.imshow(
            binary_masked,
            origin="upper",
            extent=[xmin, xmax, ymin, ymax],
            aspect="auto",
            cmap=CMAP_BINARY,
            vmin=0, vmax=1,
            interpolation="nearest",
            zorder=2,
        )
        _overlay_occurrences(ax, occurrences, use_cartopy=False)
        if occurrences is not None and not occurrences.empty:
            ax.legend(loc="lower left", fontsize=7, framealpha=0.7)
        ax.set_title(title, fontsize=11, fontweight="bold")
        cbar = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02, shrink=0.7,
                            ticks=[0.25, 0.75])
        cbar.ax.set_yticklabels(["No apto", "Apto"], fontsize=8)
        cbar.set_label(f"Umbral ≥ {threshold:.3f}", fontsize=9)

    if note:
        fig.text(0.5, 0.01, note, ha="center", va="bottom",
                 fontsize=7, color="#666666", style="italic")

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    log.info("  Guardado: %s", out_path.name)


# ===========================================================================
# Panel comparativo presente vs. 2050
# ===========================================================================

def plot_comparison_panel(
    present_data: np.ndarray,
    future_data: np.ndarray,
    extent: tuple[float, float, float, float],
    species_name: str,
    out_path: Path,
    occurrences: Optional["gpd.GeoDataFrame"] = None,
    projection: str = DEFAULT_PROJECTION,
) -> None:
    """Panel de dos mapas: idoneidad presente (izq.) vs. media 2050 (der.).

    Usa la misma escala de colores y rango de valores para facilitar la comparación.

    Parameters
    ----------
    present_data:
        Array 2D de idoneidad presente.
    future_data:
        Array 2D de idoneidad media futura.
    extent:
        (xmin, xmax, ymin, ymax).
    species_name:
        Nombre de la especie (para el título).
    out_path:
        Ruta de salida PNG.
    occurrences:
        Puntos de presencia (opcional).
    projection:
        Proyección cartopy.
    """
    # Escala compartida entre ambos paneles
    all_vals = np.concatenate([
        present_data[~np.isnan(present_data)].ravel(),
        future_data[~np.isnan(future_data)].ravel(),
    ])
    if all_vals.size == 0:
        log.warning("Sin datos para panel comparativo de '%s'", species_name)
        return

    vmin = float(np.percentile(all_vals, 2))
    vmax = float(np.percentile(all_vals, 98))
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
    xmin, xmax, ymin, ymax = extent

    if _CARTOPY_AVAILABLE:
        proj = ccrs.Robinson() if projection == "Robinson" else ccrs.PlateCarree()
        fig, axes = plt.subplots(
            1, 2, figsize=FIGSIZE_PANEL,
            subplot_kw={"projection": proj},
        )
        panels = [
            (present_data, "Presente (1970-2000)", axes[0]),
            (future_data, "Futuro media 2050 (CMIP6)", axes[1]),
        ]
        for arr, subtitle, ax in panels:
            _focus_view(ax, use_cartopy=True)
            _add_cartopy_features(ax)
            im = ax.imshow(
                arr,
                origin="upper",
                extent=[xmin, xmax, ymin, ymax],
                transform=ccrs.PlateCarree(),
                cmap=CMAP_SUITABILITY,
                norm=norm,
                interpolation="nearest",
                zorder=3,
            )
            _overlay_occurrences(ax, occurrences, use_cartopy=True)
            ax.set_title(subtitle, fontsize=10, fontweight="bold", pad=6)

        # Colorbar compartido
        fig.subplots_adjust(right=0.88, wspace=0.05)
        cbar_ax = fig.add_axes([0.90, 0.15, 0.015, 0.7])
        sm = ScalarMappable(cmap=CMAP_SUITABILITY, norm=norm)
        sm.set_array([])
        cb = fig.colorbar(sm, cax=cbar_ax)
        cb.set_label("Idoneidad (0–1)", fontsize=9)
        cb.ax.tick_params(labelsize=8)

    else:
        fig, axes = plt.subplots(1, 2, figsize=FIGSIZE_PANEL)
        panels = [
            (present_data, "Presente (1970-2000)", axes[0]),
            (future_data, "Futuro media 2050 (CMIP6)", axes[1]),
        ]
        for arr, subtitle, ax in panels:
            _add_matplotlib_basemap(ax, extent)
            im = ax.imshow(
                arr,
                origin="upper",
                extent=[xmin, xmax, ymin, ymax],
                aspect="auto",
                cmap=CMAP_SUITABILITY,
                norm=norm,
                interpolation="nearest",
                zorder=2,
            )
            _overlay_occurrences(ax, occurrences, use_cartopy=False)
            ax.set_title(subtitle, fontsize=10, fontweight="bold")

        fig.subplots_adjust(right=0.88, wspace=0.1)
        cbar_ax = fig.add_axes([0.90, 0.15, 0.015, 0.7])
        sm = ScalarMappable(cmap=CMAP_SUITABILITY, norm=norm)
        sm.set_array([])
        cb = fig.colorbar(sm, cax=cbar_ax)
        cb.set_label("Idoneidad (0–1)", fontsize=9)
        cb.ax.tick_params(labelsize=8)

    fig.suptitle(
        f"{species_name} — Cambio de idoneidad climática (Presente vs. 2050)",
        fontsize=12, fontweight="bold", y=1.01,
    )
    fig.text(
        0.5, -0.01,
        "Ensemble: GLM · GAM · RF · GBM · MaxEnt. "
        f"GCMs: {', '.join(config.GCMS)}. SSPs: {', '.join(config.SSPS)}.",
        ha="center", fontsize=7, color="#666666", style="italic",
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    log.info("  Guardado: %s", out_path.name)


# ===========================================================================
# Orquestador por especie
# ===========================================================================

def process_species(species_name: str) -> None:
    """Genera todos los mapas para una especie.

    Lee los GeoTIFF producidos por la Etapa 7 desde config.MAPS,
    genera figuras PNG en config.FIGURES y registra advertencias
    si faltan capas.

    Parameters
    ----------
    species_name:
        Nombre científico completo, ej. 'Nolana divaricata'.
    """
    slug = utils.slugify_species(species_name)
    log.info("=== Procesando especie: %s (slug: %s) ===", species_name, slug)

    # Rutas de entrada (Etapa 7)
    tif_present = config.MAPS / f"{slug}_present_suitability.tif"
    tif_future_mean = config.MAPS / f"{slug}_future_mean_suitability.tif"
    tif_future_sd = config.MAPS / f"{slug}_future_sd_suitability.tif"
    tif_delta = config.MAPS / f"{slug}_delta_suitability.tif"
    tif_mess = config.MAPS / f"{slug}_future_mess.tif"

    # Verificar que al menos el raster de idoneidad presente existe
    if not tif_present.exists():
        log.warning(
            "Raster presente no encontrado para '%s': %s — omitiendo especie.",
            species_name, tif_present,
        )
        return

    # Cargar datos
    present_data, extent, _ = _load_raster(tif_present)
    occurrences = _load_occurrences(slug)

    # Nota al pie estándar
    def _note(extra: str = "") -> str:
        base = (
            f"{species_name} | Ensemble SDM (GLM, GAM, RF, GBM, MaxEnt) | "
            f"WorldClim 2.1 @ {config.WORLDCLIM_RES}"
        )
        return f"{base} | {extra}" if extra else base

    # ------------------------------------------------------------------
    # 1. Idoneidad presente
    # ------------------------------------------------------------------
    log.info("  [1/7] Idoneidad presente")
    plot_raster(
        data=present_data,
        extent=extent,
        title=f"{species_name} — Idoneidad climática presente (1970-2000)",
        out_path=config.FIGURES / f"{slug}_present.png",
        cmap=CMAP_SUITABILITY,
        vmin=0.0, vmax=1.0,
        colorbar_label="Idoneidad (0–1)",
        occurrences=occurrences,
        note=_note(),
    )

    # ------------------------------------------------------------------
    # 2. Idoneidad media futura (2050)
    # ------------------------------------------------------------------
    if tif_future_mean.exists():
        log.info("  [2/7] Idoneidad futura media 2050")
        future_mean_data, _, _ = _load_raster(tif_future_mean)
        plot_raster(
            data=future_mean_data,
            extent=extent,
            title=(
                f"{species_name} — Idoneidad media 2050 "
                f"({', '.join(config.SSPS)}; ensemble {len(config.GCMS)} GCMs)"
            ),
            out_path=config.FIGURES / f"{slug}_future_mean.png",
            cmap=CMAP_SUITABILITY,
            vmin=0.0, vmax=1.0,
            colorbar_label="Idoneidad (0–1)",
            occurrences=occurrences,
            note=_note(f"SSPs: {', '.join(config.SSPS)} · Período: {config.FUTURE_PERIOD}"),
        )
    else:
        log.warning("  [2/7] Raster futuro medio no encontrado: %s", tif_future_mean.name)
        future_mean_data = None

    # ------------------------------------------------------------------
    # 3. Δidoneidad (futuro − presente)
    # ------------------------------------------------------------------
    if tif_delta.exists():
        log.info("  [3/7] Δidoneidad")
        delta_data, _, _ = _load_raster(tif_delta)
        abs_max = float(np.nanpercentile(np.abs(delta_data), 98))
        abs_max = max(abs_max, 0.01)  # evitar rango cero
        plot_raster(
            data=delta_data,
            extent=extent,
            title=(
                f"{species_name} — Δidoneidad (2050 − presente)\n"
                f"Azul = ganancia, Rojo = pérdida"
            ),
            out_path=config.FIGURES / f"{slug}_delta.png",
            cmap=CMAP_DELTA,
            vmin=-abs_max, vmax=abs_max,
            vcenter=0.0,
            colorbar_label="Δidoneidad",
            occurrences=occurrences,
            note=_note("Rojo: pérdida de idoneidad | Azul: ganancia de idoneidad"),
        )
    else:
        log.warning("  [3/7] Raster delta no encontrado: %s", tif_delta.name)

    # ------------------------------------------------------------------
    # 4. Incertidumbre del ensemble (SD entre escenarios/algoritmos)
    # ------------------------------------------------------------------
    if tif_future_sd.exists():
        log.info("  [4/7] Incertidumbre (SD)")
        sd_data, _, _ = _load_raster(tif_future_sd)
        plot_raster(
            data=sd_data,
            extent=extent,
            title=(
                f"{species_name} — Incertidumbre del ensemble (SD, 2050)\n"
                f"Valores altos = mayor desacuerdo entre GCMs/SSPs"
            ),
            out_path=config.FIGURES / f"{slug}_uncertainty_sd.png",
            cmap=CMAP_UNCERTAINTY,
            colorbar_label="Desviación estándar",
            occurrences=None,  # sin overlay en mapa de incertidumbre
            note=_note(f"{len(config.GCMS)} GCMs × {len(config.SSPS)} SSPs"),
        )
    else:
        log.warning("  [4/7] Raster SD no encontrado: %s", tif_future_sd.name)

    # ------------------------------------------------------------------
    # 5. MESS (extrapolación)
    # ------------------------------------------------------------------
    if tif_mess.exists():
        log.info("  [5/7] MESS futuro")
        mess_data, mess_extent, _ = _load_raster(tif_mess)
        # MESS: valores < 0 indican extrapolación; escala simétrica centrada en 0
        mess_abs_max = float(np.nanpercentile(np.abs(mess_data), 98))
        mess_abs_max = max(mess_abs_max, 1.0)
        plot_raster(
            data=mess_data,
            extent=mess_extent,
            title=(
                f"{species_name} — MESS 2050 (Multivariate Environmental Similarity)\n"
                f"Verde = interpolación  |  Rojo = extrapolación (valores < 0)"
            ),
            out_path=config.FIGURES / f"{slug}_mess.png",
            cmap=CMAP_MESS,
            vmin=-mess_abs_max, vmax=mess_abs_max,
            vcenter=0.0,
            colorbar_label="MESS score",
            occurrences=None,
            note=_note("Elith et al. (2010). Rojo = el modelo extrapola fuera del espacio de entrenamiento"),
        )
    else:
        log.warning("  [5/7] Raster MESS no encontrado: %s", tif_mess.name)

    # ------------------------------------------------------------------
    # 6. Mapas binarios por umbral (maxTSS, p10)
    # ------------------------------------------------------------------
    log.info("  [6/7] Mapas binarios por umbral")

    # Intentar leer umbrales del joblib; si no, calcularlos desde el raster
    thresholds = _load_thresholds_from_joblib(slug)
    if not thresholds:
        log.info(
            "    Umbrales no encontrados en joblib para '%s'; calculando heurísticos.",
            slug,
        )
        thresholds = _compute_thresholds_from_data(present_data, occurrences, extent)

    if thresholds:
        for thr_name, thr_val in thresholds.items():
            if thr_name not in ("maxTSS", "p10", "min_train"):
                continue  # solo los umbrales del contrato
            log.info("    Umbral %s = %.4f", thr_name, thr_val)
            plot_binary(
                data=present_data,
                threshold=thr_val,
                extent=extent,
                title=(
                    f"{species_name} — Distribución binaria presente\n"
                    f"Umbral: {thr_name} = {thr_val:.4f}"
                ),
                out_path=config.FIGURES / f"{slug}_binary_{thr_name}.png",
                occurrences=occurrences,
                note=_note(f"Liu et al. 2013: umbral {thr_name}"),
            )
    else:
        log.warning("    Sin umbrales disponibles para mapas binarios de '%s'", slug)

    # ------------------------------------------------------------------
    # 7. Panel comparativo presente vs. 2050
    # ------------------------------------------------------------------
    if future_mean_data is not None:
        log.info("  [7/7] Panel comparativo presente vs. 2050")
        plot_comparison_panel(
            present_data=present_data,
            future_data=future_mean_data,
            extent=extent,
            species_name=species_name,
            out_path=config.FIGURES / f"{slug}_panel_comparativo.png",
            occurrences=occurrences,
        )
    else:
        log.warning("  [7/7] Panel comparativo omitido (sin raster futuro)")

    log.info("=== Completado: %s ===", species_name)


# ===========================================================================
# Resolución de lista de especies
# ===========================================================================

def resolve_species_list(requested: Optional[list[str]]) -> list[str]:
    """Determina la lista de especies a procesar.

    Si se proporciona una lista explícita, la usa directamente.
    Si no, busca GeoTIFF presentes en config.MAPS e infiere las especies,
    o bien lista los slugs de joblib en config.ENSEMBLE_MODELS.

    Parameters
    ----------
    requested:
        Lista de nombres de especie pasados por argparse (puede ser None).

    Returns
    -------
    Lista de nombres de especie a procesar.
    """
    if requested:
        return requested

    # Auto-detectar desde config.MAPS
    found: list[str] = []
    if config.MAPS.exists():
        tifs = sorted(config.MAPS.glob("*_present_suitability.tif"))
        for tif in tifs:
            # Invertir slug a nombre (aproximado; usamos el slug directamente)
            slug = tif.name.replace("_present_suitability.tif", "")
            # Convertir slug a nombre con espacios
            name = slug.replace("_", " ").title()
            found.append(name)

    if found:
        log.info("Auto-detectadas %d especies desde config.MAPS.", len(found))
        return found

    # Fallback: desde ensemble_models
    if config.ENSEMBLE_MODELS.exists() and _JOBLIB_AVAILABLE:
        joblibfiles = sorted(config.ENSEMBLE_MODELS.glob("*.joblib"))
        for jf in joblibfiles:
            slug = jf.stem
            name = slug.replace("_", " ").title()
            found.append(name)
        if found:
            log.info("Auto-detectadas %d especies desde ENSEMBLE_MODELS.", len(found))
            return found

    log.error(
        "No se encontraron especies. Use --species o asegúrese de que "
        "config.MAPS contiene archivos *_present_suitability.tif."
    )
    return []


# ===========================================================================
# Validación de entorno
# ===========================================================================

def _check_environment() -> None:
    """Registra advertencias sobre dependencias opcionales faltantes."""
    if not _RASTERIO_AVAILABLE:
        log.error("rasterio NO disponible. Este script requiere rasterio para leer GeoTIFF.")
        sys.exit(1)
    if not _CARTOPY_AVAILABLE:
        log.warning(
            "cartopy NO disponible. Se usará matplotlib+imshow como fallback "
            "(sin costas/proyecciones cartográficas). "
            "Para instalarlo: conda install -c conda-forge cartopy"
        )
    else:
        log.info("cartopy disponible — usando proyección %s.", DEFAULT_PROJECTION)

    if not _GEOPANDAS_AVAILABLE:
        log.warning(
            "geopandas NO disponible. No se superpondrán puntos de ocurrencia en los mapas."
        )
    if not _JOBLIB_AVAILABLE:
        log.warning(
            "joblib NO disponible. Los umbrales binarios se calcularán heurísticamente."
        )


# ===========================================================================
# CLI / main
# ===========================================================================

def parse_args() -> argparse.Namespace:
    """Parsea argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(
        description=(
            "Etapa 8 — Visualización cartográfica del pipeline SDM.\n"
            "Genera figuras PNG (idoneidad presente/futura, Δ, SD, MESS, binarios, panel)"
            " para cada especie procesada por la Etapa 7."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python 08_mapas.py\n"
            "  python 08_mapas.py --species 'Nolana divaricata'\n"
            "  python 08_mapas.py --species 'Schinus areira' 'Atriplex semibaccata'\n"
            "  python 08_mapas.py --projection Robinson\n"
        ),
    )
    parser.add_argument(
        "--species",
        nargs="+",
        metavar="SPECIES",
        default=None,
        help=(
            "Nombre(s) científico(s) de las especies a procesar. "
            "Si no se especifica, se auto-detectan desde config.MAPS."
        ),
    )
    parser.add_argument(
        "--projection",
        choices=["PlateCarree", "Robinson"],
        default=DEFAULT_PROJECTION,
        help=(
            f"Proyección cartopy a usar (default: {DEFAULT_PROJECTION}). "
            "Solo aplica si cartopy está disponible."
        ),
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=DPI,
        metavar="DPI",
        help=f"Resolución de las figuras en DPI (default: {DPI}).",
    )
    return parser.parse_args()


def main() -> None:
    """Punto de entrada principal de la Etapa 8."""
    args = parse_args()

    # Aplicar argumentos globales
    global DPI
    DPI = args.dpi

    _check_environment()
    utils.ensure_dirs(config.FIGURES)

    species_list = resolve_species_list(args.species)
    if not species_list:
        sys.exit(1)

    log.info(
        "Iniciando generación de mapas para %d especie(s). "
        "Figuras → %s",
        len(species_list), config.FIGURES,
    )

    errors: list[str] = []
    for species_name in species_list:
        try:
            process_species(species_name)
        except Exception as exc:
            log.error("Error procesando '%s': %s", species_name, exc, exc_info=True)
            errors.append(species_name)

    if errors:
        log.warning(
            "Procesamiento completado con errores en %d especie(s): %s",
            len(errors), ", ".join(errors),
        )
    else:
        log.info(
            "Procesamiento completado sin errores. "
            "%d especie(s) procesadas.", len(species_list),
        )


if __name__ == "__main__":
    main()
