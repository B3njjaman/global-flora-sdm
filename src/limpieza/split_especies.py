"""
split_especies.py — Separación modular del dataset por especie.

Toma el dataset general limpio (`Especies_sudamerica.csv`, salida de la limpieza
V4) y lo separa en un archivo por especie, dejando además una copia general.

Etiqueta de rama
----------------
Todas las salidas van a `config.PROCESSED / f"datasets_{rama}"` (p. ej.
`datasets_version-4/`) y llevan una columna `branch` con el nombre de la rama,
para distinguirlas de salidas de iteraciones previas y poder eliminarlas en bloque.

Cada salida se escribe en `.parquet` (estándar del repo) y `.csv` (revisable a ojo).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

# config.py / utils.py viven en scripts/ — al path para importarlos.
_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import config  # noqa: E402
import utils   # noqa: E402

log = utils.get_logger("limpieza.split_especies")

# Fuente: dataset general limpio que produce la limpieza V4.
FUENTE_GENERAL: Path = config.PROCESSED / "Especies_sudamerica.csv"
COL_ESPECIE: str = "especie"


def rama_actual(default: str = "version-4") -> str:
    """Nombre de la rama git actual (para etiquetar las salidas)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=_ROOT, capture_output=True, text=True, check=True,
        )
        rama = out.stdout.strip()
        return rama or default
    except Exception:  # noqa: BLE001 — sin git, usar el valor por defecto
        return default


def dir_salida(rama: str | None = None) -> Path:
    """Carpeta de salida etiquetada con la rama: datasets_<rama>/."""
    rama = rama or rama_actual()
    return config.PROCESSED / f"datasets_{rama}"


def cargar_general(fuente: Path | None = None) -> pd.DataFrame:
    """Carga el dataset general limpio (CSV)."""
    fuente = Path(fuente) if fuente else FUENTE_GENERAL
    if not fuente.exists():
        raise FileNotFoundError(
            f"No existe el dataset general {fuente}. Corre antes la limpieza V4."
        )
    return pd.read_csv(fuente)


def _escribir(df: pd.DataFrame, base: Path) -> None:
    """Escribe el DataFrame como .parquet y .csv (mismo nombre base)."""
    df.to_parquet(base.with_suffix(".parquet"), index=False)
    df.to_csv(base.with_suffix(".csv"), index=False, encoding="utf-8")


def separar_especies(
    especies: list[str] | None = None,
    *,
    incluir_general: bool = True,
    rama: str | None = None,
    fuente: Path | None = None,
    outdir: Path | None = None,
) -> dict[str, int]:
    """Separa el dataset general en un archivo por especie (+ general opcional).

    Parámetros
    ----------
    especies        : especies a procesar (defecto: todas las del dataset).
    incluir_general : si True, escribe también `general.{parquet,csv}`.
    rama            : etiqueta de rama (defecto: rama git actual).
    fuente          : CSV general de origen (defecto: FUENTE_GENERAL).
    outdir          : carpeta de salida (defecto: datasets_<rama>/ en PROCESSED).

    Devuelve {nombre_archivo: n_filas} de lo que se escribió.
    """
    rama = rama or rama_actual()
    df = cargar_general(fuente)
    df = df.assign(branch=rama)  # etiqueta de rama en el contenido

    outdir = Path(outdir) if outdir else dir_salida(rama)
    utils.ensure_dirs(outdir)

    disponibles = df[COL_ESPECIE].dropna().unique().tolist()
    objetivo = especies if especies is not None else disponibles

    escrito: dict[str, int] = {}

    if incluir_general:
        _escribir(df, outdir / "general")
        escrito["general"] = len(df)
        log.info("General: %d filas → %s", len(df), outdir / "general.{parquet,csv}")

    for esp in objetivo:
        sub = df[df[COL_ESPECIE] == esp]
        if sub.empty:
            log.warning("Especie sin registros (se omite): %s", esp)
            continue
        slug = utils.slugify_species(esp)
        _escribir(sub, outdir / slug)
        escrito[slug] = len(sub)
        log.info("%-26s %4d filas → %s.{parquet,csv}", esp, len(sub), slug)

    return escrito
