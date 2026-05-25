"""
01_limpieza.py — Etapa 1: Limpieza de ocurrencias GBIF.

Implementa el pipeline de limpieza descrito en docs/proyecto_sdm.md §"Etapa 1":

    1. Eliminar duplicados exactos (especie + lat/lon).
    2. Filtrar incertidumbre > config.MAX_COORD_UNCERTAINTY_M
       (registros SIN incertidumbre se conservan).
    3. Detectar y eliminar centroides administrativos (Natural Earth admin-0
       y admin-1) con tolerancia config.CENTROID_TOLERANCE_KM.
    4. Eliminar coordenadas en océano (overlay con land mask Natural Earth).
    5. Eliminar (0, 0), decimales sospechosamente exactos y coordenadas
       geográficamente imposibles.
    6. Thinning espacial: 1 punto por celda de la grilla 2.5 arc-min
       (config.WORLDCLIM_RES) por especie.
    7. Asignar grupo A/B/C (config.classify_species). Grupo C se marca pero
       NO se descarta (se excluye en etapas de modelado).
    8. Reportar por log: registros iniciales/finales por paso y por especie.

Salida:
    GeoDataFrame en EPSG:4326 → config.OCCURRENCES_CLEAN (.gpkg)
    Columnas mínimas: especie, grupo, lon, lat, ano, pais, geometry.

Uso:
    python 01_limpieza.py
    python 01_limpieza.py --species "Nolana divaricata" "Eulychnia acida"
    python 01_limpieza.py --loglevel DEBUG
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Geod
from shapely.geometry import Point

# ---------------------------------------------------------------------------
# El directorio scripts/ debe estar en sys.path para que los imports relativos
# funcionen tanto al invocar directamente como desde la raíz del proyecto.
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import config
import utils

logger = utils.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constantes derivadas de config
# ---------------------------------------------------------------------------
# 2.5 arc-min = 2.5 / 60 grados = 1/24 grado
_CELL_SIZE_DEG: float = 2.5 / 60.0  # ~0.04167 °

# Tolerancia centroide en metros (pyproj usa metros para geodésicas)
_CENTROID_TOL_M: float = config.CENTROID_TOLERANCE_KM * 1_000.0

# Patrón sospechoso: parte decimal exactamente .0 (coordenada truncada)
_DECIMAL_ZERO_THRESH: float = 1e-9

# URLs Natural Earth (solo se descargan si no se puede leer localmente)
_NE_LAND_URL = (
    "https://naturalearth.s3.amazonaws.com/110m_physical/ne_110m_land.zip"
)
_NE_ADMIN0_URL = (
    "https://naturalearth.s3.amazonaws.com/110m_cultural/ne_110m_admin_0_countries.zip"
)
_NE_ADMIN1_URL = (
    "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_1_states_provinces.zip"
)

# Caché local para las capas Natural Earth (dentro de data/raw para reproducibilidad)
_NE_CACHE_DIR: Path = config.RAW / "natural_earth"

# ---------------------------------------------------------------------------
# Helpers: carga de capas Natural Earth con descarga bajo demanda
# ---------------------------------------------------------------------------


def _load_ne_layer(url: str, cache_name: str) -> Optional[gpd.GeoDataFrame]:
    """Carga una capa Natural Earth desde caché local o la descarga.

    Parámetros
    ----------
    url:
        URL del archivo .zip en el servidor de Natural Earth.
    cache_name:
        Nombre de archivo local (sin extensión) para caché en _NE_CACHE_DIR.

    Devuelve
    -------
    GeoDataFrame o None si no hay caché ni acceso a red.
    """
    cache_path = _NE_CACHE_DIR / f"{cache_name}.gpkg"

    # 1. Intentar caché local
    if cache_path.exists():
        logger.debug("Cargando capa NE desde caché: %s", cache_path)
        try:
            return gpd.read_file(cache_path)
        except Exception as exc:
            logger.warning("Caché dañada (%s), intentando descarga: %s", cache_path, exc)

    # 2. Intentar descarga
    logger.info("Descargando capa Natural Earth: %s", url)
    try:
        import requests  # solo se usa aquí; no es dep dura del módulo

        resp = requests.get(url, timeout=60)
        resp.raise_for_status()

        import io
        import zipfile

        utils.ensure_dirs(_NE_CACHE_DIR)
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            # geopandas puede leer el zip directamente vía /vsiz/
            pass  # solo verificamos que sea zip válido

        gdf = gpd.read_file(f"/vsizip/vsicurl/{url}")
        gdf.to_file(cache_path, driver="GPKG")
        logger.info("Capa guardada en caché: %s", cache_path)
        return gdf

    except Exception as exc:
        logger.warning(
            "No se pudo descargar la capa Natural Earth '%s': %s. "
            "Descárguela manualmente desde %s y guárdela en %s.",
            cache_name, exc, url, _NE_CACHE_DIR,
        )
        return None


def _load_land_mask() -> Optional[gpd.GeoDataFrame]:
    """Carga el polígono de tierra Natural Earth 110m."""
    return _load_ne_layer(_NE_LAND_URL, "ne_110m_land")


def _load_admin0_centroids() -> Optional[gpd.GeoDataFrame]:
    """Carga países Natural Earth y extrae sus centroides."""
    gdf = _load_ne_layer(_NE_ADMIN0_URL, "ne_110m_admin0_countries")
    if gdf is None:
        return None
    gdf = gdf[["geometry"]].copy()
    gdf["centroid_geom"] = gdf.geometry.centroid
    return gdf


def _load_admin1_centroids() -> Optional[gpd.GeoDataFrame]:
    """Carga provincias/estados Natural Earth 10m y extrae sus centroides.

    Nota: la capa 10m es detallada (~4 MB zip); si la red es lenta puede tardar.
    """
    gdf = _load_ne_layer(_NE_ADMIN1_URL, "ne_10m_admin1_states_provinces")
    if gdf is None:
        return None
    gdf = gdf[["geometry"]].copy()
    gdf["centroid_geom"] = gdf.geometry.centroid
    return gdf


# ---------------------------------------------------------------------------
# Paso 1 — Eliminar duplicados exactos
# ---------------------------------------------------------------------------


def eliminar_duplicados(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Elimina filas duplicadas por (especie, lat, lon).

    Conserva el primer registro encontrado.
    """
    n_antes = len(gdf)
    gdf = gdf.drop_duplicates(subset=["especie", "lat", "lon"], keep="first")
    n_despues = len(gdf)
    logger.info("Paso 1 | Duplicados eliminados: %d → %d (−%d)",
                n_antes, n_despues, n_antes - n_despues)
    return gdf.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Paso 2 — Filtrar incertidumbre excesiva
