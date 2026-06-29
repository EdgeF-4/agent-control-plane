"""``control-plane`` command: serve the API or seed a demo dataset."""

from __future__ import annotations

import argparse
import asyncio

from .config import load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="control-plane")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the API server")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)

    sub.add_parser("seed", help="seed a demonstrable dataset (run once)")

    args = parser.parse_args(argv)
    settings = load_settings()

    if args.command == "serve":
        import uvicorn

        from .main import create_app

        uvicorn.run(
            create_app(settings),
            host=args.host or settings.server.host,
            port=args.port or settings.server.port,
        )
        return 0

    if args.command == "seed":
        from .seed import seed

        asyncio.run(seed(settings))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
