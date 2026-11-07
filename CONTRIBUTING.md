# Contributing

Thanks for helping. The project has one rule that matters more than any other:

> **The real Asaas API is the specification.** When the emulator and the sandbox disagree, the emulator is wrong.

## Reporting a difference from the real API

This is the most valuable contribution you can make. Open an issue with the **"Saci disagrees with Asaas"** template and include the request, what the sandbox returned and what the emulator returned. Remove API keys and personal data first.

Even better, add a failing test to `tests/conformance/test_contract.py` that passes against the sandbox and fails against the emulator. That test becomes the fix's acceptance criterion and keeps the behaviour from drifting again.

## Adding an endpoint

1. Read the endpoint in the [official reference](https://docs.asaas.com/reference). Every reference page has an OpenAPI definition; append `.md` to its URL to read it as text.
2. Put the business rule in `src/saci/service.py`, and keep the route in `app.py` thin.
3. If the change moves money or changes a status, emit the matching webhook event inside the same transaction.
4. Add unit tests in `tests/`, and a contract test in `tests/conformance/` for anything both sides should agree on.
5. If you had to guess a behaviour, list it under "Assumed" in the fidelity section of both READMEs (`README.md` in Portuguese, `README.en.md` in English).

## Development setup

```bash
pip install -e ".[dev]"
pytest --cov
ruff check src tests
ruff format src tests
mypy
```

To run the contract tests against the real sandbox, you need a sandbox API key:

```bash
ASAAS_CONFORMANCE_BASE_URL=https://api-sandbox.asaas.com \
ASAAS_CONFORMANCE_API_KEY='$aact_...' \
pytest -m conformance -v
```

## Pull requests

- Keep each PR to one behaviour.
- Describe how you verified it, including whether you ran the contract tests against the sandbox.
- If you used AI tools to write code or tests, say so in the PR description and confirm you reviewed and ran the result.
