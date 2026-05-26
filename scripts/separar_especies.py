"""
separar_especies.py — CLI delgado: separa el dataset por especie (V4).

Orquesta `src/limpieza/split_especies.py`. Toma el dataset general limpio
(`data/processed/Especies_sudamerica.csv`) y escribe un archivo por especie
más una copia general, todo en `data/processed/datasets_<rama>/` y con una
columna `branch` que etiqueta la rama (para reconocerlas y poder borrar en
bloque las salidas de iteraciones previas).

Uso:
    python scripts/separar_especies.py                       # general + todas las especies
    python scripts/separar_especies.py --no-general          # solo por especie
    python scripts/separar_especies.py --especies "Nolana divaricata" "Encelia canescens"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Poner src/ en el path para importar el paquete 'limpieza'.
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from limpieza import split_especies  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="V4 — separa el dataset general en un archivo por especie."
    )
    parser.add_argument(
        "--especies",
        nargs="+",
        default=None,
        help="Especies a procesar (defecto: todas las del dataset general).",
    )
    parser.add_argument(
        "--no-general",
        action="store_true",
        help="No escribir el archivo general (solo los por especie).",
    )
    parser.add_argument(
        "--rama",
        default=None,
        help="Etiqueta de rama para las salidas (defecto: rama git actual).",
    )
    args = parser.parse_args()

    escrito = split_especies.separar_especies(
        especies=args.especies,
        incluir_general=not args.no_general,
        rama=args.rama,
    )
    total = sum(escrito.values()) - escrito.get("general", 0)
    print(f"\nEscritos {len(escrito)} archivos en datasets_{args.rama or split_especies.rama_actual()}/")
    print(f"Filas por especie (suma, sin contar general): {total}")
    for nombre, n in escrito.items():
        print(f"  {nombre:30s} {n:5d} filas")


if __name__ == "__main__":
    main()
