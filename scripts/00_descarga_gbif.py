"""
00_descarga_gbif.py — "Scrap 0": descarga de ocurrencias desde la API de GBIF.

Parte de cero: baja TODAS las ocurrencias con coordenadas de las 21 especies en
Sudamérica directamente de la API de GBIF (que ya agrega iNaturalist, herbarios y
museos), sin el techo de ~3.000 del export previo. No hace scraping de HTML: usa
la API REST oficial (más rápida, completa y legítima).

Paralelo: 10 hilos (descarga = I/O), una especie por tarea. Para cada especie
resuelve el taxonKey (incluye sinónimos) y pagina la búsqueda de ocurrencias.

Salida: rama_v4/data/processed/00_ocurrencias_gbif_crudo.csv (columnas normalizadas
al esquema que consume la limpieza V4).

Uso: python scripts/00_descarga_gbif.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import requests
from joblib import Parallel, delayed

_ROOT = Path(__file__).resolve().parents[1]
SALIDA = _ROOT / "rama_v4" / "data" / "processed" / "00_ocurrencias_gbif_crudo.csv"

ESPECIES = [
    "Aloysia salviifolia", "Atriplex deserticola", "Atriplex semibaccata",
    "Caesalpinia angulata", "Centaurea chilensis", "Cumulopuntia sphaerica",
    "Dinemagonum gayanum", "Encelia canescens", "Eulychnia acida",
    "Krameria cistoidea", "Miqueliopuntia miquelii", "Neltuma chilensis",
    "Nolana albescens", "Nolana divaricata", "Nolana rostrata",
    "Nolana sedifolia", "Oxalis gigantea", "Pleurophora pungens",
    "Schinus areira", "Senna cumingii", "Skytanthus acutus",
]

# Sudamérica (códigos ISO-2 GBIF)
PAISES_SA = ["CL", "AR", "PE", "BO", "CO", "BR", "EC", "PY", "UY", "VE", "GY", "SR", "GF", "FK"]

API = "https://api.gbif.org/v1"

# GBIF campo -> nuestra columna (esquema de la limpieza)
CAMPOS = {
    "scientificName": "nombre_cientifico",
    "decimalLatitude": "lat",
    "decimalLongitude": "lon",
    "coordinateUncertaintyInMeters": "incertidumbre_m",
    "country": "pais",
    "stateProvince": "region",
    "locality": "localidad",
    "eventDate": "fecha",
    "year": "ano",
    "basisOfRecord": "tipo_registro",
    "institutionCode": "institucion",
    "datasetName": "dataset",
    "catalogNumber": "catalogo",
    "key": "gbif_id",
}


def descargar_especie(nombre: str) -> pd.DataFrame:
    """Baja todas las ocurrencias con coordenadas de `nombre` en Sudamérica.

    Usa el parámetro `scientificName` (matching por nombre del backbone GBIF, que
    incluye sinónimos) en vez de un taxonKey estricto: para varias especies
    (Centaurea, Caesalpinia, Neltuma=Prosopis) el taxonKey apuntaba a un nodo con
    muy pocos registros y se perdían cientos de ocurrencias válidas.
    """
    registros: list[dict] = []
    offset, limit = 0, 300
    while True:
        params = [("hasCoordinate", "true"), ("limit", limit), ("offset", offset)]
        params += [("country", c) for c in PAISES_SA]
        params.append(("scientificName", nombre))
        r = requests.get(f"{API}/occurrence/search", params=params, timeout=60)
        data = r.json()
        for rec in data.get("results", []):
            fila = {col: rec.get(g) for g, col in CAMPOS.items()}
            fila["especie"] = nombre            # nombre canónico (para agrupar)
            registros.append(fila)
        offset += limit
        if data.get("endOfRecords", True) or offset >= 100_000:
            break
    df = pd.DataFrame(registros)
    print(f"  {nombre:26s} {len(df):6d} registros")
    return df


def main() -> None:
    print(f"Descargando 21 especies de GBIF (Sudamérica, con coordenadas) en 10 hilos...")
    partes = Parallel(n_jobs=10, backend="threading")(
        delayed(descargar_especie)(sp) for sp in ESPECIES
    )
    df = pd.concat([p for p in partes if not p.empty], ignore_index=True)
    cols = ["especie", "nombre_cientifico", "lat", "lon", "incertidumbre_m", "pais",
            "region", "localidad", "fecha", "ano", "tipo_registro", "institucion",
            "dataset", "catalogo", "gbif_id"]
    df = df.reindex(columns=cols)
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(SALIDA, index=False, encoding="utf-8")
    print(f"\nTOTAL crudo descargado: {len(df)} registros, {df['especie'].nunique()} especies")
    print(f"Guardado: {SALIDA}")


if __name__ == "__main__":
    main()
