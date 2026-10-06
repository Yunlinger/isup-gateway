"""ISUP 5 authentication and MQTT framing verified against the camera capture.

This module implements the device side; it does not load Hikvision SDK libraries.
The SDK is used only by the separate server-side validation harness.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import socket
import struct
from dataclasses import dataclass, field

from .aes_backend import aes_cbc

AUTH_IV = b"01234567" + bytes(8)
MAX_PACKET = 4 * 1024 * 1024


def mqtt_length(value: int) -> bytes:
    if not 0 <= value <= 268435455:
        raise ValueError("MQTT remaining length is out of range")
    out = bytearray()
    while True:
        digit = value & 127
        value >>= 7
        out.append(digit | (128 if value else 0))
        if not value:
            return bytes(out)


def packet(kind: int, payload: bytes = b"") -> bytes:
    if len(payload) > MAX_PACKET:
        raise ValueError("packet exceeds device size limit")
    return bytes([kind]) + mqtt_length(len(payload)) + payload


def read_exact(sock: socket.socket, count: int) -> bytes:
    out = bytearray()
    while len(out) < count:
        part = sock.recv(count - len(out))
        if not part:
            raise EOFError("ISUP peer closed the socket")
        out.extend(part)
    return bytes(out)


def read_packet(sock: socket.socket) -> tuple[int, bytes]:
    kind = read_exact(sock, 1)[0]
    size = 0
    for shift in range(0, 28, 7):
        digit = read_exact(sock, 1)[0]
        size |= (digit & 127) << shift
        if not digit & 128:
            if size > MAX_PACKET:
                raise ValueError("peer packet exceeds size limit")
            return kind, read_exact(sock, size)
    raise ValueError("invalid MQTT remaining length")


def lp16(data: bytes) -> bytes:
    if len(data) > 65535:
        raise ValueError("length-prefixed field is too long")
    return struct.pack("!H", len(data)) + data


def share_key(isup_key: str, device_id: str) -> bytes:
    """COM_PUB_GenerateSharekey: SHA256, then three MD5/uppercase-hex steps."""

    def upper(data: bytes) -> bytes:
        return data.hex().upper().encode("ascii")

    def md5(data: bytes) -> bytes:
        return hashlib.md5(data).digest()

    first = upper(md5(hashlib.sha256(isup_key.encode()).digest() + device_id.encode()))
    second = upper(md5(first + b"www.88075998.com"))
    return upper(md5(second))


def signature(shared: bytes, data: bytes) -> bytes:
    return hmac.new(shared, hashlib.sha256(data).digest(), hashlib.sha256).digest()


def master_key(shared: bytes, randoms: bytes) -> bytes:
    if len(randoms) != 4:
        raise ValueError("authentication requires four one-byte randoms")
    return hashlib.sha384(randoms + shared).digest()[:8].hex().upper().encode("ascii")


def encrypt(key: bytes, plain: bytes) -> bytes:
    padding = 16 - len(plain) % 16
    return aes_cbc(key, AUTH_IV, plain + bytes([padding]) * padding, True)


def decrypt(key: bytes, cipher: bytes) -> bytes:
    if not cipher or len(cipher) % 16:
        raise ValueError("invalid ISUP AES-CBC ciphertext length")
    padded = aes_cbc(key, AUTH_IV, cipher, False)
    padding = padded[-1]
    if not 1 <= padding <= 16 or padded[-padding:] != bytes([padding]) * padding:
        raise ValueError("invalid ISUP PKCS7 padding")
    return padded[:-padding]


def business_message(metadata: dict, body: bytes) -> bytes:
    return lp16(json.dumps(metadata, separators=(",", ":")).encode()) + body


def parse_business(data: bytes) -> tuple[dict, bytes]:
    if len(data) < 2:
        raise ValueError("truncated business header")
    length = int.from_bytes(data[:2], "big")
    if length > len(data) - 2:
        raise ValueError("truncated business metadata")
    metadata = json.loads(data[2 : 2 + length])
    if not isinstance(metadata, dict):
        raise ValueError("business metadata must be a JSON object")
    return metadata, data[2 + length :]


@dataclass
class AuthResult:
    session_key: bytes = field(repr=False)
    das: dict


def authenticate(
    host: str, port: int, device_id: str, isup_key: str, serial: str, timeout: float = 10
) -> AuthResult:
    ident, name, full_serial = device_id.encode(), b"VIRTUAL", serial.encode()
    if not 1 <= len(ident) <= 12 or len(full_serial) > 64:
        raise ValueError("ISUP device ID must be 1..12 bytes and serial at most 64 bytes")
    shared = share_key(isup_key, device_id)
    r1, r3 = secrets.token_bytes(1), secrets.token_bytes(1)
    auth1 = (
        b"\x01\x01\x00"
        + bytes([len(ident)])
        + ident
        + bytes([len(name)])
        + name
        + struct.pack("!H", 41)
        + bytes([len(ident)])
        + ident
        + r1
        + signature(shared, ident + r1)
        + bytes([len(full_serial)])
        + full_serial
    )
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(packet(0x10, auth1))
        kind, auth2 = read_packet(sock)
        if kind != 0x20 or len(auth2) != 37 or auth2[:4] != b"\x01\x00\x00\x00":
            raise ValueError(
                f"ISUP AUTH2 rejected or malformed (kind={kind:#x}, size={len(auth2)})"
            )
        r2 = auth2[4:5]
        if not hmac.compare_digest(signature(shared, ident + r1 + r2), auth2[5:]):
            raise ValueError("ISUP AUTH2 server signature does not match")
        auth3 = (
            b"\x01\x01\x00" + r3 + bytes([len(ident)]) + ident + signature(shared, ident + r2 + r3)
        )
        sock.sendall(packet(0x30, auth3))
        kind, auth4 = read_packet(sock)
        if kind != 0x50 or len(auth4) < 38 or auth4[:4] != b"\x01\x00\x00\x00":
            raise ValueError(
                f"ISUP AUTH4 rejected or malformed (kind={kind:#x}, size={len(auth4)})"
            )
        r4, cipher_len = auth4[4:5], auth4[5]
        if len(auth4) != 6 + cipher_len + 32:
            raise ValueError("ISUP AUTH4 length does not match")
        if not hmac.compare_digest(signature(shared, ident + r3 + r4), auth4[6 + cipher_len :]):
            raise ValueError("ISUP AUTH4 server signature does not match")
        session_key = decrypt(master_key(shared, r1 + r2 + r3 + r4), auth4[6 : 6 + cipher_len])
        if len(session_key) != 16:
            raise ValueError("ISUP SessionKey must be 16 bytes")
        request = json.dumps(
            {"DevSerial": device_id, "Type": "DAS", "LBSAddress": host, "LBSPort": port}
        ).encode()
        sock.sendall(packet(0xA0, b"\x01\x01\x00" + encrypt(session_key, request)))
        kind, response = read_packet(sock)
        if kind != 0xB0 or response[:4] != b"\x01\x00\x00\x00":
            raise ValueError("ISUP DAS request was rejected")
        das = json.loads(decrypt(session_key, response[4:]))
        if das.get("Type") != "DAS" or not isinstance(das.get("DasInfo"), dict):
            raise ValueError("ISUP DAS response is invalid")
        return AuthResult(session_key, das["DasInfo"])


def device_info(device_id: str, serial: str) -> dict:
    return {
        "DevSerial": serial,
        "SubSerial": device_id,
        "FirmwareVersion": "",
        "DevType": 41,
        "MAC": "00:00:00:00:00:00",
        "SDKMainVersion": "V1.0.0",
        "SDKVersion": {
            "1000": "V1.0.0",
            "1001": "V1.0.0",
            "2000": "V1.0.1",
            "3000": "V1.0.1",
            "3001": "V2.0.0",
        },
        "Status": 1,
        "isSupportNPQPreview": False,
        "isSupportNPQPlayBack": False,
        "isSupportLinkEncrypt": False,
        "isSupportHls": False,
        "isSupportBroadcast": False,
    }


def connect_packet(device_id: str, serial: str, session_key: bytes) -> bytes:
    info = json.dumps(device_info(device_id, serial), separators=(",", ":")).encode()
    will_topic = f"/Basic/pu2cenplt/{serial}/devinfo".encode()
    # The captured camera uses MQTT 3.1.1 with retained QoS1 will, clean session,
    # username=device ID, and its AES-CBC device info as the will message.
    return packet(
        0x10,
        b"\x00\x04MQTT\x04\xae\x00\x1e"
        + lp16(device_id.encode())
        + lp16(will_topic)
        + lp16(encrypt(session_key, info))
        + lp16(device_id.encode()),
    )


def publish(topic: str, plain: bytes, session_key: bytes, packet_id: int) -> bytes:
    if not 1 <= packet_id <= 65535:
        raise ValueError("QoS1 packet ID must be nonzero")
    return packet(
        0x32, lp16(topic.encode()) + struct.pack("!H", packet_id) + encrypt(session_key, plain)
    )


def parse_publish(kind: int, payload: bytes, session_key: bytes) -> tuple[str, int | None, bytes]:
    if kind >> 4 != 3 or len(payload) < 2:
        raise ValueError("invalid MQTT PUBLISH")
    length = int.from_bytes(payload[:2], "big")
    offset = 2 + length
    qos = (kind >> 1) & 3
    if qos > 1 or offset + (2 if qos else 0) > len(payload):
        raise ValueError("unsupported or truncated PUBLISH")
    topic = payload[2:offset].decode()
    packet_id = int.from_bytes(payload[offset : offset + 2], "big") if qos else None
    offset += 2 if qos else 0
    return topic, packet_id, decrypt(session_key, payload[offset:])
