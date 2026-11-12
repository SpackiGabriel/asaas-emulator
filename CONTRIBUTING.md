# Contributing

Thanks for helping. Issues and pull requests can be written in Portuguese or English.

The project has one rule that matters more than any other:

> **The real Asaas API is the specification.** When Saci and the sandbox disagree, Saci is wrong.

## Reporting a difference from the real API

This is the most valuable contribution you can make. Open an issue with the **"Saci disagrees with Asaas"** template and include the request, what the sandbox returned and what Saci returned. Remove API keys and personal data first.

Even better, add a failing test to `tests/conformance/test_contract.py` that passes against the sandbox and fails against Saci. That test becomes the fix's acceptance criterion and keeps the behaviour from drifting again.

## Development setup

```bash
pip install -e ".[dev]"
pytest --cov          # coverage must stay at 85% or above
ruff check src tests examples
ruff format src tests examples
mypy
```

To run a local server from your checkout, use `saci --port 8000`. To build the Docker image, `docker build -t saci .`.

To run the contract tests against the real sandbox, you need a sandbox API key:

```bash
ASAAS_CONFORMANCE_BASE_URL=https://api-sandbox.asaas.com \
ASAAS_CONFORMANCE_API_KEY='$aact_...' \
pytest -m conformance -v
```

Without those variables, `pytest -m conformance` runs the same suite against an in-process Saci.

## Where things live

| File | What it holds |
| --- | --- |
| `src/saci/app.py` | Routes for `/v3` and the `/_saci` control API. Kept thin |
| `src/saci/service.py` | The business rules: customers, payments, balance, transfers, webhooks, and what happens when the clock moves |
| `src/saci/store.py` | SQLite storage, atomic debits, the event outbox and the delivery queue |
| `src/saci/delivery.py` | The webhook worker: retries, ordering and interruption |
| `src/saci/codes.py` | Pix BR Code and boleto line generators |
| `src/saci/validators.py`, `money.py` | Input validation and integer-cent money |
| `src/saci/clock.py`, `config.py`, `errors.py` | Controllable clock, `SACI_*` settings, Asaas-shaped errors |

Tests in `tests/` use an in-process app through FastAPI's `TestClient`. The fixtures in `tests/conftest.py` give you a `client` with the clock frozen on 2026-03-02 and webhook delivery set to manual, a `customer`, a `make_payment` factory, and a `receiver`, which is a real HTTP server that records webhook deliveries and can be scripted to fail.

## Adding an endpoint

1. Read the endpoint in the [official reference](https://docs.asaas.com/reference). Every reference page has an OpenAPI definition; append `.md` to its URL to read it as text.
2. Put the business rule in `src/saci/service.py`, and keep the route in `app.py` thin.
3. If the change moves money or changes a status, emit the matching webhook event inside the same transaction.
4. Add unit tests in `tests/`, and a contract test in `tests/conformance/` for anything both sides should agree on.
5. Update the docs in both languages: `README.md` (Portuguese) and `README.en.md` (English) have the same sections. If you had to guess a behaviour, list it under "Suposto" / "Assumed" in the fidelity section of both.

## What CI checks

Every pull request runs Ruff lint and format checks and mypy, the tests on Python 3.11, 3.12 and 3.13, the Python and Node examples against a live server, a package build verified with `twine` and installed in a clean environment, a Dockerfile lint, and a real image build that is started and smoke-tested. The contract tests against the real sandbox run on `main` when the `ASAAS_SANDBOX_API_KEY` secret is set.

## Pull requests

- Keep each PR to one behaviour.
- Describe how you verified it, including whether you ran the contract tests against the sandbox.
- If you used AI tools to write code or tests, say so in the PR description and confirm you reviewed and ran the result.
