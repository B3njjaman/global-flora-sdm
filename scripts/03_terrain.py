"""
03_terrain.py — Derivación de variables topográficas y alineación de predictores.

Etapa 2b del pipeline SDM global-flora-sdm.

Lee elevation.tif desde config.WORLDCLIM_PRESENT, deriva pendiente (slope) y
aspecto (aspect) con xarray-spatial (preferido) o richdem (alternativa), descompone
el aspecto circular en northness = cos(aspect) y eastness = sin(aspect), alinea las
10 bioclim seleccionadas al mismo grid/CRS/extent, aplica la máscara de tierra y
escribe los 14 predictores finales en config.RASTERS_ALIGNED como {var}.tif.

Uso
---
    python 03_terrain.py [--adjust-northness] [--dry-run]

Argumentos opcionales
---------------------
--adjust-northness : bool, default False
    Si se activa, multiplica northness por sign(latitud) de cada celda para
    normalizar el efecto del hemisferio: en el hemisferio sur las laderas cálidas
    apuntan al norte (northness positiva = cálida); en el norte al sur (northness
    negativa = cálida). El ajuste produce northness * sign(lat), de modo que
    valores positivos siempre indican "cara cálida" independientemente del
    hemisferio. Por defecto se exporta northness cruda para no asumir nada sobre
    la biología de las especies (un modelo global puede aprender la interacción
    implícitamente si se incluye latitud absoluta como predictor adicional).
--dry-run : bool, default False
    Registra el plan y verifica entradas sin escribir salidas.

Referencia metodológica: docs/proyecto_sdm.md § "Topográficas"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import rioxarray  # noqa: F401  — activa el accessor .rio en xarray
import xarray as xr
from rasterio.enums import Resampling

import config
import utils

logger = utils.get_logger("03_terrain")

# ---------------------------------------------------------------------------
# Constantes internas
# ---------------------------------------------------------------------------
_ELEV_FILE = "elevation.tif"
_MASK_FILE = "land_mask.tif"
_NODATA_OUT = np.float32(np.nan)  # todas las salidas usan NaN como nodata


# ---------------------------------------------------------------------------
# Funciones de I/O
# ---------------------------------------------------------------------------

def _load_raster(path: Path, name: str) -> xr.DataArray:
    """Carga un GeoTIFF como DataArray rioxarray con nodata → NaN.

    Parameters
    ----------
    path:
        Ruta al archivo GeoTIFF.
    name:
        Nombre asignado al DataArray (campo .name).

    Returns
    -------
    xr.DataArray con dtype float32, nodata enmascarado como NaN.
    """
    da = rioxarray.open_rasterio(path, masked=True).squeeze("band", drop=True)
    da = da.astype(np.float32)
    da.name = name
    return da


def _write_raster(da: xr.DataArray, path: Path) -> None:
    """Escribe un DataArray como GeoTIFF float32 con compresión LZW.

    Parameters
    ----------
    da:
        DataArray con CRS asignado mediante da.rio.write_crs y coordenadas
        espaciales 'x'/'y'.
    path:
        Ruta de destino (el directorio padre debe existir).
    """
    da_out = da.astype(np.float32)
    da_out.rio.write_nodata(np.float32(np.nan), inplace=True, encoded=False)
    da_out.rio.to_raster(
        str(path),
        dtype="float32",
        compress="lz77",  # LZW en rasterio
        driver="GTiff",
    )


# ---------------------------------------------------------------------------
# Cálculo de terreno
# ---------------------------------------------------------------------------

def _derive_terrain_xarray_spatial(
    elevation: xr.DataArray,
) -> tuple[xr.DataArray, xr.DataArray]:
    """Deriva pendiente (grados) y aspecto (grados) con xarray-spatial.

    xarray-spatial implementa el algoritmo de Horn (1981) para slope/aspect,
    adecuado para grillas de lat/lon en WGS-84 a resolución media (~5 km).

    Parameters
    ----------
    elevation:
        DataArray de elevación en metros, CRS EPSG:4326.

    Returns
    -------
    slope_deg : xr.DataArray
        Pendiente en grados (0–90), dtype float32. Los bordes son NaN por
        la ventana 3×3 del gradiente.
    aspect_deg : xr.DataArray
        Aspecto en grados (0–360, norte = 0, sentido horario), dtype float32.
        Áreas planas (slope ≈ 0) devuelven aspect = −1 en xarray-spatial;
        se reemplazan por NaN para evitar artefactos en sin/cos.
    """
    import xrspatial  # import diferido para que el fallback funcione
    from xrspatial import slope as xs_slope, aspect as xs_aspect

    slope_deg = xs_slope(elevation).astype(np.float32)
    slope_deg.name = "slope"

    aspect_deg = xs_aspect(elevation).astype(np.float32)
    aspect_deg.name = "aspect"

    # xarray-spatial devuelve -1 para terreno plano; convertir a NaN
    aspect_deg = aspect_deg.where(aspect_deg >= 0, other=np.nan)

    logger.info("Terreno derivado con xarray-spatial (algoritmo Horn 1981).")
    return slope_deg, aspect_deg


def _derive_terrain_richdem(
    elevation: xr.DataArray,
) -> tuple[xr.DataArray, xr.DataArray]:
    """Deriva pendiente y aspecto con richdem como alternativa a xarray-spatial.

    richdem computa slope y aspecto usando el método de Horn y maneja bordes
    mejor en ciertos casos. Se usa solo si xarray-spatial no está disponible.

    Parameters
    ----------
    elevation:
        DataArray de elevación en metros, CRS EPSG:4326.

    Returns
    -------
    slope_deg, aspect_deg : xr.DataArray
        Pendiente (grados) y aspecto (grados 0–360).
    """
    import richdem as rd

    # Extraer array numpy con nodata codificado
    elev_np = elevation.values.copy()
    nodata_val = -9999.0
    elev_np = np.where(np.isnan(elev_np), nodata_val, elev_np).astype(np.float64)

    rda = rd.rdarray(elev_np, no_data=nodata_val)
    rda.geotransform = (
        float(elevation.x.values[0]),               # xmin
        float(elevation.x.diff("x").values[0]),     # pixel width
        0.0,
        float(elevation.y.values[0]),               # ymax (o ymin según orientación)
        0.0,
        float(elevation.y.diff("y").values[0]),     # pixel height (negativo si N→S)
    )
    rda.projection = config.CRS_GEO

    slope_np = rd.TerrainAttribute(rda, attrib="slope_degrees")
    aspect_np = rd.TerrainAttribute(rda, attrib="aspect")

    def _wrap_as_da(arr: np.ndarray, name: str) -> xr.DataArray:
        result = np.where(arr == nodata_val, np.nan, arr).astype(np.float32)
        da_new = elevation.copy(data=result)
        da_new.name = name
        return da_new

    slope_deg = _wrap_as_da(np.array(slope_np), "slope")
    aspect_deg = _wrap_as_da(np.array(aspect_np), "aspect")

    # richdem puede devolver -1 para terreno plano; convertir a NaN
    aspect_deg = aspect_deg.where(aspect_deg >= 0, other=np.nan)

    logger.info("Terreno derivado con richdem (fallback).")
    return slope_deg, aspect_deg


def derive_terrain(
    elevation: xr.DataArray,
) -> tuple[xr.DataArray, xr.DataArray]:
    """Punto de entrada para derivación de terreno: intenta xarray-spatial, luego richdem.

    Parameters
    ----------
    elevation:
        DataArray de elevación en metros (EPSG:4326).

    Returns
    -------
    slope_deg, aspect_deg : xr.DataArray
        Pendiente y aspecto en grados.

    Raises
    ------
    ImportError
        Si ninguna de las dos librerías (xarray-spatial, richdem) está disponible.
    """
    try:
        return _derive_terrain_xarray_spatial(elevation)
    except ImportError:
        logger.warning(
            "xarray-spatial no está disponible; intentando richdem como alternativa."
        )

    try:
        return _derive_terrain_richdem(elevation)
    except ImportError as exc:
        raise ImportError(
            "Se necesita xarray-spatial o richdem para derivar terreno. "
            "Instalar con: pip install xarray-spatial  o  pip install richdem"
        ) from exc


# ---------------------------------------------------------------------------
# Descomposición del aspecto en northness / eastness
# ---------------------------------------------------------------------------

def compute_northness_eastness(
    aspect_deg: xr.DataArray,
    adjust_by_hemisphere: bool = False,
) -> tuple[xr.DataArray, xr.DataArray]:
    """Descompone el aspecto circular en dos variables continuas.

    El aspecto expresado en grados (0–360°) es circular: 1° y 359° son casi
    idénticos pero numéricamente opuestos. Convertirlo directamente a un modelo
    lineal produce una discontinuidad artificial que sesga las predicciones.
    La descomposición en seno y coseno elimina esa discontinuidad y codifica
    la geometría de exposición de manera monótona y diferenciable:

        northness = cos(aspect_rad)   → +1 = cara norte, −1 = cara sur
        eastness  = sin(aspect_rad)   → +1 = cara este,  −1 = cara oeste

    Referencia: Beers, T.C., Dress, P.E., Wensel, L.C. (1966); Evans (1980).

    Ajuste por hemisferio (--adjust-northness)
    ------------------------------------------
    En el hemisferio norte las laderas con más radiación solar miran al sur
    (northness ≈ −1); en el sur al norte (northness ≈ +1). Si el modelo SDM
    entrena a escala global y la especie tiene presencias en ambos hemisferios,
    northness cruda puede ser confusa: la misma cara fría en el norte tiene
    northness negativa y en el sur positiva.

    La corrección `northness_adj = northness * sign(latitud)` invierte northness
    en el hemisferio norte, de modo que valores positivos siempre representen
    "cara cálida" (mayor radiación solar) independientemente del hemisferio.
    NOTA: latitud == 0 produce sign(0) = 0 → NaN; se imputa como 0 (ecuador,
    la influencia del aspecto en la radiación es mínima allí).

    Por defecto (adjust_by_hemisphere=False) se exporta northness cruda, que
    es la convención directa del coseno y es perfectamente válida si los modelos
    pueden aprender la interacción con latitud por sí mismos o si se incluye
    latitud absoluta como predictor adicional.

    Parameters
    ----------
    aspect_deg:
        DataArray de aspecto en grados [0, 360). NaN en áreas planas.
    adjust_by_hemisphere:
        Si True, aplica northness *= sign(lat). Default False.

    Returns
    -------
    northness, eastness : xr.DataArray
        Ambas en rango [−1, 1], dtype float32. NaN donde aspect es NaN.
    """
    aspect_rad = np.deg2rad(aspect_deg)
    northness: xr.DataArray = np.cos(aspect_rad).astype(np.float32)
    eastness: xr.DataArray = np.sin(aspect_rad).astype(np.float32)

    northness.name = "northness"
    eastness.name = "eastness"

    if adjust_by_hemisphere:
        # Construir array de latitudes con la misma forma que northness
        lats = aspect_deg.y  # coordenadas y = latitud en EPSG:4326
        # broadcast lat a la forma completa (y, x)
        lat_2d = xr.ones_like(aspect_deg) * lats
        sign_lat = np.sign(lat_2d).astype(np.float32)
        # sign(0) = 0 → ecuador, reemplazar por 1 (no hay inversión)
        sign_lat = sign_lat.where(sign_lat != 0, other=np.float32(1.0))
        northness = (northness * sign_lat).astype(np.float32)
        northness.name = "northness"
        logger.info(
            "Northness ajustada por hemisferio: northness * sign(lat). "
            "Valores positivos = cara cálida en ambos hemisferios."
        )
    else:
        # Northness cruda: conveniente y sin suposiciones sobre biología de especies.
        # Para uso con modelos globales se recomienda incluir latitud absoluta
        # como predictor adicional si se detecta sesgo por hemisferio en validación.
        logger.info(
            "Northness cruda (cos(aspect)). "
            "Usar --adjust-northness para corrección por hemisferio."
        )

    return northness, eastness


# ---------------------------------------------------------------------------
# Alineación de capas bioclim
# ---------------------------------------------------------------------------

def align_bioclim_layers(
    reference: xr.DataArray,
    src_dir: Path,
    variables: list[str],
) -> dict[str, xr.DataArray]:
    """Carga y reproyecta las capas bioclim al grid de referencia.

    Usa rioxarray.reproject_match para garantizar idéntico extent, resolución,
    CRS y shape que la capa de referencia. Se utiliza remuestreo bilineal por
    defecto (adecuado para variables continuas). Si el CRS de origen ya coincide
    y la grilla es la misma, reproject_match actúa como un recorte/snap sin
    degradar calidad.

    Parameters
    ----------
    reference:
        DataArray de referencia que define el grid objetivo (normalmente bio1).
    src_dir:
        Directorio con los archivos bioN.tif.
    variables:
        Lista de nombres de variables bioclim (p. ej. ["bio1", "bio4", ...]).

    Returns
    -------
    dict[str, xr.DataArray]
        Mapa {nombre_variable: DataArray alineado}.
    """
    aligned: dict[str, xr.DataArray] = {}
    for var in variables:
        tif_path = src_dir / f"{var}.tif"
        if not tif_path.exists():
            raise FileNotFoundError(
                f"Capa bioclim no encontrada: {tif_path}. "
                "Asegúrate de haber ejecutado 02_capas_presente.py."
            )
        da = _load_raster(tif_path, name=var)
        da_aligned = da.rio.reproject_match(
            reference,
            resampling=Resampling.bilinear,
        )
        da_aligned.name = var
        aligned[var] = da_aligned.astype(np.float32)
        logger.info("  Alineado: %s → shape %s", var, da_aligned.shape)
    return aligned


# ---------------------------------------------------------------------------
# Aplicación de máscara de tierra
# ---------------------------------------------------------------------------

def apply_land_mask(
    layers: dict[str, xr.DataArray],
    mask: xr.DataArray,
) -> dict[str, xr.DataArray]:
    """Enmascara océano (NaN) en todas las capas para consistencia cross-variable.

    La máscara de tierra (land_mask.tif, producida por 02_capas_presente.py)
    contiene 1 en tierra y NaN (o 0) en océano. Se aplica a todas las capas de
    salida para garantizar que ningún píxel marino tenga valor válido en ninguna
    variable, evitando inconsistencias en la extracción de puntos background.

    Parameters
    ----------
    layers:
        Diccionario {nombre: DataArray} ya alineados al grid común.
    mask:
        DataArray de máscara de tierra (1 = tierra, otro/NaN = océano).

    Returns
    -------
    dict[str, xr.DataArray]
        Mismas capas con océano → NaN.
    """
    # Normalizar máscara: 1 en tierra, NaN en océano
    mask_bool = mask.where(mask == 1)  # 1 → 1, resto → NaN

    masked: dict[str, xr.DataArray] = {}
    for name, da in layers.items():
        da_masked = da.where(~np.isnan(mask_bool))
        da_masked.name = name
        masked[name] = da_masked.astype(np.float32)
    return masked


# ---------------------------------------------------------------------------
# Verificación de alineación
# ---------------------------------------------------------------------------

def verify_alignment(layers: dict[str, xr.DataArray]) -> None:
    """Verifica que todos los DataArrays compartan shape, transform y CRS.

    Parameters
    ----------
    layers:
        Diccionario {nombre: DataArray} de las capas finales.

    Raises
    ------
    ValueError
        Si alguna capa no coincide con la de referencia en shape, CRS o transform.
    """
    names = list(layers.keys())
    ref_name = names[0]
    ref = layers[ref_name]
    ref_shape = ref.shape
    ref_crs = ref.rio.crs
    ref_transform = ref.rio.transform()

    logger.info("Verificando alineación de %d capas (referencia: %s)...", len(layers), ref_name)
    errors: list[str] = []

    for name in names[1:]:
        da = layers[name]
        if da.shape != ref_shape:
            errors.append(
                f"{name}: shape {da.shape} != {ref_shape}"
            )
        if da.rio.crs != ref_crs:
            errors.append(
                f"{name}: CRS {da.rio.crs} != {ref_crs}"
            )
        if not np.allclose(
            list(da.rio.transform())[:6],
            list(ref_transform)[:6],
            atol=1e-8,
        ):
            errors.append(
                f"{name}: transform {da.rio.transform()} != {ref_transform}"
            )

    if errors:
        for e in errors:
            logger.error("  ALINEACION FALLIDA: %s", e)
        raise ValueError(
            f"Alineación fallida en {len(errors)} capa(s). Ver log para detalles."
        )

    logger.info(
        "OK — todas las capas alineadas: shape=%s, CRS=%s, res=%.6f°",
        ref_shape,
        ref_crs,
        abs(ref.rio.resolution()[0]),
    )


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------

def main() -> None:
    """Punto de entrada del script 03_terrain.py.

    Flujo:
    1. Cargar elevation.tif.
    2. Derivar slope y aspect (xarray-spatial o richdem).
    3. Descomponer aspect → northness, eastness.
    4. Cargar y alinear las 10 bioclim al grid de bio1.
    5. Alinear elevation, slope, northness, eastness al mismo grid.
    6. Aplicar land_mask a todas las capas.
    7. Verificar alineación.
    8. Escribir 14 GeoTIFF en config.RASTERS_ALIGNED.
    """
    parser = argparse.ArgumentParser(
        description="Deriva variables topográficas y alinea predictores presentes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--adjust-northness",
        action="store_true",
        default=False,
        help=(
            "Multiplica northness por sign(latitud) para que valores positivos "
            "indiquen 'cara cálida' en ambos hemisferios. Default: northness cruda."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Verifica entradas y muestra plan sin escribir salidas.",
    )
    args = parser.parse_args()

    # ------------------------------------------------------------------ dirs
    utils.ensure_dirs(config.RASTERS_ALIGNED)
    logger.info("=== 03_terrain.py — inicio ===")
    logger.info("WORLDCLIM_PRESENT : %s", config.WORLDCLIM_PRESENT)
    logger.info("RASTERS_ALIGNED   : %s", config.RASTERS_ALIGNED)
    logger.info("adjust_northness  : %s", args.adjust_northness)
    logger.info("dry_run           : %s", args.dry_run)

    # ------------------------------------------ 1. Verificar archivos entrada
    elev_path = config.WORLDCLIM_PRESENT / _ELEV_FILE
    mask_path = config.WORLDCLIM_PRESENT / _MASK_FILE

    missing: list[str] = []
    if not elev_path.exists():
        missing.append(str(elev_path))
    if not mask_path.exists():
        missing.append(str(mask_path))
    for var in config.BIOCLIM_VARS:
        p = config.WORLDCLIM_PRESENT / f"{var}.tif"
        if not p.exists():
            missing.append(str(p))
    if missing:
        logger.error("Archivos de entrada faltantes:")
        for m in missing:
            logger.error("  %s", m)
        sys.exit(1)

    logger.info("Todos los archivos de entrada verificados (%d capas + mask).",
                len(config.BIOCLIM_VARS) + 2)

    if args.dry_run:
        logger.info("dry-run: plan verificado. Saliendo sin escribir salidas.")
        return

    # ------------------------------------------ 2. Cargar elevación
    logger.info("Cargando elevation.tif ...")
    elevation = _load_raster(elev_path, name="elevation")
    logger.info("  Elevación: shape=%s, CRS=%s", elevation.shape, elevation.rio.crs)

    # ------------------------------------------ 3. Derivar slope y aspect
    logger.info("Derivando pendiente y aspecto ...")
    slope_deg, aspect_deg = derive_terrain(elevation)
    logger.info(
        "  slope: range [%.1f, %.1f] grados",
        float(slope_deg.min()), float(slope_deg.max()),
    )
    logger.info(
        "  aspect: range [%.1f, %.1f] grados (NaN en terreno plano)",
        float(np.nanmin(aspect_deg.values)), float(np.nanmax(aspect_deg.values)),
    )

    # ------------------------------------------ 4. Northness y eastness
    logger.info("Descomponiendo aspecto en northness / eastness ...")
    northness, eastness = compute_northness_eastness(
        aspect_deg,
        adjust_by_hemisphere=args.adjust_northness,
    )
    logger.info(
        "  northness: range [%.3f, %.3f]",
        float(np.nanmin(northness.values)), float(np.nanmax(northness.values)),
    )
    logger.info(
        "  eastness:  range [%.3f, %.3f]",
        float(np.nanmin(eastness.values)), float(np.nanmax(eastness.values)),
    )

    # ------------------------------------------ 5. Cargar y alinear bioclim
    logger.info("Cargando y alineando capas bioclim (referencia: bio1) ...")
    # La referencia de alineación es bio1; todas las demás se ajustan a ella
    bio1_raw = _load_raster(config.WORLDCLIM_PRESENT / "bio1.tif", name="bio1")
    reference_grid = bio1_raw  # grid canónico del pipeline

    bioclim_aligned = align_bioclim_layers(
        reference=reference_grid,
        src_dir=config.WORLDCLIM_PRESENT,
        variables=config.BIOCLIM_VARS,
    )

    # ------------------------------------------ 6. Alinear capas topográficas al grid bio1
    logger.info("Alineando capas topográficas al grid de referencia (bio1) ...")
    topo_raw: dict[str, xr.DataArray] = {
        "elevation": elevation,
        "slope": slope_deg,
        "northness": northness,
        "eastness": eastness,
    }
    topo_aligned: dict[str, xr.DataArray] = {}
    for tvar, da in topo_raw.items():
        da_aligned = da.rio.reproject_match(
            reference_grid,
            resampling=Resampling.bilinear,
        )
        da_aligned.name = tvar
        topo_aligned[tvar] = da_aligned.astype(np.float32)
        logger.info("  Alineado: %s → shape %s", tvar, da_aligned.shape)

    # ------------------------------------------ 7. Unir todas las capas
    all_layers: dict[str, xr.DataArray] = {}
    # Preservar orden canónico: bioclim primero, luego topo (según config.PREDICTORS)
    for var in config.PREDICTORS:
        if var in bioclim_aligned:
            all_layers[var] = bioclim_aligned[var]
        elif var in topo_aligned:
            all_layers[var] = topo_aligned[var]
        else:
            # No debería ocurrir si config.PREDICTORS está sincronizado
            raise KeyError(
                f"Variable '{var}' en config.PREDICTORS pero no en bioclim ni topo. "
                "Revisar config.py."
            )

    logger.info("Total de capas ensambladas: %d", len(all_layers))

    # ------------------------------------------ 8. Aplicar máscara de tierra
    logger.info("Aplicando máscara de tierra (land_mask.tif) ...")
    mask_da = _load_raster(mask_path, name="land_mask")
    mask_aligned = mask_da.rio.reproject_match(
        reference_grid,
        resampling=Resampling.nearest,  # máscara categórica: vecino más cercano
    )
    all_layers_masked = apply_land_mask(all_layers, mask=mask_aligned)

    n_land_cells = int((mask_aligned == 1).sum())
    logger.info("  Celdas de tierra en grilla: %d", n_land_cells)

    # ------------------------------------------ 9. Verificar alineación
    verify_alignment(all_layers_masked)

    # ------------------------------------------ 10. Escribir salidas
    logger.info("Escribiendo %d GeoTIFF en %s ...", len(all_layers_masked), config.RASTERS_ALIGNED)
    for var, da in all_layers_masked.items():
        out_path = config.RASTERS_ALIGNED / f"{var}.tif"
        _write_raster(da, out_path)
        logger.info("  Escrito: %s.tif", var)

    # ------------------------------------------ 11. Resumen final
    ref = next(iter(all_layers_masked.values()))
    logger.info("=== Resumen final ===")
    logger.info("  Capas producidas : %d", len(all_layers_masked))
    logger.info("  Variables        : %s", ", ".join(all_layers_masked.keys()))
    logger.info("  Shape común      : %s", ref.shape)
    logger.info("  CRS              : %s", ref.rio.crs)
    logger.info("  Resolución       : %.6f° × %.6f°", *[abs(r) for r in ref.rio.resolution()])
    logger.info("  Directorio salida: %s", config.RASTERS_ALIGNED)
    logger.info("=== 03_terrain.py — completado ===")


if __name__ == "__main__":
    main()
