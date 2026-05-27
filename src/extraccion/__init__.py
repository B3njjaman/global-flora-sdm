"""
Paquete `extraccion` — Etapa 4 (V4): dataset modelable por especie.

Refactor modular de `scripts/04_extraccion.py`. Crece paso a paso:
  - background  : muestreo de pseudo-ausencias/background en Chile + pesos por algoritmo.
  - predictoras : filtro de colinealidad (correlación + VIF iterativo).
  - folds       : validación cruzada espacial adaptativa.
  - pipeline    : orquesta por especie.
"""
