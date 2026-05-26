"""
07b_present_suitability.py — Proyección de idoneidad PRESENTE (sin forecast).

Genera el mapa de idoneidad del ensemble sobre el clima ACTUAL para cada especie
modelable (grupos A/B). NO proyecta a 2050: el forecast CMIP6 queda **diferido como
mejora futura** (ver README §Roadmap) por su costo computacional —en particular el
MESS global, que aun vectorizado es la etapa más pesada.

Reutiliza las funciones de proyección ya probadas de 07_forecast_2050.py
(load_ensemble, build_predictor_stack, predict_ensemble, reconstruct_raster,
save_geotiff), evitando duplicar lógica.

Salida: outputs/maps/{slug}_present_suitability.tif (uno por especie).
"""
from __future__ import annotations

import argparse
import importlib

import geopandas as gpd

import config
import utils

log = utils.get_logger("07b_present")

# 07_forecast_2050 empieza con dígito → no se puede importar con `import`.
_fc = importlib.import_module("07_forecast_2050")


def modelable_species() -> list[str]:
    """Especies modelables (grupos A/B) según la columna 'grupo' del gpkg limpio."""
    gdf = gpd.read_file(config.OCCURRENCES_CLEAN)
    return sorted(gdf[gdf["grupo"].isin(["A", "B"])]["especie"].dropna().unique().tolist())


def project_present(species: str, present_bio, topo, overwrite: bool = False) -> bool:
    """Proyecta el ensemble de una especie sobre el clima presente y guarda el GeoTIFF."""
    slug = utils.slugify_species(species)
    out = config.MAPS / f"{slug}_present_suitability.tif"
    if out.exists() and not overwrite:
        log.info("  %s: ya existe, omitido.", slug)
        return True
    bundle = _fc.load_ensemble(slug)
    feats = bundle["selected_predictors"]
    # Recorte a Sudamérica: el mapa de idoneidad se enfoca en el continente.
    X, mask, ref = _fc.build_predictor_stack(
        present_bio, topo, feats, extent_bbox=config.PREDICTION_BBOX
    )
    suit = _fc.predict_ensemble(X, bundle)
    da = _fc.reconstruct_raster(suit, mask, ref)
    config.MAPS.mkdir(parents=True, exist_ok=True)
    _fc.save_geotiff(da, out)
    log.info("  %s: guardado %s", slug, out.name)
    return True


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Proyección de idoneidad presente del ensemble (sin forecast 2050)."
    )
    ap.add_argument("--species", default=None, help="Procesar solo esta especie.")
    ap.add_argument("--overwrite", action="store_true", help="Regenerar aunque exista.")
    args = ap.parse_args()

    species = [args.species] if args.species else modelable_species()
    log.info("Especies a proyectar (presente): %d", len(species))

    log.info("Cargando capas presentes compartidas (bioclim + topografía)...")
    topo = _fc.load_topo_layers()
    present_bio = _fc.load_present_bioclim()

    ok = 0
    for sp in species:
        try:
            if project_present(sp, present_bio, topo, overwrite=args.overwrite):
                ok += 1
        except Exception as exc:  # noqa: BLE001
            log.exception("  Error proyectando %s: %s", sp, exc)
    log.info("=== Proyección presente completada: %d/%d especies ===", ok, len(species))


if __name__ == "__main__":
    main()
