"""Media thread lifecycle shared by live and looped-file senders."""

from __future__ import annotations

import logging
import socket
import threading
import time

from .isup_media import hevc_rtp, media_frame, send_media_header
from .media_profile import FPS, RTP_CLOCK

LOG = logging.getLogger("isup_gateway.media")


class MediaSender:
    """One media session; stop closes the socket before waiting for its thread."""

    def __init__(self):
        self.stop_event = threading.Event()
        self.iframe_event = threading.Event()
        self.thread = None
        self.socket = None
        self.lock = threading.Lock()
        self.session_id = None

    def start(self, host, port, session_id):
        self.stop()
        self.stop_event = threading.Event()
        self.session_id = session_id
        self.thread = threading.Thread(
            target=self._send, args=(host, port, session_id), daemon=True
        )
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        with self.lock:
            if self.socket is not None:
                try:
                    self.socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        if self.thread:
            self.thread.join(timeout=11)
            if self.thread.is_alive():
                raise RuntimeError("media sender did not stop")
            self.thread = None
        self.session_id = None

    def _send(self, host, port, session_id):
        raise NotImplementedError


class FileMediaSender(MediaSender):
    """Loop prepared Annex B access units at the verified frame rate."""

    def __init__(self, units, fps=FPS):
        super().__init__()
        self.units, self.fps = units, fps

    def _send(self, host, port, session_id):
        sequence, frames, sent = 1, 0, 0
        try:
            with socket.create_connection((host, port), timeout=10) as sock:
                with self.lock:
                    self.socket = sock
                send_media_header(sock, session_id, 0x55667788)
                LOG.info("media_connected session=%s destination=%s:%s", session_id, host, port)
                started = time.monotonic()
                index = 0
                while not self.stop_event.is_set():
                    if self.iframe_event.is_set():
                        self.iframe_event.clear()
                        index = 0
                    unit = self.units[index]
                    timestamp = round(frames * RTP_CLOCK / self.fps)
                    for i, nal in enumerate(unit):
                        for seq, rtp in hevc_rtp(
                            nal, sequence, timestamp, 0x55667788, i == len(unit) - 1
                        ):
                            framed = media_frame(2, bytes(4) + rtp)
                            sock.sendall(framed)
                            sent += len(framed)
                            sequence = (seq + 1) & 65535
                    frames += 1
                    index = (index + 1) % len(self.units)
                    if frames % round(self.fps * 5) == 0:
                        LOG.info(
                            "media_progress session=%s frames=%s bytes=%s", session_id, frames, sent
                        )
                    self.stop_event.wait(max(0, started + frames / self.fps - time.monotonic()))
        except (OSError, ValueError) as exc:
            if not self.stop_event.is_set():
                LOG.error("media_failed session=%s error=%s", session_id, exc)
        finally:
            with self.lock:
                self.socket = None
            LOG.info("media_stopped session=%s frames=%s bytes=%s", session_id, frames, sent)
