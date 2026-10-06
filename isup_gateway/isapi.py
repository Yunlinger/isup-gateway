"""Minimal ISAPI responses for the supported single-channel preview profile."""

from __future__ import annotations

import json
from xml.sax.saxutils import escape

from .media_profile import WIDTH, HEIGHT, FPS


def isapi_response(request: bytes, name: str = "ISUP virtual camera", device_id: str = "") -> bytes:
    lines = request.decode(errors="replace").splitlines()
    first = lines[0] if lines else ""
    method, _, url = first.partition(" ")
    path = url.split("?", 1)[0]
    namespace = 'version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema"'
    display_name = escape(name)
    if method == "GET" and path == "/ISAPI/System/deviceInfo":
        return (
            f"<DeviceInfo {namespace}><deviceName>{display_name}</deviceName>"
            f"<deviceID>{escape(device_id)}</deviceID><serialNumber>{escape(device_id)}</serialNumber>"
            "<model>ISUP Gateway</model></DeviceInfo>"
        ).encode()
    if method == "GET" and path == "/ISAPI/Event/notification/httpHosts":
        return f"<HttpHostNotificationList {namespace}/>".encode()
    if method == "GET" and path == "/ISAPI/System/Video/inputs/channels":
        return (
            f"<VideoInputChannelList {namespace}><VideoInputChannel {namespace}>"
            f"<id>1</id><inputPort>1</inputPort><name>{display_name}</name>"
            "<videoInputEnabled>true</videoInputEnabled></VideoInputChannel></VideoInputChannelList>"
        ).encode()
    if method == "GET" and path.startswith("/ISAPI/Streaming/channels/"):
        return (
            f"<StreamingChannel {namespace}><id>101</id><channelName>{display_name}</channelName>"
            "<enabled>true</enabled><Video><enabled>true</enabled><videoInputChannelID>1</videoInputChannelID>"
            f"<videoCodecType>H.265</videoCodecType><videoResolutionWidth>{WIDTH}</videoResolutionWidth>"
            f"<videoResolutionHeight>{HEIGHT}</videoResolutionHeight><maxFrameRate>{FPS * 100}</maxFrameRate>"
            "<videoQualityControlType>CBR</videoQualityControlType><constantBitRate>1024</constantBitRate>"
            "</Video><Audio><enabled>false</enabled></Audio></StreamingChannel>"
        ).encode()
    supported = method == "PUT" and path in (
        "/ISAPI/System/Network/ISUP/heartbeat",
        "/ISAPI/System/PictureServer",
        "/ISAPI/Event/notification/httpHosts/1",
    )
    code, status, substatus = (
        (1, "OK", "ok") if supported else (4, "Invalid Operation", "notSupport")
    )
    if "format=json" in url:
        return json.dumps(
            {
                "requestURL": path,
                "statusCode": code,
                "statusString": status,
                "subStatusCode": substatus,
            }
        ).encode()
    return (
        f"<ResponseStatus {namespace}><requestURL>{escape(path)}</requestURL>"
        f"<statusCode>{code}</statusCode><statusString>{status}</statusString>"
        f"<subStatusCode>{substatus}</subStatusCode></ResponseStatus>"
    ).encode()
