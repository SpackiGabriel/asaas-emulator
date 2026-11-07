"""Run the emulator: `python -m saci` or the `saci` script."""

from __future__ import annotations

import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Saci: run the Asaas API emulator.")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()
    uvicorn.run("saci.app:create_app", factory=True, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
