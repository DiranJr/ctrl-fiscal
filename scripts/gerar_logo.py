"""Gera a logomarca do CTRL Fiscal em SVG (vetor), a partir do desenho aprovado pelo titular em 08/10/2026.

Marca: "C" (colchete) verde-escuro, canto de documento e "check" em verde-claro, traço verde-escuro saindo do check.
Texto "CTRL" (negrito) e "Fiscal" (regular) convertidos em CURVAS com Liberation Sans 2.1.5 (SIL OFL, já no
repositório em services/fiscal-api/ctrl_fiscal/danfse/assets): o SVG não depende de fonte instalada.

Saídas (mesmos arquivos nos dois frontends):
  logo-ctrl-fiscal.svg  marca + nome (cabeçalhos e telas de acesso)
  marca-ctrl-fiscal.svg somente a marca (favicon e espaços estreitos)

Requer fontTools (ferramenta de geração; não é dependência do produto):
  %LOCALAPPDATA%\\ctrl-fiscal-tools\\pwv\\Scripts\\python -I scripts\\gerar_logo.py
"""
from __future__ import annotations

from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
FONTS = ROOT / "services/fiscal-api/ctrl_fiscal/danfse/assets"
DARK = "#05442F"   # verde-escuro da marca
LIGHT = "#00A24D"  # verde-claro (check e canto do documento)
OUT_DIRS = [ROOT / "apps/painel-fiscal/public", ROOT / "apps/web/public"]

# Marca, em coordenadas do desenho original (caixa 90..460 x 160..560)
MARK = f"""<path d="M340 195.5H215a88 88 0 0 0-88 88V436a88 88 0 0 0 88 88h210" fill="none" stroke="{DARK}" stroke-width="65"/>
<path d="M357 172v88a8 8 0 0 0 8 8h84c7 0 10-8 5-13l-84-84c-5-5-13-2-13 4z" fill="{LIGHT}"/>
<path d="M209 354 280 425 388 317" fill="none" stroke="{LIGHT}" stroke-width="58" stroke-linejoin="round"/>
<path d="M366 299 380 285H450a8 8 0 0 1 8 8v40a8 8 0 0 1-8 8H408z" fill="{DARK}"/>"""


def text_path(font_file: str, text: str, x: float, baseline: float, cap_px: float, tracking: float = 0.0) -> tuple[str, float]:
    f = TTFont(str(FONTS / font_file))
    gs = f.getGlyphSet()
    cmap = f.getBestCmap()
    scale = cap_px / f["OS/2"].sCapHeight
    hmtx = f["hmtx"]
    paths = []
    for ch in text:
        name = cmap[ord(ch)]
        pen = SVGPathPen(gs)
        gs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, x, baseline)))
        paths.append(pen.getCommands())
        x += hmtx[name][0] * scale + tracking
    return "".join(paths), x - tracking


def main() -> None:
    cap = 237.0
    ctrl, end = text_path("LiberationSans-Bold.ttf", "CTRL", 538, 487, cap, tracking=-6)
    fiscal, end = text_path("LiberationSans-Regular.ttf", "Fiscal", end + 52, 487, cap)
    width = round(end + 70)
    full = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="60 130 {width - 60} 460" role="img" aria-label="CTRL Fiscal">'
            f"<title>CTRL Fiscal</title>{MARK}"
            f'<path d="{ctrl}" fill="{DARK}"/><path d="{fiscal}" fill="{DARK}"/></svg>\n')
    mark = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="70 140 410 440" role="img" aria-label="CTRL Fiscal">'
            f"<title>CTRL Fiscal</title>{MARK}</svg>\n")
    for d in OUT_DIRS:
        d.mkdir(parents=True, exist_ok=True)
        (d / "logo-ctrl-fiscal.svg").write_text(full, encoding="utf-8")
        (d / "marca-ctrl-fiscal.svg").write_text(mark, encoding="utf-8")
    print("gerado:", [str(d.relative_to(ROOT)) for d in OUT_DIRS], "largura", width - 60)


if __name__ == "__main__":
    main()
