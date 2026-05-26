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
# Acotamiento geográfico — Iteración 3
# ----------------------------------------------------------------------------
# Las especies son endémicas chilenas: separar su nicho de "todo el planeta"
# inflaba la discriminación (AUC ~0.99 triviales). El modelo se CALIBRA solo con
# background dentro de Chile (área accesible) y el mapa de idoneidad se PROYECTA
# y RECORTA a Sudamérica. Bboxes en grados (min_lon, min_lat, max_lon, max_lat).
CALIBRATION_COUNTRY = "Chile"                     # país que define el área de calibración (background + presencias)
CALIBRATION_BBOX = (-76.0, -56.0, -66.0, -17.0)   # Chile continental — fallback si no carga el polígono Natural Earth
PREDICTION_BBOX = (-82.0, -56.0, -34.0, 13.0)     # Sudamérica — extensión de predicción y recorte de mapas

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
GCMS = ["ACCESS-CM2", "IPSL-CM6A-LR", "MPI-ESM1-2-HR", "MRI-ESM2-0"]  # ≥4 GCMs (GFDL-ESM4 no tiene ssp245 a 2.5m en WorldClim)
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


# ----------------------------------------------------------------------------
# Contrato de artefactos entre etapas (fuente de verdad versionada)
# ----------------------------------------------------------------------------

ENSEMBLE_ARTIFACT_SCHEMA = """
Contrato canónico de artefactos del pipeline SDM (global-flora-sdm)
====================================================================

1. config.ENSEMBLE_MODELS/{slug}.joblib  — PRODUCTOR: 05_modelado.py
                                          CONSUMIDORES: 06, 07, 08
   Tipo: dict[str, Any]
   Claves obligatorias:
     'especie'             : str
         Nombre científico de la especie.
     'selected_predictors' : list[str]
         Orden exacto de features para TODOS los modelos.
     'scaler'              : sklearn.preprocessing.StandardScaler | None
         Ajustado sobre X_all (presencias + background) antes de CV.
     'scaled_algos'        : list[str]
         Algoritmos que reciben input escalado (p. ej. ['glm','gam','maxent']).
         Fuente de verdad: aplicar scaler solo a estos en predicción.
     'models'              : dict[str, estimador_ajustado]
         Claves permitidas: 'glm', 'gam', 'rf', 'gbm', 'maxent'.
         Entrenados con 100% de los datos tras CV.
     'cv_tss'              : dict[str, float]
         TSS medio (entre folds) por algoritmo.
     'tss_per_fold'        : dict[str, list[float]]
         TSS por fold por algoritmo (longitud = N_CV_FOLDS válidos).
     'auc_per_fold'        : dict[str, list[float]]
         AUC-ROC por fold por algoritmo (sklearn.metrics.roc_auc_score).
     'tss_weights'         : dict[str, float]
         Pesos ensemble normalizados. Peso = 0 si cv_tss < TSS_MIN_ENSEMBLE.
     'thresholds'          : dict[str, float]
         Subclaves: 'maxTSS', 'p10', 'min_train'.
         Calculados sobre las predicciones del ensemble en training completo.
     'train_env'           : pandas.DataFrame
         Columnas == selected_predictors. Una fila por registro de
         entrenamiento (presencias + background). Referencia para MESS.

2. config.SPECIES_DATASETS/{slug}_cv_preds.parquet  — PRODUCTOR: 05_modelado.py
                                                       CONSUMIDOR: 06_validacion.py
   Columnas exactas:
     'presence'    : int (0/1)
     'cv_fold'     : int
     'glm'         : float (probabilidad OOF, NaN si no disponible)
     'gam'         : float (ídem)
     'rf'          : float (ídem)
     'gbm'         : float (ídem)
     'maxent'      : float (ídem)
     'ensemble'    : float (probabilidad OOF ponderada por tss_weights)
   Nota: solo se incluyen las columnas de algoritmos efectivamente ajustados.

3. config.SPECIES_DATASETS/{slug}.parquet  — PRODUCTOR: 04_extraccion.py
   Columnas incluidas:
     'especie'     : str
     'presence'    : int (0/1)
     'lon', 'lat'  : float
     <PREDICTORS>  : float (todas las variables, incluso las no seleccionadas)
     'cv_fold'     : int

4. Selección de especies modelables (04_extraccion.py):
   Leer columna 'grupo' del GPKG config.OCCURRENCES_CLEAN (POST-thinning).
   Modelar grupo "A" y "B". NO usar config.modelable_species(utils.species_counts())
   porque ese método usa conteos pre-thinning.
"""


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
