"""
11_md_a_latex.py — Convierte los documentos Markdown de docs/ a PDF vía LaTeX.

Conversor propio (no requiere pandoc) que emite LaTeX y lo compila con XeLaTeX.
Tipografía Calibri 12 pt, blanco y negro. Soporta títulos (#..######), párrafos,
listas con viñetas/numeradas, tablas, citas (>), bloques de código (```), y
formato inline (**negrita**, *cursiva*, `código`, [texto](enlace)).

Convierte cada docs/**/*.md a un .pdf hermano. Salta informe_v4 (lo genera
12_informe_v4_latex.py, que embebe los mapas).

Uso: python scripts/11_md_a_latex.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import latex_comun as L  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
DOCS = _ROOT / "docs"

_SEP = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")  # fila separadora de tabla
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBER = re.compile(r"^\s*\d+\.\s+(.*)$")
_HRULE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")


def _celdas(linea: str) -> list[str]:
    s = linea.strip()
    s = s.replace(r"\|", "\x00")  # pipe escapado dentro de la celda
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip().replace("\x00", "|") for c in s.split("|")]


def procesar(lineas: list[str]) -> str:
    """Convierte una lista de líneas Markdown a cuerpo LaTeX. Reutilizable para el
    interior de las citas (que pueden contener viñetas y párrafos)."""
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(lineas)

    def flush():
        if buf:
            out.append(L.parrafo(" ".join(buf).strip()))
            buf.clear()

    while i < n:
        ln = lineas[i]
        s = ln.strip()

        # bloque de código
        if s.startswith("```"):
            flush()
            i += 1
            code: list[str] = []
            while i < n and not lineas[i].strip().startswith("```"):
                code.append(lineas[i])
                i += 1
            i += 1  # cierre ```
            out.append(L.codigo(code))
            continue

        # línea en blanco
        if not s:
            flush()
            i += 1
            continue

        # título
        if s.startswith("#"):
            flush()
            nivel = len(s) - len(s.lstrip("#"))
            out.append(L.titulo(nivel, s[nivel:].strip()))
            i += 1
            continue

        # regla horizontal
        if _HRULE.match(s):
            flush()
            i += 1
            continue

        # tabla (fila + separadora debajo)
        if "|" in ln and i + 1 < n and _SEP.match(lineas[i + 1]):
            flush()
            encabezados = _celdas(ln)
            i += 2
            filas = []
            while i < n and "|" in lineas[i] and lineas[i].strip():
                filas.append(_celdas(lineas[i]))
                i += 1
            out.append(L.tabla(encabezados, filas))
            continue

        # cita (acumula líneas > consecutivas y procesa su interior recursivamente)
        if s.startswith(">"):
            flush()
            internas = []
            while i < n and lineas[i].strip().startswith(">"):
                t = lineas[i].strip()
                t = t[1:] if t.startswith(">") else t
                internas.append(t[1:] if t.startswith(" ") else t)
                i += 1
            out.append("\n\\begin{quote}\n" + procesar(internas) + "\n\\end{quote}\n")
            continue

        # lista con viñetas (acumula items consecutivos)
        if _BULLET.match(ln):
            flush()
            items = []
            while i < n and _BULLET.match(lineas[i]):
                items.append(_BULLET.match(lineas[i]).group(1))
                i += 1
            out.append(L.lista(items, ordenada=False))
            continue

        # lista numerada
        if _NUMBER.match(ln):
            flush()
            items = []
            while i < n and _NUMBER.match(lineas[i]):
                items.append(_NUMBER.match(lineas[i]).group(1))
                i += 1
            out.append(L.lista(items, ordenada=True))
            continue

        # texto normal
        buf.append(s)
        i += 1

    flush()
    return "".join(out)


def convertir(md_path: Path) -> str:
    lineas = L.quitar_emojis(md_path.read_text(encoding="utf-8")).splitlines()
    return L.envolver(procesar(lineas))


def main():
    objetivo = [p for p in sorted(DOCS.rglob("*.md")) if p.stem != "informe_v4"]
    if not objetivo:
        print("No hay .md en docs/.")
        return
    print(f"Convirtiendo {len(objetivo)} documentos a PDF (LaTeX)...")
    for md in objetivo:
        pdf = md.with_suffix(".pdf")
        try:
            tex = convertir(md)
            L.compilar_a_pdf(tex, pdf, jobname=md.stem)
            print(f"  OK {md.relative_to(_ROOT)} -> {pdf.name}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR {md.name}: {exc}")
    print(f"Listo. PDF en {DOCS}/ (y docs/v4/).")


if __name__ == "__main__":
    main()
