"""One on-demand cam source and HEVC encoder, shared by ISUP destinations."""

from __future__ import annotations

from collections import deque
import logging
import os
import queue
import selectors
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import uuid

from .cam_client import CamClient
from .hevc import AnnexBParser
from .media_profile import build_encoder_command
from .settings import SourceSettings

LOG = logging.getLogger("isup_gateway.source")


class Subscription:
    def __init__(self, capacity=50):
        self.queue = queue.Queue(capacity)
        self.waiting_keyframe = True
        self.dropped = 0
        self.lock = threading.Lock()

    def resync(self):
        with self.lock:
            self.waiting_keyframe = True
            self._clear()

    def _clear(self):
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                return

    def feed(self, frame, keyframe):
        with self.lock:
            if self.queue.full():
                self.dropped += self.queue.qsize()
                self._clear()
                self.waiting_keyframe = True
            if self.waiting_keyframe:
                if not keyframe:
                    return
                self.waiting_keyframe = False
            self.queue.put_nowait(frame)


class SharedSource:
    def __init__(self, settings: SourceSettings):
        self.settings = settings
        self.client = CamClient(settings.api_base, settings.username, settings.password)
        self.lock = threading.RLock()
        self.subscribers = {}
        self.thread = None
        self.stop_event = threading.Event()
        self.process = None
        self.closing = False
        self.frames = 0
        self.starts = 0
        self.active = False
        self.last_error = ""
        self.stream_path = ""

    def subscribe(self):
        with self.lock:
            if self.closing:
                raise RuntimeError("source is closing")
            token, sub = uuid.uuid4().hex, Subscription()
            self.subscribers[token] = sub
            if self.thread is None:
                self._start_worker()
            return token, sub

    def _start_worker(self):
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run, args=(self.stop_event,), name="shared-camera-source", daemon=True
        )
        self.thread.start()

    def unsubscribe(self, token):
        with self.lock:
            self.subscribers.pop(token, None)
            if not self.subscribers:
                self.stop_event.set()
                self._terminate()

    def _terminate(self):
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass

    def broadcast(self, unit, keyframe):
        with self.lock:
            self.last_error = ""
            self.frames += 1
            frame = (self.frames, unit)
            for sub in self.subscribers.values():
                sub.feed(frame, keyframe)

    def _encode(self, stop):
        cfg = self.settings
        result = self.client.request(
            "/api/live/start",
            {
                "device_id": cfg.device_id,
                "channel": cfg.channel,
                "stream_type": cfg.stream_type,
                "audio_enabled": False,
            },
        )
        if not result.get("stream_ready"):
            raise RuntimeError("source ISUP preview not ready")
        path = urllib.parse.urlparse(result["player_url"]).path.strip("/")
        if not path.startswith("live/"):
            raise RuntimeError("source player URL has no live path")
        self.stream_path = path
        if stop.is_set():
            return
        cmd = build_encoder_command(cfg.ffmpeg, cfg.rtsp_base.rstrip("/") + "/" + path)
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        tail = deque(maxlen=8)

        def stderr_reader():
            for line in process.stderr:
                tail.append(line.decode(errors="replace").strip())

        errors = threading.Thread(target=stderr_reader, daemon=True)
        errors.start()
        with self.lock:
            self.process = process
            self.starts += 1
            self.active = True
            if stop.is_set():
                self._terminate()
        parser = AnnexBParser()
        last_frame = time.monotonic()
        next_renewal = time.monotonic() + 1800
        LOG.info("source_encoder_started device=%s channel=%s", cfg.device_id, cfg.channel)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while not stop.is_set():
                    if time.monotonic() >= next_renewal:
                        try:
                            self.client.request(
                                "/api/live/start",
                                {
                                    "device_id": cfg.device_id,
                                    "channel": cfg.channel,
                                    "stream_type": cfg.stream_type,
                                    "audio_enabled": False,
                                },
                            )
                            next_renewal = time.monotonic() + 1800
                        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
                            LOG.warning("source_lease_renewal_failed error=%s", exc)
                            next_renewal = time.monotonic() + 30
                    if time.monotonic() - last_frame > 20:
                        raise RuntimeError("source produced no video frames for 20 seconds")
                    if not selector.select(0.5):
                        if process.poll() is not None:
                            raise RuntimeError("HEVC encoder exited: " + " | ".join(tail))
                        continue
                    data = os.read(process.stdout.fileno(), 65536)
                    if not data:
                        raise RuntimeError("HEVC encoder closed: " + " | ".join(tail))
                    for unit, keyframe in parser.feed(data):
                        self.broadcast(unit, keyframe)
                        last_frame = time.monotonic()
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            errors.join(timeout=2)
            process.stdout.close()
            process.stderr.close()
            with self.lock:
                self.process = None
                self.active = False

    def _run(self, stop):
        cfg = self.settings
        try:
            while not stop.is_set():
                try:
                    self._encode(stop)
                    self.last_error = ""
                except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
                    self.last_error = str(exc)
                    if not stop.is_set():
                        LOG.error("source_failed device=%s error=%s", cfg.device_id, exc)
                finally:
                    with self.lock:
                        for sub in self.subscribers.values():
                            sub.resync()
                stop.wait(3)
        finally:
            # The deployment uses a dedicated cam account: this only releases our sessions.
            try:
                self.client.request(
                    "/api/live/stop", {"device_id": cfg.device_id, "channel": cfg.channel}
                )
            except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
                LOG.warning("source_release_failed error=%s", exc)
            with self.lock:
                self.thread = None
                self.active = False
                self.stream_path = ""
                if self.subscribers and not self.closing:
                    self._start_worker()

    def close(self):
        with self.lock:
            self.closing = True
            self.subscribers.clear()
            self.stop_event.set()
            self._terminate()
            thread = self.thread
        if thread:
            thread.join(timeout=40)
            if thread.is_alive():
                raise RuntimeError("source worker did not stop")

    def status(self):
        with self.lock:
            return {
                "device_id": self.settings.device_id,
                "active": self.active,
                "subscribers": len(self.subscribers),
                "frames": self.frames,
                "encoder_starts": self.starts,
                "last_error": self.last_error,
            }
