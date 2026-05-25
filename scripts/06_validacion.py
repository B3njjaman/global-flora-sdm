"""
06_validacion.py — Etapa 5: Evaluación y métricas del ensemble SDM.

Calcula y exporta métricas de validación para cada especie modelada,
cubriendo las 7 categorías del documento de diseño §5.1–5.8:
  1. Discriminación:      TSS, AUC-ROC, AUC-PR, F1 (spatial CV out-of-fold)
  2. Calibración:         Brier score, slope/intercept de calibración
  3. Solo-presencia:      Boyce index (CBI), OR10
  4. Robustez espacial:   TSS y AUC media ± SD entre folds
  5. Extrapolación:       MESS — % puntos/píxeles fuera del espacio de entrenamiento
  6. Ensemble:            SD entre algoritmos (incertidumbre), acuerdo binario
  7. Umbrales:            maxTSS, p10 (10th-percentile training presence)

Entradas (por especie <slug>):
  - config.SPECIES_DATASETS / <slug>.parquet            (dataset completo: presencias + background)
  - config.SPECIES_DATASETS / <slug>_predictors.json    (lista de predictores usados)
  - config.ENSEMBLE_MODELS  / <slug>.joblib             (artefacto del ensemble, ver formato abajo)
  - config.SPECIES_DATASETS / <slug>_cv_preds.parquet   (predicciones out-of-fold por algo + ensemble, opcional)

Salidas:
  - config.TABLES / metrics_<slug>.csv   (métricas de la especie)
  - config.TABLES / metrics_all.csv      (consolidado de todas las especies)
  - config.FIGURES / calib_<slug>.png    (curva de calibración, opcional si matplotlib disponible)

Formato esperado del artefacto .joblib
---------------------------------------
El joblib guardado por 05_modelado.py debe ser un dict con al menos:

    {
        "models":            {"glm": <estimador>, "gam": ..., "rf": ..., "gbm": ..., "maxent": ...},
        "tss_weights":       {"glm": 0.3, ...},        # pesos TSS del CV
        "selected_predictors": ["bio1", "bio4", ...],  # lista de columnas predictoras
        "scaled_algos":      ["glm", "gam", "maxent"], # algoritmos que reciben input escalado
        "scaler":            <StandardScaler ajustado>,
        "thresholds": {
            "maxTSS": 0.42,
            "p10":    0.18,
            "min_train": 0.05,
        },
        "tss_per_fold": {          # TSS por fold de CV
            "glm":  [0.61, 0.55, ...],
        },
        "auc_per_fold": {          # AUC-ROC por fold de CV
            "glm":  [0.82, 0.79, ...],
        },
        "train_env":  <pandas.DataFrame con columnas == selected_predictors>,
    }

Si alguna clave opcional falta, el script la calcula desde cv_preds.parquet
(si existe) o emite advertencia y omite esa sub-métrica.

Columnas esperadas en <slug>.parquet
--------------------------------------
  - presence      : int  (1 = presencia, 0 = background)
  - <predictores> : float
  - cv_fold       : int  (bloque CV)
  - lon, lat      : float (opcional, para variabilidad regional)
  - pais / region : str  (opcional)

Columnas esperadas en <slug>_cv_preds.parquet
----------------------------------------------
  - presence  : int
  - cv_fold   : int
  - glm, gam, rf, gbm, maxent : float  (probabilidades OOF por algoritmo)
  - ensemble  : float

Funciones públicas reutilizables (importadas por Etapas 7/8)
-------------------------------------------------------------
  mess(reference_points, query_points)  -> np.ndarray
  boyce_index(suitability, presence_mask, n_bins, bin_width) -> float
"""

from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.linear_model import LinearRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    f1_score,
    roc_auc_score,
)

import config
import utils

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logger = utils.get_logger("06_validacion")

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
ALGORITHM_COLS = ["glm", "gam", "rf", "gbm", "maxent"]
ENSEMBLE_COL = "ensemble"


# ===========================================================================
# SECCIÓN 1 — MESS (Multivariate Environmental Similarity Surface)
# Elith et al. 2010, Methods Ecol. Evol.
# Función pública: importada también por 07_forecast.py y 08_mapas.py
# ===========================================================================

