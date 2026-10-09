"""Renderização do DANFSe em PDF (A4 retrato, página única) conforme o Anexo I e o item 2.4.5 da NT 008 v1.02.

Medidas da NT (cm, origem no canto superior esquerdo): conteúdo de x=0,30 a 20,70; colunas em 0,30 / 5,41 /
10,51 / 15,62 (largura 5,09); cabeçalho 1,16; QR Code ≥ 1,52 cm em (17,48 ; 1,67). Fontes: títulos de bloco 7 pt
negrito caixa alta; títulos de campo 6 pt negrito; conteúdo 7 pt; identificação do documento 7 pt caixa alta;
cabeçalho 9 pt (título) / 8 pt (município) / 6 pt (ambientes). Linhas divisórias 0,5 pt, borda da página 1 pt,
sombreamento cinza 5% no cabeçalho, nos títulos de bloco, em "Emitente da NFS-e" e "Valor Líquido + IBS/CBS".
Marca d'água diagonal ≥ 50 pt, cinza K35, para CANCELADA / SUBSTITUÍDA (item 2.5).

Fontes: Arial / Microsoft Sans Serif são proprietárias e não podem ser distribuídas na imagem; usa-se Liberation
Sans (SIL OFL), metricamente compatível com Arial (mesmas larguras de caractere), embutida no PDF.
O bloco opcional "Canhoto" (2.3.3) não é impresso; o espaço vai para Descrição do Serviço / Informações Complementares.
Nada além do conteúdo do XML é impresso (item 2.1): a origem do arquivo fica só nos metadados do PDF.
"""
from __future__ import annotations

import io
from pathlib import Path

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import CMYKColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from .campos import DASH, Danfse, Pessoa
from .fontes import FontSet
from .fontes import resolve as resolve_fontes

ASSETS = Path(__file__).parent / "assets"
FONT, FONT_B, FONT_A = "DanfseSans", "DanfseSans-Bold", "DanfseSans"  # definidos por _fonts() a cada render
PRETO = CMYKColor(0, 0, 0, 1)
CINZA_5 = CMYKColor(0, 0, 0, 0.05)
CINZA_K35 = CMYKColor(0, 0, 0, 0.35)
VERMELHO = CMYKColor(0, 1, 1, 0)
PAGE_W, PAGE_H = A4
X0, W = 0.30, 20.40
COLS = (0.30, 5.41, 10.51, 15.62)
COL_W = 5.09
ROW = 0.64  # 0,63 da NT + 0,01 de respiro entre linhas
LINE_7 = 7 * 1.18 / 72 * 2.54  # entrelinha do conteúdo 7 pt, em cm
BOTTOM = 29.40  # limite inferior do conteúdo (borda em 29,50)
TEXTO_QR = ("A autenticidade desta NFS-e pode ser verificada", "pela leitura deste código QR ou pela consulta da",
            "chave de acesso no portal nacional da NFS-e")
_registered: set[str] = set()


def _fonts(fs: "FontSet") -> None:
    """Conteúdo = Microsoft Sans Serif (ou substituta); títulos = Arial Bold; marca d'água = Arial."""
    global FONT, FONT_B, FONT_A
    names = {}
    for role, path in (("conteudo", fs.conteudo), ("titulos_b", fs.titulos_negrito), ("titulos", fs.titulos_normal)):
        name = f"Danfse-{role}-{fs.origem}"
        if name not in _registered:
            pdfmetrics.registerFont(TTFont(name, str(path)))
            _registered.add(name)
        names[role] = name
    FONT, FONT_B, FONT_A = names["conteudo"], names["titulos_b"], names["titulos"]


def _fit(text: str, width_cm: float, font: str, size: float) -> str:
    text = " ".join((text or DASH).split())
    w = width_cm * cm
    if pdfmetrics.stringWidth(text, font, size) <= w:
        return text
    while text and pdfmetrics.stringWidth(text + "...", font, size) > w:
        text = text[:-1]
    return text.rstrip() + "..."


