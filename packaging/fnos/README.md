# MiCast fnOS 原生包

## 应用结构

- 架构：x86
- 依赖：应用中心 `python312`
- 公开入口：`/app/micast/`
- 网关 Socket：`${TRIM_APPDEST}/app.sock`
- 持久数据：`${TRIM_PKGVAR}`
- 运行身份：`micast`
- AirPlay 2：单入口、默认关闭

应用不监听固定的管理端口，因此 manifest 使用 `checkport=false`，由 fnOS 统一网关转发到 Unix Socket。安装和配置不使用 fnOS 表单向导；首次运行所需的管理访问、米家登录、音箱选择及 AirPlay 2 开关均由应用内引导完成。

## 生命周期

- 安装时检查 Python 3.12 与 AirPlay 2 运行时能力。
- 启动后通过 Unix Socket 健康检查确认服务就绪。
- 停止和卸载时清理 MiCast 私有的 AirPlay 2 子进程及 Socket。
- 完整卸载会删除配置和凭据，重新安装后重新初始化。
- 日志达到 5 MiB 时保留一份轮转日志。

## 构建

```powershell
pwsh -NoProfile -File scripts/build-fnos.ps1
```

Linux x86 可运行 `bash scripts/build-fnos.sh`。产物输出到 `dist/fnos/`。
