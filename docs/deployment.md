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
