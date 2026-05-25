"""
config.py — Contrato compartido del pipeline SDM (global-flora-sdm).

Punto único de verdad para rutas, constantes y parámetros usado por TODAS
las etapas (01–08). Cambiar un parámetro aquí debe propagarse a todo el pipeline.

Referencia metodológica: docs/proyecto_sdm.md
"""
from __future__ import annotations

from pathlib import Path

# ----------------------------------------------------------------------------
# Rutas (todas derivadas de la raíz del proyecto, sin rutas absolutas hardcoded)
# ----------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]

DATA = ROOT / "data"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
MODELING = DATA / "modeling"
OUTPUTS = ROOT / "outputs"

OCCURRENCES_XLSX = RAW / "gbif_distribucion_especies.xlsx"
OCCURRENCES_SHEET = "Registros GBIF"

WORLDCLIM_PRESENT = RAW / "worldclim_present"   # bioclim 2.5' + elevation (presente)
WORLDCLIM_FUTURE = RAW / "worldclim_future"     # CMIP6 por GCM × SSP

OCCURRENCES_CLEAN = PROCESSED / "ocurrencias_limpias.gpkg"
RASTERS_ALIGNED = PROCESSED / "rasters_aligned"     # bioclim + topo alineados (presente)
SPECIES_DATASETS = PROCESSED / "species_datasets"   # un .parquet por especie
ENSEMBLE_MODELS = MODELING / "ensemble_models"      # joblib por especie

FIGURES = OUTPUTS / "figures"
MAPS = OUTPUTS / "maps"
TABLES = OUTPUTS / "tables"

# ----------------------------------------------------------------------------
# Variables predictoras — Iteración 1
# ----------------------------------------------------------------------------
# Climáticas (WorldClim v2.1 bioclim). El doc de diseño dice "8" en la tabla
# resumen pero la tabla detallada lista 10 — usamos las 10 detalladas.
BIOCLIM_VARS = ["bio1", "bio4", "bio5", "bio6", "bio7",
                "bio10", "bio11", "bio12", "bio15", "bio17"]

# Topográficas derivadas del DEM de WorldClim
TOPO_VARS = ["elevation", "slope", "northness", "eastness"]

PREDICTORS = BIOCLIM_VARS + TOPO_VARS

# ----------------------------------------------------------------------------
# Resolución y proyecciones
# ----------------------------------------------------------------------------
WORLDCLIM_RES = "2.5m"          # 2.5 arc-min (~5 km) — etiqueta de WorldClim
CRS_GEO = "EPSG:4326"           # grilla de modelado (lat/lon)
CRS_EQUAL_AREA = "ESRI:54009"   # Mollweide — para cálculo de áreas (km²)

# ----------------------------------------------------------------------------
# Limpieza de ocurrencias (Etapa 1)
# ----------------------------------------------------------------------------
MAX_COORD_UNCERTAINTY_M = 10_000  # a 2.5' (~5 km) descartar > 10 km
CENTROID_TOLERANCE_KM = 1.0       # tolerancia para detectar centroides admin
THINNING_PER_CELL = 1             # 1 punto por celda raster

# ----------------------------------------------------------------------------
# Dataset modelable (Etapa 4)
# ----------------------------------------------------------------------------
N_BACKGROUND = 20_000             # puntos background globales (rango 10k–50k)
TARGET_GROUP_BACKGROUND = True    # sesgo de muestreo proporcional a esfuerzo GBIF
VIF_THRESHOLD = 10.0
CORR_THRESHOLD = 0.7              # eliminar |r| > 0.7
RANDOM_SEED = 42

# ----------------------------------------------------------------------------
# Validación cruzada espacial (Etapas 4–6)
# ----------------------------------------------------------------------------
SPATIAL_BLOCK_KM = 750            # bloques de 500–1000 km
N_CV_FOLDS = 5
TSS_MIN_ENSEMBLE = 0.5            # modelos con TSS < 0.5 excluidos del ensemble

# Umbrales para mapas binarios (reportar al menos 2)
THRESHOLDS = ["maxTSS", "p10", "min_train"]  # maxTSS, 10th percentile, min training presence

# ----------------------------------------------------------------------------
# Forecasting a 2050 (Etapas 2-futuro y 7)
# ----------------------------------------------------------------------------
GCMS = ["GFDL-ESM4", "IPSL-CM6A-LR", "MPI-ESM1-2-HR", "MRI-ESM2-0"]  # ≥4 GCMs
SSPS = ["ssp245", "ssp585"]        # SSP2-4.5 (medio) y SSP5-8.5 (alto)
FUTURE_PERIOD = "2041-2060"        # centrado en 2050

# ----------------------------------------------------------------------------
# Grupos de especies (estrategia diferenciada A/B/C, ver doc §"Estrategia")
# ----------------------------------------------------------------------------
MIN_RECORDS_TO_MODEL = 50          # < 50 → Grupo C (no modelar individualmente)

GROUP_A_COSMOPOLITAN = ["Schinus areira", "Atriplex semibaccata"]

# Especies truncadas en el techo de descarga GBIF (3.000 registros exactos).
# Para análisis riguroso: rehacer descarga particionada vía API GBIF.
TRUNCATED_SPECIES = ["Atriplex semibaccata", "Schinus areira", "Nolana divaricata"]


def classify_species(counts: dict[str, int]) -> dict[str, str]:
    """Asigna cada especie a un grupo A/B/C a partir de sus conteos.

    A = cosmopolita/introducida; B = endémica con datos suficientes (>=50);
    C = pocos registros (<50, no modelar individualmente).
    """
    groups: dict[str, str] = {}
    for sp, n in counts.items():
        if sp in GROUP_A_COSMOPOLITAN:
            groups[sp] = "A"
        elif n < MIN_RECORDS_TO_MODEL:
            groups[sp] = "C"
        else:
            groups[sp] = "B"
    return groups


# Especies modelables individualmente (A + B). C se excluye del modelado individual.
def modelable_species(counts: dict[str, int]) -> list[str]:
    g = classify_species(counts)
    return [sp for sp, grp in g.items() if grp in ("A", "B")]
