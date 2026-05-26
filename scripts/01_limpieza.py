"""
01_limpieza.py — Etapa 1 (V4): CLI delgado de la limpieza modular.

Orquesta el paquete `src/limpieza`. Estado actual del pipeline de limpieza:
  - Cargar gbif_distribucion_especies.xlsx
  - Filtrar a Sudamérica  → data/processed/Especies_sudamerica.csv

La lógica vive en `src/limpieza/` (un módulo por paso). Los pasos restantes
(duplicados, incertidumbre, coords, centroides, océano, thinning, grupos A/B/C)
se añadirán en orden. La versión monolítica previa queda en el historial git
(ramas main / acotar-chile-sudamerica) y en docs/v4/flujo_trabajo.md.

Uso:
    python scripts/01_limpieza.py
    python scripts/01_limpieza.py --metodo geografia
    python scripts/01_limpieza.py --salida ruta/Especies_sudamerica.csv
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

from limpieza import pipeline  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Etapa 1 (V4) — limpieza modular: filtro a Sudamérica."
    )
    parser.add_argument(
        "--metodo",
        choices=["pais", "geografia", "ambos"],
        default="pais",
        help="Método del filtro Sudamérica (defecto: pais).",
    )
    parser.add_argument(
        "--salida",
        type=str,
        default=None,
        help="Ruta de salida de Especies_sudamerica "
             "(defecto: data/processed/Especies_sudamerica.csv).",
    )
    args = parser.parse_args()
    pipeline.run(metodo_sa=args.metodo, salida=args.salida)


if __name__ == "__main__":
    main()
