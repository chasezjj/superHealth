"""Dashboard 系统配置页面。

集中管理 config.toml 与 Healthy 相关定时任务，保存后立刻生效。
"""

from __future__ import annotations

import hashlib
import os
import re
import signal
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import streamlit as st

from superhealth.config import (
    AdvisorConfig,
    AppConfig,
    BaichuanConfig,
    ClaudeConfig,
    DashboardConfig,
    GarminConfig,
    OutlookConfig,
    VitalsConfig,
    WeatherConfig,
    WechatConfig,
    hash_password,
    load,
    save_config,
)


def _derive_dashboard_password(new_pwd: str, stored: str) -> str:
    """根据用户输入决定最终存储的密码 hash。

    - 输入为空 → 清空密码
    - 输入与已存储的 hash 相同 → 保持原样（避免双 hash）
    - 输入为新密码 → 计算 hash
    """
    if not new_pwd:
        return ""
    from superhealth.config import verify_password

    if verify_password(new_pwd, stored):
        return stored  # 输入的是原密码，保持原 hash
    return hash_password(new_pwd)


# ---------------------------------------------------------------------------
# Crontab helpers
# ---------------------------------------------------------------------------


def _get_crontab() -> str:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode != 0:
        return ""
    return result.stdout


def _is_healthy_job(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False
    return "superhealth" in stripped


def _is_daily_pipeline_job(line: str) -> bool:
    return _is_healthy_job(line) and "daily_pipeline" in line


def _parse_cron_line(line: str) -> tuple[str, str, str, str, str, str] | None:
    parts = line.strip().split()
    if len(parts) < 6:
        return None
    return parts[0], parts[1], parts[2], parts[3], parts[4], " ".join(parts[5:])


def _sanitize_cron_command(cmd: str) -> str | None:
    """净化 crontab 命令，拒绝包含 shell 元字符的危险输入。"""
    bad_chars = {";", "|", "&", "<", ">", "`", "$", "\\", "(", ")", "{", "}", "[", "]"}
    if any(ch in cmd for ch in bad_chars):
        return None
    return cmd


_CRON_PATH_LINE = "PATH=/usr/local/bin:/usr/bin:/bin"


def _ensure_path_header(content: str) -> str:
    """确保 crontab 第一行是 PATH=… —— 如果已有 PATH 设置则保留，否则注入默认值。

    cron 默认 PATH 通常仅含 /usr/bin:/bin，导致 python3 / brew 装的可执行文件找不到；
    在文件开头声明 PATH 是最稳妥的办法。
    """
    lines = content.splitlines()
    # 找到首个非空、非注释行
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("PATH="):
            return content  # 已有 PATH，保持原样
        break
    # 没有 PATH 行 —— 在最前面插入
    new_content = _CRON_PATH_LINE + "\n" + content
    if not new_content.endswith("\n"):
        new_content += "\n"
    return new_content


def _save_crontab(content: str) -> None:
    content = _ensure_path_header(content)
    subprocess.run(["crontab", "-"], input=content, text=True, check=True)


# ---------------------------------------------------------------------------
# Cron 任务日志
# ---------------------------------------------------------------------------

_CRON_LOG_DIR = Path.home() / ".superhealth" / "logs" / "cron"
_REDIRECT_RE = re.compile(r"\s*>>\s*(\S+)\s+2>&1\s*$")


def _cron_log_path(clean_cmd: str) -> Path:
    digest = hashlib.sha1(clean_cmd.encode("utf-8")).hexdigest()[:12]
    return _CRON_LOG_DIR / f"{digest}.log"


def _split_cmd_redirect(cmd: str) -> tuple[str, Path | None]:
    """剥离命令尾部的 `>> <path> 2>&1` 后缀。返回 (干净命令, 日志路径或 None)。"""
    m = _REDIRECT_RE.search(cmd)
    if not m:
        return cmd, None
    return cmd[: m.start()].rstrip(), Path(m.group(1))


def _attach_log_redirect(clean_cmd: str) -> tuple[str, Path]:
    """给一行干净命令追加日志重定向后缀。返回 (完整命令, 日志路径)。"""
    log_path = _cron_log_path(clean_cmd)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return f"{clean_cmd} >> {log_path} 2>&1", log_path


def _line_with_log_redirect(sanitized_line: str) -> str:
    """对一整行 cron 配置（已 sanitize）的命令部分追加日志重定向。"""
    parts = sanitized_line.split(maxsplit=5)
    if len(parts) < 6:
        return sanitized_line
    time_prefix = " ".join(parts[:5])
    full_cmd, _ = _attach_log_redirect(parts[5])
    return f"{time_prefix} {full_cmd}"


def _read_log_tail(path: Path, lines: int = 200) -> str:
    """读取日志末尾若干行；不存在则返回空字符串。"""
    if not path.exists():
        return ""
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            block = 64 * 1024
            data = b""
            while size > 0 and data.count(b"\n") <= lines:
                read_size = min(block, size)
                size -= read_size
                f.seek(size)
                data = f.read(read_size) + data
        text = data.decode("utf-8", errors="replace")
        return "\n".join(text.splitlines()[-lines:])
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# vitals_receiver 进程管理
# ---------------------------------------------------------------------------

_VITALS_PID_FILE = Path.home() / ".superhealth" / "vitals_receiver.pid"


def _vitals_pid() -> int | None:
    """返回正在运行的 vitals_receiver 进程 PID，不存在或已退出则返回 None。"""
    if not _VITALS_PID_FILE.exists():
        return None
    try:
        pid = int(_VITALS_PID_FILE.read_text().strip())
        os.kill(pid, 0)  # 发送 signal 0 仅用于检测进程是否存在
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        _VITALS_PID_FILE.unlink(missing_ok=True)
        return None


def _start_vitals_receiver() -> tuple[bool, str]:
    """后台启动 vitals_receiver，返回 (成功, 消息)。"""
    if _vitals_pid() is not None:
        return False, "服务已在运行中"
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "superhealth.api.vitals_receiver"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _VITALS_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        _VITALS_PID_FILE.write_text(str(proc.pid))
        return True, f"服务已启动（PID {proc.pid}）"
    except Exception as e:
        return False, f"启动失败: {e}"


def _stop_vitals_receiver() -> tuple[bool, str]:
    """停止 vitals_receiver 进程，返回 (成功, 消息)。"""
    pid = _vitals_pid()
    if pid is None:
        return False, "服务未运行"
    try:
        os.kill(pid, signal.SIGTERM)
        _VITALS_PID_FILE.unlink(missing_ok=True)
        return True, f"已发送停止信号（PID {pid}）"
    except Exception as e:
        return False, f"停止失败: {e}"


# ---------------------------------------------------------------------------
# Page render
# ---------------------------------------------------------------------------


def render() -> None:
    st.title("系统配置")

    config = load()

    # ========================================================================
    # A. config.toml
    # ========================================================================
    st.header("应用配置 (~/.superhealth/config.toml)")

    # Garmin
    with st.expander("Garmin 账号", expanded=True):
        c1, c2 = st.columns(2)
        garmin_email = c1.text_input(
            "邮箱/手机号", value=config.garmin.email, key="cfg_garmin_email"
        )
        garmin_password = c2.text_input(
            "密码",
            value=config.garmin.password,
            type="password",
            key="cfg_garmin_pwd",
        )
        if st.button("测试 Garmin 连接", key="btn_test_garmin"):
            from superhealth.collectors.fetch_garmin import login_with_credentials, test_connection

            with st.spinner("正在验证 session..."):
                ok, msg = test_connection()

            if ok:
                st.success(msg)
            elif garmin_email and garmin_password:
                with st.spinner("Session 无效，正在用填入的账号重新登录（约 30 秒）..."):
                    try:
                        login_with_credentials(garmin_email, garmin_password)
                        ok2, msg2 = test_connection()
                        if ok2:
                            st.success(f"登录成功！{msg2}")
                        else:
                            st.error(f"登录后验证失败: {msg2}")
                    except Exception as e:
                        st.error(f"登录失败，请检查账号密码是否正确: {e}")
            else:
                st.error("账号或密码未填写，请先填入 Garmin 凭据再测试")

    # Garmin 历史数据同步
    with st.expander("Garmin 历史数据同步"):
        c1, c2 = st.columns(2)
        sync_start = c1.date_input(
            "开始日期",
            value=date.today() - timedelta(days=90),
            key="sync_start",
        )
        sync_end = c2.date_input(
            "结束日期",
            value=date.today() - timedelta(days=1),
            key="sync_end",
        )
        skip_existing = st.checkbox("跳过已有记录（增量拉取）", value=True, key="sync_skip")

        if sync_start <= sync_end:
            total_days = (sync_end - sync_start).days + 1
            try:
                from superhealth import database as db
                from superhealth.collectors.fetch_garmin import BASE_DIR

                db_path = BASE_DIR / "health.db"
                db.init_db(db_path)
                with db.get_conn(db_path) as _conn:
                    existing_count = _conn.execute(
                        "SELECT COUNT(*) FROM daily_health WHERE date BETWEEN ? AND ?",
                        (sync_start.isoformat(), sync_end.isoformat()),
                    ).fetchone()[0]
                to_fetch = total_days - existing_count if skip_existing else total_days
                st.caption(
                    f"共 {total_days} 天，数据库已有 {existing_count} 天，将拉取约 {to_fetch} 天"
                )
            except Exception:
                st.caption(f"共 {total_days} 天")
        else:
            st.warning("开始日期不能晚于结束日期")

        if st.button("开始拉取", key="btn_sync_garmin"):
            if sync_start > sync_end:
                st.error("日期范围无效")
            else:
                from superhealth import database as db
                from superhealth.collectors.fetch_garmin import (
                    BASE_DIR,
                    GarminAuthError,
                    _load_session,
                    save_day,
                )

                try:
                    _session, _user_id = _load_session()
                except GarminAuthError as e:
                    st.error(f"Session 无效，请先在上方【Garmin 账号】区域点【测试 Garmin 连接】完成登录: {e}")
                else:
                    days = [
                        sync_start + timedelta(days=i)
                        for i in range((sync_end - sync_start).days + 1)
                    ]
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    successes: list[str] = []
                    skipped: list[str] = []
                    fetch_errors: list[tuple[str, str]] = []

                    _db_path = BASE_DIR / "health.db"
                    db.init_db(_db_path)
                    with db.get_conn(_db_path) as _conn:
                        for i, _day in enumerate(days):
                            _day_str = _day.isoformat()
                            status_text.text(f"正在拉取 {_day_str}... ({i + 1}/{len(days)})")
                            _existed = _conn.execute(
                                "SELECT 1 FROM daily_health WHERE date = ? LIMIT 1",
                                (_day_str,),
                            ).fetchone()
                            try:
                                save_day(
                                    _session,
                                    _user_id,
                                    _day,
                                    retry_empty=False,
                                    skip_existing=skip_existing,
                                )
                                if _existed and skip_existing:
                                    skipped.append(_day_str)
                                else:
                                    successes.append(_day_str)
                            except Exception as _e:
                                fetch_errors.append((_day_str, str(_e)))
                            progress_bar.progress((i + 1) / len(days))

                    status_text.empty()
                    st.success(
                        f"完成：新增 {len(successes)} 天，跳过 {len(skipped)} 天，"
                        f"失败 {len(fetch_errors)} 天"
                    )
                    if fetch_errors:
                        with st.expander("失败详情"):
                            for _d, _err in fetch_errors:
                                st.text(f"{_d}: {_err}")

    # WeChat
    with st.expander("微信推送"):
        st.caption(
            "依赖部署机器上安装的 [OpenClaw](https://openclaw.io)，通过 `openclaw-weixin` 渠道将高级健康日报推送到微信。"
            "配置方式：登录 OpenClaw 控制台 → 我的账号，获取 Account ID；"
            "渠道固定填 `openclaw-weixin`；"
            "Target 填接收消息的微信用户 open_id（在 OpenClaw 控制台 → 渠道 → 微信 → 用户列表中查看）。"
        )
        c1, c2, c3 = st.columns(3)
        wechat_account = c1.text_input(
            "Account ID",
            value=config.wechat.account_id,
            key="cfg_wx_acc",
            help="OpenClaw 控制台 → 我的账号 → Account ID",
        )
        wechat_channel = c2.text_input(
            "Channel",
            value=config.wechat.channel,
            key="cfg_wx_ch",
            placeholder="openclaw-weixin",
            help="固定填 openclaw-weixin",
        )
        wechat_target = c3.text_input(
            "Target",
            value=config.wechat.target,
            key="cfg_wx_tgt",
            help="接收消息的微信用户 open_id，在 OpenClaw 控制台 → 渠道 → 微信 → 用户列表中查看",
        )

    # Vitals
    with st.expander("Health Auto Export"):
        st.markdown(
            """
**Health Auto Export** 是一款 iOS 应用，可将苹果「健康」App 中的数据**定时自动上传**到你的服务器。
配置好后，手机上测量的**血压、体重、体脂率**会实时同步到 superHealth 数据库，无需手动录入。

**工作原理：**
1. 手机安装 Health Auto Export 并授权读取「健康」数据
2. 在 App 内配置 REST API 地址，填入下方的 Host、Port 和 API Token
3. superHealth 在本机启动一个接收服务（`vitals_receiver`），监听手机推来的数据

**字段说明：**
- **API Token** — 鉴权密钥，在 Health Auto Export App 中填入相同的值（HTTP Header: `X-API-Key`）。随机生成一个长字符串即可，防止陌生人向服务器写入数据。
- **Host** — 监听的网络地址。`0.0.0.0` 表示接受来自任何来源的请求（手机和服务器不在同一局域网时需要此设置）；`127.0.0.1` 仅接受本机请求。
- **Port** — 监听端口，默认 `5000`。手机 App 中填入的 URL 格式为 `http://<服务器IP>:<Port>/health_data`。
"""
        )
        c1, c2, c3 = st.columns(3)
        vitals_token = c1.text_input(
            "API Token",
            value=config.vitals.api_token,
            type="password",
            key="cfg_vitals_token",
            help="鉴权密钥，在 Health Auto Export App 的 HTTP Header 中填入相同的值",
        )
        vitals_host = c2.text_input(
            "Host",
            value=config.vitals.host,
            key="cfg_vitals_host",
            help="监听地址：0.0.0.0 接受所有来源，127.0.0.1 仅本机",
        )
        vitals_port = c3.number_input(
            "Port",
            value=config.vitals.port,
            min_value=1,
            max_value=65535,
            key="cfg_vitals_port",
            help="监听端口，手机 App URL 格式：http://<服务器IP>:<Port>/health_data",
        )

        st.divider()
        pid = _vitals_pid()
        if pid:
            st.success(f"vitals_receiver 服务运行中（PID {pid}）")
            if st.button("停止接收服务", key="btn_stop_vitals"):
                ok, msg = _stop_vitals_receiver()
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)
                st.rerun()
        else:
            st.warning("vitals_receiver 服务未运行，手机数据无法上传")
            if st.button("启动接收服务", key="btn_start_vitals"):
                ok, msg = _start_vitals_receiver()
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)
                st.rerun()

    # Claude
    with st.expander("AI 建议引擎（Anthropic 协议）"):
        st.caption(
            "用于生成每日健康建议的大模型接口，兼容所有支持 Anthropic 协议的服务，"
            "包括官方 Claude（api.anthropic.com）以及第三方兼容代理。"
            "API Key 填服务商提供的密钥；"
            "Model 填模型名称，如 claude-sonnet-4-6；"
            "Max Tokens 控制单次回复最大长度，通常 4096 即可；"
            "Base URL 仅在使用代理或私有部署时填写，留空则连接官方地址。"
        )
        c1, c2 = st.columns(2)
        claude_key = c1.text_input(
            "API Key",
            value=config.claude.api_key,
            type="password",
            key="cfg_claude_key",
            help="服务商提供的 API 密钥",
        )
        claude_model = c2.text_input(
            "Model",
            value=config.claude.model,
            key="cfg_claude_model",
            help="模型名称，如 claude-sonnet-4-6",
        )
        c3, c4 = st.columns(2)
        claude_max_tokens = c3.number_input(
            "Max Tokens",
            value=config.claude.max_tokens,
            min_value=1,
            key="cfg_claude_mt",
            help="单次回复最大 token 数，通常 4096 即可",
        )
        claude_base_url = c4.text_input(
            "Base URL（可选）",
            value=config.claude.base_url,
            key="cfg_claude_url",
            help="使用代理或私有部署时填写，留空连接官方地址",
        )
        if st.button("测试 Anthropic 连接", key="btn_test_claude"):
            if not claude_key or not claude_model:
                st.error("请先填写 API Key 和 Model")
            else:
                with st.spinner("正在连接..."):
                    try:
                        import anthropic
                        kwargs: dict = {"api_key": claude_key}
                        if claude_base_url:
                            kwargs["base_url"] = claude_base_url
                        _client = anthropic.Anthropic(**kwargs)
                        _client.messages.create(
                            model=claude_model,
                            max_tokens=16,
                            messages=[{"role": "user", "content": "hi"}],
                        )
                        st.success(f"连接成功（model: {claude_model}）")
                    except ImportError:
                        st.error("缺少 anthropic SDK，请运行：pip install anthropic")
                    except Exception as e:
                        st.error(f"连接失败：{e}")

    # Baichuan
    with st.expander("百川 API"):
        st.caption(
            "百川大模型接口，作为 AI 健康建议的备用或并行引擎（国内访问更稳定）。"
            "百川作为医疗级大模型会给予很专业的医疗建议。"
            "API Key 填百川开放平台提供的密钥；"
            "Model 填模型名称，如 Baichuan4-Turbo；"
            "Max Tokens 控制单次回复最大长度；"
            "Base URL 默认连接百川官方地址，使用代理时可覆盖。"
        )
        c1, c2 = st.columns(2)
        bc_key = c1.text_input(
            "API Key",
            value=config.baichuan.api_key,
            type="password",
            key="cfg_bc_key",
            help="百川开放平台提供的 API 密钥",
        )
        bc_model = c2.text_input(
            "Model",
            value=config.baichuan.model,
            key="cfg_bc_model",
            help="模型名称，如 Baichuan4-Turbo",
        )
        c3, c4 = st.columns(2)
        bc_max_tokens = c3.number_input(
            "Max Tokens",
            value=config.baichuan.max_tokens,
            min_value=1,
            key="cfg_bc_mt",
            help="单次回复最大 token 数",
        )
        bc_base_url = c4.text_input(
            "Base URL",
            value=config.baichuan.base_url,
            key="cfg_bc_url",
            help="默认连接百川官方地址，使用代理时可覆盖",
        )
        if st.button("测试百川连接", key="btn_test_baichuan"):
            if not bc_key or not bc_model:
                st.error("请先填写 API Key 和 Model")
            else:
                with st.spinner("正在连接..."):
                    try:
                        from openai import OpenAI
                        _bc_client = OpenAI(api_key=bc_key, base_url=bc_base_url)
                        _bc_client.chat.completions.create(
                            model=bc_model,
                            max_tokens=16,
                            messages=[{"role": "user", "content": "hi"}],
                        )
                        st.success(f"连接成功（model: {bc_model}）")
                    except ImportError:
                        st.error("缺少 openai SDK，请运行：pip install openai")
                    except Exception as e:
                        st.error(f"连接失败：{e}")

    # Advisor
    with st.expander("建议引擎"):
        st.caption(
            "控制每日健康建议由哪个大模型生成。"
            "claude_only：仅使用上方配置的 Anthropic 协议模型；"
            "baichuan_only：仅使用百川模型；"
            "both：两个模型各生成一份建议并合并展示。"
        )
        advisor_mode = st.selectbox(
            "模式",
            options=["claude_only", "baichuan_only", "both"],
            index=["claude_only", "baichuan_only", "both"].index(config.advisor.mode),
            key="cfg_advisor_mode",
            help="claude_only / baichuan_only / both",
        )

    # Weather
    with st.expander("天气"):
        st.caption(
            "用于在健康日报中附加当日天气信息，数据来自和风天气（qweather.com）。"
            "每天生成的健康日报会根据天气情况决定进行户外和室内运动的推荐。"
            "API Key 填和风天气开放平台的密钥；"
            "城市填中文城市名（仅用于展示）；"
            "Location ID 填和风天气的地点 ID（城市搜索页可查，精度更高）；"
            "纬度/经度用于获取逐小时预报，填所在地坐标；"
            "API Host 仅在使用镜像或代理时填写，留空连接官方地址。"
        )
        c1, c2, c3 = st.columns(3)
        weather_key = c1.text_input(
            "API Key",
            value=config.weather.api_key,
            type="password",
            key="cfg_wx_key",
            help="和风天气开放平台密钥",
        )
        weather_city = c2.text_input(
            "城市",
            value=config.weather.city,
            key="cfg_wx_city",
            help="中文城市名，仅用于展示",
        )
        weather_loc = c3.text_input(
            "Location ID",
            value=config.weather.location_id,
            key="cfg_wx_loc",
            help="和风天气地点 ID，在城市搜索页查询",
        )
        c4, c5, c6 = st.columns(3)
        weather_host = c4.text_input(
            "API Host（可选）",
            value=config.weather.api_host,
            key="cfg_wx_host",
            help="使用镜像或代理时填写，留空连接官方地址",
        )
        weather_lat = c5.number_input(
            "纬度",
            value=float(config.weather.latitude),
            format="%.2f",
            key="cfg_wx_lat",
            help="所在地纬度，用于逐小时预报",
        )
        weather_lon = c6.number_input(
            "经度",
            value=float(config.weather.longitude),
            format="%.2f",
            key="cfg_wx_lon",
            help="所在地经度，用于逐小时预报",
        )
        if st.button("测试天气连接", key="btn_test_weather"):
            from superhealth.collectors.weather_collector import test_connection as test_weather

            with st.spinner("正在连接..."):
                ok, msg = test_weather()
            if ok:
                st.success(msg)
            else:
                st.error(msg)

    # Dashboard
    with st.expander("仪表盘"):
        st.caption(
            "设置 dashboard 的访问密码。启用后每次打开页面需要输入密码才能查看数据，"
            "适合将服务暴露在公网时使用。留空则不设密码，任何人均可访问。"
        )
        # 不预填充已有密码（避免将 hash 误当作新密码重新 hash）
        dashboard_pwd = st.text_input(
            "访问密码（留空表示不设密码）",
            value="",
            type="password",
            key="cfg_db_pwd",
            help="保存后生效；再次打开此页面密码框为空属正常现象，不会清除已设密码",
        )

    # Outlook
    with st.expander("Outlook / Exchange"):
        st.caption(
            "连接公司或个人的 Outlook / Exchange 邮箱，根据用户的忙闲安排合适时间的运动；"
            "运动的恢复情况也会参考日程的忙闲，某些时候恢复不好可能是日程太过紧张，"
            "系统会将其标记为污染日。"
            "用户名填登录名（通常为邮箱前缀或域账号）；"
            "邮箱填完整邮件地址；"
            "密码填邮箱登录密码或应用专用密码；"
            "时区填 IANA 时区名称，如 Asia/Shanghai。"
        )
        c1, c2 = st.columns(2)
        outlook_user = c1.text_input(
            "用户名",
            value=config.outlook.username,
            key="cfg_ol_user",
            help="登录名，通常为邮箱前缀或域账号",
        )
        outlook_email = c2.text_input(
            "邮箱",
            value=config.outlook.email,
            key="cfg_ol_email",
            help="完整邮件地址",
        )
        c3, c4 = st.columns(2)
        outlook_pwd = c3.text_input(
            "密码",
            value=config.outlook.password,
            type="password",
            key="cfg_ol_pwd",
            help="邮箱登录密码或应用专用密码",
        )
        outlook_tz = c4.text_input(
            "时区",
            value=config.outlook.timezone,
            key="cfg_ol_tz",
            help="IANA 时区名称，如 Asia/Shanghai",
        )
        if st.button("测试 Exchange 连接", key="btn_test_outlook"):
            from superhealth.collectors.outlook_collector import test_connection as test_outlook

            with st.spinner("正在连接..."):
                ok, msg = test_outlook()
            if ok:
                st.success(msg)
            else:
                st.error(msg)

    # ========================================================================
    # B. Crontab — only Healthy jobs
    # ========================================================================
    st.header("定时任务 (crontab)")

    crontab_raw = _get_crontab()
    crontab_lines = crontab_raw.splitlines()

    # ---- daily_pipeline 核心任务 ----
    _DAILY_PIPELINE_CMD = "python3 -m superhealth.daily_pipeline"

    st.markdown(
        """
**`superhealth.daily_pipeline`** 是 superHealth 的核心定时任务，每天自动按顺序完成以下工作：

1. **Garmin 数据同步** — 拉取昨日与今日的运动、睡眠、心率、压力等数据，失败时自动重试；同时补拉历史上未成功同步的日期
2. **日历数据同步** — 拉取 Outlook / Exchange 日程，用于分析忙碌程度对恢复的影响
3. **生成高级健康日报** — 基于多维度健康数据，由 AI 模型（Claude / 百川）生成个性化分析与运动建议
4. **推送微信通知** — 将日报通过 OpenClaw 发送到微信，方便随时查阅
5. **自动反馈与效果追踪** — 对比历史建议与实际运动数据，评估执行效果，为策略学习提供依据
6. **策略学习** — 根据长期效果数据调整建议策略参数，使建议越来越贴合个人状态
7. **预约提醒** — 检查即将到来的就医、检查等日程，提前发送提醒通知

完整日志保存在 `~/.superhealth/logs/cron/` 目录下。
"""
    )

    dp_line = next((line for line in crontab_lines if _is_daily_pipeline_job(line)), None)
    if dp_line:
        dp_parsed = _parse_cron_line(dp_line)
        if dp_parsed:
            dp_m_init, dp_h_init = dp_parsed[0], dp_parsed[1]
        else:
            dp_m_init, dp_h_init = "0", "7"
        st.success("状态：已启用")
    else:
        dp_m_init, dp_h_init = "0", "7"
        st.warning("状态：未启用 — 设置好时间后点击「保存定时计划」即可启用")

    col_m, col_h, col_label = st.columns([1, 1, 6])
    dp_new_m = col_m.text_input("分钟", value=dp_m_init, key="dp_cron_m", help="0–59，默认 0")
    dp_new_h = col_h.text_input("小时", value=dp_h_init, key="dp_cron_h", help="0–23，默认 7 表示早上 7 点")
    col_label.caption(
        f"命令（固定）：`{_DAILY_PIPELINE_CMD}`  ·  执行周期固定为每天（`* * *`），仅时间可调整"
    )

    if st.button("保存定时计划", key="btn_dp_save"):
        new_dp_cron = f"{dp_new_m} {dp_new_h} * * * {_DAILY_PIPELINE_CMD}"
        sanitized_dp = _sanitize_cron_command(new_dp_cron)
        if sanitized_dp is None:
            st.error("时间字段包含非法字符，请检查后重试")
        else:
            new_dp_line = _line_with_log_redirect(sanitized_dp)
            current_lines = _get_crontab().splitlines()
            updated: list[str] = []
            replaced = False
            for _l in current_lines:
                if _is_daily_pipeline_job(_l):
                    updated.append(new_dp_line)
                    replaced = True
                else:
                    updated.append(_l)
            if not replaced:
                updated.append(new_dp_line)
            _save_crontab("\n".join(updated) + "\n")
            st.success("定时计划已保存")
            st.rerun()

    # -- 其他 superhealth 定时任务（不含 daily_pipeline）--
    edited_jobs_map: dict[int, str] = {}
    job_had_redirect: dict[int, bool] = {}
    job_idx = 0
    for line in crontab_lines:
        if _is_daily_pipeline_job(line):
            continue
        if not _is_healthy_job(line):
            continue
        parsed = _parse_cron_line(line)
        if parsed is None:
            st.warning(f"无法解析的定时任务: `{line}`")
            edited_jobs_map[job_idx] = line
            job_had_redirect[job_idx] = False
            job_idx += 1
            continue

        m, h, dom, mon, dow, cmd = parsed
        clean_cmd, log_path = _split_cmd_redirect(cmd)
        had_redirect = log_path is not None
        job_had_redirect[job_idx] = had_redirect

        cols = st.columns([1.5, 1.5, 1.5, 1.5, 1.5, 6, 1])
        nm = cols[0].text_input("分", value=m, key=f"cron_m_{job_idx}")
        nh = cols[1].text_input("时", value=h, key=f"cron_h_{job_idx}")
        ndom = cols[2].text_input("日", value=dom, key=f"cron_dom_{job_idx}")
        nmon = cols[3].text_input("月", value=mon, key=f"cron_mon_{job_idx}")
        ndow = cols[4].text_input("周", value=dow, key=f"cron_dow_{job_idx}")
        ncmd = cols[5].text_input("命令", value=clean_cmd, key=f"cron_cmd_{job_idx}")
        edited_jobs_map[job_idx] = f"{nm} {nh} {ndom} {nmon} {ndow} {ncmd}"
        if cols[6].button("删除", key=f"cron_del_btn_{job_idx}"):
            remaining = list(crontab_lines)
            try:
                remaining.remove(line)
            except ValueError:
                pass
            _save_crontab(("\n".join(remaining) + "\n") if remaining else "")
            st.success("已删除定时任务")
            st.rerun()

        job_idx += 1

    # ========================================================================
    # Save
    # ========================================================================
    st.divider()
    if st.button("保存配置", type="primary", key="btn_save_cfg"):
        new_config = AppConfig(
            garmin=GarminConfig(email=garmin_email, password=garmin_password),
            wechat=WechatConfig(
                account_id=wechat_account,
                channel=wechat_channel,
                target=wechat_target,
            ),
            vitals=VitalsConfig(
                api_token=vitals_token,
                host=vitals_host,
                port=int(vitals_port),
            ),
            claude=ClaudeConfig(
                api_key=claude_key,
                model=claude_model,
                max_tokens=int(claude_max_tokens),
                base_url=claude_base_url,
            ),
            baichuan=BaichuanConfig(
                api_key=bc_key,
                model=bc_model,
                max_tokens=int(bc_max_tokens),
                base_url=bc_base_url,
            ),
            advisor=AdvisorConfig(mode=advisor_mode),
            weather=WeatherConfig(
                api_key=weather_key,
                city=weather_city,
                location_id=weather_loc,
                api_host=weather_host,
                latitude=weather_lat,
                longitude=weather_lon,
            ),
            dashboard=DashboardConfig(
                password=_derive_dashboard_password(dashboard_pwd, config.dashboard.password),
            ),
            outlook=OutlookConfig(
                username=outlook_user,
                email=outlook_email,
                password=outlook_pwd,
                timezone=outlook_tz,
            ),
        )
        # preserve hidden fields
        new_config.dashboard.session_token = config.dashboard.session_token
        new_config.dashboard.saved_password = config.dashboard.saved_password

        save_config(new_config)

        # rebuild crontab：daily_pipeline 由专属区域独立管理，原样保留
        new_lines: list[str] = []
        job_idx = 0
        for line in crontab_lines:
            if _is_daily_pipeline_job(line):
                new_lines.append(line)
            elif _is_healthy_job(line):
                if job_idx in edited_jobs_map:
                    raw = edited_jobs_map[job_idx]
                    sanitized = _sanitize_cron_command(raw)
                    if sanitized is None:
                        st.error(f"定时任务包含危险字符，已跳过: {raw}")
                        job_idx += 1
                        continue
                    if job_had_redirect.get(job_idx):
                        new_lines.append(_line_with_log_redirect(sanitized))
                    else:
                        new_lines.append(sanitized)
                job_idx += 1
            else:
                new_lines.append(line)

        _save_crontab("\n".join(new_lines) + "\n")

        st.success("配置已保存并生效")
        st.rerun()
