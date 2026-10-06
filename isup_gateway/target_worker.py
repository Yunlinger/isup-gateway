"""Registration/reconnect and optional display-name synchronization per target."""

from __future__ import annotations

import logging
import threading
import urllib.error
import urllib.parse

from .cam_client import CamClient
from .device_session import DeviceSession, DeviceSettings
from .live_sender import LiveSender
from .settings import Target
from .live_source import SharedSource

LOG = logging.getLogger("isup_gateway.target")


class TargetWorker:
    def __init__(self, target: Target, source: SharedSource):
        self.target, self.source = target, source
        self.stop = threading.Event()
        self.sender = LiveSender(source, target)
        args = DeviceSettings(
            host=target.host,
            port=target.port,
            device_id=target.device_id,
            name=target.device_name,
            isup_key=target.isup_key,
            duration=0,
        )
        self.camera = DeviceSession(args, sender=self.sender, stop_event=self.stop)
        self.last_error = ""
        self.thread = threading.Thread(target=self._run, name="isup-" + target.label, daemon=True)
        self.name_thread = threading.Thread(
            target=self._sync_name, name="name-" + target.label, daemon=True
        )

    def start(self):
        self.thread.start()
        if self.target.api_base:
            self.name_thread.start()

    def _run(self):
        while not self.stop.is_set():
            try:
                self.camera.run()
            except (OSError, EOFError, ValueError, RuntimeError) as exc:
                if not self.stop.is_set():
                    self.last_error = str(exc)
                    LOG.warning("target_reconnect label=%s error=%s", self.target.label, exc)
            finally:
                self.camera.online = False
                self.sender.stop()
            self.stop.wait(3)

    def _sync_name(self):
        t = self.target
        client = CamClient(t.api_base, t.api_username, t.api_password)
        path = "/api/devices/" + urllib.parse.quote(t.device_id, safe="")
        while not self.stop.is_set():
            if self.camera.online:
                try:
                    client.request(path, {"name": t.device_name}, method="PATCH")
                    LOG.info("target_display_name_set label=%s id=%s", t.label, t.device_id)
                    return
                except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
                    LOG.warning("target_display_name_pending label=%s error=%s", t.label, exc)
            self.stop.wait(5)

    def close(self):
        self.camera.disconnect()
        self.thread.join(timeout=25)
        if self.name_thread.is_alive():
            self.name_thread.join(timeout=35)
        if self.thread.is_alive():
            raise RuntimeError(f"target {self.target.label} did not stop")

    def status(self):
        t = self.target
        return {
            "label": t.label,
            "host": t.host,
            "port": t.port,
            "device_id": t.device_id,
            "device_name": t.device_name,
            "online": self.camera.online,
            "connection_error": "" if self.camera.online else self.last_error,
            **self.sender.status(),
        }
