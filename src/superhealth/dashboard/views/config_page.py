"""Dashboard 系统配置页面。

集中管理 config.toml 与 Healthy 相关定时任务，保存后立刻生效。
"""

from __future__ import annotations

import subprocess

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


def _save_crontab(content: str) -> None:
    subprocess.run(["crontab", "-"], input=content, text=True, check=True)


# ---------------------------------------------------------------------------
# Page render
# ---------------------------------------------------------------------------


def render() -> None:
    st.title("系统配置")

    config = load()

    # ========================================================================
    # A. config.toml
    # ========================================================================
    st.header("应用配置 (config.toml)")

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

    # WeChat
    with st.expander("微信推送"):
        c1, c2, c3 = st.columns(3)
        wechat_account = c1.text_input(
            "Account ID", value=config.wechat.account_id, key="cfg_wx_acc"
        )
        wechat_channel = c2.text_input("Channel", value=config.wechat.channel, key="cfg_wx_ch")
        wechat_target = c3.text_input("Target", value=config.wechat.target, key="cfg_wx_tgt")

    # Vitals
    with st.expander("Health Auto Export"):
        c1, c2, c3 = st.columns(3)
        vitals_token = c1.text_input(
            "API Token",
            value=config.vitals.api_token,
            type="password",
            key="cfg_vitals_token",
        )
        vitals_host = c2.text_input("Host", value=config.vitals.host, key="cfg_vitals_host")
        vitals_port = c3.number_input(
            "Port",
            value=config.vitals.port,
            min_value=1,
            max_value=65535,
            key="cfg_vitals_port",
        )

    # Claude
    with st.expander("Claude API"):
        c1, c2 = st.columns(2)
        claude_key = c1.text_input(
            "API Key",
            value=config.claude.api_key,
            type="password",
            key="cfg_claude_key",
        )
        claude_model = c2.text_input("Model", value=config.claude.model, key="cfg_claude_model")
        c3, c4 = st.columns(2)
        claude_max_tokens = c3.number_input(
            "Max Tokens",
            value=config.claude.max_tokens,
            min_value=1,
            key="cfg_claude_mt",
        )
        claude_base_url = c4.text_input(
            "Base URL (可选)",
            value=config.claude.base_url,
            key="cfg_claude_url",
        )

    # Baichuan
    with st.expander("百川 API"):
        c1, c2 = st.columns(2)
        bc_key = c1.text_input(
            "API Key",
            value=config.baichuan.api_key,
            type="password",
            key="cfg_bc_key",
        )
        bc_model = c2.text_input("Model", value=config.baichuan.model, key="cfg_bc_model")
        c3, c4 = st.columns(2)
        bc_max_tokens = c3.number_input(
            "Max Tokens",
            value=config.baichuan.max_tokens,
            min_value=1,
            key="cfg_bc_mt",
        )
        bc_base_url = c4.text_input("Base URL", value=config.baichuan.base_url, key="cfg_bc_url")

    # Advisor
    with st.expander("建议引擎"):
        advisor_mode = st.selectbox(
            "模式",
            options=["claude_only", "baichuan_only", "both"],
            index=["claude_only", "baichuan_only", "both"].index(config.advisor.mode),
            key="cfg_advisor_mode",
        )

    # Weather
    with st.expander("天气"):
        c1, c2, c3 = st.columns(3)
        weather_key = c1.text_input(
            "API Key",
            value=config.weather.api_key,
            type="password",
            key="cfg_wx_key",
        )
        weather_city = c2.text_input("城市", value=config.weather.city, key="cfg_wx_city")
        weather_loc = c3.text_input(
            "Location ID",
            value=config.weather.location_id,
            key="cfg_wx_loc",
        )
        c4, c5, c6 = st.columns(3)
        weather_host = c4.text_input(
            "API Host (可选)",
            value=config.weather.api_host,
            key="cfg_wx_host",
        )
        weather_lat = c5.number_input(
            "纬度",
            value=float(config.weather.latitude),
            format="%.2f",
            key="cfg_wx_lat",
        )
        weather_lon = c6.number_input(
            "经度",
            value=float(config.weather.longitude),
            format="%.2f",
            key="cfg_wx_lon",
        )

    # Dashboard
    with st.expander("仪表盘"):
        # 不预填充已有密码（避免将 hash 误当作新密码重新 hash）
        dashboard_pwd = st.text_input(
            "访问密码（留空表示不设密码）",
            value="",
            type="password",
            key="cfg_db_pwd",
        )

    # Outlook
    with st.expander("Outlook / Exchange"):
        c1, c2 = st.columns(2)
        outlook_user = c1.text_input("用户名", value=config.outlook.username, key="cfg_ol_user")
        outlook_email = c2.text_input("邮箱", value=config.outlook.email, key="cfg_ol_email")
        c3, c4 = st.columns(2)
        outlook_pwd = c3.text_input(
            "密码",
            value=config.outlook.password,
            type="password",
            key="cfg_ol_pwd",
        )
        outlook_tz = c4.text_input("时区", value=config.outlook.timezone, key="cfg_ol_tz")

    # ========================================================================
    # B. Crontab — only Healthy jobs
    # ========================================================================
    st.header("定时任务 (crontab)")

    crontab_raw = _get_crontab()
    crontab_lines = crontab_raw.splitlines()

    # -- existing Healthy jobs --
    edited_jobs_map: dict[int, str] = {}
    job_idx = 0
    for line in crontab_lines:
        if not _is_healthy_job(line):
            continue
        parsed = _parse_cron_line(line)
        if parsed is None:
            st.warning(f"无法解析的定时任务: `{line}`")
            edited_jobs_map[job_idx] = line
            job_idx += 1
            continue

        m, h, dom, mon, dow, cmd = parsed
        cols = st.columns([1.5, 1.5, 1.5, 1.5, 1.5, 6, 1])
        nm = cols[0].text_input("分", value=m, key=f"cron_m_{job_idx}")
        nh = cols[1].text_input("时", value=h, key=f"cron_h_{job_idx}")
        ndom = cols[2].text_input("日", value=dom, key=f"cron_dom_{job_idx}")
        nmon = cols[3].text_input("月", value=mon, key=f"cron_mon_{job_idx}")
        ndow = cols[4].text_input("周", value=dow, key=f"cron_dow_{job_idx}")
        ncmd = cols[5].text_input("命令", value=cmd, key=f"cron_cmd_{job_idx}")
        delete = cols[6].checkbox("删除", key=f"cron_del_{job_idx}")
        if not delete:
            edited_jobs_map[job_idx] = f"{nm} {nh} {ndom} {nmon} {ndow} {ncmd}"
        job_idx += 1

    if job_idx == 0:
        st.info("当前没有 Healthy 相关的定时任务。")

    # -- add new job --
    with st.expander("添加新任务"):
        c1, c2, c3, c4, c5 = st.columns(5)
        new_m = c1.text_input("分", value="0", key="new_cron_m")
        new_h = c2.text_input("时", value="7", key="new_cron_h")
        new_dom = c3.text_input("日", value="*", key="new_cron_dom")
        new_mon = c4.text_input("月", value="*", key="new_cron_mon")
        new_dow = c5.text_input("周", value="*", key="new_cron_dow")
        new_cmd = st.text_input(
            "命令",
            value="PYTHONPATH=src python -m superhealth.daily_pipeline",
            key="new_cron_cmd",
        )
        if st.button("添加此任务", key="btn_add_cron"):
            pending = st.session_state.get("pending_cron_jobs", [])
            pending.append(f"{new_m} {new_h} {new_dom} {new_mon} {new_dow} {new_cmd}")
            st.session_state["pending_cron_jobs"] = pending
            st.rerun()

    # -- pending jobs preview --
    pending = st.session_state.get("pending_cron_jobs", [])
    if pending:
        st.subheader("待添加任务")
        for i, job in enumerate(pending):
            c1, c2 = st.columns([8, 1])
            c1.code(job)
            if c2.button("移除", key=f"rm_pending_{i}"):
                pending.pop(i)
                st.session_state["pending_cron_jobs"] = pending
                st.rerun()

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

        # rebuild crontab
        new_lines: list[str] = []
        job_idx = 0
        for line in crontab_lines:
            if _is_healthy_job(line):
                if job_idx in edited_jobs_map:
                    sanitized = _sanitize_cron_command(edited_jobs_map[job_idx])
                    if sanitized is None:
                        st.error(f"定时任务包含危险字符，已跳过: {edited_jobs_map[job_idx]}")
                        continue
                    new_lines.append(sanitized)
                job_idx += 1
            else:
                new_lines.append(line)

        # append pending jobs
        for job in pending:
            sanitized = _sanitize_cron_command(job)
            if sanitized is None:
                st.error(f"待添加任务包含危险字符，已跳过: {job}")
                continue
            new_lines.append(sanitized)

        _save_crontab("\n".join(new_lines) + "\n")

        # clear pending
        st.session_state.pop("pending_cron_jobs", None)

        st.success("配置已保存并生效")
        st.rerun()
