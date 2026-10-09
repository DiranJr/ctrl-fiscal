"""Validações de formato (CNPJ/CPF, dinheiro, competência). Sem regra tributária."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

MONEY_PATTERN = r"^\d{1,13}\.\d{2}$"
COMPETENCE_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
_CNPJ_RE = re.compile(r"^[0-9A-Z]{12}[0-9]{2}$")
_CPF_RE = re.compile(r"^[0-9]{11}$")


def only_alnum(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", value or "").upper()


def _cnpj_dv(base: str) -> str:
    # Algoritmo de DV do CNPJ; para o CNPJ alfanumérico (IN RFB 2.229/2024)
    # o valor de cada caractere é ord(c) - 48, o que preserva o caso numérico.
    def calc(chars: str, weights: list[int]) -> int:
        total = sum((ord(c) - 48) * w for c, w in zip(chars, weights))
        rest = total % 11
        return 0 if rest < 2 else 11 - rest

    w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    w2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    d1 = calc(base, w1)
    d2 = calc(base + str(d1), w2)
    return f"{d1}{d2}"


def is_valid_cnpj(value: str) -> bool:
    v = only_alnum(value)
    if not _CNPJ_RE.match(v) or len(set(v)) == 1:
        return False
    return _cnpj_dv(v[:12]) == v[12:]


def is_valid_cpf(value: str) -> bool:
    v = only_alnum(value)
    if not _CPF_RE.match(v) or len(set(v)) == 1:
        return False
    for n in (9, 10):
        total = sum(int(v[i]) * (n + 1 - i) for i in range(n))
        dv = (total * 10) % 11 % 10
        if dv != int(v[n]):
            return False
    return True


def cnpj_root(value: str) -> str:
    return only_alnum(value)[:8]


def parse_money(value: str | None) -> Decimal | None:
    """Converte texto em Decimal; None permanece None (ausente != zero)."""
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    try:
        d = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("valor monetário inválido") from exc
    if not d.is_finite():
        raise ValueError("valor monetário inválido")
    return d


def money_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value.quantize(Decimal('0.01'))}"