# ---------------------------------------------------------------------------


def filtrar_incertidumbre(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Descarta registros con incertidumbre_m > config.MAX_COORD_UNCERTAINTY_M.

    Registros sin incertidumbre (NaN) se conservan: son mayoría en GBIF y
    su ausencia de metadato no implica mala calidad.
    """
    n_antes = len(gdf)
    mascara_mala = (
        gdf["incertidumbre_m"].notna()
        & (gdf["incertidumbre_m"] > config.MAX_COORD_UNCERTAINTY_M)
    )
    gdf = gdf[~mascara_mala].copy()
    n_despues = len(gdf)
    logger.info(
        "Paso 2 | Incertidumbre > %d m eliminados: %d → %d (−%d)",
        config.MAX_COORD_UNCERTAINTY_M, n_antes, n_despues, n_antes - n_despues,
    )
    return gdf.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Paso 3 — Detectar y eliminar centroides administrativos
# ---------------------------------------------------------------------------


def _distancia_minima_m(lon: float, lat: float,
                         centroides_lon: np.ndarray,
                         centroides_lat: np.ndarray,
                         geod: Geod) -> float:
    """Distancia geodésica mínima (metros) entre un punto y un array de centroides."""
    if len(centroides_lon) == 0:
        return float("inf")
    lons_rep = np.full(len(centroides_lon), lon)
    lats_rep = np.full(len(centroides_lat), lat)
    _, _, dists = geod.inv(lons_rep, lats_rep, centroides_lon, centroides_lat)
    return float(np.nanmin(dists))


def filtrar_centroides_admin(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Elimina puntos que coinciden con centroides de país o región.

    Usa Natural Earth admin-0 (110m) y admin-1 (10m). Si alguna capa no está
    disponible, el paso se omite con advertencia pero no interrumpe el pipeline.

    Tolerancia: config.CENTROID_TOLERANCE_KM km.
    """
    n_antes = len(gdf)

    # Recopilar centroides de ambos niveles administrativos
    centroid_lons: list[float] = []
    centroid_lats: list[float] = []

    for nombre, loader in [("admin-0", _load_admin0_centroids),
                            ("admin-1", _load_admin1_centroids)]:
        capa = loader()
        if capa is None:
            logger.warning(
                "Paso 3 | Capa %s no disponible; se omite detección de centroides"
                " a ese nivel. Descargue manualmente las capas Natural Earth"
                " en %s para activar este filtro.",
                nombre, _NE_CACHE_DIR,
            )
            continue
        pts = capa["centroid_geom"]
        centroid_lons.extend(pts.x.tolist())
        centroid_lats.extend(pts.y.tolist())
        logger.debug("Paso 3 | %d centroides cargados desde %s", len(pts), nombre)

    if not centroid_lons:
        logger.warning("Paso 3 | Sin centroides disponibles; paso omitido.")
        return gdf

    arr_lon = np.array(centroid_lons)
    arr_lat = np.array(centroid_lats)
    geod = Geod(ellps="WGS84")

    # Evaluar cada punto
    es_centroide = np.zeros(len(gdf), dtype=bool)
    for i, row in enumerate(gdf.itertuples(index=False)):
        dist = _distancia_minima_m(row.lon, row.lat, arr_lon, arr_lat, geod)
        if dist <= _CENTROID_TOL_M:
            es_centroide[i] = True

    gdf = gdf[~es_centroide].copy()
    n_despues = len(gdf)
    logger.info(
        "Paso 3 | Centroides admin eliminados: %d → %d (−%d)",
        n_antes, n_despues, n_antes - n_despues,
    )
    return gdf.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Paso 4 — Eliminar coordenadas en océano
# ---------------------------------------------------------------------------


def filtrar_oceano(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Descarta puntos que no intersectan con la land mask Natural Earth.

    Si la capa no está disponible el paso se omite con advertencia.
    """
    n_antes = len(gdf)
    land = _load_land_mask()
    if land is None:
        logger.warning(
            "Paso 4 | Land mask no disponible; paso de filtrado oceánico omitido."
        )
        return gdf

    # Asegurar mismo CRS
    land = land.to_crs(config.CRS_GEO)

    # Unión de polígonos de tierra para hacer sjoin eficiente
    land_union = land.dissolve()

    puntos_gdf = gdf[["geometry"]].copy()
    puntos_gdf.crs = config.CRS_GEO  # ya debe estar fijado; por si acaso

    en_tierra = gpd.sjoin(
        puntos_gdf.reset_index(),
        land_union[["geometry"]],
        how="left",
        predicate="within",
    )
    idx_tierra = en_tierra[en_tierra["index_right"].notna()]["index"].values

    gdf = gdf.loc[idx_tierra].copy()
    n_despues = len(gdf)
    logger.info(
        "Paso 4 | Coords en océano eliminadas: %d → %d (−%d)",
        n_antes, n_despues, n_antes - n_despues,
    )
    return gdf.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Paso 5 — Coordenadas sospechosas / inválidas
# ---------------------------------------------------------------------------


def _tiene_decimal_cero(valor: float) -> bool:
    """True si la parte decimal del número es exactamente 0."""
    parte_decimal = abs(valor) - math.floor(abs(valor))
    return parte_decimal < _DECIMAL_ZERO_THRESH


def filtrar_coords_sospechosas(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Elimina coordenadas geográficamente inválidas o estadísticamente sospechosas.

    Reglas aplicadas:
    - (lat, lon) = (0, 0) exacto: coordenada nula por defecto.
    - lat o lon fuera de rango válido (|lat| > 90, |lon| > 180).
    - lat y lon ambas con parte decimal == 0: registro truncado a entero.
    - lat NaN o lon NaN: no utilizables.
    """
    n_antes = len(gdf)

    mascara_nula = gdf["lat"].isna() | gdf["lon"].isna()

    mascara_cero = (
        (gdf["lat"].abs() < _DECIMAL_ZERO_THRESH)
        & (gdf["lon"].abs() < _DECIMAL_ZERO_THRESH)
    )

    mascara_rango = (gdf["lat"].abs() > 90) | (gdf["lon"].abs() > 180)

    # Ambas coordenadas truncadas a entero (decimal == 0)
    mascara_truncada = gdf.apply(
        lambda r: _tiene_decimal_cero(r["lat"]) and _tiene_decimal_cero(r["lon"]),
        axis=1,
    )

    mascara_mala = mascara_nula | mascara_cero | mascara_rango | mascara_truncada

    n_nula = mascara_nula.sum()
    n_cero = mascara_cero.sum()
    n_rango = mascara_rango.sum()
    n_trunc = (mascara_truncada & ~(mascara_nula | mascara_cero | mascara_rango)).sum()

    gdf = gdf[~mascara_mala].copy()
    n_despues = len(gdf)

    logger.info(
        "Paso 5 | Coords sospechosas eliminadas: %d → %d (−%d) "
        "[NaN=%d, (0,0)=%d, rango=%d, truncadas=%d]",
        n_antes, n_despues, n_antes - n_despues,
        n_nula, n_cero, n_rango, n_trunc,
    )
    return gdf.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Paso 6 — Thinning espacial (1 punto / celda 2.5 arc-min / especie)
# ---------------------------------------------------------------------------


def _asignar_celda(lat: pd.Series, lon: pd.Series) -> pd.Series:
    """Devuelve una clave de celda como string 'col_fila' para la grilla 2.5'."""
    col = np.floor(lon / _CELL_SIZE_DEG).astype(int)
    fila = np.floor(lat / _CELL_SIZE_DEG).astype(int)
    return pd.Series(
        [f"{c}_{f}" for c, f in zip(col, fila)], index=lat.index
    )


def thinning_espacial(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Retiene 1 punto por celda de la grilla 2.5 arc-min por especie.

    Implementación:
      - Se asigna a cada punto la celda de la grilla de 2.5' = 1/24 ° mediante
        floor division de lat/lon.
      - Dentro de cada (especie, celda) se conserva el primer registro
        (después del ordenamiento previo los registros están en orden original).
    """
    n_antes = len(gdf)
    gdf = gdf.copy()
    gdf["_celda"] = _asignar_celda(gdf["lat"], gdf["lon"])

    # Para reproducibilidad: dentro de cada celda, conservar el registro con
    # menor incertidumbre_m (NaN tratado como inf, por eso se conserva cualquiera
    # si todos son NaN).
    gdf["_inc_sort"] = gdf["incertidumbre_m"].fillna(np.inf)
    gdf = gdf.sort_values(["especie", "_celda", "_inc_sort"])
    gdf = gdf.drop_duplicates(subset=["especie", "_celda"], keep="first")
    gdf = gdf.drop(columns=["_celda", "_inc_sort"])

    n_despues = len(gdf)
    logger.info(
        "Paso 6 | Thinning 2.5' (1 pt/celda/especie): %d → %d (−%d)",
        n_antes, n_despues, n_antes - n_despues,
    )
    return gdf.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Paso 7 — Asignar grupo A/B/C
# ---------------------------------------------------------------------------


def asignar_grupos(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Añade la columna 'grupo' (A/B/C) a partir de conteos DESPUÉS del thinning.

    Importante: los conteos se calculan sobre el GeoDataFrame ya limpiado para
    que el grupo refleje los registros realmente útiles, no los crudos.
    Se emite un aviso por especie del grupo C.
    """
    conteos_limpios = gdf["especie"].value_counts().to_dict()
    grupos = config.classify_species(conteos_limpios)

    gdf = gdf.copy()
    gdf["grupo"] = gdf["especie"].map(grupos)

    # Registrar advertencias para grupo C
    especies_c = [sp for sp, g in grupos.items() if g == "C"]
    if especies_c:
        logger.warning(
            "Paso 7 | %d especie(s) Grupo C (<50 registros tras limpieza) — "
            "se conservan en el .gpkg pero NO se modelarán individualmente: %s",
            len(especies_c), ", ".join(sorted(especies_c)),
        )

    # Log resumen de grupos
    resumen = gdf.groupby("grupo")["especie"].nunique()
    for grp, n_sp in resumen.items():
        logger.info("Paso 7 | Grupo %s: %d especie(s)", grp, n_sp)

    return gdf.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Reporte por especie
# ---------------------------------------------------------------------------


def _reportar_por_especie(inicial: pd.DataFrame,
                           final: gpd.GeoDataFrame) -> None:
    """Imprime en el log la tabla de registros iniciales vs. finales por especie."""
    conteo_ini = inicial["especie"].value_counts().rename("inicial")
    conteo_fin = final["especie"].value_counts().rename("final")
    tabla = pd.concat([conteo_ini, conteo_fin], axis=1).fillna(0).astype(int)
    tabla["retenidos_%"] = (tabla["final"] / tabla["inicial"] * 100).round(1)
    tabla = tabla.sort_values("inicial", ascending=False)

    logger.info("=== Resumen por especie ===")
    logger.info("%-35s %8s %8s %10s", "Especie", "Inicial", "Final", "Ret.%")
    for sp, row in tabla.iterrows():
        logger.info("%-35s %8d %8d %10.1f", sp, row["inicial"], row["final"],
                    row["retenidos_%"])
    logger.info("TOTAL: %d → %d registros (%.1f %% retenidos)",
                tabla["inicial"].sum(), tabla["final"].sum(),
                tabla["final"].sum() / tabla["inicial"].sum() * 100
                if tabla["inicial"].sum() > 0 else 0.0)


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------


def construir_geodataframe(df: pd.DataFrame) -> gpd.GeoDataFrame:
    """Convierte el DataFrame cargado en GeoDataFrame EPSG:4326.

    Se eliminan antes filas con lat/lon nulas para evitar errores en
    la construcción de geometrías.
    """
    df = df.dropna(subset=["lat", "lon"]).copy()
    geometrias = [Point(lon, lat) for lon, lat in zip(df["lon"], df["lat"])]
    gdf = gpd.GeoDataFrame(df, geometry=geometrias, crs=config.CRS_GEO)
    return gdf


def main() -> None:
    """Punto de entrada del script de limpieza."""
    # ------------------------------------------------------------------
    # Argumentos de línea de comandos
    # ------------------------------------------------------------------
    parser = argparse.ArgumentParser(
        description="Etapa 1 — Limpieza de ocurrencias GBIF para SDM global.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--species",
        nargs="+",
        metavar="ESPECIE",
        help="Procesar solo estas especie(s). Por defecto: todas.",
    )
    parser.add_argument(
        "--loglevel",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Nivel de log (defecto: INFO).",
    )
    args = parser.parse_args()

    # Ajustar nivel de log
    logger.setLevel(args.loglevel)

    # ------------------------------------------------------------------
    # Preparar directorios de salida
    # ------------------------------------------------------------------
    utils.ensure_dirs(config.PROCESSED, _NE_CACHE_DIR)

    # ------------------------------------------------------------------
    # Carga de datos crudos
    # ------------------------------------------------------------------
    logger.info("Cargando ocurrencias crudas desde: %s", config.OCCURRENCES_XLSX)
    df_crudo = utils.load_raw_occurrences()
    logger.info("Registros crudos cargados: %d", len(df_crudo))

    # Filtro opcional por especie
    if args.species:
        especies_filtro = set(args.species)
        df_crudo = df_crudo[df_crudo["especie"].isin(especies_filtro)].copy()
        logger.info("Filtro --species: %d registros para %d especie(s)",
                    len(df_crudo), len(especies_filtro))
        if df_crudo.empty:
            logger.error("Ningún registro coincide con las especies indicadas. "
                         "Verificar nombres exactos.")
            sys.exit(1)

    # Guardar snapshot inicial para el reporte final
    df_inicial = df_crudo.copy()

    # ------------------------------------------------------------------
    # Convertir a GeoDataFrame (con eliminación de lat/lon nulas inicial)
    # ------------------------------------------------------------------
    gdf = construir_geodataframe(df_crudo)
    logger.info("GeoDataFrame inicial: %d registros (coords no nulas)", len(gdf))

    # ------------------------------------------------------------------
    # Pipeline de limpieza
    # ------------------------------------------------------------------
    gdf = eliminar_duplicados(gdf)
    gdf = filtrar_incertidumbre(gdf)
    gdf = filtrar_coords_sospechosas(gdf)  # paso 5 antes que pasos con red
    gdf = filtrar_centroides_admin(gdf)     # paso 3
    gdf = filtrar_oceano(gdf)               # paso 4
    gdf = thinning_espacial(gdf)            # paso 6
    gdf = asignar_grupos(gdf)               # paso 7

    # ------------------------------------------------------------------
    # Reporte final
    # ------------------------------------------------------------------
    logger.info("=== Limpieza completada: %d registros finales ===", len(gdf))
    _reportar_por_especie(df_inicial, gdf)

    # ------------------------------------------------------------------
    # Seleccionar y ordenar columnas de salida (mínimas + complementarias)
    # ------------------------------------------------------------------
    columnas_minimas = ["especie", "grupo", "lon", "lat", "ano", "pais", "geometry"]
    columnas_extra = [
        c for c in [
            "nombre_cientifico", "incertidumbre_m", "region", "localidad",
            "fecha", "tipo_registro", "institucion", "dataset", "catalogo", "gbif_id",
        ]
        if c in gdf.columns
    ]
    gdf_salida = gdf[columnas_minimas + columnas_extra].copy()

    # ------------------------------------------------------------------
    # Guardar GeoPackage
    # ------------------------------------------------------------------
    logger.info("Guardando resultado en: %s", config.OCCURRENCES_CLEAN)
    gdf_salida.to_file(config.OCCURRENCES_CLEAN, driver="GPKG", layer="ocurrencias")
    logger.info("Archivo guardado exitosamente.")


if __name__ == "__main__":
    main()
