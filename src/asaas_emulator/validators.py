"""Input validation that mirrors what the real API rejects.

The sandbox refuses customers with an invalid CPF/CNPJ check digit, so the emulator
does too. Accepting garbage here would let a test pass locally and fail in staging.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from .errors import invalid


def only_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _cpf_is_valid(cpf: str) -> bool:
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for size in (9, 10):
        total = sum(int(d) * w for d, w in zip(cpf[:size], range(size + 1, 1, -1), strict=True))
        check = (total * 10) % 11 % 10
        if check != int(cpf[size]):
            return False
    return True


def _cnpj_is_valid(cnpj: str) -> bool:
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False
    weights = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    for size in (12, 13):
        w = weights if size == 12 else [6, *weights]
        total = sum(int(d) * k for d, k in zip(cnpj[:size], w, strict=True))
        remainder = total % 11
        check = 0 if remainder < 2 else 11 - remainder
        if check != int(cnpj[size]):
            return False
    return True


def cpf_cnpj(value: str | None) -> tuple[str, str]:
    """Return (digits, personType) or raise an Asaas-shaped validation error."""
    digits = only_digits(value or "")
    if len(digits) == 11 and _cpf_is_valid(digits):
        return digits, "FISICA"
    if len(digits) == 14 and _cnpj_is_valid(digits):
        return digits, "JURIDICA"
    raise invalid("invalid_cpfCnpj", "O CPF/CNPJ informado é inválido.")


def parse_date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise invalid(f"invalid_{field}", f"O campo {field} deve ser informado.")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        message = f"O campo {field} deve estar no formato AAAA-MM-DD."
        raise invalid(f"invalid_{field}", message) from exc


def positive_amount(value: object, field: str = "value") -> float:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise invalid(f"invalid_{field}", f"O campo {field} deve ser informado.")
    try:
        amount = float(value)
    except ValueError as exc:
        raise invalid(f"invalid_{field}", f"O campo {field} é inválido.") from exc
    if amount <= 0:
        raise invalid(f"invalid_{field}", f"O campo {field} deve ser maior que zero.")
    return round(amount, 2)


def required_str(body: dict, field: str) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value.strip():
        raise invalid(f"invalid_{field}", f"O campo {field} deve ser informado.")
    return value.strip()
