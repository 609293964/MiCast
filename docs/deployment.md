# 部署说明

## Windows

Windows 单文件程序提供 Web 管理界面、经典 AirPlay 与 DLNA 接收。首次运行时允许 Windows 防火墙的专用网络访问，以便 mDNS、SSDP 和音频入口在局域网可见。

## 飞牛 fnOS 原生包

fnOS x86 原生包依赖应用中心的 Python 3.12，通过统一网关打开管理界面。经典 AirPlay 与 DLNA 可直接使用；单个 AirPlay 2 入口为实验性功能，默认关闭。

MiCast 的设备发现依赖局域网广播/组播，NAS 与手机、音箱应处于可互相访问的同一局域网，交换机或无线网络不能隔离 mDNS 与 SSDP。

## Docker

0.1.0 只提供经典版单容器镜像。容器需要 host 网络才能可靠参与 mDNS、SSDP 和 AirPlay 会话。

```bash
cd docker
docker compose -f docker-compose.classic.yml up -d --build
```

多实例 AirPlay 2 编排目前仅保留在 `docker/experimental/docker-compose.multi.yml`，不属于 0.1.0 发布内容。

AirPlay 2 单例编排见 `docker/docker-compose.single.yml`：它是实验性版本，只启动一个固定接收器，不使用 Docker Socket，也不提供动态新增实例。

## 数据、备份与迁移

MiCast 的持久化文件只有四个：`micast.json`（全部设置）、`access.json`（访问控制）、`xiaomi-account.json`（账号资料）和 `xiaomi-tokens.enc`（加密的小米登录令牌）。它们随存储模式放在不同位置：

| 存储模式 | 数据目录 |
| --- | --- |
| 便携版（`portable.flag` 或 `MICAST_PORTABLE=1`） | 可执行文件旁的 `data/` |
| 安装版（Windows / macOS / Linux） | `%APPDATA%\MiCast`、`~/Library/Application Support/MiCast`、`~/.local/share/micast` |
| fnOS 原生包 | fnOS 为该应用分配的持久化目录（`$TRIM_PKGVAR`，通常为 `/vol1/@appcenter/micast`） |
| Docker / 编排器 | `MICAST_DATA_DIR` 环境变量挂载的目录 |

备份与迁移时注意：

- **`micast.json`、`access.json`、`xiaomi-account.json` 是纯 JSON/文本**，可以直接复制到另一台机器或另一种存储模式的数据目录中完成迁移。
- **`xiaomi-tokens.enc` 的加密密钥派生自机器特征**（首选网卡 MAC 地址，Linux 上回退到 `/etc/machine-id`）。因此：
  - 同一台机器重装系统/重装应用，只要 MAC 地址与 machine-id 不变，旧备份中的 `xiaomi-tokens.enc` 一般仍可解密。
  - **换机、更换网卡或 machine-id 变化后，旧备份中的 `xiaomi-tokens.enc` 将无法解密**，此时界面会自动弹出恢复二维码，重新扫码登录即可生成新的令牌文件，无需重新配置其他设置。
  - 如果需要可跨机迁移的令牌文件，可以设置 `MICAST_ENCRYPTION_KEY` 环境变量固定加密密钥。
- **fnOS 卸载默认保留配置**：卸载向导中的数据策略默认为 `keep_config`（保留 `micast.json` 等配置文件），只有显式选择"全部删除"才会移除数据；重装应用后设置原样恢复。
