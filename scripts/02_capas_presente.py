"""
02_capas_presente.py — Descarga y preparación de capas WorldClim v2.1 (presente).

Etapa 2 del pipeline SDM (global-flora-sdm).  Descarga los archivos ZIP de
WorldClim v2.1 bioclim 2.5 arc-min y la capa de elevación, extrae únicamente
las 10 variables bioclimáticas definidas en config.BIOCLIM_VARS, genera la
máscara de tierra con Natural Earth y verifica la alineación entre todas las
capas.

Salidas en config.WORLDCLIM_PRESENT:
    bio1.tif … bio17.tif  (10 capas, selección de BIOCLIM_VARS)
    elevation.tif
    land_mask.tif          (1 = tierra, 0 = océano / sin datos)

Uso:
    python 02_capas_presente.py
    python 02_capas_presente.py --overwrite   # re-descarga aunque ya exista
"""
from __future__ import annotations

import argparse
import sys
import time
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import rasterio
import requests
from affine import Affine
from rasterio.features import rasterize
from tqdm import tqdm

import config
import utils

# ---------------------------------------------------------------------------
# Constantes de descarga
# ---------------------------------------------------------------------------
_BASE_URL = "https://geodata.ucdavis.edu/climate/worldclim/2_1/base"
_URLS: Dict[str, str] = {
    "bioclim": f"{_BASE_URL}/wc2.1_2.5m_bio.zip",
    "elevation": f"{_BASE_URL}/wc2.1_2.5m_elev.zip",
}

# Tiempo de espera (segundos) y reintentos
_TIMEOUT_CONNECT = 30
_TIMEOUT_READ = 120
_MAX_RETRIES = 5
_BACKOFF_BASE = 2.0          # espera exponencial: 2^intento segundos

# Mapeo nombre interno → nombre de archivo WorldClim
# wc2.1_2.5m_bio_{N}.tif  (N sin cero inicial)
def _wc_bioclim_name(var: str) -> str:
    """Devuelve el nombre de archivo WorldClim para una variable bioclim.

    Ejemplo: 'bio1' -> 'wc2.1_2.5m_bio_1.tif'
    """
    num = var.replace("bio", "")
    return f"wc2.1_2.5m_bio_{num}.tif"


_ELEV_WC_NAME = "wc2.1_2.5m_elev.tif"

# ---------------------------------------------------------------------------
# Helpers de descarga robusta
# ---------------------------------------------------------------------------

