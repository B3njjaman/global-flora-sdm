# tuned_params/

Hiperparámetros tuneados por algoritmo, consumidos por `05_modelado.py::_build_models`.

- Un archivo `{algo}.json` por algoritmo: `glm`, `gam`, `rf`, `gbm`, `maxent`.
- Cada JSON es un **dict de kwargs** del constructor del estimador correspondiente
  (`sklearn.LogisticRegression`, `pygam.LogisticGAM`, `sklearn.RandomForestClassifier`,
  `lightgbm.LGBMClassifier`, `elapid.MaxentModel`).
- Si un archivo no existe, se usan los defaults del contrato (comportamiento previo).

Ejemplo `rf.json`:
```json
{"n_estimators": 800, "max_depth": 24, "min_samples_leaf": 2, "max_features": "sqrt"}
```

Los valores aquí provienen de búsquedas de hiperparámetros evaluadas con el CV
espacial leave-one-block-out sobre las especies con CV válido (≥4 folds con
presencias). NO se optimiza contra las endémicas de CV degenerado (presencias en
1–2 folds), cuyo TSS es ruido.
