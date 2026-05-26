"""
quemar_background_inservible.py — Poda del background no-hábitat.

Diagnóstico: el target-group background quedó casi uniforme sobre toda la tierra
(el suavizado de Laplace en 04_extraccion.py aplastó la señal de esfuerzo GBIF),
de modo que ~45% de los puntos de background caían en zonas polares/glaciales
(Antártida, Ártico, Siberia) donde ninguna de estas especies árido/templadas
podría registrarse. Eso infla artificialmente la discriminación de los modelos.

Acción: eliminar ("quemar") las filas de BACKGROUND que son no-hábitat según un
criterio transparente y ligado a los datos (ninguna presencia cae en esos rangos):

    Quemar background si  bio1 < -5 °C   (boreal/polar/glacial)
                     o    |lat| > 55°    (fuera del rango realizado + margen)
                     o    bio4 == 0      (estacionalidad térmica nula = artefacto)

Las PRESENCIAS nunca se tocan (todas son registros GBIF válidos: bio1 ∈ [-? , ],
lat ∈ [-42.9, 40.9]).

Reversibilidad: las filas quemadas se guardan en
    data/processed/species_datasets/_quemado/{slug}_quemado.parquet
y el dataset original siempre puede regenerarse con 04_extraccion.py.

Salidas:
  - Sobrescribe cada {slug}.parquet sin el background inservible.
  - Regenera outputs/tables/base_de_datos_completa.{csv,xlsx}.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config
import utils

logger = utils.get_logger("quemar_bg")

BURN_DIR = config.SPECIES_DATASETS / "_quemado"

# Umbrales (ligados a los datos: ninguna presencia los viola)
BIO1_MIN = -5.0      # °C: por debajo = boreal/polar/glacial
LAT_ABS_MAX = 55.0   # rango realizado de presencias ≈ ±43°, +margen


def burn_mask(df: pd.DataFrame) -> np.ndarray:
    """True = fila a quemar (solo aplica a background)."""
    is_bg = df["presence"].to_numpy() == 0
    bad_clima = df["bio1"].to_numpy() < BIO1_MIN
    bad_lat = df["lat"].to_numpy(float).__abs__() > LAT_ABS_MAX
    bad_seas = df["bio4"].to_numpy() == 0
    return is_bg & (bad_clima | bad_lat | bad_seas)


def main() -> None:
    utils.ensure_dirs(BURN_DIR, config.TABLES)
    parquets = sorted(
        p for p in config.SPECIES_DATASETS.glob("*.parquet")
        if not p.stem.endswith("_cv_preds")
    )

    combined: list[pd.DataFrame] = []
    tot_before = tot_after = tot_burned = 0

    logger.info("%-26s %8s %8s %8s %8s", "especie", "bg_antes", "quemado", "bg_desp", "%quem")
    for pq in parquets:
        slug = pq.stem
        df = pd.read_parquet(pq)
        n_bg0 = int((df["presence"] == 0).sum())

        mask = burn_mask(df)
        burned = df[mask].copy()
        kept = df[~mask].reset_index(drop=True)

        # Guardar lo quemado (reversibilidad) y sobrescribir dataset
        if len(burned):
            burned.to_parquet(BURN_DIR / f"{slug}_quemado.parquet", index=False)
        kept.to_parquet(pq, index=False)

        n_bg1 = int((kept["presence"] == 0).sum())
        logger.info("%-26s %8d %8d %8d %7.1f%%",
                    slug, n_bg0, int(mask.sum()), n_bg1,
                    100 * mask.sum() / max(1, n_bg0))

        tot_before += n_bg0; tot_after += n_bg1; tot_burned += int(mask.sum())

        export = kept.copy(); export.insert(0, "slug", slug)
        combined.append(export)

    all_df = pd.concat(combined, ignore_index=True)
    csv_out = config.TABLES / "base_de_datos_completa.csv"
    xlsx_out = config.TABLES / "base_de_datos_completa.xlsx"
    all_df.to_csv(csv_out, index=False)
    all_df.to_excel(xlsx_out, index=False, sheet_name="base_datos", engine="openpyxl")

    logger.info("=" * 70)
    logger.info("Background: %d → %d  (quemados %d, %.1f%%)",
                tot_before, tot_after, tot_burned,
                100 * tot_burned / max(1, tot_before))
    logger.info("Presencias intactas: %d", int((all_df["presence"] == 1).sum()))
    logger.info("Base actualizada: %d filas → %s y %s",
                len(all_df), csv_out.name, xlsx_out.name)
    logger.info("Filas quemadas respaldadas en: %s", BURN_DIR)


if __name__ == "__main__":
    main()
