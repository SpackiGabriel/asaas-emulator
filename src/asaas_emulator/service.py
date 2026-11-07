"""The emulated provider: customers, payments, balance, transfers and webhook events.

Route handlers stay thin and call into this module, so the rules live in one place and
can be exercised without HTTP.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import segno

from . import codes
from .clock import Clock
from .config import Settings
from .errors import NotFound, invalid
from .money import from_cents, to_cents
from .store import Store
from .validators import cpf_cnpj, parse_date, positive_amount, required_str

PAYMENT_BILLING_TYPES = ("UNDEFINED", "BOLETO", "CREDIT_CARD", "PIX")
CONFIRMABLE_BILLING_TYPES = ("UNDEFINED", "BOLETO", "PIX")
OPEN_STATUSES = ("PENDING", "OVERDUE")
PIX_KEY_TYPES = ("CPF", "CNPJ", "EMAIL", "PHONE", "EVP")
SEND_TYPES = ("NON_SEQUENTIALLY", "SEQUENTIALLY")

CUSTOMER_FIELDS = (
    "email",
    "phone",
    "mobilePhone",
    "address",
    "addressNumber",
    "complement",
    "province",
    "postalCode",
    "externalReference",
    "notificationDisabled",
    "additionalEmails",
    "municipalInscription",
    "stateInscription",
    "observations",
    "groupName",
    "company",
    "foreignCustomer",
)

# The real API answers with these customer fields even though they are never sent.
_CUSTOMER_READ_ONLY = {"city": None, "cityName": None, "state": None, "country": "Brasil"}


def public(resource: dict[str, Any]) -> dict[str, Any]:
    """Strip emulator-internal fields (prefixed with "_") before a resource leaves."""
    return {k: v for k, v in resource.items() if not k.startswith("_")}


def page(items: list[dict[str, Any]], offset: int, limit: int) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    window = items[offset : offset + limit]
    return {
        "object": "list",
        "hasMore": offset + len(window) < len(items),
        "totalCount": len(items),
        "limit": limit,
        "offset": offset,
        "data": [public(i) for i in window],
    }


class Emulator:
    def __init__(self, settings: Settings, store: Store | None = None, clock: Clock | None = None):
        self.settings = settings
        self.store = store or Store(settings.database)
        self.clock = clock or Clock()

    # -- helpers ----------------------------------------------------------------------

    def _today(self) -> str:
        return self.clock.today().isoformat()

    def _require(self, kind: str, account: str, resource_id: str) -> dict[str, Any]:
        resource = self.store.get(kind, account, resource_id)
        if resource is None:
            raise NotFound(resource_id)
        return resource

    def _emit(self, account: str, event: str, key: str, resource: dict[str, Any]) -> None:
        n = self.store.next_seq("event")
        self.store.record_event(
            account,
            {
                # Documented format: evt_<hex>&<number>
                "id": f"evt_{n:032x}&{n}",
                "event": event,
                "dateCreated": self.clock.now().strftime("%Y-%m-%d %H:%M:%S"),
                key: public(resource),
            },
        )

    def _payment_fee(self, billing_type: str) -> Decimal:
        fees = self.settings.fees
        return fees.pix_payment if billing_type == "PIX" else fees.boleto_payment

    def _net_value(self, value: float, billing_type: str) -> float:
        net = to_cents(value) - to_cents(self._payment_fee(billing_type))
        return from_cents(max(net, 0))

    # -- customers --------------------------------------------------------------------

    def create_customer(self, account: str, body: dict[str, Any]) -> dict[str, Any]:
        name = required_str(body, "name")
        digits, person_type = cpf_cnpj(body.get("cpfCnpj"))
        customer = {
            "object": "customer",
            "id": self.store.next_id("cus"),
            "dateCreated": self._today(),
            "name": name,
            **{f: body.get(f) for f in CUSTOMER_FIELDS},
            **_CUSTOMER_READ_ONLY,
            "cpfCnpj": digits,
            "personType": person_type,
            "deleted": False,
        }
        customer["notificationDisabled"] = bool(customer["notificationDisabled"])
        customer["foreignCustomer"] = bool(customer["foreignCustomer"])
        return public(self.store.insert("customer", account, customer))

    def update_customer(self, account: str, customer_id: str, body: dict[str, Any]):
        customer = self._require("customer", account, customer_id)
        if "name" in body:
            customer["name"] = required_str(body, "name")
        if "cpfCnpj" in body:
            customer["cpfCnpj"], customer["personType"] = cpf_cnpj(body["cpfCnpj"])
        for field in CUSTOMER_FIELDS:
            if field in body:
                customer[field] = body[field]
        return public(self.store.save("customer", account, customer))

    def get_customer(self, account: str, customer_id: str) -> dict[str, Any]:
        return public(self._require("customer", account, customer_id))

    def list_customers(self, account: str, filters: dict[str, str]) -> list[dict[str, Any]]:
        items = [c for c in self.store.all("customer", account) if not c["deleted"]]
        if name := filters.get("name"):
            items = [c for c in items if name.lower() in c["name"].lower()]
        for field in ("email", "cpfCnpj", "groupName", "externalReference"):
            if (value := filters.get(field)) is not None:
                items = [c for c in items if (c.get(field) or "") == value]
        return items

    def set_customer_deleted(self, account: str, customer_id: str, deleted: bool):
        customer = self._require("customer", account, customer_id)
        customer["deleted"] = deleted
        self.store.save("customer", account, customer)
        return customer

    # -- payments ---------------------------------------------------------------------

    def create_payment(self, account: str, body: dict[str, Any], base_url: str) -> dict[str, Any]:
        customer_id = body.get("customer")
        customer = self.store.get("customer", account, customer_id) if customer_id else None
        if customer is None or customer["deleted"]:
            raise invalid("invalid_customer", "O cliente informado não existe.")
        billing_type = body.get("billingType")
        if billing_type not in PAYMENT_BILLING_TYPES:
            raise invalid("invalid_billingType", "Forma de pagamento inválida.")
        if billing_type == "CREDIT_CARD":
            raise invalid(
                "unsupported_billingType",
                "asaas-emulator does not emulate card payments yet.",
            )
        value = positive_amount(body.get("value"))
        due = parse_date(body.get("dueDate"), "dueDate")
        if due < self.clock.today():
            raise invalid(
                "invalid_dueDate", "A data de vencimento não pode ser anterior à data atual."
            )

        with self.store.transaction():
            payment_id = self.store.next_id("pay")
            number = int(payment_id.split("_")[1])
            payment = {
                "object": "payment",
                "id": payment_id,
                "dateCreated": self._today(),
                "customer": customer_id,
                "subscription": None,
                "installment": None,
                "paymentLink": None,
                "value": value,
                "netValue": self._net_value(value, billing_type),
                "originalValue": None,
                "interestValue": None,
                "description": body.get("description"),
                "billingType": billing_type,
                "canBePaidAfterDueDate": True,
                "pixTransaction": None,
                "pixQrCodeId": None,
                "status": "PENDING",
                "dueDate": due.isoformat(),
                "originalDueDate": due.isoformat(),
                "paymentDate": None,
                "clientPaymentDate": None,
                "installmentNumber": None,
                "invoiceUrl": f"{base_url}i/{payment_id}",
                "invoiceNumber": f"{number:08d}",
                "externalReference": body.get("externalReference"),
                "deleted": False,
                "anticipated": False,
                "anticipable": False,
                "creditDate": None,
                "estimatedCreditDate": None,
                "transactionReceiptUrl": None,
                "nossoNumero": None,
                "bankSlipUrl": None,
                "discount": body.get("discount")
                or {"value": 0, "dueDateLimitDays": 0, "type": "FIXED"},
                "fine": body.get("fine") or {"value": 0},
                "interest": body.get("interest") or {"value": 0},
                "postalService": bool(body.get("postalService", False)),
            }
            if billing_type in ("BOLETO", "UNDEFINED"):
                _, _, nosso = codes.boleto(payment_id, value, 0)
                payment["nossoNumero"] = nosso
                payment["bankSlipUrl"] = f"{base_url}i/{payment_id}"
            self.store.insert("payment", account, payment)
            self._emit(account, "PAYMENT_CREATED", "payment", payment)
        return public(payment)

    def get_payment(self, account: str, payment_id: str) -> dict[str, Any]:
        return public(self._require("payment", account, payment_id))

    def list_payments(self, account: str, filters: dict[str, str]) -> list[dict[str, Any]]:
        items = [p for p in self.store.all("payment", account) if not p["deleted"]]
        for field in ("customer", "billingType", "status", "externalReference", "paymentDate"):
            if (value := filters.get(field)) is not None:
                items = [p for p in items if p.get(field) == value]
        for field in ("dueDate", "paymentDate", "dateCreated"):
            if ge := filters.get(f"{field}[ge]"):
                items = [p for p in items if p.get(field) and p[field] >= ge]
            if le := filters.get(f"{field}[le]"):
                items = [p for p in items if p.get(field) and p[field] <= le]
        return items

    def update_payment(self, account: str, payment_id: str, body: dict[str, Any]):
        with self.store.transaction():
            payment = self._require("payment", account, payment_id)
            if payment["deleted"] or payment["status"] not in OPEN_STATUSES:
                raise invalid("invalid_action", "Só é possível alterar cobranças em aberto.")
            if "billingType" in body:
                if body["billingType"] not in CONFIRMABLE_BILLING_TYPES:
                    raise invalid("invalid_billingType", "Forma de pagamento inválida.")
                payment["billingType"] = body["billingType"]
            if "value" in body:
                payment["value"] = positive_amount(body["value"])
            if "dueDate" in body:
                due = parse_date(body["dueDate"], "dueDate")
                if due < self.clock.today():
                    raise invalid(
                        "invalid_dueDate",
                        "A data de vencimento não pode ser anterior à data atual.",
                    )
                payment["dueDate"] = due.isoformat()
                payment["status"] = "PENDING"
            for field in ("description", "externalReference", "discount", "fine", "interest"):
                if field in body:
                    payment[field] = body[field]
            payment["netValue"] = self._net_value(payment["value"], payment["billingType"])
            self.store.save("payment", account, payment)
            self._emit(account, "PAYMENT_UPDATED", "payment", payment)
        return public(payment)

    def delete_payment(self, account: str, payment_id: str) -> dict[str, Any]:
        with self.store.transaction():
            payment = self._require("payment", account, payment_id)
            if payment["status"] not in OPEN_STATUSES:
                raise invalid("invalid_action", "Não é possível remover uma cobrança já paga.")
            payment["deleted"] = True
            self.store.save("payment", account, payment)
            self._emit(account, "PAYMENT_DELETED", "payment", payment)
        return {"deleted": True, "id": payment_id}

    def restore_payment(self, account: str, payment_id: str) -> dict[str, Any]:
        with self.store.transaction():
            payment = self._require("payment", account, payment_id)
            if not payment["deleted"]:
                raise invalid("invalid_action", "A cobrança não está removida.")
            payment["deleted"] = False
            self.store.save("payment", account, payment)
            self._emit(account, "PAYMENT_RESTORED", "payment", payment)
        return public(payment)

    def identification_field(self, account: str, payment_id: str) -> dict[str, str]:
        payment = self._require("payment", account, payment_id)
        if payment["billingType"] not in ("BOLETO", "UNDEFINED"):
            raise invalid("invalid_billingType", "A cobrança não é um boleto.")
        epoch = date(2025, 2, 22)  # FEBRABAN due-date factor restarted at 1000 on this day
        factor = 1000 + (date.fromisoformat(payment["dueDate"]) - epoch).days
        line, barcode, nosso = codes.boleto(payment_id, payment["value"], factor)
        return {"identificationField": line, "nossoNumero": nosso, "barCode": barcode}

    def pix_qr_code(self, account: str, payment_id: str) -> dict[str, Any]:
        payment = self._require("payment", account, payment_id)
        if payment["billingType"] == "CREDIT_CARD":
            raise invalid("invalid_billingType", "A cobrança não aceita Pix.")
        payload = codes.pix_payload(payment_id, payment["value"])
        qr = segno.make(payload, error="m")
        image = qr.png_data_uri(scale=4).split(",", 1)[1]
        expiration = datetime.fromisoformat(payment["dueDate"]) + timedelta(days=365)
        return {
            "encodedImage": image,
            "payload": payload,
            "expirationDate": expiration.strftime("%Y-%m-%d %H:%M:%S"),
            "description": payment.get("description"),
        }

    def confirm_payment(self, account: str, payment_id: str) -> dict[str, Any]:
        """Sandbox action: the customer paid, and the money is available."""
        with self.store.transaction():
            payment = self._require("payment", account, payment_id)
            if payment["deleted"]:
                raise invalid("invalid_action", "A cobrança está removida.")
            if payment["billingType"] not in CONFIRMABLE_BILLING_TYPES:
                raise invalid(
                    "invalid_billingType", "Apenas cobranças Pix ou boleto podem ser confirmadas."
                )
            if payment["status"] not in OPEN_STATUSES:
                raise invalid("invalid_action", "A cobrança já foi confirmada.")
            today = self._today()
            payment.update(
                status="RECEIVED",
                paymentDate=today,
                clientPaymentDate=today,
                creditDate=today,
                estimatedCreditDate=today,
                netValue=self._net_value(payment["value"], payment["billingType"]),
                transactionReceiptUrl=f"{payment['invoiceUrl']}/receipt",
            )
            self.store.save("payment", account, payment)
            self.store.credit(account, to_cents(payment["netValue"]))
            self._emit(account, "PAYMENT_RECEIVED", "payment", payment)
        return public(payment)

    def force_overdue(self, account: str, payment_id: str) -> dict[str, Any]:
        with self.store.transaction():
            payment = self._require("payment", account, payment_id)
            if payment["deleted"] or payment["status"] != "PENDING":
                raise invalid("invalid_action", "Apenas cobranças pendentes podem vencer.")
            payment["status"] = "OVERDUE"
            self.store.save("payment", account, payment)
            self._emit(account, "PAYMENT_OVERDUE", "payment", payment)
        return public(payment)

    def receive_in_cash(self, account: str, payment_id: str, body: dict[str, Any]):
        with self.store.transaction():
            payment = self._require("payment", account, payment_id)
            if payment["deleted"] or payment["status"] not in OPEN_STATUSES:
                raise invalid("invalid_action", "A cobrança não está em aberto.")
            paid_on = parse_date(body.get("paymentDate", self._today()), "paymentDate")
            payment.update(
                status="RECEIVED_IN_CASH",
                paymentDate=paid_on.isoformat(),
                clientPaymentDate=paid_on.isoformat(),
                value=positive_amount(body.get("value", payment["value"])),
            )
            payment["netValue"] = payment["value"]  # cash never touches the account balance
            self.store.save("payment", account, payment)
            self._emit(account, "PAYMENT_RECEIVED", "payment", payment)
        return public(payment)

    def undo_received_in_cash(self, account: str, payment_id: str) -> dict[str, Any]:
        with self.store.transaction():
            payment = self._require("payment", account, payment_id)
            if payment["status"] != "RECEIVED_IN_CASH":
                raise invalid("invalid_action", "A cobrança não foi recebida em dinheiro.")
            payment.update(status="PENDING", paymentDate=None, clientPaymentDate=None)
            payment["netValue"] = self._net_value(payment["value"], payment["billingType"])
            if date.fromisoformat(payment["dueDate"]) < self.clock.today():
                payment["status"] = "OVERDUE"
            self.store.save("payment", account, payment)
            self._emit(account, "PAYMENT_RECEIVED_IN_CASH_UNDONE", "payment", payment)
        return public(payment)

    # -- balance and transfers --------------------------------------------------------

    def balance(self, account: str) -> dict[str, float]:
        return {"balance": from_cents(self.store.balance_cents(account))}

    def create_transfer(self, account: str, body: dict[str, Any], base_url: str):
        value = positive_amount(body.get("value"))
        operation = body.get("operationType") or ("PIX" if body.get("pixAddressKey") else "TED")
        if operation not in ("PIX", "TED"):
            raise invalid("invalid_operationType", "Tipo de operação inválido.")
        pix_key = body.get("pixAddressKey")
        bank_account = body.get("bankAccount")
        if pix_key:
            key_type = body.get("pixAddressKeyType")
            if key_type not in PIX_KEY_TYPES:
                raise invalid("invalid_pixAddressKeyType", "Tipo de chave Pix inválido.")
            bank_account = {"pixAddressKey": pix_key}
        elif not isinstance(bank_account, dict):
            raise invalid(
                "invalid_bankAccount", "Informe uma chave Pix ou os dados da conta bancária."
            )
        schedule = body.get("scheduleDate")
        if schedule is not None:
            schedule = parse_date(schedule, "scheduleDate").isoformat()

        fees = self.settings.fees
        fee = fees.pix_transfer if operation == "PIX" else fees.ted_transfer
        total = to_cents(value) + to_cents(fee)

        with self.store.transaction():
            if not self.store.debit_if_available(account, total):
                raise invalid("insufficient_balance", "Saldo insuficiente para a transferência.")
            transfer = {
                "object": "transfer",
                "id": self.store.next_uuid("transfer"),
                "type": operation,
                "dateCreated": self._today(),
                "value": value,
                "netValue": value,
                "status": "PENDING",
                "transferFee": float(fee),
                "effectiveDate": None,
                "scheduleDate": schedule,
                "endToEndIdentifier": None,
                "authorized": True,
                "failReason": None,
                "externalReference": body.get("externalReference"),
                "transactionReceiptUrl": None,
                "operationType": operation,
                "description": body.get("description"),
                "recurring": None,
                "bankAccount": bank_account,
                "_debitedCents": total,
            }
            self.store.insert("transfer", account, transfer)
            self._emit(account, "TRANSFER_CREATED", "transfer", transfer)
            if self._settles_now(transfer):
                self._finish_transfer(account, transfer, base_url)
        return public(transfer)

    def _settles_now(self, transfer: dict[str, Any]) -> bool:
        if self.settings.transfer_mode != "auto":
            return False
        schedule = transfer.get("scheduleDate")
        return schedule is None or schedule <= self._today()

    def _finish_transfer(self, account: str, transfer: dict[str, Any], base_url: str) -> None:
        n = self.store.next_seq("e2e")
        transfer.update(
            status="DONE",
            effectiveDate=self._today(),
            endToEndIdentifier=f"E{n:031d}" if transfer["type"] == "PIX" else None,
            transactionReceiptUrl=f"{base_url}transfers/{transfer['id']}/receipt",
        )
        self.store.save("transfer", account, transfer)
        self._emit(account, "TRANSFER_DONE", "transfer", transfer)

    def get_transfer(self, account: str, transfer_id: str) -> dict[str, Any]:
        return public(self._require("transfer", account, transfer_id))

    def list_transfers(self, account: str, filters: dict[str, str]) -> list[dict[str, Any]]:
        items = self.store.all("transfer", account)
        if kind := filters.get("type"):
            items = [t for t in items if t["type"] == kind]
        if ge := filters.get("dateCreated[ge]"):
            items = [t for t in items if t["dateCreated"] >= ge]
        if le := filters.get("dateCreated[le]"):
            items = [t for t in items if t["dateCreated"] <= le]
        return items

    def cancel_transfer(self, account: str, transfer_id: str) -> dict[str, Any]:
        return self._close_transfer(account, transfer_id, "CANCELLED", None)

    def fail_transfer(self, account: str, transfer_id: str, reason: str) -> dict[str, Any]:
        return self._close_transfer(account, transfer_id, "FAILED", reason)

    def _close_transfer(
        self, account: str, transfer_id: str, status: str, reason: str | None
    ) -> dict[str, Any]:
        with self.store.transaction():
            transfer = self._require("transfer", account, transfer_id)
            if transfer["status"] != "PENDING":
                raise invalid("invalid_action", "Apenas transferências pendentes podem mudar.")
            transfer["status"] = status
            transfer["failReason"] = reason
            self.store.save("transfer", account, transfer)
            self.store.credit(account, transfer["_debitedCents"])  # money goes back
            self._emit(account, f"TRANSFER_{status}", "transfer", transfer)
        return public(transfer)

    def complete_transfer(self, account: str, transfer_id: str, base_url: str):
        with self.store.transaction():
            transfer = self._require("transfer", account, transfer_id)
            if transfer["status"] != "PENDING":
                raise invalid("invalid_action", "Apenas transferências pendentes podem mudar.")
            self._finish_transfer(account, transfer, base_url)
        return public(transfer)

    # -- webhooks ---------------------------------------------------------------------

    def create_webhook(self, account: str, body: dict[str, Any]) -> dict[str, Any]:
        webhook = {
            "id": self.store.next_uuid("webhook"),
            "name": required_str(body, "name"),
            "url": required_str(body, "url"),
            "email": required_str(body, "email"),
            "enabled": bool(body.get("enabled", True)),
            "interrupted": bool(body.get("interrupted", False)),
            "apiVersion": body.get("apiVersion", 3),
            "authToken": body.get("authToken"),
            "sendType": body.get("sendType", "SEQUENTIALLY"),
            "penalizedRequestsCount": 0,
            "events": body.get("events") or [],
            "_consecutiveFailures": 0,
        }
        self._validate_webhook(webhook)
        self.store.insert("webhook", account, webhook)
        return self.webhook_view(webhook)

    def update_webhook(self, account: str, webhook_id: str, body: dict[str, Any]):
        webhook = self._require("webhook", account, webhook_id)
        for field in (
            "name",
            "url",
            "email",
            "enabled",
            "interrupted",
            "apiVersion",
            "authToken",
            "sendType",
            "events",
        ):
            if field in body:
                webhook[field] = body[field]
        if body.get("interrupted") is False:
            webhook["_consecutiveFailures"] = 0
        self._validate_webhook(webhook)
        self.store.save("webhook", account, webhook)
        return self.webhook_view(webhook)

    def _validate_webhook(self, webhook: dict[str, Any]) -> None:
        if not str(webhook["url"]).startswith(("http://", "https://")):
            raise invalid("invalid_url", "A URL do webhook é inválida.")
        if webhook["sendType"] not in SEND_TYPES:
            raise invalid("invalid_sendType", "Tipo de envio inválido.")
        if not isinstance(webhook["events"], list) or not webhook["events"]:
            raise invalid("invalid_events", "Informe ao menos um evento.")

    @staticmethod
    def webhook_view(webhook: dict[str, Any]) -> dict[str, Any]:
        view = public(webhook)
        view["hasAuthToken"] = bool(view.pop("authToken", None))
        return view

    def get_webhook(self, account: str, webhook_id: str) -> dict[str, Any]:
        return self.webhook_view(self._require("webhook", account, webhook_id))

    def list_webhooks(self, account: str) -> list[dict[str, Any]]:
        return self.store.all("webhook", account)

    def delete_webhook(self, account: str, webhook_id: str) -> dict[str, Any]:
        with self.store.transaction() as db:
            self._require("webhook", account, webhook_id)
            db.execute("DELETE FROM resources WHERE kind = 'webhook' AND id = ?", (webhook_id,))
            db.execute(
                "DELETE FROM deliveries WHERE webhook_id = ? AND status = 'PENDING'", (webhook_id,)
            )
        return {"deleted": True, "id": webhook_id}

    # -- time -------------------------------------------------------------------------

    def tick(self, base_url: str) -> None:
        """Apply everything that becomes true when the date changes.

        Called after the clock moves, so tests can say "it is now 3 days later" and
        observe overdue charges and scheduled transfers settle, with their events.
        """
        today = self._today()
        with self.store.transaction():
            for account, payment in self.store.all_accounts("payment"):
                if (
                    not payment["deleted"]
                    and payment["status"] == "PENDING"
                    and payment["dueDate"] < today
                ):
                    payment["status"] = "OVERDUE"
                    self.store.save("payment", account, payment)
                    self._emit(account, "PAYMENT_OVERDUE", "payment", payment)
            for account, transfer in self.store.all_accounts("transfer"):
                if transfer["status"] == "PENDING" and self._settles_now(transfer):
                    self._finish_transfer(account, transfer, base_url)
