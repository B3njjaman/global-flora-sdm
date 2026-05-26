"""
05_modelado.py — Etapa 4: Ensemble SDM (GLM, GAM, RF, GBM, MaxEnt).

Pipeline:
  1. Carga dataset por especie ({slug}.parquet + {slug}_predictors.json).
  2. Spatial CV leave-one-block-out usando columna cv_fold.
  3. Calcula TSS por algoritmo en cada fold (umbral de Youden); promedia entre folds.
  4. Construye ensemble ponderado por TSS; excluye modelos con TSS < config.TSS_MIN_ENSEMBLE.
  5. Reentrena todos los modelos con 100% de los datos.
  6. Calcula umbrales del ensemble sobre training (maxTSS, p10, min_train).
  7. Guarda predicciones out-of-fold en {slug}_cv_preds.parquet (para Etapa 6).
  8. Serializa todo en config.ENSEMBLE_MODELS/{slug}.joblib.

Uso:
  python 05_modelado.py --species "Nolana divaricata"
  python 05_modelado.py          # procesa todas las especies con dataset disponible

Robustez: si pyGAM o elapid fallan en alguna especie, se logea y el ensemble
se adapta automáticamente con los algoritmos restantes.
"""
from __future__ import annotations

import argparse
import json
import traceback
import warnings
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

import config
import utils

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logger = utils.get_logger("05_modelado")


# ---------------------------------------------------------------------------
# Constantes internas
# ---------------------------------------------------------------------------
_ALGO_NAMES: list[str] = ["glm", "gam", "rf", "gbm", "maxent"]

# Algoritmos que necesitan escalado de predictores
_NEEDS_SCALING: set[str] = {"glm", "gam", "maxent"}


# ---------------------------------------------------------------------------
# Helpers: TSS
# ---------------------------------------------------------------------------

def _tss_youden(y_true: np.ndarray, y_prob: np.ndarray) -> tuple[float, float]:
    """Calcula el TSS máximo (criterio de Youden) y el umbral óptimo.

    Recorre los cuantiles de y_prob como candidatos a umbral para maximizar
    TSS = sensitividad + especificidad - 1 (Allouche et al. 2006).

    Parámetros
    ----------
    y_true : array-like de 0/1
    y_prob : array-like de probabilidades [0, 1]

    Retorna
    -------
    (tss_max, umbral_optimo)
    """
    thresholds = np.unique(y_prob)
    best_tss = -1.0
    best_thresh = 0.5

    n_pos = y_true.sum()
    n_neg = len(y_true) - n_pos

    if n_pos == 0 or n_neg == 0:
        return 0.0, 0.5

    for thresh in thresholds:
        pred = (y_prob >= thresh).astype(int)
        tp = ((pred == 1) & (y_true == 1)).sum()
        tn = ((pred == 0) & (y_true == 0)).sum()
        sensitivity = tp / n_pos
        specificity = tn / n_neg
        tss = sensitivity + specificity - 1.0
        if tss > best_tss:
            best_tss = tss
            best_thresh = thresh

    return float(best_tss), float(best_thresh)


# ---------------------------------------------------------------------------
# Helpers: construcción de modelos
# ---------------------------------------------------------------------------

def _load_tuned(algo: str, defaults: dict[str, Any]) -> dict[str, Any]:
    """Carga hiperparámetros tuneados desde scripts/tuned_params/{algo}.json.

    Devuelve los `defaults` con los valores tuneados sobreescritos. Si el archivo
    no existe o falla la lectura, retorna los defaults intactos (comportamiento
    idéntico al previo al tuning). El JSON debe ser un dict de kwargs válidos
    para el constructor del algoritmo correspondiente.
    """
    params = dict(defaults)
    tuned_path = Path(__file__).resolve().parent / "tuned_params" / f"{algo}.json"
    if tuned_path.exists():
        try:
            with open(tuned_path, "r", encoding="utf-8") as fh:
                overrides = json.load(fh)
            if isinstance(overrides, dict):
                params.update(overrides)
                logger.info("  %-8s hiperparámetros tuneados: %s", algo, overrides)
        except Exception as exc:  # noqa: BLE001
            logger.warning("  %-8s no se pudo leer tuned_params/%s.json: %s", algo, algo, exc)
    return params