def mess(
    reference_points: np.ndarray,
    query_points: np.ndarray,
) -> np.ndarray:
    """Calcula MESS (Multivariate Environmental Similarity Surface).

    Para cada punto de consulta, MESS = mínimo de la similitud de Elith
    sobre todas las variables predictoras.  Un valor negativo indica
    extrapolación (novedad ambiental respecto del rango de entrenamiento).

    Implementa el algoritmo original de Elith et al. (2010) eq. 1–3:
      - Si p_i <= f_i:  MESS_i = (p_i - min_i) / (max_i - min_i) * 100 - 100
        → equivalente: MESS_i = 2 * f_i - 100  cuando p_i = min_i → -100
        Versión exacta: MESS_i = (f_i - 0) * 100 – 100  si f_i es el percentil
        de p_i en la referencia.  Usamos la formulación directa sobre percentiles.

    Formulación exacta usada (Elith 2010, ecuación en el apéndice):
      Sea f_i = proporción de puntos de referencia con valor <= p_i (ECDF).
      - Si f_i = 0  o  p_i < min_ref:  S_i = (p_i - min_ref) / (max_ref - min_ref) * 100
      - Si 0 < f_i <= 0.5:             S_i = 2 * f_i * 100
      - Si 0.5 < f_i < 1:              S_i = (1 - f_i) * 2 * 100
      - Si f_i = 1  o  p_i > max_ref:  S_i = (p_i - max_ref) / (max_ref - min_ref) * 100  (negativo)
    MESS(punto) = min(S_i sobre todas las variables)

    Parámetros
    ----------
    reference_points : ndarray, shape (n_ref, n_vars)
        Valores de las variables en los puntos de entrenamiento / referencia.
    query_points : ndarray, shape (n_query, n_vars)
        Valores de las variables en los puntos a evaluar (nueva área o futuro).

    Retorna
    -------
    mess_values : ndarray, shape (n_query,)
        Valores MESS por punto. Negativos = extrapolación.

    Notas
    -----
    - Las columnas de reference_points y query_points deben estar en el mismo
      orden y representar las mismas variables.
    - NaN en query_points propaga NaN en el resultado para esa fila.
    - Rango de salida: [-100, 100]; fuera si la variable tiene rango de ref = 0.
    """
    reference_points = np.asarray(reference_points, dtype=float)
    query_points = np.asarray(query_points, dtype=float)

    if reference_points.ndim == 1:
        reference_points = reference_points[:, np.newaxis]
    if query_points.ndim == 1:
        query_points = query_points[:, np.newaxis]

    n_ref, n_vars = reference_points.shape
    n_query = query_points.shape[0]

    if query_points.shape[1] != n_vars:
        raise ValueError(
            f"reference_points tiene {n_vars} variables pero "
            f"query_points tiene {query_points.shape[1]}."
        )

    ref_min = np.nanmin(reference_points, axis=0)   # (n_vars,)
    ref_max = np.nanmax(reference_points, axis=0)   # (n_vars,)
    ref_range = ref_max - ref_min                    # (n_vars,)

    # Similarity matrix: shape (n_query, n_vars)
    similarity = np.full((n_query, n_vars), np.nan)

    for j in range(n_vars):
        ref_col = reference_points[:, j]
        ref_col_clean = ref_col[~np.isnan(ref_col)]
        q_col = query_points[:, j]
        r = ref_range[j]

        for i in range(n_query):
            p = q_col[i]
            if np.isnan(p):
                similarity[i, j] = np.nan
                continue

            # Proporción de puntos de referencia con valor <= p (ECDF en p)
            f = np.sum(ref_col_clean <= p) / len(ref_col_clean)

            if f == 0 or p < ref_min[j]:
                # Extrapolación por debajo del mínimo
                if r == 0:
                    similarity[i, j] = 0.0
                else:
                    similarity[i, j] = (p - ref_min[j]) / r * 100.0
            elif f <= 0.5:
                similarity[i, j] = 2.0 * f * 100.0
            elif f < 1.0:
                similarity[i, j] = (1.0 - f) * 2.0 * 100.0
            else:
                # Extrapolación por encima del máximo
                if r == 0:
                    similarity[i, j] = 0.0
                else:
                    similarity[i, j] = (p - ref_max[j]) / r * 100.0

    # MESS = mínimo de similitud variable a variable
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mess_values = np.nanmin(similarity, axis=1)

    return mess_values


# ===========================================================================
# SECCIÓN 2 — Boyce index (CBI)
# Hirzel et al. 2006, Ecol. Modell.
# Función pública: importada también por 08_mapas.py
# ===========================================================================

def boyce_index(
    suitability: np.ndarray,
    presence_mask: np.ndarray,
    n_bins: int = 20,
    bin_width: float | None = None,
) -> float:
    """Calcula el Continuous Boyce Index (CBI) de Hirzel et al. (2006).

    El CBI mide si los sitios de presencia se concentran en áreas de alta
    idoneidad predicha.  Es la métrica más honesta para datos solo-presencia
    porque no requiere ausencias verdaderas.

    Algoritmo:
      1. Definir bins de idoneidad en [0, 1] con ancho fijo (bin_width) o
         distribución en n_bins iguales.
      2. Por bin: calcular ratio P/E = (proporción de presencias en el bin) /
         (proporción del espacio predicho en el bin).
      3. CBI = correlación de Spearman entre el valor medio del bin y P/E,
         calculada solo sobre bins con al menos una observación.

    Parámetros
    ----------
    suitability : ndarray, shape (n_points,)
        Idoneidad predicha en [0, 1] para TODOS los puntos (presencias + background).
    presence_mask : ndarray of bool/int, shape (n_points,)
        True / 1 para presencias, False / 0 para background.
    n_bins : int
        Número de bins deslizantes si bin_width es None.  Por defecto 20.
    bin_width : float o None
        Ancho del bin en unidades de idoneidad.  Si se provee, n_bins se ignora.
        Hirzel et al. recomiendan bin_width = 0.1.

    Retorna
    -------
    cbi : float
        CBI en [-1, 1]. NaN si hay insuficientes bins válidos.

    Notas
    -----
    - Requiere al menos 5 bins con P/E > 0 para una correlación estable.
    - Implementación propia (no depende de elapid); compatible con la de elapid.
    - Bins deslizantes (moving window) según Hirzel 2006.
    """
    suitability = np.asarray(suitability, dtype=float)
    presence_mask = np.asarray(presence_mask, dtype=bool)

    # Validar rangos
    valid = ~np.isnan(suitability)
    suitability = suitability[valid]
    presence_mask = presence_mask[valid]

    if len(suitability) == 0 or presence_mask.sum() == 0:
        logger.warning("boyce_index: sin datos válidos o sin presencias.")
        return np.nan

    # Clips defensivos
    suitability = np.clip(suitability, 0.0, 1.0)

    n_total = len(suitability)
    n_presence = presence_mask.sum()

    # Definir centros de bins
    if bin_width is not None:
        centers = np.arange(bin_width / 2, 1.0, bin_width)
        half = bin_width / 2
        edges = [(c - half, c + half) for c in centers]
    else:
        # Bins deslizantes equiespaciados
        bin_width_calc = 1.0 / n_bins
        centers = np.linspace(bin_width_calc / 2, 1.0 - bin_width_calc / 2, n_bins)
        half = bin_width_calc / 2
        edges = [(c - half, c + half) for c in centers]

    pe_ratios = []
    valid_centers = []

    for center, (lo, hi) in zip(centers, edges):
        in_bin = (suitability >= lo) & (suitability <= hi)
        n_bin_total = in_bin.sum()
        n_bin_pres = (in_bin & presence_mask).sum()

        if n_bin_total == 0:
            continue

        # P = proporción de presencias en el bin / presencias totales
        p_pres = n_bin_pres / n_presence if n_presence > 0 else 0.0
        # E = proporción del espacio en el bin / espacio total
        p_space = n_bin_total / n_total

        if p_space == 0:
            continue

        pe = p_pres / p_space
        pe_ratios.append(pe)
        valid_centers.append(center)

    if len(valid_centers) < 5:
        logger.warning(
            "boyce_index: solo %d bins válidos (mínimo 5). CBI = NaN.", len(valid_centers)
        )
        return np.nan

    # Correlación de Spearman entre centros y ratios P/E
    centers_arr = np.array(valid_centers)
    pe_arr = np.array(pe_ratios)

    # Spearman manual (rank correlation) para no depender de scipy
    rank_c = _rank_array(centers_arr)
    rank_pe = _rank_array(pe_arr)
    n = len(rank_c)
    cbi = float(np.corrcoef(rank_c, rank_pe)[0, 1])

    return cbi


