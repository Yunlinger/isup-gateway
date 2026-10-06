"""Independent outbound ISUP media session subscribed to the shared source."""

from __future__ import annotations

import logging
import queue
import secrets
import socket

from .isup_media import media_frame, hevc_rtp, send_media_header
from .media_profile import FPS, RTP_CLOCK
from .media_sender import MediaSender

LOG = logging.getLogger("isup_gateway.sender")


class LiveSender(MediaSender):
    def __init__(self, source, target):
        super().__init__()
        self.source = source
        self.target = target
        self.frames = 0
        self.bytes = 0
        self.last_error = ""

    def _send(self, host, port, session_id):
        token, sub = None, None
        sequence, first_index = 1, None
        self.frames, self.bytes, self.last_error = 0, 0, ""
        ssrc = secrets.randbits(32)
        host = self.target.media_host_override or host
        port = self.target.media_port_override or port
        try:
            with socket.create_connection((host, port), timeout=10) as sock:
                with self.lock:
                    self.socket = sock
                send_media_header(sock, session_id, ssrc)
                token, sub = self.source.subscribe()
                LOG.info(
                    "target_media_connected label=%s id=%s session=%s destination=%s:%s",
                    self.target.label,
                    self.target.device_id,
                    session_id,
                    host,
                    port,
                )
                while not self.stop_event.is_set():
                    if self.iframe_event.is_set():
                        self.iframe_event.clear()
                        sub.resync()  # Encoder emits a fresh IDR at least once per second.
                    try:
                        index, unit = sub.queue.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    if first_index is None:
                        first_index = index
                    timestamp = (index - first_index) * (RTP_CLOCK // FPS)
                    for i, nal in enumerate(unit):
                        for seq, rtp in hevc_rtp(
                            nal, sequence, timestamp, ssrc, i == len(unit) - 1
                        ):
                            framed = media_frame(2, bytes(4) + rtp)
                            sock.sendall(framed)
                            self.bytes += len(framed)
                            sequence = (seq + 1) & 65535
                    self.frames += 1
                    if self.frames % (FPS * 5) == 0:
                        LOG.info(
                            "target_media_progress label=%s session=%s frames=%s bytes=%s dropped=%s",
                            self.target.label,
                            session_id,
                            self.frames,
                            self.bytes,
                            sub.dropped,
                        )
        except (OSError, ValueError, RuntimeError) as exc:
            if not self.stop_event.is_set():
                self.last_error = str(exc)
                LOG.error("target_media_failed label=%s error=%s", self.target.label, exc)
        finally:
            if token:
                self.source.unsubscribe(token)
            with self.lock:
                self.socket = None
            LOG.info(
                "target_media_stopped label=%s session=%s frames=%s",
                self.target.label,
                session_id,
                self.frames,
            )

    def status(self):
        return {
            "session_id": self.session_id,
            "previewing": bool(self.thread and self.thread.is_alive()),
            "frames": self.frames,
            "bytes": self.bytes,
            "last_error": self.last_error,
        }