def _build_models() -> dict[str, Any]:
    """Instancia los 5 modelos con sus hiperparámetros.

    Cada algoritmo arranca con los defaults del contrato y, si existe
    scripts/tuned_params/{algo}.json, lo sobreescribe con los valores tuneados.
    Los modelos de pyGAM y elapid se importan en tiempo de ejecución para que un
    fallo de instalación no rompa todo el script.

    Retorna
    -------
    dict con las claves disponibles de _ALGO_NAMES.
    """
    models: dict[str, Any] = {}

    # GLM — LogisticRegression L2 (escalado externo)
    models["glm"] = LogisticRegression(**_load_tuned("glm", {
        "penalty": "l2",
        "solver": "lbfgs",
        "max_iter": 1000,
        "random_state": config.RANDOM_SEED,
    }))

    # GAM — pyGAM LogisticGAM (escalado externo)
    try:
        from pygam import LogisticGAM  # type: ignore
        models["gam"] = LogisticGAM(**_load_tuned("gam", {}))
    except ImportError:
        logger.warning("pyGAM no instalado — GAM excluido del ensemble.")

    # RF — RandomForest (sin escalado)
    from sklearn.ensemble import RandomForestClassifier
    models["rf"] = RandomForestClassifier(**_load_tuned("rf", {
        "n_estimators": 500,
        "class_weight": "balanced",
        "n_jobs": -1,
        "random_state": config.RANDOM_SEED,
    }))

    # GBM — LightGBM (sin escalado)
    try:
        import lightgbm as lgb  # type: ignore
        models["gbm"] = lgb.LGBMClassifier(**_load_tuned("gbm", {
            "num_leaves": 31,
            "learning_rate": 0.05,
            "n_estimators": 300,
            "class_weight": "balanced",
            "random_state": config.RANDOM_SEED,
            "verbose": -1,
        }))
    except ImportError:
        logger.warning("lightgbm no instalado — GBM excluido del ensemble.")

    # MaxEnt — elapid (escalado externo)
    try:
        from elapid import MaxentModel  # type: ignore
        models["maxent"] = MaxentModel(**_load_tuned("maxent", {}))
    except ImportError:
        logger.warning("elapid no instalado — MaxEnt excluido del ensemble.")

    return models


# ---------------------------------------------------------------------------
# Helpers: predict_proba unificado
# ---------------------------------------------------------------------------

def _predict_proba(model: Any, X: np.ndarray, algo: str) -> np.ndarray:
    """Obtiene probabilidades de presencia (clase 1) de forma uniforme.

    pyGAM y elapid tienen interfaces distintas a scikit-learn.

    Parámetros
    ----------
    model : modelo entrenado
    X : array 2-D de predictores (ya escalado si corresponde)
    algo : nombre del algoritmo (clave en _ALGO_NAMES)

    Retorna
    -------
    array 1-D de probabilidades [0, 1]
    """
    if algo == "gam":
        # pyGAM: predict_proba devuelve 1-D directamente
        return model.predict_proba(X)
    elif algo == "maxent":
        # elapid: predict devuelve probabilidades (suitability)
        try:
            return model.predict(X)
        except Exception:
            # Algunos builds de elapid usan predict_proba
            proba = model.predict_proba(X)
            if proba.ndim == 2:
                return proba[:, 1]
            return proba
    else:
        # scikit-learn compatible: predict_proba devuelve (n, 2)
        return model.predict_proba(X)[:, 1]


# ---------------------------------------------------------------------------
# Core: entrenamiento de un algoritmo (con manejo de errores)
# ---------------------------------------------------------------------------

def _fit_model(
    model: Any,
    algo: str,
    X_train: np.ndarray,
    y_train: np.ndarray,
    scaler: StandardScaler | None,
) -> Any:
    """Ajusta un modelo; aplica escalado si procede.

    Parámetros
    ----------
    model : instancia (no entrenada) del algoritmo
    algo : nombre del algoritmo
    X_train : predictores sin escalar
    y_train : vector de 0/1
    scaler : StandardScaler fitted o None

    Retorna
    -------
    Modelo ajustado (in-place, pero también retornado para claridad).
    """
    X = scaler.transform(X_train) if (algo in _NEEDS_SCALING and scaler is not None) else X_train

    if algo == "gam":
        model.fit(X, y_train)
    elif algo == "maxent":
        # elapid.MaxentModel espera X como DataFrame o array; y es 0/1
        model.fit(X, y_train)
    else:
        model.fit(X, y_train)

    return model


# ---------------------------------------------------------------------------
# Core: spatial CV
# ---------------------------------------------------------------------------

