"""CLI for the configurable live ISUP gateway."""

from __future__ import annotations

# Support the existing "python /path/live_gateway.py" service command.
if __package__ in (None, ""):
    import sys
    from pathlib import Path as _Path

    sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    __package__ = "isup_gateway"

import argparse
import json
import logging
import signal
import threading

from .settings import load_settings, public_settings
from .runtime import GatewayRuntime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--check", action="store_true", help="validate configuration without printing secrets"
    )
    parser.add_argument("--status-file")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        settings, targets = load_settings(args.config)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    if args.check:
        print(json.dumps(public_settings(settings, targets), ensure_ascii=False, indent=2))
        return
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    GatewayRuntime(settings, targets, args.status_file).run(stop)


if __name__ == "__main__":
    main()
