"""Single media profile validated against the target Hikvision SDK.

The private system header and RTP descriptors are profile-specific fixtures.
Changing WIDTH/HEIGHT alone does not make another resolution supported.
"""

WIDTH = 704
HEIGHT = 576
FPS = 25
RTP_CLOCK = 90000

HEVC_SYSTEM_HEADER = bytes.fromhex(
    "00000080494d4b48010200000400050011710110401f000000fa000000000000000000000000000000000000"
)
X265_PARAMETERS = (
    f"repeat-headers=1:aud=1:keyint={FPS}:min-keyint={FPS}:scenecut=0:"
    "bframes=0:info=0:pools=1:frame-threads=1:log-level=error"
)


def build_encoder_command(ffmpeg: str, rtsp_url: str) -> list[str]:
    """Use RTSP's verified -timeout option and omit the large encoder-info SEI."""
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-rtsp_transport",
        "tcp",
        "-timeout",
        "10000000",
        "-i",
        rtsp_url,
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        f"scale={WIDTH}:{HEIGHT},fps={FPS}",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx265",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-x265-params",
        X265_PARAMETERS,
        "-flush_packets",
        "1",
        "-f",
        "hevc",
        "pipe:1",
    ]
