"""Captured ISUP TCP media framing and RFC 7798 HEVC RTP helpers."""

from __future__ import annotations

from dataclasses import dataclass
import struct

from .media_profile import HEVC_SYSTEM_HEADER


def send_media_header(sock, session_id: int, ssrc: int) -> None:
    """Bind the receiver's session, then describe the validated HEVC profile."""
    session = str(session_id).encode("ascii")
    if len(session) >= 64:
        raise ValueError("media session ID too long")
    sock.sendall(media_frame(1, session.ljust(64, b"\0")))
    sock.sendall(media_frame(2, HEVC_SYSTEM_HEADER))
    for descriptor in hik_hevc_descriptors(0, ssrc):
        sock.sendall(media_frame(2, bytes(4) + descriptor))


def media_frame(kind: int, payload: bytes) -> bytes:
    if len(payload) > 65535:
        raise ValueError("ISUP media payload is too large")
    return bytes([0x24, kind]) + struct.pack("!H", len(payload)) + payload


def media_frames(data: bytes):
    offset = 0
    while offset < len(data):
        if len(data) - offset < 4 or data[offset] != 0x24:
            raise ValueError(f"invalid media frame at offset {offset}")
        kind = data[offset + 1]
        size = int.from_bytes(data[offset + 2 : offset + 4], "big")
        end = offset + 4 + size
        if end > len(data):
            raise ValueError(f"truncated media frame at offset {offset}")
        yield kind, data[offset + 4 : end]
        offset = end


@dataclass
class RTP:
    payload_type: int
    sequence: int
    timestamp: int
    ssrc: int
    marker: bool
    payload: bytes


def parse_rtp(data: bytes) -> RTP:
    if len(data) < 12 or data[0] >> 6 != 2:
        raise ValueError("not an RTP v2 packet")
    offset = 12 + (data[0] & 15) * 4
    if offset > len(data):
        raise ValueError("truncated RTP CSRC list")
    if data[0] & 16:
        if offset + 4 > len(data):
            raise ValueError("truncated RTP extension")
        offset += 4 + 4 * int.from_bytes(data[offset + 2 : offset + 4], "big")
        if offset > len(data):
            raise ValueError("truncated RTP extension body")
    end = len(data)
    if data[0] & 32:
        padding = data[-1]
        if not padding or padding > end - offset:
            raise ValueError("invalid RTP padding")
        end -= padding
    seq, ts, ssrc = struct.unpack_from("!HII", data, 2)
    return RTP(data[1] & 127, seq, ts, ssrc, bool(data[1] & 128), data[offset:end])


class HEVCDepacketizer:
    """No DONL (matching the real camera); reject incomplete FU sequences."""

    def __init__(self):
        self.pending = None
        self.next_sequence = None
        self.timestamp = None
        self.dropped = 0

    def feed(self, rtp: RTP) -> list[bytes]:
        payload = rtp.payload
        if len(payload) < 2:
            raise ValueError("truncated HEVC NAL")
        kind = (payload[0] >> 1) & 63
        if kind < 48:
            if self.pending is not None:
                self.dropped += 1
            self.pending = None
            return [payload]
        if kind == 48:
            nals, offset = [], 2
            while offset < len(payload):
                if offset + 2 > len(payload):
                    raise ValueError("truncated HEVC aggregation length")
                size = int.from_bytes(payload[offset : offset + 2], "big")
                offset += 2
                if size < 2 or offset + size > len(payload):
                    raise ValueError("invalid HEVC aggregation unit")
                nals.append(payload[offset : offset + size])
                offset += size
            return nals
        if kind != 49 or len(payload) < 4:
            raise ValueError("unsupported HEVC RTP packet")
        fu = payload[2]
        if fu & 128:
            if self.pending is not None:
                self.dropped += 1
            header = bytes([(payload[0] & 0x81) | ((fu & 63) << 1), payload[1]])
            self.pending = bytearray(header + payload[3:])
            self.timestamp = rtp.timestamp
        elif (
            self.pending is None
            or self.next_sequence != rtp.sequence
            or self.timestamp != rtp.timestamp
        ):
            self.pending = None
            self.dropped += 1
            return []
        else:
            self.pending.extend(payload[3:])
        self.next_sequence = (rtp.sequence + 1) & 65535
        if fu & 64:
            nal = bytes(self.pending)
            self.pending = None
            return [nal]
        return []


def hevc_rtp(
    nal: bytes, sequence: int, timestamp: int, ssrc: int, marker: bool, max_payload: int = 1200
):
    """Yield RTP packets and sequence numbers for one HEVC NAL (RFC 7798)."""
    if len(nal) < 2 or max_payload < 4:
        raise ValueError("invalid HEVC NAL or payload limit")
    if len(nal) <= max_payload:
        payloads = [nal]
    else:
        payloads = []
        prefix = bytes([(nal[0] & 0x81) | (49 << 1), nal[1]])
        fu_type = (nal[0] >> 1) & 63
        for offset in range(2, len(nal), max_payload - 3):
            end = min(len(nal), offset + max_payload - 3)
            fu = fu_type | (128 if offset == 2 else 0) | (64 if end == len(nal) else 0)
            payloads.append(prefix + bytes([fu]) + nal[offset:end])
    for i, payload in enumerate(payloads):
        seq = (sequence + i) & 65535
        header = struct.pack(
            "!BBHII",
            0x80,
            96 | (128 if marker and i == len(payloads) - 1 else 0),
            seq,
            timestamp & 0xFFFFFFFF,
            ssrc,
        )
        yield seq, header + payload


def hik_hevc_descriptors(timestamp: int, ssrc: int):
    """Hikvision PT112 descriptors from the captured HEVC 704x576 profile.

    The SDK demuxer needs the private stream descriptor to associate dynamic
    payload type 96 with HEVC. These descriptor bodies are profile fixtures,
    not a general implementation of every Hikvision metadata TLV.
    """
    bodies = [
        bytes.fromhex(
            "0001000c400e484b01001aa324fcf66800ffffff450a249000e94554ffffffff4112484b000102030405060708090a0b0c0d0e0f"
        ),
        bytes.fromhex("00010007420e071010ea02c00240111000003a99430a0140fe007d0303e803ff"),
    ]
    for i, body in enumerate(bodies):
        yield struct.pack("!BBHII", 0x90, 0xF0, 1 + i, timestamp & 0xFFFFFFFF, ssrc) + body