def _wrap(text: str, width_cm: float, font: str, size: float) -> list[str]:
    w = width_cm * cm
    out: list[str] = []
    for para in (text or "").replace("\r\n", "\n").split("\n"):
        words = para.split()
        if not words:
            out.append("")
            continue
        line = ""
        for word in words:
            cand = f"{line} {word}" if line else word
            if pdfmetrics.stringWidth(cand, font, size) <= w:
                line = cand
                continue
            if line:
                out.append(line)
            while pdfmetrics.stringWidth(word, font, size) > w:  # palavra maior que a linha
                cut = len(word)
                while cut > 1 and pdfmetrics.stringWidth(word[:cut], font, size) > w:
                    cut -= 1
                out.append(word[:cut])
                word = word[cut:]
            line = word
        out.append(line)
    while out and out[-1] == "":
        out.pop()
    return out


class _Pdf:
    def __init__(self, c: Canvas) -> None:
        self.c = c

    def y(self, top_cm: float) -> float:
        return PAGE_H - top_cm * cm

    def text(self, x: float, top: float, s: str, font: str = FONT, size: float = 7, color=PRETO) -> None:  # noqa: ANN001
        self.c.setFillColor(color)
        self.c.setFont(font, size)
        self.c.drawString(x * cm, self.y(top), s)

    def center(self, x: float, w: float, top: float, s: str, font: str, size: float, color=PRETO) -> None:  # noqa: ANN001
        self.c.setFillColor(color)
        self.c.setFont(font, size)
        self.c.drawCentredString((x + w / 2) * cm, self.y(top), s)

    def shade(self, x: float, top: float, w: float, h: float) -> None:
        self.c.setFillColor(CINZA_5)
        self.c.rect(x * cm, self.y(top + h), w * cm, h * cm, stroke=0, fill=1)

    def hline(self, top: float) -> None:
        self.c.setStrokeColor(PRETO)
        self.c.setLineWidth(0.5)
        self.c.line(X0 * cm, self.y(top), (X0 + W) * cm, self.y(top))

    def field(self, col: int, top: float, label: str, value: str, span: int = 1, label_size: float = 6,
              upper: bool = False, value_size: float = 7) -> None:
        x = COLS[col] + 0.05
        width = COL_W * span + 0.02 * (span - 1) - 0.10
        self.text(x, top + 0.23, _fit(label.upper() if upper else label, width, FONT_B, label_size), FONT_B, label_size)
        self.text(x, top + 0.52, _fit(value, width, FONT, value_size), FONT, value_size)


def _block_title(p: _Pdf, top: float, title: str, h: float = ROW) -> None:
    p.shade(COLS[0], top, COL_W, h)
    p.text(COLS[0] + 0.05, top + 0.30, _fit(title.upper(), COL_W - 0.1, FONT_B, 7), FONT_B, 7)


def _msg_block(p: _Pdf, top: float, msg: str) -> float:
    """Bloco suprimido (notas 2, 3 e 4): só a mensagem, altura mínima 0,32 cm, largura 20,40 cm."""
    p.hline(top)
    p.center(X0, W, top + 0.24, msg, FONT, 7)
    return top + 0.32


def _pessoa_rows(p: _Pdf, top: float, title: str, ps: Pessoa, with_im: bool = True) -> float:
    p.hline(top)
    _block_title(p, top, title)
    p.field(1, top, "CNPJ / CPF / NIF", ps.doc)
    if with_im:
        p.field(2, top, "Indicador Municipal (Inscrição)", ps.im)
    p.field(3, top, "Telefone", ps.fone)
    r = top + ROW
    p.field(0, r, "Nome / Nome Empresarial", ps.nome, span=2)
    p.field(2, r, "Município / Sigla UF", ps.municipio)
    p.field(3, r, "Código IBGE / CEP", ps.ibge_cep)
    r += ROW
    p.field(0, r, "Endereço", ps.endereco, span=2)
    p.field(2, r, "E-mail", ps.email, span=2)
    return r + ROW


def _height_info(d: Danfse) -> tuple[list[str], int]:
    lines = _wrap(d.info_complementar, W - 0.10, FONT, 7) if d.info_complementar else []
    return lines, len(lines) + 1  # + linha fixa dos totais aproximados


