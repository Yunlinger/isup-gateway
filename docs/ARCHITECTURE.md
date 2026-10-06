# 模块与生命周期

网关只有一个 `SharedSource`，每个启用目标有一个 `TargetWorker`。
目标的设备注册先启动，媒体线程由平台下发的预览指令触发。

## 数据和控制

1. `DeviceSession` 完成四步认证、解出 SessionKey、取得 DAS 地址。
2. 在 DAS 建立 MQTT 控制连接，订阅设备消息并发送心跳。
3. 取流指令包含目标接收地址与 SessionID；`LiveSender` 建立独立 TCP 连接。
4. 首个发送线程向 `SharedSource` 订阅，源调用 cam API 开始真实摄像头预览。
5. 一个 FFmpeg 从本机 RTSP 读取视频、编码 HEVC、通过 stdout 输出 Annex B。
6. `AnnexBParser` 按 AUD 拆帧，`Subscription` 将帧送入每个目标的独立有界队列。
7. 每个 `LiveSender` 独立计算 RTP 序号、SSRC、时间戳，再做 ISUP TCP 封装。

目标的 SessionKey、媒体 SessionID 和 RTP 序号不能在平台之间复用。
共享的是编码后的访问单元，不是带目标会话身份的媒体包。

## 停止和慢目标

单目标停止时，关闭它的媒体 socket、等待媒体线程退出并解除源订阅。
还有其他订阅者时源继续运行；最后一个订阅者离开时停止 FFmpeg 并释放源 API
会话。清理期间来了新订阅者，等待旧源清理完成后启动新源线程。

媒体队列满时，只丢弃该目标的积压帧，等待下一关键帧再恢复。编码器每秒产生
一次关键帧；请求 I 帧时也将该目标恢复点移到下一关键帧。
当前一个虚拟设备只保留一个活动媒体发送会话，新开始指令会替换旧会话；同一
平台多个观看者应由其平台服务共享该设备流。

## 模块边界

- 配置：`settings.py`。不要在日志或 `--check` 中输出配置对象的密钥。
- cam API：`cam_client.py`。源适配器变更从这里和 `live_source.py` 开始。
- 视频配置：`media_profile.py`。尺寸、帧率、系统头与私有描述必须相互一致。
- 设备控制：`device_session.py`、`isapi.py`。只响应已验证指令，未知指令不猜答复。
- 媒体编码：`isup_media.py`。不要把 WebRTC、MP4 或普通 RTP 直接送入 ISUP 端口。
- 协调：`runtime.py`。先中断所有控制连接，再等待各线程退出和释放源。

控制包限制为 4 MiB，Annex B 缓冲限制为 8 MiB，目标视频队列为 50 帧。
认证错误和媒体格式错误会记录并重试；当前媒体连接异常退出后需要平台重新
发起预览，不应在没有新指令时擅自复用旧 SessionID。

## 兼容性

`python -m isup_gateway` 是首选入口。安装后提供 `isup-gateway` 命令。
仓库中直接运行 `python isup_gateway/live_gateway.py` 仍支持原参数。
旧服务器使用平铺文件部署时，需要上传完整的 `isup_gateway/` 包并修改 systemd
为模块入口，不能只替换原来的一个脚本。

## 发布内容

仅发布网关 Python 包、自动测试、无密钥示例、部署模板和文档。SDK 库、
反汇编、抓包、日志、用户数据库、真实配置、视频和监控截图不属于源码发布内容。
本项目未指定开源许可证，默认保留版权；依赖及 SDK 的许可由各自权利人规定。
