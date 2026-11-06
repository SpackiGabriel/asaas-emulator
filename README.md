# asaas-emulator

A stateful, deterministic local emulator of the [Asaas](https://www.asaas.com) payments API (v3), for development and CI.

Point your integration at `http://localhost:8000` instead of `https://api-sandbox.asaas.com` and you get customers, Pix and boleto charges, a real account balance, Pix transfers and signed webhook deliveries, with no account, no network and no flakiness. It works with any language, because it is just an HTTP server.

```bash
docker build -t asaas-emulator https://github.com/SpackiGabriel/asaas-emulator.git
docker run --rm -p 8000:8000 asaas-emulator
```

## Why

Testing an Asaas integration usually means one of two bad options:

- **The real sandbox.** It needs an account, it is slow, it is shared state you cannot reset, and it cannot be told "it is now three days later" or "this webhook endpoint is down".
- **A hand-written mock.** It returns whatever the author remembered, usually a happy path, and slowly drifts away from the real API until tests pass against something that no longer exists.

This project takes a third route: an emulator that keeps real state (balances go up and down, money moves, events are queued), behaves deterministically, and is checked against the real sandbox by a contract test suite. A fake that lies is worse than no fake.

## What it emulates

| Area | Endpoints |
| --- | --- |
| Customers | `POST/GET /v3/customers`, `GET/PUT/DELETE /v3/customers/{id}`, `POST .../restore` |
| Payments | `POST/GET /v3/payments`, `GET/PUT/DELETE /v3/payments/{id}`, `POST .../restore`, `GET .../status` |
| Pix and boleto | `GET .../pixQrCode`, `GET .../identificationField` |
| Cash | `POST .../receiveInCash`, `POST .../undoReceivedInCash` |
| Sandbox actions | `POST /v3/sandbox/payment/{id}/confirm`, `POST /v3/sandbox/payment/{id}/overdue` |
| Balance | `GET /v3/finance/balance` |
| Transfers | `POST/GET /v3/transfers`, `GET /v3/transfers/{id}`, `DELETE /v3/transfers/{id}/cancel` |
| Webhooks | `POST/GET /v3/webhooks`, `GET/PUT/DELETE /v3/webhooks/{id}`, plus actual delivery |

Responses follow the shapes in the official OpenAPI reference: the `{"errors": [{"code", "description"}]}` error envelope, the `{"object": "list", "hasMore", "totalCount", ...}` list envelope, a bare `404` for unknown ids, and authentication through the `access_token` header.

Details that integrations commonly depend on are generated with real structure:

- **Pix payloads** are valid BR Codes (EMV) with a correct CRC16, and `encodedImage` is a real, scannable QR code PNG.
- **Boleto lines** are 47 digits and barcodes 44 digits in the FEBRABAN layout, with valid mod-10 and mod-11 check digits and the current due-date factor.
- **CPF and CNPJ** are validated by check digit, as the sandbox does.

## Quick start

With Docker:

```bash
docker build -t asaas-emulator https://github.com/SpackiGabriel/asaas-emulator.git
docker run --rm -p 8000:8000 asaas-emulator
```

With pip (Python 3.11+):

```bash
pip install git+https://github.com/SpackiGabriel/asaas-emulator.git
asaas-emulator --port 8000
```

A published container image and PyPI package are planned for the first tagged release.

A complete flow, from charge to payout:

```bash
H='access_token: $aact_local'
API=http://localhost:8000

# 1. A customer and a Pix charge
curl -s -X POST $API/v3/customers -H "$H" -H 'Content-Type: application/json' \
  -d '{"name": "Maria Souza", "cpfCnpj": "52998224725"}'
curl -s -X POST $API/v3/payments -H "$H" -H 'Content-Type: application/json' \
  -d '{"customer": "cus_000000000001", "billingType": "PIX", "value": 101.99, "dueDate": "2030-01-10"}'

# 2. The customer pays (the same sandbox action Asaas offers)
curl -s -X POST $API/v3/sandbox/payment/pay_000000000001/confirm -H "$H"
curl -s $API/v3/finance/balance -H "$H"          # {"balance": 100.0}, after the 1.99 fee

# 3. Pay someone out
curl -s -X POST $API/v3/transfers -H "$H" -H 'Content-Type: application/json' \
  -d '{"value": 60, "pixAddressKey": "owner@example.com", "pixAddressKeyType": "EMAIL"}'
curl -s $API/v3/finance/balance -H "$H"          # {"balance": 40.0}
```

Ids are sequential (`cus_000000000001`, `pay_000000000001`, ...), so a fresh emulator always produces the same traffic for the same test.

### In a test suite

In GitHub Actions, start it before your tests and point your client at it:

```yaml
- run: |
    pip install git+https://github.com/SpackiGabriel/asaas-emulator.git
    asaas-emulator --port 8000 &
    until curl -fs localhost:8000/_emulator/health; do sleep 0.5; done
- run: pytest
  env:
    ASAAS_BASE_URL: http://localhost:8000
```

Reset state between tests with `POST /_emulator/reset`.

## Behaviour worth knowing

**Every API key is a separate account.** Customers, payments, balances and webhooks never cross between keys, so parallel tests (or several tenants of a multi-tenant product) can share one emulator. Any non-empty key is accepted unless `ASAAS_EMULATOR_API_KEYS` restricts them.

**Balances start at zero.** A generous starting balance would hide the most common real failure. Fund an account the way you would in the sandbox (create a charge and confirm it), or set it directly with `PUT /_emulator/balance`.

**Money cannot be double-spent.** Transfers debit the balance with a single conditional `UPDATE ... WHERE balance >= amount`, so concurrent requests cannot overdraw an account. There is a test that fires eight transfers at once to prove it.

**Webhooks fail the way real ones do.** Deliveries carry the `asaas-access-token` header, are retried on any non-2xx answer or network error, keep the same event `id` across retries (delivery is at-least-once, so receivers must deduplicate), respect `SEQUENTIALLY` ordering, and stop when the queue is interrupted after 15 consecutive failures, exactly as documented. Set `interrupted` back to `false` to resume.

**Time is yours.** `POST /_emulator/clock` freezes or advances the date. Pending charges whose due date passes become `OVERDUE` and emit `PAYMENT_OVERDUE`; scheduled transfers settle.

**State changes and their events commit together.** A payment never changes status without its webhook event being queued in the same transaction (a transactional outbox), so tests never see one without the other.

## Control API

These endpoints do not exist in Asaas. They are how tests drive the emulator.

| Endpoint | What it does |
| --- | --- |
| `GET /_emulator/health` | Liveness, version and current emulated time |
| `POST /_emulator/reset` | Wipes all accounts and resets the clock |
| `GET /_emulator/clock` | Current emulated time |
| `POST /_emulator/clock` | `{"now": "2026-01-31T09:00:00"}` freezes time; `{"advanceDays": 3}` moves it |
| `PUT /_emulator/balance` | `{"balance": 500}` sets the balance of the calling account |
| `GET /_emulator/events` | Every event produced for the calling account, in order |
| `GET /_emulator/deliveries` | Every webhook delivery attempt, with status and last error |
| `POST /_emulator/webhooks/deliver` | Attempts all pending deliveries now, ignoring backoff |
| `POST /_emulator/transfers/{id}/done` | Settles a pending transfer |
| `POST /_emulator/transfers/{id}/fail` | Fails a pending transfer and returns the money; `{"failReason": "..."}` |

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `ASAAS_EMULATOR_DATABASE` | `:memory:` | SQLite path; use a file to keep state across restarts |
| `ASAAS_EMULATOR_API_KEYS` | (empty) | Comma-separated accepted keys; empty accepts any key |
| `ASAAS_EMULATOR_FEE_PIX_PAYMENT` | `1.99` | Flat fee on Pix charges |
| `ASAAS_EMULATOR_FEE_BOLETO_PAYMENT` | `1.99` | Flat fee on boleto charges |
| `ASAAS_EMULATOR_FEE_PIX_TRANSFER` | `0.00` | Fee on outgoing Pix transfers |
| `ASAAS_EMULATOR_FEE_TED_TRANSFER` | `0.00` | Fee on outgoing TED transfers |
| `ASAAS_EMULATOR_TRANSFER_MODE` | `auto` | `auto` settles transfers immediately; `manual` waits for the control API |
| `ASAAS_EMULATOR_DELIVERY_MODE` | `background` | `background` delivers webhooks continuously; `manual` only on request |
| `ASAAS_EMULATOR_WEBHOOK_MAX_FAILURES` | `15` | Consecutive failures before a webhook is interrupted |
| `ASAAS_EMULATOR_WEBHOOK_RETRY_SECONDS` | `1.0` | Delay before retrying a failed delivery |

## Fidelity

Every behaviour falls in one of two groups, and the difference matters.

**Taken from the official documentation:** endpoint paths, request and response fields, status and event enums, the error and list envelopes, the `access_token` and `asaas-access-token` headers, the webhook payload shape and event id format, at-least-once delivery, interruption after 15 consecutive failures, and the sandbox confirm and overdue actions.

**Assumed, pending confirmation by the contract suite against the sandbox:**

- which events a sandbox confirmation emits (the emulator emits `PAYMENT_RECEIVED`, not `PAYMENT_CONFIRMED` first);
- the exact error `code` strings for validation failures and insufficient balance;
- the default fee amounts and transfer id format;
- whether forcing overdue changes `dueDate` (the emulator keeps it).

If you find a difference, the contract suite is the place to prove it: add a failing test in `tests/conformance/` and the emulator is the side that changes.

## Contract tests against the real sandbox

`tests/conformance/` holds tests that must pass on both sides. By default they run against an in-process emulator. With a sandbox key they run against Asaas itself:

```bash
ASAAS_CONFORMANCE_BASE_URL=https://api-sandbox.asaas.com \
ASAAS_CONFORMANCE_API_KEY='$aact_...' \
pytest -m conformance -v
```

They create their own data and compare balances as deltas, because the sandbox is shared and never reset. CI runs them on every push to `main` when the `ASAAS_SANDBOX_API_KEY` secret is set.

## Not emulated yet

Card payments, subscriptions, installments, split, refunds and chargebacks, payment links, bill payments, anticipations, invoices (NFS-e) and subaccounts. Requests for card charges return a clear `unsupported_billingType` error instead of pretending.

## Development

```bash
pip install -e ".[dev]"
pytest              # unit, integration and contract tests against the emulator
ruff check src tests
```

The code is small on purpose. `service.py` holds the provider's rules, `store.py` the SQLite state, `delivery.py` the webhook worker and `app.py` the HTTP surface.

## Background

This started from an in-process Asaas fake maintained inside a production SaaS that automates the monthly financial close for property-management companies on top of Asaas: billing tenants, reconciling payments and paying property owners. Most of the decisions above (zero starting balance, atomic debits, state shared through a database instead of memory, events committed with the change that caused them) came from bugs that a simpler mock had hidden. This project is a clean-room rewrite of those ideas as a standalone, language-agnostic tool.

## Disclaimer

Not affiliated with or endorsed by Asaas. "Asaas" is a trademark of its owner. This tool never talks to the real API except in the opt-in contract tests.
