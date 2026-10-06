# 用视频文件模拟实时摄像头

该入口用于协议联调，与真实视频网关共享认证、控制和媒体封装代码。
设备端发送 HEVC RTP，服务端 SDK 输出 PS；无需预先生成海康 PS。

准备固定配置视频：

```bash
ffmpeg -hide_banner -loglevel error -y -i input.mp4 -t 12 \
  -vf scale=704:576,fps=25 -an -pix_fmt yuv420p \
  -c:v libx265 -preset ultrafast -tune zerolatency \
  -x265-params 'repeat-headers=1:aud=1:keyint=25:min-keyint=25:scenecut=0:bframes=0:info=0:log-level=error' \
  -f hevc demo.h265
ffprobe -v error -select_streams v:0 \
  -show_entries stream=codec_name,width,height,r_frame_rate -of json demo.h265
```

必须使用 `aud=1`、重复参数头和 `info=0`。程序检查 AUD/VPS，但不会自行校验
分辨率；其他配置尚未验证。视频文件整体读入内存，因此建议使用短片循环播放。

在 bash 设置平台密钥并运行：

```bash
read -r -s -p 'ISUP key: ' ISUP_KEY
export ISUP_KEY
.venv/bin/python -m isup_gateway.virtual_camera \
  --host 192.0.2.10 --port 7661 --device-id FILECAM001 \
  --name 'File camera' --video /absolute/path/demo.h265
```

ID 与运行中的其他设备必须不同。平台允许注册后，通过其 SDK 对该设备发起
通道 1、主码流实时预览，程序才开始发送视频。
`--duration` 可用于有限时长测试；默认持续运行，`Ctrl+C` 停止。

`isup_gateway/deploy/isup-virtual-camera.service` 和 `camera.env.example` 提供
服务模板。模板假定项目放在 `/root/isup-virtual-camera`，其中包含 Python 包和
`.venv`。密钥配置文件权限设置为 600，不提交到 Git。