def _rank_array(arr: np.ndarray) -> np.ndarray:
    """Rango promedio para calcular correlación de Spearman sin scipy."""
    n = len(arr)
    order = np.argsort(arr)
    ranks = np.empty(n, dtype=float)
    ranks[order] = np.arange(1, n + 1)
    # Empates: asignar rango promedio
    _, inv, counts = np.unique(arr, return_inverse=True, return_counts=True)
    for idx, cnt in enumerate(counts):
        if cnt > 1:
            tied_ranks = ranks[inv == idx]
            ranks[inv == idx] = tied_ranks.mean()
    return ranks


# ===========================================================================
# SECCIÓN 3 — Métricas de discriminación
# ===========================================================================

def _tss(y_true: np.ndarray, y_prob: np.ndarray, threshold: float) -> float:
    """TSS = sensibilidad + especificidad − 1 a un umbral dado."""
    y_pred = (y_prob >= threshold).astype(int)
    tp = ((y_pred == 1) & (y_true == 1)).sum()
    tn = ((y_pred == 0) & (y_true == 0)).sum()
    fp = ((y_pred == 1) & (y_true == 0)).sum()
    fn = ((y_pred == 0) & (y_true == 1)).sum()
    sensitivity = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    specificity = tn / (tn + fp) if (tn + fp) > 0 else 0.0
    return float(sensitivity + specificity - 1.0)


def _max_tss_threshold(y_true: np.ndarray, y_prob: np.ndarray) -> tuple[float, float]:
    """Devuelve (umbral_maxTSS, TSS_máximo) barriendo thresholds."""
    thresholds = np.unique(y_prob)
    best_tss = -999.0
    best_thr = 0.5
    for thr in thresholds:
        t = _tss(y_true, y_prob, thr)
        if t > best_tss:
            best_tss = t
            best_thr = thr
    return float(best_thr), float(best_tss)


