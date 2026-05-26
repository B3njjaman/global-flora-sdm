"""
io.py — Carga del dataset GBIF crudo y normalización de columnas.

Fuente única: `config.OCCURRENCES_XLSX` (data/raw/gbif_distribucion_especies.xlsx),
hoja `config.OCCURRENCES_SHEET`. Misma lógica de renombrado que la versión previa
(`utils.load_raw_occurrences`), aquí aislada como módulo del paquete `limpieza`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

# config.py vive en scripts/ — ponerlo en el path para importarlo desde el paquete.
_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import config  # noqa: E402

# Mapeo de columnas del .xlsx → snake_case ASCII estable que asume el pipeline.
RENOMBRES: dict[str, str] = {
    "Especie": "especie",
    "Nombre cientifico GBIF": "nombre_cientifico",
    "Latitud": "lat",
    "Longitud": "lon",
    "Incertidumbre (m)": "incertidumbre_m",
    "Pais": "pais",
    "Region / Estado": "region",
    "Localidad": "localidad",
    "Fecha": "fecha",
    "Ano": "ano",
    "Tipo de registro": "tipo_registro",
    "Institucion": "institucion",
    "Dataset": "dataset",
    "Catalogo": "catalogo",
    "GBIF ID": "gbif_id",
}


def cargar_ocurrencias_crudas(
    xlsx: Path | None = None,
    hoja: str | None = None,
) -> pd.DataFrame:
    """Lee el .xlsx de GBIF y normaliza los nombres de columna.

    Parámetros
    ----------
    xlsx : ruta del .xlsx (defecto: config.OCCURRENCES_XLSX).
    hoja : nombre de la hoja (defecto: config.OCCURRENCES_SHEET).

    Devuelve un DataFrame con columnas en snake_case (especie, lat, lon, pais, …).
    """
    xlsx = Path(xlsx) if xlsx else config.OCCURRENCES_XLSX
    hoja = hoja or config.OCCURRENCES_SHEET
    df = pd.read_excel(xlsx, sheet_name=hoja)
    return df.rename(columns=RENOMBRES)
