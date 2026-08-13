"""``control-plane`` command: serve the API or seed a demo dataset."""

from __future__ import annotations

import argparse
import asyncio
import sys

from .config import load_settings


class ActionableArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(
            2,
            f"control-plane: error: {message}. Next: run 'control-plane --help', "
            "correct the command or arguments, then retry.\n",
        )


def main(argv: list[str] | None = None) -> int:
    parser = ActionableArgumentParser(prog="control-plane")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the API server")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)

    sub.add_parser("seed", help="seed a demonstrable dataset (run once)")

    args = parser.parse_args(argv)
    try:
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
    except Exception as error:
        print(
            f"control-plane {args.command} failed: {type(error).__name__}: {error}. "
            f"Next: correct the reported configuration, dependency, database, or "
            f"filesystem error, then rerun 'control-plane {args.command}'.",
            file=sys.stderr,
        )
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