def _p10_threshold(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    """10th percentile de las probabilidades predichas EN presencias de entrenamiento."""
    pres_probs = y_prob[y_true == 1]
    if len(pres_probs) == 0:
        return 0.0
    return float(np.percentile(pres_probs, 10))


def _omission_rate(y_true: np.ndarray, y_prob: np.ndarray, threshold: float) -> float:
    """Tasa de omisión (falsos negativos / presencias totales) a un umbral."""
    pres = y_true == 1
    n_pres = pres.sum()
    if n_pres == 0:
        return np.nan
    omitted = ((y_prob[pres] < threshold)).sum()
    return float(omitted / n_pres)


def compute_discrimination_metrics(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    threshold: float,
) -> dict[str, float]:
    """Calcula TSS, AUC-ROC, AUC-PR y F1 para un vector de predicciones.

    Parámetros
    ----------
    y_true   : etiquetas binarias (1 = presencia, 0 = background).
    y_prob   : probabilidades predichas en [0, 1].
    threshold: umbral de binarización para TSS y F1.

    Retorna
    -------
    dict con métricas como floats (NaN si no se puede calcular).
    """
    metrics: dict[str, float] = {}
    y_true = np.asarray(y_true, dtype=int)
    y_prob = np.asarray(y_prob, dtype=float)

    # AUC-ROC
    try:
        metrics["auc_roc"] = float(roc_auc_score(y_true, y_prob))
    except Exception:
        metrics["auc_roc"] = np.nan

    # AUC-PR
    try:
        metrics["auc_pr"] = float(average_precision_score(y_true, y_prob))
    except Exception:
        metrics["auc_pr"] = np.nan

    # TSS
    metrics["tss"] = _tss(y_true, y_prob, threshold)

    # F1
    y_pred = (y_prob >= threshold).astype(int)
    try:
        metrics["f1"] = float(f1_score(y_true, y_pred, zero_division=0))
    except Exception:
        metrics["f1"] = np.nan

    return metrics


# ===========================================================================
# SECCIÓN 4 — Métricas de calibración
# ===========================================================================

def compute_calibration_metrics(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    n_bins: int = 10,
) -> dict[str, float]:
    """Calcula Brier score y slope/intercept de calibración.

    Parámetros
    ----------
    y_true  : etiquetas binarias.
    y_prob  : probabilidades predichas.
    n_bins  : número de bins para la curva de calibración (slope/intercept).

    Retorna
    -------
    dict: brier_score, calib_slope, calib_intercept.
    """
    y_true = np.asarray(y_true, dtype=int)
    y_prob = np.asarray(y_prob, dtype=float)
    metrics: dict[str, float] = {}

    # Brier score
    try:
        metrics["brier_score"] = float(brier_score_loss(y_true, y_prob))
    except Exception:
        metrics["brier_score"] = np.nan

    # Slope e intercept de calibración (regresión lineal sobre curva de calibración)
    try:
        prob_true, prob_pred = calibration_curve(
            y_true, y_prob, n_bins=n_bins, strategy="uniform"
        )
        if len(prob_pred) >= 2:
            lr = LinearRegression().fit(prob_pred.reshape(-1, 1), prob_true)
            metrics["calib_slope"] = float(lr.coef_[0])
            metrics["calib_intercept"] = float(lr.intercept_)
        else:
            metrics["calib_slope"] = np.nan
            metrics["calib_intercept"] = np.nan
    except Exception:
        metrics["calib_slope"] = np.nan
        metrics["calib_intercept"] = np.nan

    return metrics


# ===========================================================================
# SECCIÓN 5 — Métricas de robustez espacial (por fold)
# ===========================================================================

def compute_spatial_cv_metrics(
    cv_preds: pd.DataFrame,
    threshold: float,
) -> dict[str, float]:
    """Calcula media ± SD de TSS y AUC entre folds de validación espacial.

    Parámetros
    ----------
    cv_preds  : DataFrame con columnas presencia, fold, pred_ensemble
                (y opcionalmente pred_glm … pred_maxent).
    threshold : umbral para binarización (maxTSS del ensemble completo).

    Retorna
    -------
    dict con tss_cv_mean, tss_cv_std, auc_cv_mean, auc_cv_std por algoritmo
    y ensemble.
    """
    metrics: dict[str, float] = {}
    folds = cv_preds["cv_fold"].unique()

    algo_cols = [c for c in ALGORITHM_COLS if c in cv_preds.columns]
    all_cols = algo_cols + ([ENSEMBLE_COL] if ENSEMBLE_COL in cv_preds.columns else [])

    for col in all_cols:
        key = col  # column names are already the algorithm names (no 'pred_' prefix)
        tss_folds = []
        auc_folds = []
        for fold in folds:
            fold_df = cv_preds[cv_preds["cv_fold"] == fold]
            yt = fold_df["presence"].values
            yp = fold_df[col].values

            # Necesitamos al menos ambas clases por fold
            if len(np.unique(yt)) < 2:
                continue
            tss_folds.append(_tss(yt, yp, threshold))
            try:
                auc_folds.append(float(roc_auc_score(yt, yp)))
            except Exception:
                pass

        if tss_folds:
            metrics[f"tss_{key}_mean"] = float(np.mean(tss_folds))
            metrics[f"tss_{key}_std"] = float(np.std(tss_folds))
        if auc_folds:
            metrics[f"auc_{key}_mean"] = float(np.mean(auc_folds))
            metrics[f"auc_{key}_std"] = float(np.std(auc_folds))

    return metrics


def compute_regional_variability(
    cv_preds: pd.DataFrame,
    threshold: float,
) -> pd.DataFrame | None:
    """Calcula TSS y AUC por región/continente si la columna existe.

    Busca columnas 'pais', 'region' o 'continente' en cv_preds.
    Retorna DataFrame con TSS y AUC por grupo, o None si no hay columna.
    """
    region_col = None
    for candidate in ("continente", "region", "pais"):
        if candidate in cv_preds.columns:
            region_col = candidate
            break

    if region_col is None or ENSEMBLE_COL not in cv_preds.columns:
        return None

    rows = []
    for region, grp in cv_preds.groupby(region_col):
        yt = grp["presence"].values
        yp = grp[ENSEMBLE_COL].values
        if len(np.unique(yt)) < 2 or len(yt) < 10:
            continue
        tss_val = _tss(yt, yp, threshold)
        try:
            auc_val = float(roc_auc_score(yt, yp))
        except Exception:
            auc_val = np.nan
        rows.append({region_col: region, "n": len(yt), "tss": tss_val, "auc": auc_val})

    return pd.DataFrame(rows) if rows else None


# ===========================================================================
# SECCIÓN 6 — Métricas del ensemble (incertidumbre y acuerdo)
# ===========================================================================

def compute_ensemble_metrics(
    cv_preds: pd.DataFrame,
    threshold: float,
) -> dict[str, float]:
    """Calcula SD entre algoritmos (incertidumbre) y acuerdo binario.

    Parámetros
    ----------
    cv_preds  : DataFrame con columnas pred_glm … pred_maxent.
    threshold : umbral para binarizar predicciones individuales.

    Retorna
    -------
    dict: algo_sd_mean, algo_sd_median, binary_agreement_mean,
          binary_agreement_5of5_pct.
    """
    metrics: dict[str, float] = {}
    algo_cols = [c for c in ALGORITHM_COLS if c in cv_preds.columns]

    if len(algo_cols) < 2:
        logger.warning("compute_ensemble_metrics: menos de 2 algoritmos disponibles.")
        return metrics

    preds_matrix = cv_preds[algo_cols].values  # shape (n, n_algo)

    # SD entre algoritmos por punto (proxy de incertidumbre)
    sd_per_point = np.std(preds_matrix, axis=1, ddof=1)
    metrics["algo_sd_mean"] = float(np.nanmean(sd_per_point))
    metrics["algo_sd_median"] = float(np.nanmedian(sd_per_point))

    # Acuerdo binario: cuántos modelos predicen presencia
    binary_matrix = (preds_matrix >= threshold).astype(int)
    agreement = binary_matrix.sum(axis=1)  # 0..n_algo
    n_algo = len(algo_cols)
    metrics["binary_agreement_mean"] = float(np.mean(agreement))
    metrics["binary_agreement_max_possible"] = float(n_algo)
    # % de puntos con acuerdo unánime (todos predicen presencia)
    unanimous = (agreement == n_algo).sum()
    metrics["binary_agreement_unanimous_pct"] = float(unanimous / len(agreement) * 100)

    return metrics


# ===========================================================================
# SECCIÓN 7 — MESS y extrapolación
# ===========================================================================

def compute_mess_metrics(
    train_df: pd.DataFrame,
    eval_df: pd.DataFrame,
    predictors: list[str],
) -> dict[str, float]:
    """Calcula MESS y estadísticas de extrapolación.

    Compara los puntos de evaluación (presencias + background del dataset
    completo) contra el espacio ambiental de entrenamiento.

    Parámetros
    ----------
    train_df   : DataFrame con columnas = predictores (puntos de entrenamiento).
    eval_df    : DataFrame con columnas = predictores (puntos a evaluar).
    predictors : lista de nombres de columnas predictoras.

    Retorna
    -------
    dict: mess_mean, mess_min, mess_pct_extrapolation, n_eval_points.
    """
    metrics: dict[str, float] = {}

    # Filtrar columnas disponibles en ambos DataFrames
    avail = [p for p in predictors if p in train_df.columns and p in eval_df.columns]
    if not avail:
        logger.warning("compute_mess_metrics: ningún predictor disponible en ambos DataFrames.")
        return metrics

    ref = train_df[avail].dropna().values
    query = eval_df[avail].values

    if len(ref) == 0 or len(query) == 0:
        return metrics

    logger.info(
        "  MESS: %d puntos referencia × %d puntos consulta × %d variables",
        len(ref), len(query), len(avail),
    )

    mess_vals = mess(ref, query)

    metrics["mess_mean"] = float(np.nanmean(mess_vals))
    metrics["mess_min"] = float(np.nanmin(mess_vals))
    metrics["mess_max"] = float(np.nanmax(mess_vals))
    metrics["mess_pct_extrapolation"] = float(
        np.nanmean(mess_vals < 0) * 100.0
    )
    metrics["n_mess_points"] = int(np.sum(~np.isnan(mess_vals)))

    return metrics


# ===========================================================================
# SECCIÓN 8 — Figura de calibración (opcional)
# ===========================================================================

def _save_calibration_figure(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    slug: str,
    algo_label: str = "ensemble",
    n_bins: int = 10,
) -> None:
    """Guarda curva de calibración como PNG en config.FIGURES.

    No aborta el pipeline si matplotlib no está disponible.
    """
    try:
        import matplotlib.pyplot as plt

        prob_true, prob_pred = calibration_curve(
            y_true, y_prob, n_bins=n_bins, strategy="uniform"
        )

        fig, ax = plt.subplots(figsize=(5, 5))
        ax.plot([0, 1], [0, 1], "k--", label="Perfecta")
        ax.plot(prob_pred, prob_true, "o-", label=algo_label)
        ax.set_xlabel("Probabilidad predicha")
        ax.set_ylabel("Fracción observada de presencias")
        ax.set_title(f"Calibración — {slug} ({algo_label})")
        ax.legend()
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        fig.tight_layout()
        out = config.FIGURES / f"calib_{slug}.png"
        fig.savefig(out, dpi=120)
        plt.close(fig)
        logger.info("  Figura de calibración guardada: %s", out.name)
    except ImportError:
        logger.warning("  matplotlib no disponible; figura de calibración omitida.")
    except Exception as exc:
        logger.warning("  No se pudo guardar figura de calibración: %s", exc)


# ===========================================================================
# SECCIÓN 9 — Carga de artefactos
# ===========================================================================

def _load_joblib(path: Path) -> dict[str, Any] | None:
    """Carga artefacto .joblib y devuelve dict, o None si falla."""
    try:
        import joblib

        obj = joblib.load(path)
        if not isinstance(obj, dict):
            logger.warning(
                "El artefacto %s no es un dict (tipo: %s). "
                "Se asume formato alternativo; algunas métricas podrían no calcularse.",
                path.name, type(obj).__name__,
            )
            return {"_raw": obj}
        return obj
    except ImportError:
        logger.error("joblib no disponible. Instalar: pip install joblib")
        return None
    except FileNotFoundError:
        logger.warning("Artefacto no encontrado: %s", path)
        return None
    except Exception as exc:
        logger.error("Error al cargar %s: %s", path, exc)
        return None


def _load_species_data(slug: str) -> tuple[pd.DataFrame | None, list[str]]:
    """Carga el parquet de la especie y la lista de predictores.

    Retorna (df, predictors).  df es None si no se puede cargar.
    """
    parquet_path = config.SPECIES_DATASETS / f"{slug}.parquet"
    json_path = config.SPECIES_DATASETS / f"{slug}_predictors.json"

    if not parquet_path.exists():
        logger.error("Dataset no encontrado: %s", parquet_path)
        return None, []

    try:
        df = pd.read_parquet(parquet_path)
    except Exception as exc:
        logger.error("Error al leer %s: %s", parquet_path, exc)
        return None, []

    predictors: list[str] = []
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                predictors = json.load(f)
        except Exception as exc:
            logger.warning("Error al leer %s: %s; usando config.PREDICTORS.", json_path, exc)
    else:
        logger.warning(
            "Archivo de predictores no encontrado: %s; usando config.PREDICTORS.", json_path
        )

    if not predictors:
        predictors = [p for p in config.PREDICTORS if p in df.columns]

    return df, predictors


def _load_cv_preds(slug: str) -> pd.DataFrame | None:
    """Carga predicciones out-of-fold si existen."""
    path = config.SPECIES_DATASETS / f"{slug}_cv_preds.parquet"
    if not path.exists():
        logger.info("  cv_preds no encontrado para %s; métricas OOF omitidas.", slug)
        return None
    try:
        df = pd.read_parquet(path)
        logger.info("  cv_preds cargado: %d filas, columnas: %s", len(df), list(df.columns))
        return df
    except Exception as exc:
        logger.warning("  Error al leer cv_preds %s: %s", path, exc)
        return None


# ===========================================================================
# SECCIÓN 10 — Validación de una especie (orquestador principal)
# ===========================================================================

def validate_species(slug: str) -> dict[str, Any]:
    """Ejecuta toda la evaluación para una especie.

    Parámetros
    ----------
    slug : identificador de especie (ej. 'schinus_areira').

    Retorna
    -------
    dict con todas las métricas calculadas (float/str/NaN).
    Siempre incluye 'slug' y 'status'.
    """
    logger.info("=" * 60)
    logger.info("Validando especie: %s", slug)
    logger.info("=" * 60)

    row: dict[str, Any] = {"slug": slug, "status": "ok"}

    # -----------------------------------------------------------------
    # 1. Cargar datos
    # -----------------------------------------------------------------
    df, predictors = _load_species_data(slug)
    if df is None:
        row["status"] = "error_dataset"
        return row

    if "presence" not in df.columns:
        logger.error("Columna 'presence' no encontrada en %s.parquet", slug)
        row["status"] = "error_columna_presencia"
        return row

    y_all = df["presence"].values.astype(int)
    n_pres = int((y_all == 1).sum())
    n_bg = int((y_all == 0).sum())
    row["n_presencias"] = n_pres
    row["n_background"] = n_bg
    row["n_predictores"] = len(predictors)
    logger.info("  n presencias=%d  n background=%d  n predictores=%d",
                n_pres, n_bg, len(predictors))

    # -----------------------------------------------------------------
    # 2. Cargar artefacto del ensemble
    # -----------------------------------------------------------------
    joblib_path = config.ENSEMBLE_MODELS / f"{slug}.joblib"
    artifact = _load_joblib(joblib_path)
    if artifact is None:
        logger.warning("  Artefacto no disponible; métricas de modelos omitidas.")
        artifact = {}

    # Extraer thresholds del artefacto si existen (los calcula 05_modelado.py)
    saved_thresholds: dict[str, float] = artifact.get("thresholds", {})

    # -----------------------------------------------------------------
    # 3. Predicciones out-of-fold (cv_preds)
    # -----------------------------------------------------------------
    cv_preds = _load_cv_preds(slug)
    has_cv = cv_preds is not None and ENSEMBLE_COL in cv_preds.columns

    # Si tenemos cv_preds, usarlas como base de discriminación/calibración
    if has_cv:
        y_oof = cv_preds["presence"].values.astype(int)
        y_prob_ens = cv_preds[ENSEMBLE_COL].values.astype(float)
    else:
        # Fallback: predecir sobre el dataset completo con el ensemble (NO recomendado,
        # puede inflar métricas — se advierte en el CSV)
        y_oof = y_all
        y_prob_ens = _predict_with_artifact(artifact, df, predictors, slug)
        if y_prob_ens is None:
            logger.warning(
                "  No hay cv_preds ni predicciones disponibles. "
                "Métricas de discriminación omitidas."
            )
            y_prob_ens = np.full(len(y_oof), np.nan)
        else:
            logger.warning(
                "  ADVERTENCIA: métricas calculadas sobre datos de entrenamiento "
                "(no out-of-fold). AUC/TSS posiblemente inflados."
            )
            row["metrics_source"] = "training_set_WARNING"

    if not has_cv:
        row["metrics_source"] = row.get("metrics_source", "training_set_WARNING")
    else:
        row["metrics_source"] = "spatial_cv_oof"

    # -----------------------------------------------------------------
    # 4. Umbrales
    # -----------------------------------------------------------------
    # maxTSS
    if "maxTSS" in saved_thresholds:
        thr_maxtss = float(saved_thresholds["maxTSS"])
        tss_max_val = _tss(y_oof, y_prob_ens, thr_maxtss)
    else:
        thr_maxtss, tss_max_val = _max_tss_threshold(y_oof, y_prob_ens)

    row["threshold_maxTSS"] = thr_maxtss
    row["tss_at_maxTSS"] = tss_max_val

    # p10
    if "p10" in saved_thresholds:
        thr_p10 = float(saved_thresholds["p10"])
    else:
        thr_p10 = _p10_threshold(y_oof, y_prob_ens)

    row["threshold_p10"] = thr_p10

    logger.info("  Umbrales: maxTSS=%.4f (TSS=%.4f), p10=%.4f",
                thr_maxtss, tss_max_val, thr_p10)

    # -----------------------------------------------------------------
    # 5. Discriminación (ensemble)
    # -----------------------------------------------------------------
    logger.info("  Calculando discriminación...")
    disc = compute_discrimination_metrics(y_oof, y_prob_ens, thr_maxtss)
    for k, v in disc.items():
        row[f"ens_{k}"] = v
    logger.info("  AUC=%.4f  TSS=%.4f  AUC-PR=%.4f  F1=%.4f",
                disc.get("auc_roc", np.nan), disc.get("tss", np.nan),
                disc.get("auc_pr", np.nan), disc.get("f1", np.nan))

    # Discriminación por algoritmo (si cv_preds disponible)
    if has_cv:
        for algo_col in ALGORITHM_COLS:
            if algo_col not in cv_preds.columns:
                continue
            algo_key = algo_col.replace("pred_", "")
            yp_algo = cv_preds[algo_col].values.astype(float)
            thr_algo, _ = _max_tss_threshold(y_oof, yp_algo)
            disc_algo = compute_discrimination_metrics(y_oof, yp_algo, thr_algo)
            for k, v in disc_algo.items():
                row[f"{algo_key}_{k}"] = v

    # -----------------------------------------------------------------
    # 6. Calibración (ensemble)
    # -----------------------------------------------------------------
    logger.info("  Calculando calibración...")
    calib = compute_calibration_metrics(y_oof, y_prob_ens)
    for k, v in calib.items():
        row[f"ens_{k}"] = v
    logger.info("  Brier=%.4f  calib_slope=%.4f  calib_intercept=%.4f",
                calib.get("brier_score", np.nan),
                calib.get("calib_slope", np.nan),
                calib.get("calib_intercept", np.nan))

    # Figura de calibración
    if not np.all(np.isnan(y_prob_ens)):
        _save_calibration_figure(y_oof, y_prob_ens, slug)

    # -----------------------------------------------------------------
    # 7. Solo-presencia: Boyce index y OR10
    # -----------------------------------------------------------------
    logger.info("  Calculando Boyce index y OR10...")

    # Usar predicciones sobre todo el dataset para Boyce (requiere background + presencias)
    y_prob_full = _get_full_predictions(artifact, df, predictors, cv_preds, slug)

    if y_prob_full is not None and not np.all(np.isnan(y_prob_full)):
        cbi = boyce_index(y_prob_full, y_all.astype(bool))
        row["ens_boyce_index"] = cbi
        logger.info("  Boyce index (CBI) = %.4f", cbi)
    else:
        row["ens_boyce_index"] = np.nan
        logger.warning("  Boyce index: predicciones no disponibles.")

    # OR10 (usando threshold p10 sobre predicciones OOF de presencias)
    or10 = _omission_rate(y_oof, y_prob_ens, thr_p10)
    row["ens_or10"] = or10
    logger.info("  OR10 = %.4f (esperado teórico ~0.10)", or10)

    # -----------------------------------------------------------------
    # 8. Robustez espacial (media ± SD entre folds)
    # -----------------------------------------------------------------
    if has_cv and "cv_fold" in cv_preds.columns:
        logger.info("  Calculando robustez espacial por fold...")
        cv_metrics = compute_spatial_cv_metrics(cv_preds, thr_maxtss)
        for k, v in cv_metrics.items():
            row[k] = v

        # Variabilidad regional
        regional = compute_regional_variability(cv_preds, thr_maxtss)
        if regional is not None:
            reg_path = config.TABLES / f"regional_cv_{slug}.csv"
            regional.to_csv(reg_path, index=False)
            logger.info("  Variabilidad regional guardada: %s", reg_path.name)

        # Log resumen folds ensemble
        if "tss_ensemble_mean" in row:
            logger.info(
                "  TSS ensemble folds: %.4f ± %.4f",
                row.get("tss_ensemble_mean", np.nan),
                row.get("tss_ensemble_std", np.nan),
            )
    else:
        logger.info("  cv_preds sin columna 'cv_fold'; robustez espacial omitida.")

        # Intentar leer métricas por fold del artefacto (guardadas por 05_modelado.py)
        if "tss_per_fold" in artifact:
            for algo_key, fold_vals in artifact["tss_per_fold"].items():
                if fold_vals:
                    row[f"tss_{algo_key}_mean"] = float(np.mean(fold_vals))
                    row[f"tss_{algo_key}_std"] = float(np.std(fold_vals))

        if "auc_per_fold" in artifact:
            for algo_key, fold_vals in artifact["auc_per_fold"].items():
                if fold_vals:
                    row[f"auc_{algo_key}_mean"] = float(np.mean(fold_vals))
                    row[f"auc_{algo_key}_std"] = float(np.std(fold_vals))

    # -----------------------------------------------------------------
    # 9. Extrapolación: MESS
    # -----------------------------------------------------------------
    logger.info("  Calculando MESS...")
    pres_df = df[df["presence"] == 1]
    mess_metrics = compute_mess_metrics(pres_df, df, predictors)
    for k, v in mess_metrics.items():
        row[f"mess_{k}"] = v
    if "mess_pct_extrapolation" in mess_metrics:
        logger.info(
            "  MESS: %.1f%% puntos en extrapolación (MESS < 0)",
            mess_metrics["mess_pct_extrapolation"],
        )

    # -----------------------------------------------------------------
    # 10. Métricas del ensemble (incertidumbre y acuerdo binario)
    # -----------------------------------------------------------------
    if has_cv:
        logger.info("  Calculando métricas de ensemble (SD, acuerdo binario)...")
        ens_metrics = compute_ensemble_metrics(cv_preds, thr_maxtss)
        for k, v in ens_metrics.items():
            row[k] = v
        logger.info(
            "  SD entre algoritmos (media) = %.4f  | acuerdo unánime = %.1f%%",
            ens_metrics.get("algo_sd_mean", np.nan),
            ens_metrics.get("binary_agreement_unanimous_pct", np.nan),
        )

    return row


# ===========================================================================
# SECCIÓN 11 — Helpers de predicción
# ===========================================================================

def _predict_with_artifact(
    artifact: dict[str, Any],
    df: pd.DataFrame,
    predictors: list[str],
    slug: str,
) -> np.ndarray | None:
    """Genera predicciones del ensemble sobre df usando los modelos del artefacto.

    Solo se usa como fallback cuando no hay cv_preds.
    """
    if not artifact or "models" not in artifact or "tss_weights" not in artifact:
        return None

    models = artifact["models"]
    weights = artifact["tss_weights"]
    scaler = artifact.get("scaler")
    scaled_algos: list[str] = artifact.get("scaled_algos", [])

    # Usar predictores del artefacto si están disponibles
    art_predictors = artifact.get("selected_predictors", predictors)
    avail = [p for p in art_predictors if p in df.columns]
    if not avail:
        logger.warning("  _predict_with_artifact: ningún predictor disponible en el DataFrame.")
        return None

    X = df[avail].values
    weighted_sum = np.zeros(len(df))
    total_weight = 0.0

    for algo_name, model in models.items():
        w = weights.get(algo_name, 1.0)
        if w <= 0:
            continue
        try:
            X_input = (
                scaler.transform(X)
                if (algo_name in scaled_algos and scaler is not None)
                else X
            )
            if hasattr(model, "predict_proba"):
                prob = model.predict_proba(X_input)[:, 1]
            elif hasattr(model, "predict"):
                prob = model.predict(X_input).astype(float)
                prob = np.clip(prob, 0.0, 1.0)
            else:
                logger.warning("  Modelo %s sin método predict_proba/predict.", algo_name)
                continue
            weighted_sum += w * prob
            total_weight += w
        except Exception as exc:
            logger.warning("  Error al predecir con %s: %s", algo_name, exc)

    if total_weight == 0:
        return None

    return weighted_sum / total_weight


def _get_full_predictions(
    artifact: dict[str, Any],
    df: pd.DataFrame,
    predictors: list[str],
    cv_preds: pd.DataFrame | None,
    slug: str,
) -> np.ndarray | None:
    """Obtiene predicciones sobre todo el dataset (para Boyce index).

    Prefiere cv_preds si cubre todo el dataset; si no, usa el modelo directamente.
    """
    if cv_preds is not None and ENSEMBLE_COL in cv_preds.columns:
        if len(cv_preds) == len(df):
            return cv_preds[ENSEMBLE_COL].values.astype(float)
        # cv_preds parcial: rellenar con predicciones del modelo para filas faltantes
        logger.info(
            "  cv_preds (%d filas) != dataset (%d filas); usando predicciones del modelo.",
            len(cv_preds), len(df),
        )

    # Fallback: predecir directamente
    return _predict_with_artifact(artifact, df, predictors, slug)


# ===========================================================================
# SECCIÓN 12 — Exportación de tabla resumen (§5.8)
# ===========================================================================

SUMMARY_COLUMNS_ORDER = [
    # Identificación
    "slug", "status", "metrics_source",
    "n_presencias", "n_background", "n_predictores",
    # Umbrales
    "threshold_maxTSS", "tss_at_maxTSS", "threshold_p10",
    # Discriminación (ensemble)
    "ens_auc_roc", "ens_auc_pr", "ens_tss", "ens_f1",
    # Calibración
    "ens_brier_score", "ens_calib_slope", "ens_calib_intercept",
    # Solo-presencia
    "ens_boyce_index", "ens_or10",
    # Robustez espacial (ensemble)
    "tss_ensemble_mean", "tss_ensemble_std",
    "auc_ensemble_mean", "auc_ensemble_std",
    # MESS
    "mess_mess_mean", "mess_mess_min", "mess_mess_pct_extrapolation", "mess_n_mess_points",
    # Ensemble SD / acuerdo
    "algo_sd_mean", "algo_sd_median",
    "binary_agreement_mean", "binary_agreement_unanimous_pct",
    # Discriminación por algoritmo (TSS + AUC)
    "glm_tss", "glm_auc_roc",
    "gam_tss", "gam_auc_roc",
    "rf_tss",  "rf_auc_roc",
    "gbm_tss", "gbm_auc_roc",
    "maxent_tss", "maxent_auc_roc",
]


def build_summary_row(metrics: dict[str, Any]) -> dict[str, Any]:
    """Construye la fila de la tabla resumen §5.8 con columnas en el orden estándar."""
    row: dict[str, Any] = {}
    for col in SUMMARY_COLUMNS_ORDER:
        row[col] = metrics.get(col, np.nan)
    # Añadir columnas extra que no estaban en el orden predefinido
    for k, v in metrics.items():
        if k not in row:
            row[k] = v
    return row


def export_species_metrics(
    metrics: dict[str, Any],
    slug: str,
) -> Path:
    """Exporta métricas de una especie a CSV.

    Retorna la ruta del archivo generado.
    """
    utils.ensure_dirs(config.TABLES)
    out_path = config.TABLES / f"metrics_{slug}.csv"
    row = build_summary_row(metrics)
    pd.DataFrame([row]).to_csv(out_path, index=False)
    logger.info("  Métricas exportadas: %s", out_path.name)
    return out_path


def export_consolidated_metrics(
    all_metrics: list[dict[str, Any]],
) -> Path:
    """Consolida métricas de todas las especies en metrics_all.csv."""
    utils.ensure_dirs(config.TABLES)
    out_path = config.TABLES / "metrics_all.csv"

    rows = [build_summary_row(m) for m in all_metrics]
    df = pd.DataFrame(rows)

    # Reordenar columnas: primero las del orden estándar, luego el resto
    standard_cols = [c for c in SUMMARY_COLUMNS_ORDER if c in df.columns]
    extra_cols = [c for c in df.columns if c not in standard_cols]
    df = df[standard_cols + extra_cols]

    df.to_csv(out_path, index=False)
    logger.info("Consolidado exportado: %s (%d especies)", out_path.name, len(rows))
    return out_path


# ===========================================================================
# SECCIÓN 13 — main
# ===========================================================================

def parse_args() -> argparse.Namespace:
    """Parsea argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(
        description=(
            "Etapa 5 — Evaluación y métricas del ensemble SDM. "
            "Calcula TSS, AUC, Brier, Boyce index, MESS, CV espacial y más."
        )
    )
    parser.add_argument(
        "--species",
        type=str,
        nargs="+",
        default=None,
        help=(
            "Nombre(s) de especie a validar, separados por espacio. "
            "Ejemplos: --species 'Schinus areira' 'Nolana divaricata'. "
            "Si se omite, procesa todas las especies con dataset disponible."
        ),
    )
    parser.add_argument(
        "--slug",
        type=str,
        nargs="+",
        default=None,
        help=(
            "Slug(s) directo(s) de especie (ej. schinus_areira). "
            "Alternativa a --species cuando ya se conoce el slug."
        ),
    )
    return parser.parse_args()


def discover_slugs() -> list[str]:
    """Descubre slugs disponibles buscando archivos .parquet en SPECIES_DATASETS."""
    parquets = list(config.SPECIES_DATASETS.glob("*.parquet"))
    # Excluir archivos de cv_preds
    slugs = [
        p.stem for p in parquets
        if not p.stem.endswith("_cv_preds") and not p.stem.endswith("_predictors")
    ]
    return sorted(slugs)


def main() -> None:
    """Punto de entrada principal de la etapa de validación."""
    args = parse_args()
    utils.ensure_dirs(config.TABLES, config.FIGURES)

    # Determinar slugs a procesar
    slugs: list[str] = []

    if args.slug:
        slugs = args.slug
    elif args.species:
        slugs = [utils.slugify_species(sp) for sp in args.species]
    else:
        slugs = discover_slugs()
        if not slugs:
            logger.warning(
                "No se encontraron datasets en %s. "
                "Ejecutar primero 04_extraccion.py.",
                config.SPECIES_DATASETS,
            )
            return
        logger.info("Especies descubiertas automáticamente: %s", slugs)

    logger.info("Validando %d especie(s): %s", len(slugs), slugs)

    all_metrics: list[dict[str, Any]] = []

    for slug in slugs:
        try:
            metrics = validate_species(slug)
        except Exception as exc:
            logger.error("Error inesperado validando %s: %s", slug, exc, exc_info=True)
            metrics = {"slug": slug, "status": f"error_inesperado: {exc}"}

        export_species_metrics(metrics, slug)
        all_metrics.append(metrics)

    if all_metrics:
        consolidated_path = export_consolidated_metrics(all_metrics)
        logger.info("Validación completada. Tabla consolidada: %s", consolidated_path)

        # Log resumen de TSS por especie
        logger.info("-" * 60)
        logger.info("RESUMEN TSS ensemble por especie:")
        for m in all_metrics:
            tss_val = m.get("ens_tss", m.get("tss_at_maxTSS", np.nan))
            auc_val = m.get("ens_auc_roc", np.nan)
            cbi_val = m.get("ens_boyce_index", np.nan)
            logger.info(
                "  %-30s  TSS=%.3f  AUC=%.3f  CBI=%.3f",
                m["slug"],
                tss_val if not (isinstance(tss_val, float) and np.isnan(tss_val)) else -999,
                auc_val if not (isinstance(auc_val, float) and np.isnan(auc_val)) else -999,
                cbi_val if not (isinstance(cbi_val, float) and np.isnan(cbi_val)) else -999,
            )
        logger.info("-" * 60)
    else:
        logger.warning("No se generaron métricas.")


if __name__ == "__main__":
    main()
