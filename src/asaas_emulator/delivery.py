"""Webhook delivery, with the failure modes integrations have to survive.

Behaviour modelled on the documentation:
- at-least-once delivery, so receivers must deduplicate on the event id;
- the configured authToken travels in the `asaas-access-token` header;
- any non-2xx answer or network error is retried later;
- SEQUENTIALLY webhooks never deliver an event while an older one is still pending;
- after N consecutive failures (15 by default) the webhook is marked interrupted and
  its queue stops until someone sets `interrupted` back to false.
"""

from __future__ import annotations

import json
import logging
import threading
import time

import httpx

from .service import Emulator

log = logging.getLogger("asaas_emulator.delivery")


class Dispatcher:
    def __init__(self, emulator: Emulator) -> None:
        self.emulator = emulator
        self._client = httpx.Client(timeout=emulator.settings.webhook_timeout_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="webhooks", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._client.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.deliver_due()
            except Exception:  # pragma: no cover - keep the worker alive
                log.exception("webhook dispatcher iteration failed")
            self._stop.wait(0.1)

    def deliver_due(self, *, ignore_backoff: bool = False) -> int:
        """Attempt every delivery that is due. Returns how many attempts were made."""
        with self._lock:
            store = self.emulator.store
            now = float("inf") if ignore_backoff else time.time()
            attempts = 0
            for delivery in store.due_deliveries(now):
                webhook = store.get("webhook", delivery["account"], delivery["webhook_id"])
                if webhook is None or not webhook["enabled"] or webhook["interrupted"]:
                    continue
                if webhook["sendType"] == "SEQUENTIALLY" and store.pending_ahead(
                    webhook["id"], delivery["event_id"]
                ):
                    continue
                attempts += 1
                self._attempt(delivery, webhook)
            return attempts

    def _attempt(self, delivery, webhook) -> None:
        store = self.emulator.store
        settings = self.emulator.settings
        event = store.event(delivery["event_id"])
        headers = {"Content-Type": "application/json", "User-Agent": "asaas-emulator"}
        if webhook.get("authToken"):
            headers["asaas-access-token"] = webhook["authToken"]

        status_code: int | None = None
        error: str | None = None
        try:
            response = self._client.post(webhook["url"], content=json.dumps(event), headers=headers)
            status_code = response.status_code
            ok = 200 <= response.status_code < 300
            if not ok:
                error = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            ok = False
            error = f"{type(exc).__name__}: {exc}"

        with store.transaction():
            current = store.get("webhook", delivery["account"], webhook["id"])
            if current is None:
                return
            if ok:
                store.mark_delivery(
                    delivery["id"], status="DELIVERED", status_code=status_code, error=None
                )
                current["_consecutiveFailures"] = 0
            else:
                retry_at = time.time() + settings.webhook_retry_seconds
                store.mark_delivery(
                    delivery["id"],
                    status="PENDING",
                    status_code=status_code,
                    error=error,
                    next_attempt_at=retry_at,
                )
                current["_consecutiveFailures"] += 1
                current["penalizedRequestsCount"] += 1
                if current["_consecutiveFailures"] >= settings.webhook_max_failures:
                    current["interrupted"] = True
                    log.warning(
                        "webhook %s interrupted after %s consecutive failures",
                        current["id"],
                        current["_consecutiveFailures"],
                    )
            store.save("webhook", delivery["account"], current)
