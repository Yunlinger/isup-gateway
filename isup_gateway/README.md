# ISUP 网关源码

完整安装、三平台配置、systemd 部署和维护说明见仓库根目录的 [README](../README.md)。

- 实时网关入口：`python -m isup_gateway --config config.local.json`
- 文件模拟入口：`python -m isup_gateway.virtual_camera --help`
- 自动测试：`python -m unittest discover -s tests -v`
- 架构说明：[ARCHITECTURE.md](../docs/ARCHITECTURE.md)
- 联调证据与限制：[VALIDATION.md](../docs/VALIDATION.md)

该包实现 ISUP 5 注册、心跳及 TCP 实时预览。当前媒体配置为 HEVC
704×576@25fps，无音频。源平台本机取流用 RTSP，目标到浏览器的展示由平台实现。