def render(d: Danfse, marca: str | None = None, *, title: str = "DANFSe", subject: str = "",
           fontes: "FontSet | None" = None) -> bytes:
    """marca: None | 'CANCELADA' | 'SUBSTITUÍDA' (definida pelos eventos oficiais da NFS-e)."""
    fs = fontes or resolve_fontes()
    _fonts(fs)
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=A4, pageCompression=1, invariant=1, initialFontName=FONT, initialFontSize=7)
    c.setTitle(title)
    c.setAuthor("")
    c.setCreator("CTRL Fiscal")
    c.setProducer("CTRL Fiscal - DANFSe gerado pela plataforma a partir do XML oficial (NT SE/CGNFS-e 008 v1.02)")
    c.setSubject(subject)
    c.setKeywords(f"fontes={fs.origem}; {fs.detalhe}")
    p = _Pdf(c)

    # ---- Pré-cálculo de alturas (página única): blocos fixos + áreas variáveis
    def pessoa_h(ps: Pessoa | None) -> float:
        return 3 * ROW if ps is not None else 0.32

    fixed = (4.34 + ROW * 4 + pessoa_h(d.tomador) + pessoa_h(d.destinatario) + pessoa_h(d.intermediario)
             + ROW + 0.40 + 0.30  # serviço + descrição do código + rótulo "Descrição do Serviço"
             + ROW * 4 + ROW * (2 if d.imprime_pis_cofins_proprio else 1) + ROW * 4 + 0.70 * 2
             + 0.39 + 0.10)
    info_lines, info_n = _height_info(d)
    desc_lines = _wrap(d.descricao, W - 0.10, FONT, 7)
    free = BOTTOM - fixed
    need_desc = max(len(desc_lines), 2) * LINE_7 + 0.10
    need_info = info_n * LINE_7 + 0.10
    if need_desc + need_info <= free:
        h_desc = need_desc + (free - need_desc - need_info)  # sobra vai para a descrição (como no modelo)
        h_info = need_info
    else:
        h_info = min(need_info, max(free * 0.45, 2 * LINE_7 + 0.10))
        h_desc = free - h_info

    # ---- Sombreamentos (antes da marca d'água e do texto)
    p.shade(X0, 0.30, W, 1.16)

    # ---- Marca d'água (abaixo do conteúdo para não ocultar campos)
    if marca:
        c.saveState()
        c.setFillColor(CINZA_K35)
        c.setFont(FONT_A, 96)
        c.translate(PAGE_W / 2, PAGE_H / 2)
        c.rotate(55)
        c.drawCentredString(0, -30, marca)
        c.restoreState()

    # ---- Borda da página (1 pt)
    c.setStrokeColor(PRETO)
    c.setLineWidth(1)
    c.rect(0.20 * cm, p.y(29.50), 20.60 * cm, 29.30 * cm, stroke=1, fill=0)

    # ---- Cabeçalho
    c.drawImage(str(ASSETS / "logo-nfse-horizontal-480.png"), 0.49 * cm, p.y(0.44 + 0.85), width=4.00 * cm,
                height=0.85 * cm, preserveAspectRatio=True, anchor="w", mask="auto")
    p.center(5.41, 10.19, 0.72, "DANFSe v2.0", FONT_B, 9)
    p.center(5.41, 10.19, 1.06, "Documento Auxiliar da NFS-e", FONT_B, 9)
    if d.homologacao:
        p.center(5.41, 10.19, 1.40, "NFS-e SEM VALIDADE JURÍDICA", FONT_B, 9, VERMELHO)
    if d.cabecalho_municipio:
        mun = _wrap(d.cabecalho_municipio, 5.0, FONT, 8)[:2]
        for i, ln in enumerate(mun):
            p.text(15.62, 0.62 + i * 0.30, ln, FONT, 8)
    p.text(15.62, 1.17, _fit(d.ambiente_gerador, 5.0, FONT, 6), FONT, 6)
    p.text(15.62, 1.40, _fit(d.tipo_ambiente, 5.0, FONT, 6), FONT, 6)

    # ---- Dados da NFS-e (identificação: títulos 7 pt caixa alta)
    p.hline(1.48)
    p.field(0, 1.48, "Chave de Acesso da NFS-e", d.chave, span=3, label_size=7, upper=True)
    for col, (lab, val) in enumerate((("Número da NFS-e", d.numero), ("Competência da NFS-e", d.competencia),
                                      ("Data e Hora da Emissão da NFS-e", d.dh_nfse))):
        p.field(col, 2.27, lab, val, label_size=7, upper=True)
    for col, (lab, val) in enumerate((("Número da DPS", d.ndps), ("Série da DPS", d.serie),
                                      ("Data e Hora da Emissão da DPS", d.dh_dps))):
        p.field(col, 2.96, lab, val, label_size=7, upper=True)
    p.shade(COLS[0], 3.65, COL_W, 0.67)
    for col, (lab, val) in enumerate((("Emitente da NFS-e", d.emitente), ("Situação da NFS-e", d.situacao),
                                      ("Finalidade", d.finalidade))):
        p.field(col, 3.65, lab, val, label_size=7, upper=True)
    qr = QrCodeWidget(d.qr_url, barLevel="M")
    b = qr.getBounds()
    size = 1.60 * cm
    dr = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0, size / (b[3] - b[1]), 0, 0])
    dr.add(qr)
    renderPDF.draw(dr, c, 17.48 * cm, p.y(1.62 + 1.60))
    for i, ln in enumerate(TEXTO_QR):
        p.center(15.80, 4.72, 3.48 + i * 0.25, ln, FONT, 6)

    # ---- Prestador / fornecedor
    top = 4.34
    top = _pessoa_rows(p, top, "Prestador / Fornecedor", d.prestador)
    p.field(0, top, "Simples Nacional na Data de Competência", d.simples)
    p.field(1, top, "Regime de Apuração Tributária pelo SN", d.regime_sn, span=3)
    top += ROW

    # ---- Tomador / destinatário / intermediário (notas 2 e 3)
    top = _pessoa_rows(p, top, "Tomador / Adquirente", d.tomador) if d.tomador else _msg_block(p, top, d.tomador_msg)
    if d.destinatario:
        top = _pessoa_rows(p, top, "Destinatário da Operação", d.destinatario, with_im=False)
    else:
        top = _msg_block(p, top, d.destinatario_msg)
    if d.intermediario:
        top = _pessoa_rows(p, top, "Intermediário da Operação", d.intermediario)
    else:
        top = _msg_block(p, top, d.intermediario_msg)

    # ---- Serviço prestado
    p.hline(top)
    _block_title(p, top, "Serviço Prestado")
    p.field(1, top, "Código de Tributação Nacional / Municipal", d.cod_trib)
    p.field(2, top, "Código da NBS", d.nbs)
    p.field(3, top, "Local da Prestação / Sigla UF / País", d.local_prestacao)
    top += ROW
    p.text(X0 + 0.05, top + 0.27, _fit(d.desc_cod_trib, W - 0.10, FONT, 7), FONT, 7)
    top += 0.40
    p.text(X0 + 0.05, top + 0.22, "Descrição do Serviço", FONT_B, 6)
    top += 0.30
    max_desc = max(1, int((h_desc - 0.10) / LINE_7))
    if len(desc_lines) > max_desc:
        desc_lines = desc_lines[:max_desc]
        desc_lines[-1] = _fit(desc_lines[-1] + " ...", W - 0.10, FONT, 7)
        if not desc_lines[-1].endswith("..."):
            desc_lines[-1] = desc_lines[-1].rstrip() + "..."
    for i, ln in enumerate(desc_lines):
        p.text(X0 + 0.05, top + 0.25 + i * LINE_7, ln, FONT, 7)
    top += h_desc

    # ---- Tributação municipal (ISSQN)
    i = d.issqn
    p.hline(top)
    _block_title(p, top, "Tributação Municipal (ISSQN)")
    p.field(1, top, "Tipo de Tributação do ISSQN", i["tributacao"])
    p.field(2, top, "Município / Sigla UF / País de Incidência do ISSQN", i["incidencia"], span=2)
    top += ROW
    for col, (lab, k) in enumerate((("Regime Especial de Tributação do ISSQN", "regime_especial"),
                                    ("Tipo de Imunidade do ISSQN", "imunidade"),
                                    ("Suspensão da Exigibilidade do ISSQN", "suspensao"),
                                    ("Número Processo Suspensão", "processo"))):
        p.field(col, top, lab, i[k])
    top += ROW
    for col, (lab, k) in enumerate((("Benefício Municipal", "beneficio"), ("Cálculo do BM", "calculo_bm"),
                                    ("Total Deduções/Reduções", "deducoes"), ("Desconto Incondicionado", "desc_incond"))):
        p.field(col, top, lab, i[k])
    top += ROW
    for col, (lab, k) in enumerate((("BC ISSQN", "bc"), ("Alíquota Aplicada", "aliquota"),
                                    ("Retenção do ISSQN", "retencao"), ("ISSQN Apurado", "apurado"))):
        p.field(col, top, lab, i[k])
    top += ROW

    # ---- Tributação federal (exceto CBS)
    f = d.federal
    p.hline(top)
    _block_title(p, top, "Tributação Federal (Exceto CBS)")
    p.field(1, top, "IRRF", f["irrf"])
    p.field(2, top, "Contribuição Previdenciária - Retida", f["cp"])
    p.field(3, top, "Contribuições Sociais - Retidas", f["contrib"])
    top += ROW
    if d.imprime_pis_cofins_proprio:
        p.field(0, top, "PIS - Débito Apuração Própria", f["pis"])
        p.field(1, top, "COFINS - Débito Apuração Própria", f["cofins"])
        p.field(2, top, "Descrição Contrib. Sociais - Retidas", f["desc_contrib"], span=2)
        top += ROW

    # ---- Tributação IBS / CBS
    g = d.ibscbs
    p.hline(top)
    _block_title(p, top, "Tributação IBS / CBS")
    p.field(1, top, "CST / cClassTrib", g["cst"])
    p.field(2, top, "Indicador de Operação / Código IBGE Incidência / Município Incidência / Sigla UF", g["indicador"], span=2)
    top += ROW
    for row in ((("Exclusões e Reduções da Base de Cálculo", "exclusoes"), ("Base de Cálculo Após Exclusões e Reduções", "bc"),
                 ("Red. Alíquota IBS / Red. Alíquota CBS", "red_aliq"), ("Alíquota - IBS UF / IBS Mun", "aliq_uf_mun")),
                (("Alíq. Efetiva Municipal - IBS", "aliq_efet_mun"), ("Valor Apurado Municipal - IBS", "v_mun"),
                 ("Alíq. Efetiva Estadual - IBS", "aliq_efet_uf"), ("Valor Apurado Estadual - IBS", "v_uf")),
                (("Valor Total Apurado - IBS", "v_ibs"), ("Alíquota - CBS", "aliq_cbs"),
                 ("Alíquota Efetiva - CBS", "aliq_efet_cbs"), ("Valor Total Apurado - CBS", "v_cbs"))):
        for col, (lab, k) in enumerate(row):
            p.field(col, top, lab, g[k])
        top += ROW

    # ---- Valor total da NFS-e
    t = d.totais
    p.hline(top)
    _block_title(p, top, "Valor Total da NFS-e", h=0.70)
    p.field(1, top, "Valor da Operação / Serviço", t["servico"], label_size=7, upper=True)
    p.field(2, top, "Desconto Incondicionado", t["desc_incond"])
    p.field(3, top, "Desconto Condicionado", t["desc_cond"])
    top += 0.70
    p.shade(COLS[3], top, COL_W + 0.06, 0.70)
    p.field(0, top, "Total das Retenções (ISSQN / Federais)", t["retencoes"])
    p.field(1, top, "Valor Líquido da NFS-e", t["liquido"], label_size=7, upper=True)
    p.field(2, top, "Total do IBS/CBS", t["ibscbs"])
    p.field(3, top, "Valor Líquido da NFS-e + IBS/CBS", t["liquido_ibscbs"], label_size=7, upper=True)
    top += 0.70

    # ---- Informações complementares
    p.hline(top)
    p.text(X0 + 0.05, top + 0.30, "INFORMAÇÕES COMPLEMENTARES", FONT_B, 7)
    top += 0.39
    max_info = max(1, int((h_info - 0.10) / LINE_7))
    if len(info_lines) > max_info - 1:  # a linha dos totais aproximados é fixa (não é truncada)
        info_lines = info_lines[: max(0, max_info - 1)]
        if info_lines:
            info_lines[-1] = _fit(info_lines[-1] + " ...", W - 0.10, FONT, 7)
            if not info_lines[-1].endswith("..."):
                info_lines[-1] = info_lines[-1].rstrip() + "..."
    for k, ln in enumerate(info_lines + [_fit(d.totais_aproximados, W - 0.10, FONT, 7)]):
        p.text(X0 + 0.05, top + 0.25 + k * LINE_7, ln, FONT, 7)

    c.showPage()
    c.save()
    return buf.getvalue()
