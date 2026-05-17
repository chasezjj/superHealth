# SuperHealth service management

SuperHealth 提供跨平台服务托管入口。dashboard 系统配置页不会暴露独立的“系统服务托管”区域，而是在具体业务按钮里调用这些脚本：

- Health Auto Export 的“启动接收服务”会托管 `vitals_receiver`。
- daily_pipeline 的“保存定时计划”会托管每日定时任务。

## 跨平台脚本

```bash
bash scripts/install_services.sh
bash scripts/services_status.sh
bash scripts/uninstall_services.sh
```

单服务入口：

```bash
bash scripts/manage_service.sh start vitals_receiver
bash scripts/manage_service.sh stop vitals_receiver
bash scripts/manage_service.sh schedule daily_pipeline 7 5
bash scripts/manage_service.sh status daily_pipeline
```

脚本会按操作系统自动分发：

- macOS：调用 `launchd` 脚本，安装用户级 `LaunchAgent`。
- Linux：调用 `systemd` 脚本，安装 system service 和 timer。

## 托管的服务

- `vitals_receiver`：常驻服务，异常退出自动重启。
- `superhealth.daily_pipeline`：每日定时任务。

## 注意

安装托管服务前，请先停止旧的 `nohup` 进程、Docker 容器或重复的 cron 任务，避免端口冲突或 daily_pipeline 重复运行。

如果当前 dashboard 是手工启动的，点击“安装/启动三项服务”可能会因为 dashboard 端口已被当前进程占用而失败。此时可在终端停掉手工进程后运行：

```bash
bash scripts/install_services.sh
```
