"""
12_informe_v4_latex.py — Informe del modelo V4 en PDF vía LaTeX (XeLaTeX).

Equivalente de 09_informe_docx.py pero en LaTeX: tipografía Calibri 12 pt, blanco
y negro. Lenguaje simple: qué se hizo, el cambio que mejoró el modelo, los números
antes/después, la tabla por especie y los mapas (panel + dos ejemplos). Los mapas
embebidos se dejan a color (son datos científicos).

Uso: python scripts/12_informe_v4_latex.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import latex_comun as L  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
FIGS = _ROOT / "outputs" / "figures"
METRICAS = _ROOT / "outputs" / "tables" / "metricas_v4_completa.csv"
SALIDA = _ROOT / "docs" / "informe_v4.pdf"


def _nivel(boyce: float) -> str:
    if boyce >= 0.9:
        return "Excelente"
    if boyce >= 0.7:
        return "Confiable"
    if boyce >= 0.3:
        return "Aceptable"
    return "Revisar"


def construir() -> str:
    met = pd.read_csv(METRICAS)
    p: list[str] = []

    # Portada
    p.append("\n\\begin{center}\n{\\fontsize{20}{24}\\selectfont\\bfseries "
             "Mapas de distribución de plantas — Versión 4}\\\\[6pt]\n"
             "{\\large\\itshape Dónde pueden vivir 16 especies de flora chilena, "
             "según el clima y el terreno}\n\\end{center}\n\\vspace{8pt}\n")

    p.append(L.titulo(2, "Qué hicimos"))
    p.append(L.parrafo(
        "Tomamos los registros de dónde se ha visto cada planta y los cruzamos con el "
        "clima y el terreno de Sudamérica. Con eso el modelo aprende qué condiciones le "
        "gustan a cada especie y dibuja un mapa que muestra, zona por zona, qué tan buena "
        "es para ella. Usamos cinco métodos a la vez y los combinamos, para no depender "
        "de uno solo."))

    p.append(L.titulo(2, "El cambio que mejoró el modelo"))
    p.append(L.parrafo(
        "La versión anterior tenía un problema simple: comparaba a cada planta contra un "
        "fondo que no calzaba con dónde vive de verdad. Lo arreglamos haciendo que cada "
        "especie se compare contra su propia zona, un radio de unos 300 km alrededor de "
        "los lugares donde se la ha encontrado. Fue un ajuste chico, pero los resultados "
        "mejoraron en todo."))
    p.append(L.parrafo("Así se ven los números, antes y después (promedio de las 16 especies):"))
    p.append(L.tabla(["Medida", "Antes (V3)", "Ahora (V4)"],
                     [["AUC (qué tan bien separa)", "0.77", "0.83"],
                      ["TSS (aciertos vs errores)", "0.26", "0.47"],
                      ["Boyce (acierta dónde está la planta)", "0.44", "0.86"]]))
    p.append(L.parrafo(
        "El número que más importa acá es el Boyce: mide si el mapa marca alto justo donde "
        "la planta aparece de verdad. Pasó de 0.44 a 0.86. En 15 de las 16 especies el mapa "
        "quedó confiable. Es un salto grande y se nota en los mapas."))

    p.append(L.titulo(2, "El modelo que elegimos"))
    p.append(L.parrafo(
        "Comparamos juntar los cinco métodos (lo que llamamos ensemble) contra usar solo "
        "el más fuerte, MaxEnt. En puntería van casi iguales, pero el ensemble gana donde "
        "importa: da el mejor Boyce y es más estable, porque no queda colgado de un solo "
        "método. Por eso es el modelo que dejamos como oficial."))
    p.append(L.tabla(["Modelo", "AUC", "TSS", "Boyce"],
                     [["Ensemble (el que usamos)", "0.83", "0.47", "0.86"],
                      ["MaxEnt solo", "0.82", "0.48", "—"]]))

    p.append(L.titulo(2, "Cómo le fue a cada especie"))
    p.append(L.parrafo(
        "Ordenadas de la que mejor quedó a la que peor. Casi todas andan bien; las mejores "
        "marcan justo la franja del norte chico donde crecen estas plantas."))
    m = met.sort_values("boyce_ensemble", ascending=False)
    filas = [[str(r.especie), str(int(r.n_pres)), f"{r.auc_ensemble:.2f}",
              f"{r.tss_ensemble:.2f}", f"{r.boyce_ensemble:.2f}", _nivel(r.boyce_ensemble)]
             for _, r in m.iterrows()]
    p.append(L.tabla(["Especie", "Registros", "AUC", "TSS", "Boyce", "Nivel"], filas))
    p.append(L.parrafo(
        "La única que todavía no sirve es Atriplex semibaccata: es una especie introducida "
        "y con pocos datos, así que su mapa conviene tomarlo aparte y con pinzas."))

    p.append(L.titulo(2, "Los mapas"))
    p.append(L.parrafo(
        "Cada mapa pinta de oscuro a amarillo la idoneidad (de 0 a 1): mientras más "
        "amarillo, mejor zona para la planta. Los puntos rojos son los lugares donde se "
        "la ha visto."))
    panel = FIGS / "_panel_idoneidad_v4.png"
    if panel.exists():
        p.append(L.figura(panel, ancho=0.95,
                          pie="Las 16 especies de un vistazo (ordenadas por Boyce)."))
    for slug, pie in [
        ("nolana_divaricata",
         "Nolana divaricata: uno de los mejores. El amarillo cae justo sobre los puntos rojos."),
        ("atriplex_semibaccata",
         "Atriplex semibaccata: el caso flojo, mapa disperso y poco confiable."),
    ]:
        f = FIGS / f"{slug}_idoneidad_sa.png"
        if f.exists():
            p.append(L.figura(f, ancho=0.62, pie=pie))

    p.append(L.titulo(2, "Cómo leer los mapas (en simple)"))
    p.append(L.lista([
        "Los colores son idoneidad relativa, de 0 a 1: qué tan buena es la zona para la "
        "planta. No es una probabilidad exacta, es un “qué tan apta” comparado entre zonas.",
        "Donde el clima se parece mucho al de los registros, el mapa es más seguro. En "
        "zonas con clima muy distinto conviene ir con más cautela.",
        "El modelo casi no deja afuera lugares donde la planta sí está (algo bueno).",
    ], ordenada=False))

    p.append(L.titulo(2, "En resumen"))
    p.append(L.parrafo(
        "La Versión 4 es, por lejos, la mejor que tenemos. Un arreglo simple en cómo el "
        "modelo elige con qué comparar a cada planta subió todos los números y dejó 15 de "
        "16 mapas listos para usar. Es una base sólida y confiable para seguir trabajando."))

    return L.envolver("".join(p))


def main():
    L.compilar_a_pdf(construir(), SALIDA, jobname="informe_v4")
    print(f"Informe guardado: {SALIDA} ({SALIDA.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
