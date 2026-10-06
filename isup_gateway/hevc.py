"""HEVC access-unit parsing for files and incrementally read FFmpeg output."""

from __future__ import annotations

import re


def access_units(data: bytes) -> list[list[bytes]]:
    units, current = [], []
    starts = list(re.finditer(b"\x00{2,3}\x01", data))
    if not starts:
        raise ValueError("video must be HEVC Annex B with AUD NAL units")
    for i, start in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(data)
        nal = data[start.end() : end]
        if len(nal) < 2:
            continue
        kind = (nal[0] >> 1) & 63
        if kind == 35:
            if current:
                units.append(current)
                current = []
        else:
            current.append(nal)
    if current:
        units.append(current)
    if not any(((nal[0] >> 1) & 63) == 32 for unit in units for nal in unit):
        raise ValueError("video is missing HEVC VPS; H264 is not supported by this profile")
    if not any(((data[m.end()] >> 1) & 63) == 35 for m in starts if m.end() < len(data)):
        raise ValueError("video requires AUD (encode with x265 aud=1)")
    if not units or len(units) > 100000:
        raise ValueError("invalid video frame count")
    return units


class AnnexBParser:
    """Incremental HEVC Annex B parsing; no assumption about pipe chunk boundaries."""

    def __init__(self):
        self.buffer = bytearray()
        self.unit = []
        self.parameters = {}

    def feed(self, data: bytes):
        self.buffer.extend(data)
        if len(self.buffer) > 8 * 1024 * 1024:
            raise ValueError("HEVC NAL exceeds source size limit")
        starts = list(re.finditer(b"\x00{2,3}\x01", self.buffer))
        result = []
        for first, following in zip(starts, starts[1:]):
            nal = bytes(self.buffer[first.end() : following.start()])
            if len(nal) < 2:
                continue
            kind = (nal[0] >> 1) & 63
            if kind == 35:
                if self.unit and any((n[0] >> 1) & 63 < 32 for n in self.unit):
                    keyframe = any(16 <= ((n[0] >> 1) & 63) <= 23 for n in self.unit)
                    unit = self.unit
                    if keyframe:
                        missing = [
                            self.parameters[k]
                            for k in (32, 33, 34)
                            if k in self.parameters and not any((n[0] >> 1) & 63 == k for n in unit)
                        ]
                        unit = missing + unit
                    result.append((unit, keyframe))
                self.unit = []
            else:
                if kind in (32, 33, 34):
                    self.parameters[kind] = nal
                self.unit.append(nal)
        if starts:
            del self.buffer[: starts[-1].start()]
        return result
