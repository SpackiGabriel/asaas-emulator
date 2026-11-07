# Changelog

All notable changes are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-06

First public release.

### Added

- Customers: create, list, retrieve, update, delete and restore, with CPF/CNPJ check-digit validation.
- Payments with `PIX`, `BOLETO` and `UNDEFINED` billing: create, list with filters, retrieve, update, delete, restore and status.
- Pix QR codes with valid BR Code payloads (CRC16) and scannable PNG images.
- Boleto typeable lines and barcodes in the FEBRABAN layout with valid check digits.
- Sandbox actions to confirm a payment and force it overdue; cash receipt and its undo.
- Account balance with flat, configurable fees and atomic debits.
- Pix and TED transfers with automatic or manual settlement, cancellation and simulated failures.
- Webhook configuration and real delivery: `asaas-access-token` header, retries with a stable event id, sequential ordering and queue interruption after consecutive failures.
- One isolated account per API key.
- Control API to reset state, move the clock, set balances and inspect events and deliveries.
- Contract test suite that runs against both the emulator and the real Asaas sandbox.
- Docker image on GitHub Container Registry for amd64 and arm64, CI workflows, and examples in Python and Node.js.

[0.1.0]: https://github.com/SpackiGabriel/asaas-emulator/releases/tag/v0.1.0
