"""CNPJ fictício com DV válido para dados de smoke (não representa empresa real)."""


def cnpj_full(base12: str) -> str:
    def calc(chars: str, weights: list[int]) -> int:
        rest = sum((ord(c) - 48) * w for c, w in zip(chars, weights)) % 11
        return 0 if rest < 2 else 11 - rest

    d1 = calc(base12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return base12 + str(d1) + str(calc(base12 + str(d1), [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]))
