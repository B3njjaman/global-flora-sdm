"""
armar_entrega.py — Ensambla el paquete .zip para presentar (iteracion 2).

1. Regenera base_de_datos_completa.{csv,xlsx} desde los datasets actuales
   (slope corregido + background podado + cv_fold adaptativo).
2. Crea la carpeta entrega_jefe/ con: informes, base de datos, metricas y mapas.
3. Escribe un indice 00_LEEME.md (sin emojis).
4. Comprime todo en entrega_jefe.zip en la raiz del proyecto.
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pandas as pd

import config
import utils

log = utils.get_logger("armar_entrega")

ROOT = config.ROOT
ENTREGA = ROOT / "entrega_jefe"
ZIP_PATH = ROOT / "entrega_jefe.zip"


def regenerar_base_datos() -> tuple[Path, Path]:
    """Combina los parquets actuales en CSV + XLSX."""
    parts = []
    for pq in sorted(config.SPECIES_DATASETS.glob("*.parquet")):
        if pq.stem.endswith("_cv_preds"):
            continue
        df = pd.read_parquet(pq)
        df.insert(0, "slug", pq.stem)
        parts.append(df)
    full = pd.concat(parts, ignore_index=True)
    csv = config.TABLES / "base_de_datos_completa.csv"
    xlsx = config.TABLES / "base_de_datos_completa.xlsx"
    full.to_csv(csv, index=False)
    full.to_excel(xlsx, index=False, sheet_name="base_datos", engine="openpyxl")
    log.info("Base de datos: %d filas, %d especies", len(full), full["slug"].nunique())
    return csv, xlsx


def escribir_indice(dest: Path, n_mapas: int) -> None:
    txt = """# Entrega - Modelos de distribucion de especies (iteracion 2)

Paquete para revision. Modelos de idoneidad de habitat presente, escala global
(~5 km), para 14 especies de flora, validados con validacion cruzada espacial.

## Resultados en una linea
Media: TSS 0.82 - AUC 0.94 - Boyce 0.68 - Brier 0.04 (14 de 14 especies validables).
El ensemble iguala a MaxEnt (TSS 0.822 vs 0.826) y lo supera levemente en AUC
(0.944 vs 0.939), ganando en 8 de 14 especies, con el plus de robustez e
incertidumbre. Excepcion: schinus_areira (introducida) no transfiere bien entre
regiones (Boyce negativo).

## Contenido del paquete
- 01_informes/
  - informe_modelo.md       : como funciona el modelo y resultados.
  - proceso_completo.md     : bitacora ultra-detallada de todo el trabajo.
  - lista_auditoria.md      : checklist de correcciones, calidad y validacion.
  - metricas_explicadas.md  : que significa cada metrica (para no-especialistas).
- 02_base_de_datos/
  - base_de_datos_completa.xlsx / .csv : datos por punto (presencias + background)
    con las 14 variables predictoras (clima WorldClim + topografia).
- 03_metricas/
  - metrics_all.csv         : metricas completas por especie y algoritmo.
- 04_mapas/
  - <especie>_present.png   : %d mapas de idoneidad presente (0-1) con ocurrencias.

## Que NO incluye (declarado honestamente)
Proyeccion a 2050 (no ejecutada/validada) y validacion temporal por hindcasting.
Ver limitaciones en informe_modelo.md y lista_auditoria.md.
""" % n_mapas
    (dest / "00_LEEME.md").write_text(txt, encoding="utf-8")


def main() -> None:
    if ENTREGA.exists():
        shutil.rmtree(ENTREGA)
    (ENTREGA / "01_informes").mkdir(parents=True)
    (ENTREGA / "02_base_de_datos").mkdir()
    (ENTREGA / "03_metricas").mkdir()
    (ENTREGA / "04_mapas").mkdir()

    csv, xlsx = regenerar_base_datos()

    # Informes
    for d in ["informe_modelo.md", "proceso_completo.md", "lista_auditoria.md",
              "metricas_explicadas.md"]:
        src = ROOT / "docs" / d
        if src.exists():
            shutil.copy2(src, ENTREGA / "01_informes" / d)

    # Base de datos
    shutil.copy2(xlsx, ENTREGA / "02_base_de_datos" / xlsx.name)
    shutil.copy2(csv, ENTREGA / "02_base_de_datos" / csv.name)

    # Metricas
    shutil.copy2(config.TABLES / "metrics_all.csv", ENTREGA / "03_metricas" / "metrics_all.csv")

    # Mapas (PNG de idoneidad presente)
    n = 0
    for png in sorted(config.FIGURES.glob("*_present.png")):
        shutil.copy2(png, ENTREGA / "04_mapas" / png.name)
        n += 1

    escribir_indice(ENTREGA, n)

    # Comprimir
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(ENTREGA.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(ENTREGA.parent))

    size_mb = ZIP_PATH.stat().st_size / 1e6
    n_files = sum(1 for _ in ENTREGA.rglob("*") if _.is_file())
    log.info("ZIP listo: %s (%.1f MB, %d archivos, %d mapas)", ZIP_PATH, size_mb, n_files, n)


if __name__ == "__main__":
    main()
