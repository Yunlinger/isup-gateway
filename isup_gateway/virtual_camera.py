"""Loop a prepared HEVC file as an independent ISUP 5 virtual camera."""

from __future__ import annotations

# Support the existing "python /path/live_gateway.py" service command.
if __package__ in (None, ""):
    import sys
    from pathlib import Path as _Path

    sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    __package__ = "isup_gateway"

import argparse
import logging
import os
from pathlib import Path
import time

from .device_session import DeviceSession, DeviceSettings
from .hevc import access_units
from .media_sender import FileMediaSender

LOG = logging.getLogger("isup_gateway.file_camera")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, default=7661)
    p.add_argument("--device-id", default="VIRISUP001")
    p.add_argument("--name", default="ISUP virtual camera")
    p.add_argument("--key-env", default="ISUP_KEY")
    p.add_argument("--video", required=True, help="HEVC Annex B with AUD, 704x576 at 25fps")
    p.add_argument("--fps", type=float, default=25)
    p.add_argument("--duration", type=float, default=0, help="seconds; zero runs until interrupted")
    args = p.parse_args()
    if args.fps != 25:
        p.error("the validated media profile requires 25 fps")
    if args.duration < 0:
        p.error("duration cannot be negative")
    if not os.environ.get(args.key_env):
        p.error(f"set {args.key_env} to the platform's ISUP key")
    connection = DeviceSettings(
        args.host, args.port, args.device_id, os.environ[args.key_env], args.name, args.duration
    )
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        while True:
            try:
                DeviceSession(
                    connection,
                    FileMediaSender(access_units(Path(args.video).read_bytes()), args.fps),
                ).run()
                break
            except (OSError, EOFError) as exc:
                if args.duration:
                    raise
                LOG.warning("connection_lost error=%s; reconnecting in 3 seconds", exc)
                time.sleep(3)
    except KeyboardInterrupt:
        LOG.info("device_shutdown")


if __name__ == "__main__":
    main()
