"""Paquete de limpieza de ocurrencias (V4 — reconstrucción modular).

Un módulo por paso del pipeline de limpieza; `pipeline.py` los orquesta.
Estado actual: carga del dataset GBIF + filtro a Sudamérica. Los pasos
restantes (duplicados, incertidumbre, coords, centroides, océano, thinning,
grupos A/B/C) se irán añadiendo en orden. Ver docs/v4/flujo_trabajo.md.
"""
