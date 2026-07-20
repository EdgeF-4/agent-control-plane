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
    sub.add_parser("anchor", help="pin a Merkle root over the recorder run heads now")
    sub.add_parser("anchor-verify", help="verify the anchor log and detect run drift")

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

    if args.command in ("anchor", "anchor-verify"):
        from .anchor import Anchorer
        from .engines import EngineHub

        hub = EngineHub(settings)
        try:
            anchorer = Anchorer(settings, hub.recorder_config)
            if args.command == "anchor":
                rec = anchorer.run_once()
                print(
                    f"anchored {rec['run_count']} run(s) — merkle root "
                    f"{rec['merkle_root']} (seq {rec['seq']})"
                )
                if rec.get("s3") is not None:
                    print("s3:", rec["s3"])
                return 0
            result = anchorer.verify()
            log = result["anchor_log"]
            print(
                f"anchors: {result['anchor_count']}  log_ok: {log['ok']}  "
                f"latest_root: {result['latest_merkle_root']}"
            )
            if not log["ok"]:
                print("  anchor log broken:", log["reason"])
            for d in result["drift"]:
                print(
                    f"  DRIFT {d['run_id']}: anchored {d['anchored_head'][:12]} "
                    f"now {d['current_head'][:12]} chain_ok={d['chain_ok']}"
                )
            print("OK" if result["ok"] else "FAILED")
            return 0 if result["ok"] else 1
        finally:
            hub.close()

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
