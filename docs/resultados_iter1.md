# Resultados — Iteración 1

Síntesis de la primera iteración del pipeline SDM (`global-flora-sdm`): modelos
ensemble entrenados, validación con CV espacial, e idoneidad del presente.
El **forecast a 2050 queda diferido** como mejora (ver §"Trabajo futuro").

## Datos

- **Ocurrencias GBIF:** 13.354 → **4.566** tras limpieza (dedup, incertidumbre,
  máscara de océano, thinning espacial a 2.5′). 14 especies modelables (grupos A/B).
- **Predictoras:** 10 bioclim WorldClim v2.1 + 4 topográficas (elevación, pendiente,
  northness, eastness), alineadas a 2.5′ (~5 km) global.
- **Background:** 20.000 puntos (target-group). Selección de predictores por
  correlación (|r|>0.7) + VIF (>10). CV espacial en bloques de 750 km.

## Validación (CV espacial leave-one-block-out)

| Especie | Presencias | TSS (media±sd) | AUC | Boyce | Veredicto |
|---|---:|---|---|---|---|
| *Miqueliopuntia miquelii* | 129 | 0.86 ± 0.14 | 0.99 | 0.68 | sólido |
| *Schinus areira* | 1309 | 0.74 ± 0.26 | 0.98 | 0.98 | sólido |
| *Encelia canescens* | 271 | 0.72 ± 0.34 | 1.00 | 0.85 | sólido |
| *Atriplex semibaccata* | 1054 | 0.71 ± 0.11 | 0.98 | 0.79 | sólido |
| *Oxalis gigantea* | 68 | 0.54 ± 0.10 | 0.97 | 0.78 | bueno |
| *Nolana sedifolia* | 37 | 0.53 ± 0.47 | 0.92 | 0.08 | variable |
| *Cumulopuntia sphaerica* | 135 | 0.46 ± 0.44 | 0.86 | 0.93 | variable |
| *Senna cumingii* | 92 | 0.44 ± 0.30 | 0.98 | 0.32 | moderado |
| *Nolana divaricata* | 45 | 0.38 ± 0.12 | 1.00 | 0.57 | moderado |
| *Pleurophora pungens* | 56 | 0.33 ± 0.07 | 0.84 | 0.77 | moderado |
| *Neltuma chilensis* | 184 | 0.08 ± 0.06 | 0.91 | 0.43 | débil |
| *Krameria cistoidea* | 223 | ≈ 0.00 | 0.75 | −0.75 | degenerado |
| *Eulychnia acida* | 150 | 0.00 | 0.07 | — | degenerado |
| *Skytanthus acutus* | 84 | 0.00 | 0.54 | — | degenerado |

Tabla completa (TSS/AUC/AUC-PR/F1/Brier/Boyce/OR10/MESS por algoritmo y ensemble):
`outputs/tables/metrics_all.csv`.

## Lectura de los resultados

- **10 especies con modelos de moderados a sólidos**, aptas para inferir idoneidad.
- **3-4 endémicas degeneradas** (*Krameria, Eulychnia, Skytanthus*): **no por falta
  de datos** (tienen 84-223 presencias) sino por la **geometría del CV espacial** —
  sus presencias caen en 1-2 bloques de 750 km, y el leave-one-block-out deja fuera
  todo su rango, colapsando el TSS. Es la tensión esperada entre CV espacial honesto
  y endémicas de rango estrecho (ver `docs/proyecto_sdm.md`, nota 7).
- **SD alta entre folds** (p. ej. *Nolana sedifolia* 0.53±0.47) = mala transferencia
  entre regiones; coherente con la naturaleza endémica.

## Salidas de la iteración

- `outputs/maps/{slug}_present_suitability.tif` — idoneidad del presente (0–1), 14 sp.
- `outputs/figures/{slug}_present.png` — mapas con overlay de ocurrencias (Etapa 8).
- `outputs/tables/metrics_*.csv` — métricas de validación.
- `data/modeling/ensemble_models/{slug}.joblib` — modelos ensemble reutilizables.

## Trabajo futuro (prioridad)

1. **Bloque CV adaptativo por especie** (~250-350 km para endémicas de rango estrecho)
   → reparte presencias en ≥3 folds y rescata los modelos degenerados sin más datos.
2. **Forecast 2050 (diferido).** Implementado en `07_forecast_2050.py`: descarga y
   proyecta las 8 capas CMIP6 (ACCESS-CM2/IPSL/MPI/MRI × ssp245/ssp585). Pendiente:
   optimizar el **MESS global** (cuello de botella; ya vectorizado con `searchsorted`,
   falta procesarlo por bloques/submuestreo para que escale a 37M píxeles).
3. **Relleno de borde costero** en terreno (rellenar NaN de tierra con 0 antes de
   enmascarar) para recuperar presencias costeras (p. ej. *N. sedifolia* 77→37).
4. **Modelo regional** para las endémicas chilenas; **pooling** de congéneres *Nolana*.
5. Re-descarga GBIF particionada para las especies truncadas en 3.000 registros.