def _spatial_cv(
    df: pd.DataFrame,
    predictors: list[str],
    models_proto: dict[str, Any],
    scaler: StandardScaler,
) -> tuple[dict[str, float], dict[str, list[float]], dict[str, list[float]], pd.DataFrame]:
    """Validación cruzada espacial leave-one-block-out.

    Para cada fold k: entrena con k!=fold, predice fold k, calcula TSS y AUC.

    Parámetros
    ----------
    df : DataFrame con presencia, cv_fold y columnas de predictores
    predictors : lista de predictores seleccionados
    models_proto : dict con instancias de modelos (SE CLONAN por fold)
    scaler : StandardScaler ya ajustado sobre todos los datos

    Retorna
    -------
    cv_tss : TSS medio por algoritmo
    tss_per_fold : TSS por fold y algoritmo
    auc_per_fold : AUC-ROC por fold y algoritmo
    oof_preds : DataFrame con predicciones out-of-fold
    """
    folds = sorted(df["cv_fold"].unique())
    y_all = df["presence"].values
    X_all = df[predictors].values

    # Acumuladores
    tss_per_fold: dict[str, list[float]] = {algo: [] for algo in models_proto}
    auc_per_fold: dict[str, list[float]] = {algo: [] for algo in models_proto}
    # OOF predictions: index alineado con df
    oof: dict[str, np.ndarray] = {algo: np.full(len(df), np.nan) for algo in models_proto}
    idx_all = np.arange(len(df))

    for fold in folds:
        mask_test = df["cv_fold"].values == fold
        mask_train = ~mask_test

        if mask_test.sum() == 0 or mask_train.sum() == 0:
            logger.warning("Fold %s vacío o sin datos de entrenamiento — omitido.", fold)
            continue

        X_tr = X_all[mask_train]
        y_tr = y_all[mask_train]
        X_te = X_all[mask_test]
        y_te = y_all[mask_test]

        # Necesitamos al menos 1 presencia y 1 ausencia en test para calcular TSS
        if y_te.sum() == 0 or (len(y_te) - y_te.sum()) == 0:
            logger.warning(
                "Fold %s sin presencias o sin ausencias en test — TSS no calculable.", fold
            )
            continue

        for algo, proto in models_proto.items():
            try:
                import copy
                model_fold = copy.deepcopy(proto)
                _fit_model(model_fold, algo, X_tr, y_tr, scaler)
                X_te_s = (
                    scaler.transform(X_te) if (algo in _NEEDS_SCALING and scaler is not None) else X_te
                )
                proba = _predict_proba(model_fold, X_te_s, algo)
                tss, _ = _tss_youden(y_te, proba)
                tss_per_fold[algo].append(tss)
                try:
                    auc_fold = float(roc_auc_score(y_te, proba))
                except Exception:
                    auc_fold = float("nan")
                auc_per_fold[algo].append(auc_fold)
                oof[algo][idx_all[mask_test]] = proba
            except Exception as exc:
                logger.warning(
                    "Fold %s — algoritmo '%s' falló: %s. Fold omitido para este algoritmo.",
                    fold, algo, exc,
                )
                logger.debug(traceback.format_exc())

    # TSS medio por algoritmo
    cv_tss: dict[str, float] = {}
    for algo in models_proto:
        if tss_per_fold[algo]:
            cv_tss[algo] = float(np.mean(tss_per_fold[algo]))
        else:
            cv_tss[algo] = 0.0
            logger.warning("Algoritmo '%s': ningún fold produjo TSS — TSS=0 asignado.", algo)

    # DataFrame OOF
    oof_df = pd.DataFrame({"presence": y_all, "cv_fold": df["cv_fold"].values})
    for algo in models_proto:
        oof_df[algo] = oof[algo]

    return cv_tss, tss_per_fold, auc_per_fold, oof_df


# ---------------------------------------------------------------------------
# Core: cálculo de pesos del ensemble
# ---------------------------------------------------------------------------