def _download_with_resume(
    url: str,
    dest: Path,
    logger,
    overwrite: bool = False,
    chunk_size: int = 1024 * 1024,  # 1 MB
) -> Path:
    """Descarga ``url`` en ``dest`` con soporte de reanudación HTTP Range.

    Si el archivo ya existe y tiene el tamaño correcto (Content-Length)
    se omite la descarga salvo que ``overwrite`` sea True.

    Implementa reintentos con back-off exponencial ante errores de red.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            # HEAD rápido para obtener tamaño esperado
            head = requests.head(
                url,
                timeout=(_TIMEOUT_CONNECT, _TIMEOUT_READ),
                allow_redirects=True,
            )
            head.raise_for_status()
            remote_size = int(head.headers.get("Content-Length", 0))

            local_size = dest.stat().st_size if dest.exists() else 0

            if dest.exists() and not overwrite:
                if remote_size and local_size == remote_size:
                    logger.info("Ya existe (tamaño OK): %s — omitiendo descarga.", dest.name)
                    return dest
                elif local_size < remote_size:
                    logger.info(
                        "Archivo incompleto (%d/%d bytes) — reanudando.", local_size, remote_size
                    )
                else:
                    # Archivo presente pero tamaño desconocido (sin Content-Length)
                    logger.info("Ya existe: %s — omitiendo (usa --overwrite para reforzar).", dest.name)
                    return dest

            # Preparar cabecera Range para reanudar
            headers: Dict[str, str] = {}
            mode = "wb"
            if dest.exists() and not overwrite and local_size:
                headers["Range"] = f"bytes={local_size}-"
                mode = "ab"
                logger.info("Reanudando desde byte %d.", local_size)
            elif overwrite:
                local_size = 0

            total = remote_size or None
            desc = dest.name[:40]

            with requests.get(
                url,
                headers=headers,
                stream=True,
                timeout=(_TIMEOUT_CONNECT, _TIMEOUT_READ),
                allow_redirects=True,
            ) as resp:
                resp.raise_for_status()
                # 206 = partial content (reanudación OK); 200 = descarga completa
                if resp.status_code == 200 and mode == "ab":
                    # El servidor no soporta Range, reiniciar
                    mode = "wb"
                    local_size = 0

                with open(dest, mode) as fh, tqdm(
                    total=total,
                    initial=local_size,
                    unit="B",
                    unit_scale=True,
                    unit_divisor=1024,
                    desc=desc,
                    leave=False,
                ) as bar:
                    for chunk in resp.iter_content(chunk_size=chunk_size):
                        if chunk:
                            fh.write(chunk)
                            bar.update(len(chunk))

            # Verificar tamaño final
            final_size = dest.stat().st_size
            if remote_size and final_size != remote_size:
                raise IOError(
                    f"Tamaño final {final_size} != esperado {remote_size}. Archivo corrupto."
                )

            logger.info("Descarga completa: %s (%d bytes).", dest.name, final_size)
            return dest

        except (requests.RequestException, IOError, OSError) as exc:
            wait = _BACKOFF_BASE ** attempt
            logger.warning(
                "Intento %d/%d fallido para %s: %s. Reintentando en %.0fs…",
                attempt, _MAX_RETRIES, url, exc, wait,
            )
            if attempt < _MAX_RETRIES:
                time.sleep(wait)
            else:
                logger.error("Se agotaron los reintentos para %s.", url)
                raise


def _extract_zip(zip_path: Path, dest_dir: Path, logger) -> List[Path]:
    """Extrae ``zip_path`` en ``dest_dir`` y devuelve la lista de rutas extraídas."""
    logger.info("Descomprimiendo %s …", zip_path.name)
    extracted: List[Path] = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.namelist():
            target = dest_dir / Path(member).name  # aplanar estructura interna
            if target.suffix.lower() in (".tif", ".tiff"):
                zf.extract(member, dest_dir)
                # mover al nivel de dest_dir si quedó en subdirectorio
                extracted_path = dest_dir / member
                if extracted_path != target and extracted_path.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    extracted_path.rename(target)
                extracted.append(target)
    logger.info("Extraídos %d GeoTIFF de %s.", len(extracted), zip_path.name)
    return extracted


# ---------------------------------------------------------------------------
# Selección y renombrado de capas bioclimáticas
# ---------------------------------------------------------------------------

def _select_bioclim_vars(
    wc_dir: Path,
    bioclim_vars: List[str],
    logger,
    overwrite: bool = False,
) -> Dict[str, Path]:
    """Copia/renombra los TIF de WorldClim al esquema `bioN.tif`.

    Mapeo:  wc2.1_2.5m_bio_{N}.tif  →  bio{N}.tif
    Solo conserva las variables de ``bioclim_vars``.

    Devuelve dict { 'bio1': Path('bio1.tif'), … }
    """
    selected: Dict[str, Path] = {}
    for var in bioclim_vars:
        src_name = _wc_bioclim_name(var)
        src = wc_dir / src_name
        dst = wc_dir / f"{var}.tif"

        if not src.exists():
            raise FileNotFoundError(
                f"No se encontró '{src_name}' en {wc_dir}. "
                "Verifica que la descarga fue exitosa."
            )

        if dst.exists() and not overwrite:
            logger.info("Ya existe: %s — no se sobreescribe.", dst.name)
        else:
            import shutil
            shutil.copy2(src, dst)
            logger.info("Renombrado: %s → %s", src_name, dst.name)

        selected[var] = dst

    return selected


def _prepare_elevation(wc_dir: Path, logger, overwrite: bool = False) -> Path:
    """Renombra wc2.1_2.5m_elev.tif → elevation.tif."""
    src = wc_dir / _ELEV_WC_NAME
    dst = wc_dir / "elevation.tif"

    if not src.exists():
        raise FileNotFoundError(
            f"No se encontró '{_ELEV_WC_NAME}' en {wc_dir}."
        )

    if dst.exists() and not overwrite:
        logger.info("Ya existe: elevation.tif — no se sobreescribe.")
    else:
        import shutil
        shutil.copy2(src, dst)
        logger.info("Renombrado: %s → elevation.tif", _ELEV_WC_NAME)

    return dst


# ---------------------------------------------------------------------------
# Máscara de tierra (Natural Earth)
# ---------------------------------------------------------------------------

def _download_natural_earth(dest_dir: Path, logger) -> Path:
    """Descarga el shapefile de polígonos de tierra de Natural Earth 110m.

    URL primaria (GitHub release); fallback a naturalearth.com.
    Devuelve la ruta al .shp extraído.
    """
    urls = [
        # fuente primaria — bucket oficial S3 de Natural Earth (estable)
        "https://naturalearth.s3.amazonaws.com/110m_physical/ne_110m_land.zip",
        # espejo — GitHub Natural Earth (ruta zips/)
        "https://github.com/nvkelso/natural-earth-vector/raw/master/zips/110m_physical/ne_110m_land.zip",
    ]

    zip_path = dest_dir / "ne_110m_land.zip"
    shp_path = dest_dir / "ne_110m_land.shp"

    if shp_path.exists():
        logger.info("Natural Earth land mask ya descargado.")
        return shp_path

    last_exc: Exception | None = None
    for url in urls:
        try:
            logger.info("Descargando Natural Earth desde %s …", url)
            _download_with_resume(url, zip_path, logger)
            break
        except Exception as exc:
            logger.warning("URL fallida: %s — %s", url, exc)
            last_exc = exc
    else:
        raise RuntimeError(
            "No se pudo descargar Natural Earth land polygons. "
            f"Último error: {last_exc}"
        )

    logger.info("Descomprimiendo Natural Earth …")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_dir)

    if not shp_path.exists():
        raise FileNotFoundError(
            f"Se esperaba 'ne_110m_land.shp' en {dest_dir} tras descomprimir."
        )

    return shp_path


def _build_land_mask(
    reference_tif: Path,
    ne_shp: Path,
    output: Path,
    logger,
    overwrite: bool = False,
) -> Path:
    """Rasteriza los polígonos de tierra de Natural Earth a la grilla de referencia.

    El resultado es un GeoTIFF uint8: 1 = tierra, 0 = océano / sin datos.
    """
    if output.exists() and not overwrite:
        logger.info("land_mask.tif ya existe — omitiendo generación.")
        return output

    try:
        import geopandas as gpd
    except ImportError as exc:
        raise ImportError(
            "geopandas es necesario para generar la máscara de tierra. "
            "Instálalo con: pip install geopandas"
        ) from exc

    logger.info("Leyendo polígonos Natural Earth …")
    land_gdf = gpd.read_file(ne_shp)

    with rasterio.open(reference_tif) as ref:
        transform = ref.transform
        width = ref.width
        height = ref.height
        crs = ref.crs
        meta = ref.meta.copy()

    # Reprojectar si es necesario (Natural Earth está en WGS84, igual que WorldClim)
    if land_gdf.crs is not None and land_gdf.crs.to_epsg() != 4326:
        logger.info("Reproyectando Natural Earth a EPSG:4326 …")
        land_gdf = land_gdf.to_crs("EPSG:4326")

    logger.info("Rasterizando polígonos de tierra …")
    shapes = (
        (geom, 1)
        for geom in land_gdf.geometry
        if geom is not None and not geom.is_empty
    )
    mask_array = rasterize(
        shapes,
        out_shape=(height, width),
        transform=transform,
        fill=0,
        dtype=np.uint8,
        all_touched=True,
    )

    meta.update(
        dtype=rasterio.uint8,
        count=1,
        nodata=255,
        compress="lzw",
        predictor=2,
    )

    logger.info("Guardando land_mask.tif …")
    with rasterio.open(output, "w", **meta) as dst:
        dst.write(mask_array, 1)

    logger.info("land_mask.tif generado: shape %s.", mask_array.shape)
    return output


# ---------------------------------------------------------------------------
# Verificación de alineación
# ---------------------------------------------------------------------------

def _round_transform(transform: Affine, decimals: int = 8) -> Tuple:
    """Devuelve los componentes relevantes del Affine redondeados."""
    return (
        round(transform.a, decimals),   # resolución x
        round(transform.e, decimals),   # resolución y (negativa)
        round(transform.c, decimals),   # x origen
        round(transform.f, decimals),   # y origen
    )


def _verify_alignment(layer_paths: Dict[str, Path], logger) -> None:
    """Verifica que todas las capas tengan igual extent, resolución y CRS.

    Registra shape / transform / CRS de cada capa.  Si alguna difiere,
    lanza un RuntimeError con mensaje detallado.
    """
    logger.info("=== Verificación de alineación (%d capas) ===", len(layer_paths))

    reference_name: str | None = None
    reference_profile: Dict | None = None
    mismatches: List[str] = []

    for name, path in layer_paths.items():
        with rasterio.open(path) as src:
            profile = {
                "shape": (src.height, src.width),
                "transform_key": _round_transform(src.transform),
                "crs": src.crs.to_epsg() if src.crs else None,
                "bounds": src.bounds,
            }
        logger.info(
            "  %-15s | shape=%s | res=(%.6f, %.6f) | CRS=EPSG:%s | bounds=(%g, %g, %g, %g)",
            name,
            profile["shape"],
            abs(profile["transform_key"][0]),
            abs(profile["transform_key"][1]),
            profile["crs"],
            *profile["bounds"],
        )

        if reference_profile is None:
            reference_name = name
            reference_profile = profile
            continue

        issues: List[str] = []
        if profile["shape"] != reference_profile["shape"]:
            issues.append(
                f"shape {profile['shape']} != ref {reference_profile['shape']}"
            )
        if profile["transform_key"] != reference_profile["transform_key"]:
            issues.append(
                f"transform {profile['transform_key']} != ref {reference_profile['transform_key']}"
            )
        if profile["crs"] != reference_profile["crs"]:
            issues.append(
                f"CRS EPSG:{profile['crs']} != ref EPSG:{reference_profile['crs']}"
            )

        if issues:
            mismatches.append(f"  '{name}' vs. '{reference_name}': " + "; ".join(issues))

    if mismatches:
        msg = (
            "ALINEACIÓN INCORRECTA — las siguientes capas difieren de la referencia:\n"
            + "\n".join(mismatches)
            + "\n\nAbortando. Revisa las capas descargadas o re-ejecuta con --overwrite."
        )
        logger.error(msg)
        raise RuntimeError(msg)

    logger.info("Todas las capas están correctamente alineadas.")


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------

def main() -> None:
    """Punto de entrada principal del script."""
    parser = argparse.ArgumentParser(
        description=(
            "Descarga y prepara capas WorldClim v2.1 bioclim 2.5' + elevation "
            "para el pipeline SDM (global-flora-sdm)."
        )
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-descarga y sobreescribe archivos aunque ya existan.",
    )
    args = parser.parse_args()

    logger = utils.get_logger(__name__)
    utils.ensure_dirs(config.WORLDCLIM_PRESENT)

    wc_dir: Path = config.WORLDCLIM_PRESENT

    # ------------------------------------------------------------------
    # 1. Descargar ZIPs de WorldClim
    # ------------------------------------------------------------------
    logger.info("=== ETAPA 2 — Capas presentes WorldClim v2.1 (%.1s) ===", config.WORLDCLIM_RES)
    logger.info("Directorio de salida: %s", wc_dir)

    for key, url in _URLS.items():
        zip_dest = wc_dir / Path(url).name
        logger.info("--- Descargando %s ---", key)
        logger.info("URL: %s", url)
        try:
            _download_with_resume(url, zip_dest, logger, overwrite=args.overwrite)
        except Exception as exc:
            logger.error("Fallo definitivo en descarga de %s: %s", key, exc)
            sys.exit(1)

        # Extraer solo si los TIFs de ESTE zip aún no están, o si --overwrite.
        # (Antes se usaba un glob global, lo que hacía que tras extraer bio.zip
        #  se omitiera por error la extracción de elev.zip.)
        with zipfile.ZipFile(zip_dest, "r") as _zf:
            members = [Path(m).name for m in _zf.namelist()
                       if Path(m).suffix.lower() in (".tif", ".tiff")]
        already_extracted = bool(members) and all((wc_dir / m).exists() for m in members)
        if already_extracted and not args.overwrite:
            logger.info("TIFs de %s ya extraídos (%d archivos) — omitiendo.", key, len(members))
        else:
            try:
                _extract_zip(zip_dest, wc_dir, logger)
            except Exception as exc:
                logger.error("Error al descomprimir %s: %s", zip_dest.name, exc)
                sys.exit(1)

    # ------------------------------------------------------------------
    # 2. Seleccionar y renombrar capas bioclimáticas
    # ------------------------------------------------------------------
    logger.info("=== Seleccionando %d variables bioclimáticas ===", len(config.BIOCLIM_VARS))
    logger.info("Variables: %s", config.BIOCLIM_VARS)
    logger.info(
        "Mapeo: wc2.1_2.5m_bio_N.tif → bioN.tif  (N = número sin cero inicial)"
    )

    try:
        bio_paths = _select_bioclim_vars(
            wc_dir, config.BIOCLIM_VARS, logger, overwrite=args.overwrite
        )
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    # ------------------------------------------------------------------
    # 3. Renombrar elevación
    # ------------------------------------------------------------------
    logger.info("=== Preparando capa de elevación ===")
    try:
        elev_path = _prepare_elevation(wc_dir, logger, overwrite=args.overwrite)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    # ------------------------------------------------------------------
    # 4. Máscara de tierra (Natural Earth)
    # ------------------------------------------------------------------
    logger.info("=== Generando máscara de tierra (Natural Earth) ===")
    ne_dir = wc_dir / "_natural_earth"
    utils.ensure_dirs(ne_dir)
    land_mask_path = wc_dir / "land_mask.tif"

    # Usar bio1.tif como referencia de grilla
    reference_tif = bio_paths[config.BIOCLIM_VARS[0]]

    try:
        ne_shp = _download_natural_earth(ne_dir, logger)
        _build_land_mask(reference_tif, ne_shp, land_mask_path, logger, overwrite=args.overwrite)
    except Exception as exc:
        logger.warning(
            "No se pudo generar land_mask.tif: %s  "
            "(el pipeline puede continuar sin ella, pero 01_limpieza.py la requiere).",
            exc,
        )
        land_mask_path = None  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # 5. Verificar alineación de todas las capas
    # ------------------------------------------------------------------
    logger.info("=== Verificando alineación ===")
    all_layers: Dict[str, Path] = {}
    all_layers.update(bio_paths)
    all_layers["elevation"] = elev_path
    if land_mask_path is not None and land_mask_path.exists():
        all_layers["land_mask"] = land_mask_path

    try:
        _verify_alignment(all_layers, logger)
    except RuntimeError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    # ------------------------------------------------------------------
    # Resumen final
    # ------------------------------------------------------------------
    logger.info("=== RESUMEN ===")
    logger.info("Directorio: %s", wc_dir)
    logger.info("Capas bioclimáticas producidas: %s", [p.name for p in bio_paths.values()])
    logger.info("Elevación: %s", elev_path.name)
    if land_mask_path is not None:
        logger.info("Máscara de tierra: %s", land_mask_path.name if land_mask_path.exists() else "NO GENERADA")
    logger.info(
        "Total de capas verificadas: %d", len(all_layers)
    )
    logger.info("Etapa 2 (presente) completada.")


if __name__ == "__main__":
    main()
