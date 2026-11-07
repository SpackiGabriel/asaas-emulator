"""HTTP surface: the Asaas v3 endpoints plus an emulator-only control API.

Anything under /v3 behaves like the provider. Anything under /_saci does not exist
in the real API: it is how a test suite resets state, moves time, settles transfers or
inspects what webhooks would have received.
"""

from __future__ import annotations

import html
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from . import __version__
from .config import Settings
from .delivery import Dispatcher
from .errors import AsaasError, NotFound, invalid, unauthorized
from .money import to_cents
from .service import Emulator, page


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    emulator = Emulator(settings)
    dispatcher = Dispatcher(emulator)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if settings.delivery_mode == "background":
            dispatcher.start()
        yield
        dispatcher.stop()

    app = FastAPI(
        title="Saci",
        version=__version__,
        description="Saci: local, deterministic emulator of the Asaas v3 API.",
        lifespan=lifespan,
    )
    app.state.emulator = emulator
    app.state.dispatcher = dispatcher

    # -- plumbing ---------------------------------------------------------------------

    @app.exception_handler(AsaasError)
    async def _asaas_error(_: Request, exc: AsaasError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status_code)

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, __: NotFound) -> Response:
        return Response(status_code=404)

    def account(
        access_token: str | None = Header(default=None, convert_underscores=False),
    ) -> str:
        if not access_token or (settings.api_keys and access_token not in settings.api_keys):
            raise unauthorized()
        emulator.store.ensure_account(access_token)
        return access_token

    async def body(request: Request) -> dict[str, Any]:
        raw = await request.body()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise invalid("invalid_object", "O corpo da requisição não é um JSON válido.") from exc
        if not isinstance(data, dict):
            raise invalid("invalid_object", "O corpo da requisição deve ser um objeto.")
        return data

    def listing(request: Request, items: list[dict[str, Any]]) -> dict[str, Any]:
        q = request.query_params
        try:
            offset, limit = int(q.get("offset", 0)), int(q.get("limit", 10))
        except ValueError as exc:
            raise invalid("invalid_object", "offset e limit devem ser números.") from exc
        return page(items, offset, limit)

    def base(request: Request) -> str:
        return str(request.base_url)

    # -- customers --------------------------------------------------------------------

    @app.post("/v3/customers")
    async def create_customer(data: dict = Depends(body), acc: str = Depends(account)):
        return emulator.create_customer(acc, data)

    @app.get("/v3/customers")
    def list_customers(request: Request, acc: str = Depends(account)):
        return listing(request, emulator.list_customers(acc, dict(request.query_params)))

    @app.get("/v3/customers/{customer_id}")
    def get_customer(customer_id: str, acc: str = Depends(account)):
        return emulator.get_customer(acc, customer_id)

    @app.put("/v3/customers/{customer_id}")
    async def update_customer(
        customer_id: str, data: dict = Depends(body), acc: str = Depends(account)
    ):
        return emulator.update_customer(acc, customer_id, data)

    @app.delete("/v3/customers/{customer_id}")
    def delete_customer(customer_id: str, acc: str = Depends(account)):
        emulator.set_customer_deleted(acc, customer_id, True)
        return {"deleted": True, "id": customer_id}

    @app.post("/v3/customers/{customer_id}/restore")
    def restore_customer(customer_id: str, acc: str = Depends(account)):
        return emulator.get_customer(
            acc, emulator.set_customer_deleted(acc, customer_id, False)["id"]
        )

    # -- payments ---------------------------------------------------------------------

    @app.post("/v3/payments")
    async def create_payment(
        request: Request, data: dict = Depends(body), acc: str = Depends(account)
    ):
        return emulator.create_payment(acc, data, base(request))

    @app.get("/v3/payments")
    def list_payments(request: Request, acc: str = Depends(account)):
        return listing(request, emulator.list_payments(acc, dict(request.query_params)))

    @app.get("/v3/payments/{payment_id}")
    def get_payment(payment_id: str, acc: str = Depends(account)):
        return emulator.get_payment(acc, payment_id)

    @app.put("/v3/payments/{payment_id}")
    async def update_payment(
        payment_id: str, data: dict = Depends(body), acc: str = Depends(account)
    ):
        return emulator.update_payment(acc, payment_id, data)

    @app.delete("/v3/payments/{payment_id}")
    def delete_payment(payment_id: str, acc: str = Depends(account)):
        return emulator.delete_payment(acc, payment_id)

    @app.post("/v3/payments/{payment_id}/restore")
    def restore_payment(payment_id: str, acc: str = Depends(account)):
        return emulator.restore_payment(acc, payment_id)

    @app.get("/v3/payments/{payment_id}/status")
    def payment_status(payment_id: str, acc: str = Depends(account)):
        return {"status": emulator.get_payment(acc, payment_id)["status"]}

    @app.get("/v3/payments/{payment_id}/identificationField")
    def identification_field(payment_id: str, acc: str = Depends(account)):
        return emulator.identification_field(acc, payment_id)

    @app.get("/v3/payments/{payment_id}/pixQrCode")
    def pix_qr_code(payment_id: str, acc: str = Depends(account)):
        return emulator.pix_qr_code(acc, payment_id)

    @app.post("/v3/payments/{payment_id}/receiveInCash")
    async def receive_in_cash(
        payment_id: str, data: dict = Depends(body), acc: str = Depends(account)
    ):
        return emulator.receive_in_cash(acc, payment_id, data)

    @app.post("/v3/payments/{payment_id}/undoReceivedInCash")
    def undo_received_in_cash(payment_id: str, acc: str = Depends(account)):
        return emulator.undo_received_in_cash(acc, payment_id)

    # -- sandbox actions --------------------------------------------------------------

    @app.post("/v3/sandbox/payment/{payment_id}/confirm")
    def sandbox_confirm(payment_id: str, acc: str = Depends(account)):
        return emulator.confirm_payment(acc, payment_id)

    @app.post("/v3/sandbox/payment/{payment_id}/overdue")
    def sandbox_overdue(payment_id: str, acc: str = Depends(account)):
        return emulator.force_overdue(acc, payment_id)

    # -- finance and transfers --------------------------------------------------------

    @app.get("/v3/finance/balance")
    def balance(acc: str = Depends(account)):
        return emulator.balance(acc)

    @app.post("/v3/transfers")
    async def create_transfer(
        request: Request, data: dict = Depends(body), acc: str = Depends(account)
    ):
        return emulator.create_transfer(acc, data, base(request))

    @app.get("/v3/transfers")
    def list_transfers(request: Request, acc: str = Depends(account)):
        return listing(request, emulator.list_transfers(acc, dict(request.query_params)))

    @app.get("/v3/transfers/{transfer_id}")
    def get_transfer(transfer_id: str, acc: str = Depends(account)):
        return emulator.get_transfer(acc, transfer_id)

    @app.delete("/v3/transfers/{transfer_id}/cancel")
    def cancel_transfer(transfer_id: str, acc: str = Depends(account)):
        return emulator.cancel_transfer(acc, transfer_id)

    # -- webhooks ---------------------------------------------------------------------

    @app.post("/v3/webhooks")
    async def create_webhook(data: dict = Depends(body), acc: str = Depends(account)):
        return emulator.create_webhook(acc, data)

    @app.get("/v3/webhooks")
    def list_webhooks(request: Request, acc: str = Depends(account)):
        result = listing(request, emulator.list_webhooks(acc))
        result["data"] = [emulator.webhook_view(w) for w in result["data"]]
        return result

    @app.get("/v3/webhooks/{webhook_id}")
    def get_webhook(webhook_id: str, acc: str = Depends(account)):
        return emulator.get_webhook(acc, webhook_id)

    @app.put("/v3/webhooks/{webhook_id}")
    async def update_webhook(
        webhook_id: str, data: dict = Depends(body), acc: str = Depends(account)
    ):
        return emulator.update_webhook(acc, webhook_id, data)

    @app.delete("/v3/webhooks/{webhook_id}")
    def delete_webhook(webhook_id: str, acc: str = Depends(account)):
        return emulator.delete_webhook(acc, webhook_id)

    # -- public pages -----------------------------------------------------------------

    @app.get("/i/{payment_id}", response_class=HTMLResponse, include_in_schema=False)
    def invoice(payment_id: str):
        found = [p for _, p in emulator.store.all_accounts("payment") if p["id"] == payment_id]
        if not found:
            raise NotFound(payment_id)
        p = found[0]
        rows = "".join(
            f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(p.get(k)))}</td></tr>"
            for k in ("id", "value", "billingType", "status", "dueDate", "description")
        )
        return (
            f"<!doctype html><title>Invoice {html.escape(p['id'])}</title>"
            f"<h1>Saci invoice</h1><table>{rows}</table>"
        )

    # -- control API (not part of Asaas) ----------------------------------------------

    @app.get("/_saci/health")
    def health():
        return {"status": "ok", "version": __version__, "now": emulator.clock.now().isoformat()}

    @app.post("/_saci/reset")
    def reset():
        emulator.store.reset()
        emulator.clock.reset()
        return {"reset": True}

    @app.get("/_saci/clock")
    def get_clock():
        return {"now": emulator.clock.now().isoformat(), "frozen": emulator.clock.frozen}

    @app.post("/_saci/clock")
    async def set_clock(request: Request, data: dict = Depends(body)):
        if "now" in data:
            try:
                emulator.clock.freeze(datetime.fromisoformat(data["now"]))
            except (TypeError, ValueError) as exc:
                raise invalid("invalid_now", "Use ISO 8601, e.g. 2026-01-31T09:00:00.") from exc
        if "advanceDays" in data:
            emulator.clock.advance(timedelta(days=int(data["advanceDays"])))
        emulator.tick(base(request))
        return get_clock()

    @app.put("/_saci/balance")
    async def set_balance(data: dict = Depends(body), acc: str = Depends(account)):
        emulator.store.set_balance(acc, to_cents(data.get("balance", 0)))
        return emulator.balance(acc)

    @app.get("/_saci/events")
    def events(acc: str = Depends(account)):
        return {"data": emulator.store.events(acc)}

    @app.get("/_saci/deliveries")
    def deliveries(acc: str = Depends(account)):
        return {"data": emulator.store.deliveries(acc)}

    @app.post("/_saci/webhooks/deliver")
    def deliver_now():
        return {"attempts": dispatcher.deliver_due(ignore_backoff=True)}

    @app.post("/_saci/transfers/{transfer_id}/done")
    def transfer_done(request: Request, transfer_id: str, acc: str = Depends(account)):
        return emulator.complete_transfer(acc, transfer_id, base(request))

    @app.post("/_saci/transfers/{transfer_id}/fail")
    async def transfer_fail(
        transfer_id: str, data: dict = Depends(body), acc: str = Depends(account)
    ):
        return emulator.fail_transfer(acc, transfer_id, data.get("failReason", "Simulated"))

    return app
