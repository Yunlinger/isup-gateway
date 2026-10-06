"""Gateway configuration models, validation and a secret-free summary."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class SourceSettings:
    api_base: str
    username: str
    password: str = field(repr=False)
    device_id: str = "CAMERA001"
    channel: int = 1
    stream_type: int = 1
    rtsp_base: str = "rtsp://127.0.0.1:8554"
    ffmpeg: str = "ffmpeg"


@dataclass(frozen=True)
class Target:
    label: str
    host: str
    port: int
    device_id: str
    device_name: str
    isup_key: str = field(repr=False)
    enabled: bool = True
    media_host_override: str = ""
    media_port_override: int | None = None
    # Optional cam API; registration and video do not depend on this API.
    api_base: str = ""
    api_username: str = ""
    api_password: str = field(default="", repr=False)


def _text(value, name: str, *, required=True) -> None:
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f"{name} must be a {'nonempty ' if required else ''}string")


def _port(value, name: str) -> None:
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError(f"{name} must be an integer between 1 and 65535")


def _url(value, name: str, schemes: tuple[str, ...]) -> None:
    _text(value, name)
    parsed = urlparse(value)
    if parsed.scheme not in schemes or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"{name} must be a {'/'.join(schemes)} URL without embedded credentials")
    try:
        parsed.port
    except ValueError:
        raise ValueError(f"{name} has an invalid port") from None


def load_settings(path: str | Path) -> tuple[SourceSettings, list[Target]]:
    """Reject invalid identities before any source/platform connection is made."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("source"), dict):
        raise ValueError("config must contain a source object")
    if not isinstance(raw.get("targets"), list) or not raw["targets"]:
        raise ValueError("config must contain at least one target")
    source = SourceSettings(**raw["source"])
    _url(source.api_base, "source.api_base", ("http", "https"))
    _url(source.rtsp_base, "source.rtsp_base", ("rtsp",))
    for key in ("username", "password", "device_id", "ffmpeg"):
        _text(getattr(source, key), "source." + key)
    if type(source.channel) is not int or source.channel < 1:
        raise ValueError("source.channel must be a positive integer")
    if type(source.stream_type) is not int or source.stream_type not in (0, 1):
        raise ValueError("source.stream_type must be 0 (main) or 1 (substream)")
    if not all(isinstance(item, dict) for item in raw["targets"]):
        raise ValueError("targets must contain objects")
    targets = [Target(**item) for item in raw["targets"]]
    labels, identities = set(), set()
    for target in targets:
        for key in ("label", "host", "device_id", "device_name", "isup_key"):
            _text(getattr(target, key), "target." + key)
        for key in ("api_base", "api_username", "api_password", "media_host_override"):
            _text(getattr(target, key), "target." + key, required=False)
        if type(target.enabled) is not bool:
            raise ValueError("target.enabled must be a JSON boolean")
        if target.label in labels:
            raise ValueError("target labels must be unique")
        labels.add(target.label)
        if "://" in target.host or "/" in target.host or any(c.isspace() for c in target.host):
            raise ValueError("target.host must be a hostname or IP, without scheme or port")
        _port(target.port, "target.port")
        if (
            not target.device_id.isascii()
            or not 1 <= len(target.device_id) <= 12
            or not all(c.isalnum() or c in "_-" for c in target.device_id)
        ):
            raise ValueError("ISUP device IDs must be 1..12 ASCII letters/digits/_/-")
        if len(target.device_name.encode("utf-8")) > 128:
            raise ValueError("device_name must fit in 128 UTF8 bytes")
        if target.media_port_override is not None:
            _port(target.media_port_override, "target.media_port_override")
        identity = (target.host.lower(), target.port, target.device_id)
        if identity in identities:
            raise ValueError("duplicate target device registration")
        identities.add(identity)
        if target.api_base:
            _url(target.api_base, "target.api_base", ("http", "https"))
            if not (target.api_username and target.api_password):
                raise ValueError("target cam API needs api_username and api_password")
    return source, targets


def public_settings(source: SourceSettings, targets: list[Target]) -> dict:
    """Do not serialize credentials, including through dataclass repr/asdict."""
    return {
        "ok": True,
        "source_device": source.device_id,
        "targets": [
            {
                "label": t.label,
                "host": t.host,
                "port": t.port,
                "device_id": t.device_id,
                "device_name": t.device_name,
                "enabled": t.enabled,
            }
            for t in targets
        ],
    }
