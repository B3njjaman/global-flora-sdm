"""
latex_comun.py — utilidades compartidas para generar los documentos del proyecto
en LaTeX (XeLaTeX), con tipografía Calibri 12 pt, blanco y negro.

No es un script ejecutable: lo importan 11_md_a_latex.py y 12_informe_v4_latex.py.

Decisiones de estilo:
  - Fuente Calibri (cuerpo) y Consolas (monoespaciada, para código y diagramas).
  - 12 pt de cuerpo; títulos 14-18 pt en Calibri negrita.
  - Sin colores: todo negro sobre blanco; tablas con líneas finas (booktabs).
  - Se eliminan emojis/símbolos decorativos del texto fuente.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

# --------------------------------------------------------------------------- #
# Preámbulo común
# --------------------------------------------------------------------------- #
PREAMBULO = r"""\documentclass[12pt]{article}

\usepackage{fontspec}
\setmainfont{Calibri}
\setmonofont{Consolas}

\usepackage[a4paper,top=2.5cm,bottom=2.5cm,left=2.5cm,right=2.5cm]{geometry}
\usepackage{array}
\usepackage{booktabs}
\usepackage{tabularx}
\usepackage{graphicx}
\usepackage{enumitem}
\usepackage{microtype}
\usepackage[hidelinks]{hyperref}

\setlist{leftmargin=1.6em, topsep=4pt, itemsep=2pt, parsep=0pt}
\setlength{\parindent}{0pt}
\setlength{\parskip}{6pt plus 1pt}
\linespread{1.06}
\frenchspacing
% Sin patrones de guionado en español: se desactiva el guionado y se afloja la
% justificación (evita cortes incorrectos tipo "mod-elo"; las tablas van en bandera).
\hyphenpenalty=10000
\exhyphenpenalty=10000
\tolerance=2000
\emergencystretch=3em

