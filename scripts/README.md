# 脚本

从仓库根目录执行下列命令。Windows 脚本使用 PowerShell 7（`pwsh`）。

| 用途 | 入口 |
| --- | --- |
| Windows 源码启动 | `pwsh -File scripts/start-local.ps1`，或双击 `scripts/start-local.cmd` |
| Linux 源码启动 | `sh scripts/start-local.sh` |
| Windows 打包 | `pwsh -File scripts/build-windows.ps1` |
| Windows 构建 fnOS 包 | `pwsh -File scripts/build-fnos.ps1` |
| Linux 构建 fnOS 包 | `bash scripts/build-fnos.sh` |
| 测量音频流交付间隔 | `python scripts/measure_stream.py STREAM_URL` |
| Windows 防火墙配置 | 管理员终端运行 `pwsh -File scripts/configure-windows-firewall.ps1` |

源码启动会准备依赖并构建前端。防火墙工具默认绑定仓库虚拟环境的 Python，也可通过 `-Program` 指定 MiCast 可执行文件；只允许专用网络内本地子网的入站 TCP/UDP，不固定运行时端口。

`start.sh` 由 Docker 主镜像调用。`receiver-session-start.sh`、`receiver-session-stop.sh` 和 `receiver-volume.sh` 由 Docker AirPlay 2 接收器调用，用于会话和音量回传。
