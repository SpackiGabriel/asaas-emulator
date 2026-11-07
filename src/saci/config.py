"""Runtime configuration, read from environment variables.

Every knob has a default that matches the real sandbox as closely as we know it.
Defaults that are assumptions rather than documented behaviour are marked as such
in the README's fidelity table.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

TransferMode = Literal["auto", "manual"]
DeliveryMode = Literal["background", "manual"]


def _env(name: str, default: str) -> str:
    return os.environ.get(f"SACI_{name}", default)


@dataclass(frozen=True)
class Fees:
    """Flat fees charged by the emulated provider, in BRL."""

    pix_payment: Decimal = Decimal("1.99")
    boleto_payment: Decimal = Decimal("1.99")
    pix_transfer: Decimal = Decimal("0.00")
    ted_transfer: Decimal = Decimal("0.00")


@dataclass(frozen=True)
class Settings:
    database: str = ":memory:"
    # Comma-separated list of accepted API keys. Empty means "accept any non-empty key",
    # with each distinct key getting its own isolated account.
    api_keys: frozenset[str] = frozenset()
    fees: Fees = field(default_factory=Fees)
    # "auto": transfers settle (DONE) as soon as they are created.
    # "manual": they stay PENDING until settled through the control API.
    transfer_mode: TransferMode = "auto"
    # "background": a worker thread delivers webhooks as they are produced.
    # "manual": deliveries only happen when POST /_saci/webhooks/deliver is called.
    delivery_mode: DeliveryMode = "background"
    # Asaas documents that a webhook queue may be interrupted after 15 consecutive failures.
    webhook_max_failures: int = 15
    # Delay before retrying a failed delivery, in seconds. Kept short so local test
    # suites do not stall; the real provider backs off far more slowly.
    webhook_retry_seconds: float = 1.0
    webhook_timeout_seconds: float = 5.0

    @classmethod
    def from_env(cls) -> Settings:
        keys = {k.strip() for k in _env("API_KEYS", "").split(",") if k.strip()}
        fees = Fees(
            pix_payment=Decimal(_env("FEE_PIX_PAYMENT", "1.99")),
            boleto_payment=Decimal(_env("FEE_BOLETO_PAYMENT", "1.99")),
            pix_transfer=Decimal(_env("FEE_PIX_TRANSFER", "0.00")),
            ted_transfer=Decimal(_env("FEE_TED_TRANSFER", "0.00")),
        )
        transfer_mode = _env("TRANSFER_MODE", "auto")
        delivery_mode = _env("DELIVERY_MODE", "background")
        if transfer_mode not in ("auto", "manual"):
            raise ValueError(f"invalid SACI_TRANSFER_MODE: {transfer_mode!r}")
        if delivery_mode not in ("background", "manual"):
            raise ValueError(f"invalid SACI_DELIVERY_MODE: {delivery_mode!r}")
        return cls(
            database=_env("DATABASE", ":memory:"),
            api_keys=frozenset(keys),
            fees=fees,
            transfer_mode=transfer_mode,  # type: ignore[arg-type]
            delivery_mode=delivery_mode,  # type: ignore[arg-type]
            webhook_max_failures=int(_env("WEBHOOK_MAX_FAILURES", "15")),
            webhook_retry_seconds=float(_env("WEBHOOK_RETRY_SECONDS", "1.0")),
            webhook_timeout_seconds=float(_env("WEBHOOK_TIMEOUT_SECONDS", "5.0")),
        )