% Columnas de tabla con peso de ancho propio (\hsize): L texto, R número, C centro.
\newcolumntype{L}[1]{>{\raggedright\arraybackslash\hsize=#1\hsize}X}
\newcolumntype{R}[1]{>{\raggedleft\arraybackslash\hsize=#1\hsize}X}
\newcolumntype{C}[1]{>{\centering\arraybackslash\hsize=#1\hsize}X}
"""

# --------------------------------------------------------------------------- #
# Limpieza de texto y escapado LaTeX
# --------------------------------------------------------------------------- #
# Emojis y símbolos decorativos a eliminar (no toca flechas, ±, ≈, ×, ≥, ≤, —).
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000026FF\U00002700-\U000027BF"
    "\U0000FE00-\U0000FE0F]|⟳|⭐|⭕",
    re.UNICODE,
)

_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_INLINE = re.compile(r"(\*\*.+?\*\*|`[^`]+`|\*[^*]+\*)")

# Símbolos matemáticos que Calibri no tiene; se renderizan en modo matemático.
_SIMBOLOS = {
    "−": r"\ensuremath{-}",        # − minus
    "≈": r"\ensuremath{\approx}",  # ≈
    "≤": r"\ensuremath{\le}",      # ≤
    "≥": r"\ensuremath{\ge}",      # ≥
    "∩": r"\ensuremath{\cap}",     # ∩
    "∈": r"\ensuremath{\in}",      # ∈
    "′": r"\ensuremath{'}",        # ′ prima (arc-min)
}


def quitar_emojis(texto: str) -> str:
    return _EMOJI.sub("", texto)


def _simbolos(s: str) -> str:
    for k, v in _SIMBOLOS.items():
        s = s.replace(k, v)
    return s


def escapar(s: str) -> str:
    """Escapa los caracteres especiales de LaTeX. XeLaTeX maneja el resto de Unicode."""
    s = s.replace("\\", "\x00BS\x00")
    for a, b in (("&", r"\&"), ("%", r"\%"), ("$", r"\$"), ("#", r"\#"),
                 ("_", r"\_"), ("{", r"\{"), ("}", r"\}")):
        s = s.replace(a, b)
    s = s.replace("~", r"\textasciitilde{}").replace("^", r"\textasciicircum{}")
    s = s.replace("\x00BS\x00", r"\textbackslash{}")
    return s


def _txt(s: str) -> str:
    return _simbolos(escapar(s))


def _codigo_inline(s: str) -> str:
    """`código` con puntos de corte (\\allowbreak) para que los identificadores largos
    no se monten sobre la columna siguiente en las tablas."""
    e = escapar(s)
    e = e.replace(r"\_", r"\_\allowbreak{}").replace("/", r"/\allowbreak{}")
    e = e.replace(".", r".\allowbreak{}")
    return r"\texttt{" + e + "}"


def inline(texto: str) -> str:
    """Convierte **negrita**, *cursiva*, `código` y [texto](url) a LaTeX."""
    texto = _LINK.sub(r"\1", texto)
    out: list[str] = []
    for tok in _INLINE.split(texto):
        if not tok:
            continue
        if tok.startswith("**") and tok.endswith("**") and len(tok) > 4:
            out.append(r"\textbf{" + _txt(tok[2:-2]) + "}")
        elif tok.startswith("`") and tok.endswith("`") and len(tok) > 2:
            out.append(_codigo_inline(tok[1:-1]))
        elif tok.startswith("*") and tok.endswith("*") and len(tok) > 2:
            out.append(r"\textit{" + _txt(tok[1:-1]) + "}")
        else:
            out.append(_txt(tok))
    return "".join(out)


# --------------------------------------------------------------------------- #
# Bloques de alto nivel
# --------------------------------------------------------------------------- #
_TITULO = {
    1: r"\fontsize{18}{22}\selectfont\bfseries",
    2: r"\fontsize{14}{18}\selectfont\bfseries",
    3: r"\fontsize{12.5}{16}\selectfont\bfseries",
}


def titulo(nivel: int, texto: str) -> str:
    estilo = _TITULO.get(nivel, r"\fontsize{12}{15}\selectfont\bfseries")
    pre = "16pt" if nivel <= 2 else "10pt"
    return (f"\n\\par\\addvspace{{{pre}}}\\noindent{{{estilo} "
            + inline(texto) + "}\\par\\addvspace{4pt}\n")


def parrafo(texto: str) -> str:
    return "\n" + inline(texto) + "\n"


def lista(items: list[str], ordenada: bool) -> str:
    env = "enumerate" if ordenada else "itemize"
    cuerpo = "".join(f"  \\item {inline(it)}\n" for it in items)
    return f"\n\\begin{{{env}}}\n{cuerpo}\\end{{{env}}}\n"


def cita(texto: str) -> str:
    return "\n\\begin{quote}\n" + inline(texto) + "\n\\end{quote}\n"


def codigo(lineas: list[str]) -> str:
    # Tamaño adaptativo: las líneas anchas (diagramas) se achican para no salirse
    # del margen (verbatim no parte líneas). Consolas ~0.6 em/carácter; ancho útil ~455 pt.
    maxlen = max((len(l) for l in lineas), default=0)
    fs = max(6.0, min(10.0, 760.0 / max(maxlen, 1)))
    cuerpo = "\n".join(lineas)
    return (f"\n{{\\fontsize{{{fs:.1f}}}{{{fs * 1.2:.1f}}}\\selectfont\n"
            "\\begin{verbatim}\n" + cuerpo + "\n\\end{verbatim}\n}\n")


_NUM = re.compile(r"^[\s$+\-±~≈≤≥−<>=.,/%()0-9]*\d[\s$+\-±~≈≤≥−<>=.,/%()0-9]*$")


def _peso_y_alineacion(encabezados, filas):
    ncol = len(encabezados)
    letras, pesos = [], []
    for c in range(ncol):
        col = [(f[c] if c < len(f) else "") for f in filas]
        col = [x for x in col if x.strip()]
        numericos = sum(1 for x in col if _NUM.match(x.strip())) if col else 0
        es_num = col and numericos >= 0.5 * len(col)
        letras.append("R" if es_num else "L")
        pesos.append(0.7 if es_num else 1.8)
    escala = ncol / sum(pesos)
    pesos = [p * escala for p in pesos]
    return letras, pesos


def tabla(encabezados: list[str], filas: list[list[str]]) -> str:
    ncol = len(encabezados)
    letras, pesos = _peso_y_alineacion(encabezados, filas)
    colspec = "".join(f"{l}{{{p:.3f}}}" for l, p in zip(letras, pesos))
    enc = " & ".join(r"\textbf{" + inline(h) + "}" for h in encabezados) + r" \\"
    cuerpo = []
    for f in filas:
        celdas = [inline(f[c]) if c < len(f) else "" for c in range(ncol)]
        cuerpo.append(" & ".join(celdas) + r" \\")
    return ("\n{\\small\n"
            f"\\begin{{tabularx}}{{\\textwidth}}{{{colspec}}}\n"
            "\\toprule\n" + enc + "\n\\midrule\n"
            + "\n".join(cuerpo) + "\n"
            "\\bottomrule\n\\end{tabularx}\n}\n")


def figura(ruta: Path, ancho: float = 0.85, pie: str | None = None) -> str:
    p = Path(ruta).as_posix()
    s = ("\n\\begin{center}\n"
         f"\\includegraphics[width={ancho:.2f}\\textwidth]{{{p}}}\n")
    if pie:
        s += "\\\\[3pt]{\\small\\itshape " + inline(pie) + "}\n"
    s += "\\end{center}\n"
    return s


def envolver(cuerpo: str) -> str:
    return PREAMBULO + "\n\\begin{document}\n" + cuerpo + "\n\\end{document}\n"


# --------------------------------------------------------------------------- #
# Compilación
# --------------------------------------------------------------------------- #
def compilar_a_pdf(tex: str, salida_pdf: Path, jobname: str = "doc") -> Path:
    """Escribe el .tex en un dir temporal, corre XeLaTeX 2 veces y copia el PDF."""
    salida_pdf.parent.mkdir(parents=True, exist_ok=True)
    jobname = re.sub(r"[^A-Za-z0-9_-]", "_", jobname)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / f"{jobname}.tex").write_text(tex, encoding="utf-8")
        res = None
        for _ in range(2):
            res = subprocess.run(
                ["xelatex", "-interaction=nonstopmode", "-halt-on-error",
                 "-jobname", jobname, f"{jobname}.tex"],
                cwd=tmp, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
        pdf = tmp / f"{jobname}.pdf"
        if not pdf.exists():
            log = tmp / f"{jobname}.log"
            cola = (log.read_text(encoding="utf-8", errors="replace")[-4000:]
                    if log.exists() else (res.stdout[-4000:] if res else ""))
            raise RuntimeError(f"XeLaTeX no generó PDF ({jobname}):\n{cola}")
        shutil.copyfile(pdf, salida_pdf)
    return salida_pdf
