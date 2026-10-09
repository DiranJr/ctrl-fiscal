"""Fontes do DANFSe (NT 008, item 2.4: Arial nos títulos, Microsoft Sans Serif nos conteúdos).

Arial e Microsoft Sans Serif são proprietárias: a plataforma NÃO as distribui (nem no repositório nem na imagem).
Fornecimento LOCAL: o operador que tiver licença adequada aponta DANFSE_FONT_DIR para um diretório (montado somente
leitura) com arial.ttf, arialbd.ttf e micross.ttf. Antes de usar, confere-se no próprio arquivo (tabela OS/2,
campo fsType) se a licença da fonte permite incorporação em documento com subconjunto; se não permitir ou faltar
arquivo, usa-se a substituta Liberation Sans (SIL OFL, mesmas métricas da Arial) e o desvio fica registrado.

A escolha aparece no PDF (metadado Keywords) e no cabeçalho HTTP X-Danfse-Fontes. Equivalência métrica NÃO é
declarada como conformidade integral.

Procedência dos arquivos em assets/ (conferida em 08/10/2026):
- LiberationSans-Regular.ttf / -Bold.ttf: liberation-fonts-ttf-2.1.5 (github.com/liberationfonts), SIL OFL 1.1
  (LICENSE-LiberationFonts.txt). SHA-256 76d04c18…8ee8 (Regular) e 788abee4…9173 (Bold).
- logo-nfse-horizontal-480.png: logomarca oficial da NFS-e indicada no item 2.4.3 da NT 008 (gov.br/nfse,
  "Logo - NFS-e - Horizontal.png", original 1920x389), reduzida para 480 px sem outra alteração.
  SHA-256 058b2bf1…f313.
- Especificação aplicada: NT SE/CGNFS-e 008 v1.02 (14/07/2026), SHA-256 1265f403…b0ff.
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass, replace
from pathlib import Path

ASSETS = Path(__file__).parent / "assets"
FS_RESTRICTED = 0x0002  # Restricted License embedding: proibida a incorporação
FS_NO_SUBSET = 0x0100  # proíbe subconjunto (o gerador incorpora subconjuntos)
FS_BITMAP_ONLY = 0x0200


@dataclass(frozen=True)
class FontSet:
    titulos_negrito: Path  # rótulos/títulos (Arial Bold)
    titulos_normal: Path  # marca d'água (Arial)
    conteudo: Path  # conteúdo dos campos (Microsoft Sans Serif)
    origem: str  # LOCAL_LICENCIADA | SUBSTITUTA_LIBERATION
    detalhe: str


LIBERATION = FontSet(ASSETS / "LiberationSans-Bold.ttf", ASSETS / "LiberationSans-Regular.ttf",
                     ASSETS / "LiberationSans-Regular.ttf", "SUBSTITUTA_LIBERATION",
                     "Liberation Sans 2.1.5 (SIL OFL), métricas da Arial; Arial/Microsoft Sans Serif não fornecidas")


def fs_type(path: Path) -> int | None:
    """Lê OS/2.fsType de um TrueType/OpenType (None se não houver tabela OS/2)."""
    data = path.read_bytes()
    num_tables = struct.unpack(">H", data[4:6])[0]
    for i in range(num_tables):
        rec = 12 + 16 * i
        tag = data[rec:rec + 4]
        if tag == b"OS/2":
            offset = struct.unpack(">I", data[rec + 8:rec + 12])[0]
            return struct.unpack(">H", data[offset + 8:offset + 10])[0]
    return None


def embedding_allowed(path: Path) -> tuple[bool, str]:
    t = fs_type(path)
    if t is None:
        return False, "sem tabela OS/2"
    if t & FS_RESTRICTED:
        return False, f"fsType={t:#06x}: incorporação proibida"
    if t & FS_NO_SUBSET:
        return False, f"fsType={t:#06x}: subconjunto proibido"
    if t & FS_BITMAP_ONLY:
        return False, f"fsType={t:#06x}: só bitmap"
    return True, f"fsType={t:#06x}"


def _find(d: Path, name: str) -> Path | None:
    for p in d.iterdir():
        if p.name.lower() == name:
            return p
    return None


def resolve(font_dir: str | None = None) -> FontSet:
    d = font_dir if font_dir is not None else os.environ.get("DANFSE_FONT_DIR", "")
    if not d:
        return LIBERATION
    base = Path(d)
    if not base.is_dir():
        return replace(LIBERATION, detalhe=f"DANFSE_FONT_DIR inexistente; {LIBERATION.detalhe}")
    files = {n: _find(base, n) for n in ("arialbd.ttf", "arial.ttf", "micross.ttf")}
    missing = [n for n, p in files.items() if p is None]
    if missing:
        return replace(LIBERATION, detalhe=f"faltam {', '.join(missing)} em DANFSE_FONT_DIR; {LIBERATION.detalhe}")
    checks = {n: embedding_allowed(p) for n, p in files.items()}
    refused = [f"{n} ({why})" for n, (ok, why) in checks.items() if not ok]
    if refused:
        return replace(LIBERATION, detalhe=f"licença das fontes locais não permite incorporação: {'; '.join(refused)}")
    return FontSet(files["arialbd.ttf"], files["arial.ttf"], files["micross.ttf"], "LOCAL_LICENCIADA",
                   "Arial/Arial Bold/Microsoft Sans Serif do diretório local licenciado ("
                   + ", ".join(f"{n} {why}" for n, (_, why) in checks.items()) + ")")
