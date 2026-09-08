# Docker 部署

## 0.1.0 经典版

0.1.0 只发布经典版镜像。它使用 `docker/Dockerfile`，提供经典 AirPlay、DLNA、同步播放、立体声组合和 Web 管理界面。

构建镜像：

```bash
docker build -f docker/Dockerfile -t micast:0.1.0 .
```

运行时必须使用 host 网络，以便 mDNS、SSDP 和 AirPlay 音频端口参与局域网发现：

```bash
docker run -d --name micast --restart unless-stopped \
  --network host \
  -e MICAST_DATA_DIR=/data \
  -v "$(pwd)/data:/data" \
  micast:0.1.0
```

`docker-compose.classic.yml` 只是经典版的本地辅助配置，不代表额外发行版本。

## AirPlay 2 单例版（实验性）

`docker-compose.single.yml` 使用一个固定的 AirPlay 2 接收器，不启动编排器，也不挂载 Docker Socket。控制器和接收器通过内部网络传输 PCM，接收器通过 macvlan 参与局域网发现。

启动前复制并填写配置：

```bash
cp docker/.env.example docker/.env
docker compose --env-file docker/.env -f docker/docker-compose.single.yml up -d --build
```

首次打开 Web 引导页后启用 AirPlay 2；此版本只保留一个 AirPlay 2 入口，不提供新增实例按钮。`MICAST_LAN_*` 参数必须与实际 NAS 网卡和局域网匹配。

## 实验性编排

`experimental/docker-compose.multi.yml` 保留多实例 AirPlay 2 编排方案。它需要 macvlan、宿主机 Docker Socket、独立的 LAN 地址段和人工网络配置，目前不属于 0.1.0 发布内容。

所有 macvlan 参数都必须在 `docker/.env` 中填写，不再提供假定的 `192.168.0.0/24` 默认值。地址段必须避开路由器 DHCP 池，并确认宿主机网卡名称正确。

单例版当前与 0.1.0 使用同一控制器镜像和接收器构建文件，Compose 负责拓扑差异；它仍是实验性版本，完成实机验证后再发布独立镜像标签。
