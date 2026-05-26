"""
grupos.py — Paso 7: asignación de grupo A/B/C por especie.

Etiqueta cada registro con el grupo de su especie (config.classify_species):

  - A = cosmopolita/introducida con datos suficientes.
  - B = endémica con datos suficientes (>= config.MIN_RECORDS_TO_MODEL).
  - C = pocos registros (< piso); se marca pero NO se descarta (se excluye
        en las etapas de modelado).

Los conteos se calculan DESPUÉS del thinning, es decir, sobre el df que recibe
esta función, para que el grupo refleje los registros realmente útiles y no los
crudos. A diferencia de la versión previa (`01_limpieza.asignar_grupos`, que
operaba sobre un GeoDataFrame), aquí se opera sobre un DataFrame plano.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

# config.py / utils.py viven en scripts/ — ponerlo en el path para importarlos
# desde el paquete.
_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import config  # noqa: E402
import utils  # noqa: E402

logger = utils.get_logger("limpieza.grupos")


def asignar_grupos(df: pd.DataFrame) -> pd.DataFrame:
    """Añade la columna 'grupo' (A/B/C) según los conteos por especie.

    Los conteos se calculan sobre `df` (ya limpiado/thinned). No se eliminan
    filas: el grupo C solo se etiqueta. Devuelve el DataFrame con la columna
    'grupo' añadida y el índice reseteado.
    """
    conteos = df["especie"].value_counts().to_dict()
    grupos = config.classify_species(conteos)

    df = df.copy()
    df["grupo"] = df["especie"].map(grupos)

    # Advertencia por especie del grupo C (pocos registros, no modelable).
    especies_c = sorted(sp for sp, g in grupos.items() if g == "C")
    if especies_c:
        logger.warning(
            "Paso 7 | %d especie(s) Grupo C (<%d registros tras limpieza) — "
            "se conservan pero NO se modelarán individualmente: %s",
            len(especies_c), config.MIN_RECORDS_TO_MODEL, ", ".join(especies_c),
        )

    # Resumen de cuántas especies hay por grupo.
    resumen = df.groupby("grupo")["especie"].nunique()
    for grupo, n_sp in resumen.items():
        logger.info("Paso 7 | Grupo %s: %d especie(s)", grupo, n_sp)

    return df.reset_index(drop=True)