def _compute_weights(cv_tss: dict[str, float]) -> dict[str, float]:
    """Convierte TSS de CV en pesos normalizados; excluye TSS < config.TSS_MIN_ENSEMBLE.

    Si ningún modelo supera el umbral, se retornan pesos iguales para todos
    (fallback de emergencia con advertencia).

    Parámetros
    ----------
    cv_tss : TSS medio de CV por algoritmo

    Retorna
    -------
    dict de pesos normalizados que suman 1 (excepto si todos son 0).
    """
    raw: dict[str, float] = {}
    for algo, tss in cv_tss.items():
        if tss >= config.TSS_MIN_ENSEMBLE:
            raw[algo] = tss
        else:
            raw[algo] = 0.0
            logger.info(
                "Algoritmo '%s' excluido del ensemble (TSS=%.3f < umbral %.2f).",
                algo, tss, config.TSS_MIN_ENSEMBLE,
            )

    total = sum(raw.values())
    if total == 0.0:
        logger.warning(
            "Ningún algoritmo supera TSS_MIN_ENSEMBLE=%.2f. "
            "Se usarán pesos iguales como fallback.",
            config.TSS_MIN_ENSEMBLE,
        )
        n = len(cv_tss)
        return {algo: 1.0 / n for algo in cv_tss}

    return {algo: w / total for algo, w in raw.items()}


# ---------------------------------------------------------------------------
# Core: umbrales sobre training
# ---------------------------------------------------------------------------

def _compute_thresholds(
    ensemble_proba: np.ndarray,
    y_true: np.ndarray,
) -> dict[str, float]:
    """Calcula los 3 umbrales del ensemble sobre las predicciones de training.

    Parámetros
    ----------
    ensemble_proba : probabilidades del ensemble sobre todos los datos de training
    y_true : vector 0/1

    Retorna
    -------
    dict con claves 'maxTSS', 'p10', 'min_train'
    """
    presence_proba = ensemble_proba[y_true == 1]

    # maxTSS: umbral de Youden sobre training (optimista, solo referencia interna)
    _, thresh_maxtss = _tss_youden(y_true, ensemble_proba)

    # p10: 10° percentil de probabilidad en presencias de training
    thresh_p10 = float(np.percentile(presence_proba, 10)) if len(presence_proba) > 0 else 0.0

    # min_train: mínimo de probabilidad en presencias de training
    thresh_min = float(presence_proba.min()) if len(presence_proba) > 0 else 0.0

    return {
        "maxTSS": thresh_maxtss,
        "p10": thresh_p10,
        "min_train": thresh_min,
    }


# ---------------------------------------------------------------------------
# Core: predicción del ensemble
# ---------------------------------------------------------------------------

def _ensemble_predict(
    fitted_models: dict[str, Any],
    tss_weights: dict[str, float],
    X: np.ndarray,
    scaler: StandardScaler | None,
) -> np.ndarray:
    """Calcula la predicción ensemble ponderada por TSS.

    Parámetros
    ----------
    fitted_models : modelos ya entrenados con todos los datos
    tss_weights : pesos normalizados por algoritmo
    X : predictores sin escalar
    scaler : StandardScaler fitted o None

    Retorna
    -------
    array 1-D de probabilidades ensemble
    """
    ensemble = np.zeros(len(X))
    total_w = 0.0

    for algo, model in fitted_models.items():
        w = tss_weights.get(algo, 0.0)
        if w == 0.0:
            continue
        try:
            X_s = scaler.transform(X) if (algo in _NEEDS_SCALING and scaler is not None) else X
            proba = _predict_proba(model, X_s, algo)
            ensemble += w * proba
            total_w += w
        except Exception as exc:
            logger.warning("Predicción ensemble — algoritmo '%s' falló: %s", algo, exc)

    if total_w > 0:
        ensemble /= total_w

    return ensemble


# ---------------------------------------------------------------------------
# Pipeline principal por especie
# ---------------------------------------------------------------------------

