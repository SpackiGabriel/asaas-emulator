"""Generators for payment artefacts that clients parse: Pix "copia e cola" and boleto lines.

These are not decorative. Integrations commonly validate a BR Code's CRC or store a
boleto line in a fixed-width column, so the emulator emits values with the same shape
and a correct checksum, derived deterministically from the payment id.
"""

from __future__ import annotations

import hashlib


def _emv(tag: str, value: str) -> str:
    return f"{tag}{len(value):02d}{value}"


def _crc16_ccitt(payload: str) -> str:
    crc = 0xFFFF
    for byte in payload.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return f"{crc:04X}"


def pix_payload(payment_id: str, amount: float, merchant: str = "ASAAS EMULATOR") -> str:
    """A static BR Code (EMV QRCPS) payload with a valid CRC16."""
    key = hashlib.sha256(payment_id.encode()).hexdigest()[:32]
    evp = f"{key[:8]}-{key[8:12]}-{key[12:16]}-{key[16:20]}-{key[20:32]}"
    account = _emv("00", "br.gov.bcb.pix") + _emv("01", evp)
    txid = payment_id.replace("_", "").upper()[:25]
    body = (
        _emv("00", "01")
        + _emv("26", account)
        + _emv("52", "0000")
        + _emv("53", "986")
        + _emv("54", f"{amount:.2f}")
        + _emv("58", "BR")
        + _emv("59", merchant[:25])
        + _emv("60", "SAO PAULO")
        + _emv("62", _emv("05", txid))
        + "6304"
    )
    return body + _crc16_ccitt(body)


def _mod10(digits: str) -> int:
    total = 0
    for i, d in enumerate(reversed(digits)):
        product = int(d) * (2 if i % 2 == 0 else 1)
        total += product // 10 + product % 10
    return (10 - total % 10) % 10


def _mod11_barcode(digits: str) -> int:
    total = sum(int(d) * (2 + i % 8) for i, d in enumerate(reversed(digits)))
    check = 11 - total % 11
    return 1 if check in (0, 10, 11) else check


def boleto(payment_id: str, amount: float, due_factor: int) -> tuple[str, str, str]:
    """Return (identificationField, barCode, nossoNumero) with valid check digits.

    Uses the FEBRABAN layout: 44-digit barcode and 47-digit typeable line.
    Bank 461 is Asaas's code; the free field is derived from the payment id.
    """
    seed = int(hashlib.sha256(payment_id.encode()).hexdigest(), 16)
    free_field = f"{seed % 10**25:025d}"
    nosso_numero = free_field[-10:]
    bank_currency = "4619"
    factor = f"{due_factor % 10000:04d}"
    value = f"{round(amount * 100):010d}"
    without_dv = bank_currency + factor + value + free_field
    dv = _mod11_barcode(without_dv)
    barcode = bank_currency + str(dv) + factor + value + free_field

    f1 = bank_currency + free_field[:5]
    f2 = free_field[5:15]
    f3 = free_field[15:25]
    line = (
        f1
        + str(_mod10(f1))
        + f2
        + str(_mod10(f2))
        + f3
        + str(_mod10(f3))
        + str(dv)
        + factor
        + value
    )
    return line, barcode, nosso_numero
