"""ISUP registration, MQTT control and preview commands for one virtual device."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
import select
import socket
import struct
import threading
import time
import xml.etree.ElementTree as ET

from .isapi import isapi_response
from .media_sender import MediaSender
from .isup_protocol import (
    authenticate,
    connect_packet,
    lp16,
    packet,
    read_packet,
    parse_publish,
    parse_business,
    business_message,
    publish,
)

LOG = logging.getLogger("isup_gateway.device")


@dataclass(frozen=True)
class DeviceSettings:
    """Device-side credentials and endpoint; platform management API is separate."""

    host: str
    port: int
    device_id: str
    isup_key: str = field(repr=False)
    name: str = "ISUP virtual camera"
    duration: float = 0


class DeviceSession:
    def __init__(self, args: DeviceSettings, sender: MediaSender, stop_event=None):
        self.args = args
        self.serial = "VIRTUAL-ISUP-CAMERA-" + args.device_id
        self.sender = sender
        self.stop_event = stop_event or threading.Event()
        self.online = False
        self.control_socket = None
        self.socket_lock = threading.Lock()
        self.message_id = 0

    def respond(self, sock, topic, metadata, body, session_key):
        self.message_id = self.message_id % 65535 + 1
        sock.sendall(
            publish(
                topic,
                business_message({"CmdVer": "1.0", "Seq": metadata["Seq"]}, body),
                session_key,
                self.message_id,
            )
        )

    def command(self, sock, topic, meta, body, key):
        command = tuple(topic.strip("/").split("/")[-2:])
        LOG.info("command topic=%s seq=%s", topic, meta.get("Seq"))
        if command == ("5000", "19713"):
            self.respond(
                sock,
                "/5000/19714",
                meta,
                isapi_response(
                    body, getattr(self.args, "name", "ISUP virtual camera"), self.args.device_id
                ),
                key,
            )
        elif command == ("3101", "12545"):
            xml = ET.fromstring(body)
            receiver, extra = xml.find("ReceiverInfo"), xml.find("ReceiverInfoEx")
            if receiver is None or extra is None or receiver.get("TransProto") != "TCP":
                self.respond(
                    sock, "/3101/12546", meta, b"<Response><Result>3701</Result></Response>", key
                )
                return
            if xml.findtext("IsEncrypt", "0") != "0" or xml.findtext("IsLinkEncrypt", "0") != "0":
                self.respond(
                    sock, "/3101/12546", meta, b"<Response><Result>3701</Result></Response>", key
                )
                return
            host = receiver.get("Address") or receiver.get("Domain")
            port = int(extra.get("Port") or receiver.get("Port"))
            session_id = int(extra.get("SessionID"))
            self.sender.start(host, port, session_id)
            self.respond(sock, "/3101/12546", meta, b"<Response><Result>0</Result></Response>", key)
        elif command == ("3100", "13416"):
            self.sender.iframe_event.set()
            self.respond(sock, "/3100/13417", meta, b"<Response><Result>0</Result></Response>", key)
        elif command == ("3101", "12547"):
            session_id = int(ET.fromstring(body).findtext("Session"))
            if session_id == self.sender.session_id:
                self.sender.stop()
            self.respond(sock, "/3101/12548", meta, b"<Response><Result>0</Result></Response>", key)
        else:
            LOG.warning(
                "unsupported_command topic=%s body=%s", topic, body[:300].decode(errors="replace")
            )

    def run(self):
        a = self.args
        auth = authenticate(a.host, a.port, a.device_id, a.isup_key, self.serial)
        LOG.info("isup_auth_verified device=%s", a.device_id)
        das_host = auth.das.get("Address") or auth.das.get("Domain")
        das_port = int(auth.das["Port"])
        with socket.create_connection((das_host, das_port), timeout=10) as sock:
            with self.socket_lock:
                self.control_socket = sock
            sock.sendall(connect_packet(a.device_id, self.serial, auth.session_key))
            if read_packet(sock) != (0x20, b"\0\0"):
                raise ValueError("MQTT CONNECT rejected")
            sock.sendall(packet(0x80, b"\0\0" + lp16(f"/{a.device_id}/#".encode()) + b"\1"))
            LOG.info("isup_mqtt_connected device=%s", a.device_id)
            self.online = True
            deadline = time.monotonic() + a.duration if a.duration else float("inf")
            last_ping = time.monotonic()
            try:
                while not self.stop_event.is_set() and time.monotonic() < deadline:
                    if time.monotonic() - last_ping >= 5:
                        sock.sendall(packet(0xC0))
                        last_ping = time.monotonic()
                    if not select.select([sock], [], [], 0.2)[0]:
                        continue
                    kind, payload = read_packet(sock)
                    if kind >> 4 == 3:
                        topic, packet_id, plain = parse_publish(kind, payload, auth.session_key)
                        if packet_id is not None:
                            sock.sendall(packet(0x40, struct.pack("!H", packet_id)))
                        meta, body = parse_business(plain)
                        self.command(sock, topic, meta, body, auth.session_key)
                    elif kind not in (0x40, 0x90, 0xD0):
                        LOG.warning("unhandled_mqtt_packet kind=%#x length=%s", kind, len(payload))
            finally:
                self.online = False
                with self.socket_lock:
                    self.control_socket = None
                self.sender.stop()
                try:
                    sock.sendall(packet(0xE0))
                except OSError:
                    pass

    def disconnect(self):
        self.stop_event.set()
        with self.socket_lock:
            if self.control_socket is not None:
                try:
                    self.control_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