def process_species(slug: str, especie: str) -> bool:
    """Ejecuta el pipeline completo de modelado para una especie.

    Parámetros
    ----------
    slug : nombre de archivo (sin extensión), e.g. 'nolana_divaricata'
    especie : nombre científico completo, e.g. 'Nolana divaricata'

    Retorna
    -------
    True si completó correctamente, False si hubo un error fatal.
    """
    logger.info("=" * 70)
    logger.info("Especie: %s  (slug: %s)", especie, slug)

    # ------------------------------------------------------------------
    # 1. Carga de datos
    # ------------------------------------------------------------------
    parquet_path = config.SPECIES_DATASETS / f"{slug}.parquet"
    json_path = config.SPECIES_DATASETS / f"{slug}_predictors.json"

    if not parquet_path.exists():
        logger.error("Dataset no encontrado: %s", parquet_path)
        return False
    if not json_path.exists():
        logger.error("Archivo de predictores no encontrado: %s", json_path)
        return False

    df = pd.read_parquet(parquet_path)
    with open(json_path, "r", encoding="utf-8") as fh:
        selected_predictors: list[str] = json.load(fh)

    logger.info("Registros: %d  |  Predictores: %d  |  Folds: %s",
                len(df), len(selected_predictors),
                sorted(df["cv_fold"].unique()))

    # Validar columnas requeridas
    required_cols = {"especie", "presence", "lon", "lat", "cv_fold"} | set(selected_predictors)
    missing = required_cols - set(df.columns)
    if missing:
        logger.error("Columnas faltantes en el dataset: %s", missing)
        return False

    X_all = df[selected_predictors].values.astype(float)
    y_all = df["presence"].values.astype(int)

    # ------------------------------------------------------------------
    # 2. Scaler (ajustado sobre todos los datos — se usa en CV y reentrenamiento)
    # ------------------------------------------------------------------
    scaler = StandardScaler()
    scaler.fit(X_all)

    # ------------------------------------------------------------------
    # 3. Construir prototipos de modelos
    # ------------------------------------------------------------------
    models_proto = _build_models()
    if not models_proto:
        logger.error("No hay ningún algoritmo disponible — abortando especie %s.", especie)
        return False

    logger.info("Algoritmos disponibles: %s", list(models_proto.keys()))

    # ------------------------------------------------------------------
    # 4. Spatial CV → TSS por algoritmo
    # ------------------------------------------------------------------
    logger.info("Iniciando spatial CV leave-one-block-out...")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cv_tss, cv_tss_per_fold, cv_auc_per_fold, oof_df = _spatial_cv(
            df, selected_predictors, models_proto, scaler
        )

    for algo, tss in cv_tss.items():
        folds_ok = len(cv_tss_per_fold[algo])
        logger.info("  %-8s TSS medio = %.4f  (%d folds)", algo, tss, folds_ok)

    # ------------------------------------------------------------------
    # 5. Pesos del ensemble
    # ------------------------------------------------------------------
    tss_weights = _compute_weights(cv_tss)
    logger.info("Pesos ensemble: %s",
                {k: f"{v:.3f}" for k, v in tss_weights.items()})

    # ------------------------------------------------------------------
    # 6. Reentrenar con todos los datos
    # ------------------------------------------------------------------
    logger.info("Reentrenando modelos con 100%% de los datos...")
    fitted_models: dict[str, Any] = {}
    for algo, proto in models_proto.items():
        try:
            import copy
            model_full = copy.deepcopy(proto)
            _fit_model(model_full, algo, X_all, y_all, scaler)
            fitted_models[algo] = model_full
            logger.info("  %-8s reentrenado OK", algo)
        except Exception as exc:
            logger.warning("  %-8s falló en reentrenamiento: %s", algo, exc)
            logger.debug(traceback.format_exc())

    if not fitted_models:
        logger.error("Ningún modelo pudo entrenarse con los datos completos — especie omitida.")
        return False

    # ------------------------------------------------------------------
    # 7. Umbrales del ensemble sobre training
    # ------------------------------------------------------------------
    ensemble_train_proba = _ensemble_predict(fitted_models, tss_weights, X_all, scaler)
    thresholds = _compute_thresholds(ensemble_train_proba, y_all)
    logger.info("Umbrales ensemble — maxTSS: %.4f | p10: %.4f | min_train: %.4f",
                thresholds["maxTSS"], thresholds["p10"], thresholds["min_train"])

    # ------------------------------------------------------------------
    # 8. Predicciones OOF del ensemble para Etapa 6
    # ------------------------------------------------------------------
    # Calcular columna ensemble OOF ponderada
    oof_algo_cols = [c for c in _ALGO_NAMES if c in oof_df.columns]
    ens_oof = np.zeros(len(oof_df))
    total_w = 0.0
    for algo in oof_algo_cols:
        w = tss_weights.get(algo, 0.0)
        if w == 0.0:
            continue
        col_vals = oof_df[algo].values
        valid_mask = ~np.isnan(col_vals)
        ens_oof[valid_mask] += w * col_vals[valid_mask]
        total_w += w

    if total_w > 0:
        ens_oof /= total_w
    ens_oof[ens_oof == 0.0] = np.nan  # marcar filas sin OOF como NaN

    oof_df["ensemble"] = ens_oof

    # Guardar OOF parquet
    oof_path = config.SPECIES_DATASETS / f"{slug}_cv_preds.parquet"
    oof_df.to_parquet(oof_path, index=False)
    logger.info("Predicciones OOF guardadas: %s", oof_path)

    # ------------------------------------------------------------------
    # 9. Serializar resultado
    # ------------------------------------------------------------------
    utils.ensure_dirs(config.ENSEMBLE_MODELS)

    # train_env: DataFrame con columnas == selected_predictors, una fila por
    # registro de entrenamiento (presencias + background). Referencia para MESS.
    train_env_df = df[selected_predictors].copy()

    # scaled_algos: lista de algoritmos que reciben input escalado
    scaled_algos: list[str] = [a for a in fitted_models if a in _NEEDS_SCALING]

    result: dict[str, Any] = {
        "especie": especie,
        "selected_predictors": selected_predictors,
        "scaler": scaler,
        "scaled_algos": scaled_algos,
        "models": fitted_models,
        "cv_tss": cv_tss,
        "tss_per_fold": cv_tss_per_fold,
        "auc_per_fold": cv_auc_per_fold,
        "tss_weights": tss_weights,
        "thresholds": thresholds,
        "train_env": train_env_df,
    }

    out_path = config.ENSEMBLE_MODELS / f"{slug}.joblib"
    joblib.dump(result, out_path, compress=3)
    logger.info("Ensemble guardado: %s", out_path)
    return True


