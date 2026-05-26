"""
descarga.py — Paso 1 (capas): adquisición de las capas WorldClim presentes.

REUSA lo ya descargado en `config.WORLDCLIM_PRESENT`: si las capas `bioN.tif` y
`elevation.tif` ya existen, NO vuelve a bajar los ~628 MB del zip de WorldClim.
Si faltan, descarga los zips de WorldClim v2.1 (bioclim + elevación) con
reanudación HTTP, los extrae y selecciona/renombra solo las variables de
`config.BIOCLIM_VARS` al esquema `bioN.tif`.

Misma lógica que la versión previa (`02_capas_presente.py`), aquí aislada como
módulo del paquete `capas`.
"""
from __future__ import annotations

import shutil
import sys
import time
import zipfile
from pathlib import Path

# config.py / utils.py viven en scripts/ — al path para importarlos.
_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import config  # noqa: E402
import utils   # noqa: E402

log = utils.get_logger("capas.descarga")

# --- Constantes de descarga (WorldClim v2.1, 2.5 arc-min) ---
_BASE_URL = "https://geodata.ucdavis.edu/climate/worldclim/2_1/base"
_URLS: dict[str, str] = {
    "bioclim": f"{_BASE_URL}/wc2.1_2.5m_bio.zip",
    "elevation": f"{_BASE_URL}/wc2.1_2.5m_elev.zip",
}
_ELEV_WC_NAME = "wc2.1_2.5m_elev.tif"
_TIMEOUT = (30, 120)   # (connect, read) segundos
_MAX_RETRIES = 5
_BACKOFF_BASE = 2.0


def _wc_bioclim_name(var: str) -> str:
    """'bio1' -> 'wc2.1_2.5m_bio_1.tif' (nombre original de WorldClim)."""
    return f"wc2.1_2.5m_bio_{var.replace('bio', '')}.tif"


def _download_with_resume(url: str, dest: Path, overwrite: bool = False) -> Path:
    """Descarga `url` en `dest` con reanudación HTTP Range y reintentos."""
    import requests  # import perezoso: solo si de verdad hay que descargar
    dest.parent.mkdir(parents=True, exist_ok=True)
    for intento in range(1, _MAX_RETRIES + 1):
        try:
            head = requests.head(url, timeout=_TIMEOUT, allow_redirects=True)
            head.raise_for_status()
            remoto = int(head.headers.get("Content-Length", 0))
            local = dest.stat().st_size if dest.exists() else 0
            if dest.exists() and not overwrite and remoto and local == remoto:
                log.info("Ya existe (tamaño OK): %s — omitiendo descarga.", dest.name)
                return dest
            headers, modo = {}, "wb"
            if dest.exists() and not overwrite and local:
                headers["Range"] = f"bytes={local}-"
                modo = "ab"
                log.info("Reanudando %s desde byte %d.", dest.name, local)
            with requests.get(url, headers=headers, stream=True, timeout=_TIMEOUT,
                              allow_redirects=True) as resp:
                resp.raise_for_status()
                if resp.status_code == 200 and modo == "ab":
                    modo = "wb"  # el servidor no soporta Range
                with open(dest, modo) as fh:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            fh.write(chunk)
            if remoto and dest.stat().st_size != remoto:
                raise IOError(f"Tamaño final {dest.stat().st_size} != {remoto}.")
            log.info("Descarga completa: %s.", dest.name)
            return dest
        except Exception as exc:  # noqa: BLE001
            espera = _BACKOFF_BASE ** intento
            log.warning("Intento %d/%d falló (%s): %s. Reintento en %.0fs.",
                        intento, _MAX_RETRIES, dest.name, exc, espera)
            time.sleep(espera)
    raise RuntimeError(f"No se pudo descargar {url} tras {_MAX_RETRIES} intentos.")


def _extract_zip(zip_path: Path, dest_dir: Path) -> None:
    """Extrae los GeoTIFF de `zip_path` en `dest_dir` (estructura aplanada)."""
    log.info("Descomprimiendo %s …", zip_path.name)
    n = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.namelist():
            if Path(member).suffix.lower() in (".tif", ".tiff"):
                zf.extract(member, dest_dir)
                origen = dest_dir / member
                destino = dest_dir / Path(member).name
                if origen != destino and origen.exists():
                    origen.rename(destino)
                n += 1
    log.info("Extraídos %d GeoTIFF de %s.", n, zip_path.name)


def _seleccionar_bioclim(wc_dir: Path, overwrite: bool = False) -> dict[str, Path]:
    """Copia `wc2.1_2.5m_bio_{N}.tif` → `bioN.tif` solo para config.BIOCLIM_VARS."""
    sel: dict[str, Path] = {}
    for var in config.BIOCLIM_VARS:
        src = wc_dir / _wc_bioclim_name(var)
        dst = wc_dir / f"{var}.tif"
        if not src.exists():
            raise FileNotFoundError(f"Falta {src.name} en {wc_dir}.")
        if not (dst.exists() and not overwrite):
            shutil.copy2(src, dst)
            log.info("Renombrado: %s → %s", src.name, dst.name)
        sel[var] = dst
    return sel


def _preparar_elevacion(wc_dir: Path, overwrite: bool = False) -> Path:
    """Copia `wc2.1_2.5m_elev.tif` → `elevation.tif`."""
    src = wc_dir / _ELEV_WC_NAME
    dst = wc_dir / "elevation.tif"
    if not src.exists():
        raise FileNotFoundError(f"Falta {_ELEV_WC_NAME} en {wc_dir}.")
    if not (dst.exists() and not overwrite):
        shutil.copy2(src, dst)
        log.info("Renombrado: %s → elevation.tif", _ELEV_WC_NAME)
    return dst


def capas_presentes(overwrite: bool = False) -> dict[str, Path]:
    """Devuelve {nombre: ruta} de las capas presentes (10 bioclim + elevación).

    Reusa las capas ya preparadas (`bioN.tif`, `elevation.tif`) si existen; solo
    descarga/extrae/selecciona cuando falta alguna o `overwrite=True`.
    """
    wc = config.WORLDCLIM_PRESENT
    esperadas = {v: wc / f"{v}.tif" for v in config.BIOCLIM_VARS}
    esperadas["elevation"] = wc / "elevation.tif"

    faltan = [n for n, p in esperadas.items() if not p.exists()]
    if not faltan and not overwrite:
        log.info("Reusando %d capas presentes ya disponibles en %s",
                 len(esperadas), wc)
        return esperadas

    log.info("Faltan %d capas (%s) — adquiriendo desde WorldClim.",
             len(faltan), ", ".join(faltan))
    for nombre, url in _URLS.items():
        _download_with_resume(url, wc / Path(url).name, overwrite=overwrite)
        _extract_zip(wc / Path(url).name, wc)
    capas = _seleccionar_bioclim(wc, overwrite=overwrite)
    capas["elevation"] = _preparar_elevacion(wc, overwrite=overwrite)
    return capas
