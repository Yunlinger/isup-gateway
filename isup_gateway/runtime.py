"""Own the shared source and all independent platform workers."""

from __future__ import annotations

import json
from pathlib import Path
import threading
import time

from .live_source import SharedSource
from .settings import SourceSettings, Target
from .target_worker import TargetWorker


class GatewayRuntime:
    def __init__(self, settings: SourceSettings, targets: list[Target], status_file=None):
        self.source = SharedSource(settings)
        self.workers = [TargetWorker(t, self.source) for t in targets if t.enabled]
        self.status_file = Path(status_file) if status_file else None

    def status(self) -> dict:
        return {
            "updated_at": time.time(),
            "source": self.source.status(),
            "targets": [worker.status() for worker in self.workers],
        }

    def write_status(self) -> None:
        if self.status_file is None:
            return
        self.status_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.status_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.status(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.status_file)

    def run(self, stop: threading.Event) -> None:
        try:
            for worker in self.workers:
                worker.start()
            while not stop.is_set():
                self.write_status()
                stop.wait(2)
        finally:
            self.close()

    def close(self) -> None:
        # Interrupt all control connections first, before joining any worker.
        for worker in self.workers:
            worker.camera.disconnect()
        try:
            for worker in self.workers:
                worker.close()
        finally:
            self.source.close()
            self.write_status()