# ---------------------------------------------------------------------------
# Descubrimiento automático de especies
# ---------------------------------------------------------------------------

def discover_species() -> list[tuple[str, str]]:
    """Busca todos los .parquet en SPECIES_DATASETS y reconstruye (slug, especie).

    Solo incluye los que también tienen su .json de predictores.

    Retorna
    -------
    Lista de tuplas (slug, especie) lista para procesar.
    """
    pairs: list[tuple[str, str]] = []
    if not config.SPECIES_DATASETS.exists():
        return pairs

    for p in sorted(config.SPECIES_DATASETS.glob("*.parquet")):
        # Excluir archivos de cv_preds
        if p.stem.endswith("_cv_preds"):
            continue
        json_candidate = p.with_suffix(".json").with_stem(p.stem + "_predictors")
        # Compatibilidad con nombre {slug}_predictors.json
        json_path = config.SPECIES_DATASETS / f"{p.stem}_predictors.json"
        if not json_path.exists():
            logger.warning("Sin JSON de predictores para '%s' — omitido.", p.stem)
            continue
        # Intentar recuperar nombre de especie desde el parquet
        try:
            sample = pd.read_parquet(p, columns=["especie"])
            especie = sample["especie"].iloc[0]
        except Exception:
            # Si no tiene columna especie, usar slug como fallback
            especie = p.stem.replace("_", " ").title()
        pairs.append((p.stem, especie))

    return pairs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parsea argumentos de línea de comando."""
    parser = argparse.ArgumentParser(
        description=(
            "Etapa 4 SDM — Entrena ensemble (GLM, GAM, RF, GBM, MaxEnt) "
            "con spatial CV y guarda resultados por especie."
        )
    )
    parser.add_argument(
        "--species",
        type=str,
        default=None,
        help=(
            "Nombre científico de la especie a procesar, e.g. 'Nolana divaricata'. "
            "Si se omite, procesa todas las especies con dataset disponible."
        ),
    )
    return parser.parse_args()


def main() -> None:
    """Punto de entrada principal."""
    args = parse_args()

    utils.ensure_dirs(config.ENSEMBLE_MODELS)

    if args.species:
        # Modo especie única
        especie = args.species.strip()
        slug = utils.slugify_species(especie)
        ok = process_species(slug, especie)
        if not ok:
            raise SystemExit(f"Modelado fallido para '{especie}'.")
    else:
        # Modo batch: descubrir todas las especies con dataset
        pairs = discover_species()
        if not pairs:
            logger.error(
                "No se encontraron datasets en %s. "
                "Ejecutar primero 04_extraccion.py.",
                config.SPECIES_DATASETS,
            )
            raise SystemExit(1)

        logger.info("Especies a procesar: %d", len(pairs))
        results: dict[str, bool] = {}
        for slug, especie in pairs:
            try:
                ok = process_species(slug, especie)
                results[especie] = ok
            except Exception as exc:
                logger.error("Error inesperado procesando '%s': %s", especie, exc)
                logger.debug(traceback.format_exc())
                results[especie] = False

        # Resumen final
        n_ok = sum(results.values())
        n_fail = len(results) - n_ok
        logger.info("=" * 70)
        logger.info("RESUMEN FINAL: %d/%d especies completadas exitosamente.", n_ok, len(results))
        if n_fail:
            failed = [sp for sp, ok in results.items() if not ok]
            logger.warning("Fallidas: %s", failed)


if __name__ == "__main__":
    main()
