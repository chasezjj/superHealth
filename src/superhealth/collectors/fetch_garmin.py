#!/usr/bin/env python3
"""从 Garmin Connect 中国区拉取健康数据并存为 Markdown 文件。

用法:
    # 拉取昨天的数据（默认）
    python -m superhealth.fetch_garmin

    # 拉取指定日期
    python -m superhealth.fetch_garmin --date 2026-03-25

    # 拉取日期范围
    python -m superhealth.fetch_garmin --range 2026-03-01 2026-03-25

首次使用前需要登录:
    python -m superhealth.fetch_garmin --login
"""

import argparse
import json
import logging
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from superhealth import database as db
from superhealth.models import (
    ActivityData,
    BodyBatteryData,
    DailyHealth,
    Exercise,
    HeartRateData,
    HRVData,
    RespirationData,
    SleepData,
    SpO2Data,
    StressData,
)

log = logging.getLogger(__name__)


class GarminAuthError(Exception):
    """Raised when Garmin authentication fails or session is missing."""


def _normalize_activity_name(name: str) -> str:
    """清洗 Garmin activityName：去除地域前缀，统一繁体字。"""
    name = re.sub(r"^.+?区\s*-\s*", "", name)
    name = re.sub(r"^.+?区\s+", "", name)
    name = name.replace("跑步機", "跑步机")
    return name


_PKG_DIR = Path(__file__).parent.parent  # src/superhealth/
BASE_DIR = _PKG_DIR.parent.parent  # superhealth/ (project root)
OUTPUT_DIR = BASE_DIR / "activity-data"
SESSION_FILE = Path.home() / ".garmin_cn_session.json"
_LEGACY_CONFIG_FILE = Path.home() / ".garmin_cn_config.json"  # 旧格式，向后兼容

SSO_BASE = "https://sso.garmin.cn"
CONNECT_BASE = "https://connect.garmin.cn"
API_BASE = f"{CONNECT_BASE}/gc-api"


# ─── 认证 ──────────────────────────────────────────────────────────


def _login_via_playwright(email, password):
    """通过 Playwright 浏览器自动化登录 Garmin Connect CN。
    返回 (cookies, csrf_token, user_id)。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("需要安装 playwright: pip install playwright && playwright install chromium")
        raise GarminAuthError("需要安装 playwright")

    user_id = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        # 拦截 API 请求获取 user_id
        def on_response(response):
            nonlocal user_id
            if "usersummary/daily/" in response.url and response.status == 200:
                # URL 格式: .../daily/{user_id}?calendarDate=...
                parts = response.url.split("usersummary/daily/")
                if len(parts) > 1:
                    user_id = parts[1].split("?")[0]

        page.on("response", on_response)

        log.info("正在打开登录页面...")
        page.goto(
            f"{SSO_BASE}/portal/sso/en-US/sign-in?clientId=GarminConnect"
            f"&service={CONNECT_BASE}/app",
            wait_until="networkidle",
            timeout=30000,
        )
        page.wait_for_timeout(2000)

        log.info("正在填写登录信息...")
        page.locator(
            "input[type='text'], input[type='email'], input[autocomplete='username']"
        ).first.fill(email)
        page.locator("input[type='password']").first.fill(password)
        page.wait_for_timeout(500)

        page.locator("button:has-text('Sign In'), button[type='submit']").first.click()
        log.info("正在登录...")

        page.wait_for_url("**/connect.garmin.cn/**", timeout=30000)
        page.wait_for_timeout(3000)

        # 提取 CSRF token（带 3 次重试）
        csrf_token = ""
        for attempt in range(1, 4):
            csrf_token = page.evaluate(
                "() => document.querySelector('meta[name=\"csrf-token\"]')?.content || ''"
            )
            if csrf_token:
                break
            log.warning("CSRF token 获取为空，第 %d/3 次重试...", attempt)
            page.reload(wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000 + attempt * 2000)
        if not csrf_token:
            raise RuntimeError("无法获取 CSRF token")

        # 访问 daily-summary 以获取 user_id
        if not user_id:
            page.goto(
                f"{CONNECT_BASE}/modern/daily-summary", wait_until="networkidle", timeout=30000
            )
            page.wait_for_timeout(3000)

        cookies = context.cookies()
        browser.close()

    log.info("登录成功! User ID: %s", user_id)
    return cookies, csrf_token, user_id


def _save_session(cookies, csrf_token, user_id):
    """保存 session 到文件。"""
    data = {
        "csrf_token": csrf_token,
        "user_id": user_id,
        "cookies": [
            {"name": c["name"], "value": c["value"], "domain": c["domain"], "path": c["path"]}
            for c in cookies
            if "garmin" in c["domain"]
        ],
    }
    SESSION_FILE.write_text(json.dumps(data, indent=2))
    SESSION_FILE.chmod(0o600)
    log.info("Session 已保存到 %s", SESSION_FILE)


def _load_session():
    """加载 session，返回 (requests.Session, user_id)。"""
    if not SESSION_FILE.exists():
        raise GarminAuthError(f"未找到 session ({SESSION_FILE})，请先运行: python -m superhealth.collectors.fetch_garmin --login")

    data = json.loads(SESSION_FILE.read_text())
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "NK": "NT",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "connect-csrf-token": data["csrf_token"],
            "X-App-Ver": "5.22.0.21d",
            "X-Lang": "zh-CN",
        }
    )
    for c in data["cookies"]:
        session.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
    return session, data.get("user_id", "")


def _is_expired(r):
    return r.status_code in (401, 403) or (
        r.status_code == 200 and "text/html" in r.headers.get("content-type", "")
    )


def test_connection() -> tuple[bool, str]:
    """测试 Garmin session 是否有效，不触发重新登录。返回 (ok, message)。"""
    try:
        session, user_id = _load_session()
    except GarminAuthError as e:
        return False, str(e)
    except Exception as e:
        return False, f"加载 session 失败: {e}"

    if not user_id:
        return False, "Session 中未找到 user_id，请重新运行 --login"

    today = date.today().isoformat()
    url = f"{API_BASE}/usersummary-service/usersummary/daily/{user_id}"
    try:
        r = session.get(url, params={"calendarDate": today}, timeout=10)
    except Exception as e:
        return False, f"网络请求失败: {e}"

    if _is_expired(r):
        return False, "Session 已过期，请重新登录（命令行运行 --login）"

    if r.status_code == 200:
        return True, f"连接成功 (user_id: {user_id})"

    return False, f"API 返回异常状态码: {r.status_code}"


def _api_get(session, path, params=None):
    """调用 Garmin Connect CN API，session 过期时自动重新登录。"""
    url = f"{API_BASE}/{path.lstrip('/')}"
    r = session.get(url, params=params)
    if _is_expired(r):
        new_session, _ = _auto_relogin()
        # 更新当前 session 的 headers 和 cookies
        session.headers.update(new_session.headers)
        session.cookies.update(new_session.cookies)
        r = session.get(url, params=params)
        if _is_expired(r):
            raise RuntimeError("重新登录后仍无法访问 API")
    r.raise_for_status()
    return r.json()


def _save_config(email: str, password: str) -> None:
    """保存账号密码到 ~/.superhealth/config.toml。"""
    from superhealth.config import CONFIG_PATH, save_garmin

    save_garmin(email, password)
    log.info("账号密码已保存到 %s", CONFIG_PATH)


def _load_config() -> tuple[str | None, str | None]:
    """从 ~/.superhealth/config.toml 加载账号密码；向后兼容旧 JSON 文件。"""
    from superhealth.config import load as load_cfg

    conf = load_cfg()
    if conf.garmin.is_complete():
        return conf.garmin.email, conf.garmin.password
    # 向后兼容：旧 ~/.garmin_cn_config.json
    if _LEGACY_CONFIG_FILE.exists():
        data = json.loads(_LEGACY_CONFIG_FILE.read_text())
        return data.get("email"), data.get("password")
    return None, None


def _auto_relogin():
    """Session 过期时自动重新登录。"""
    email, password = _load_config()
    if not email or not password:
        raise GarminAuthError("Session 已过期且未保存凭据，请重新登录: python -m superhealth.collectors.fetch_garmin --login")
    log.info("Session 已过期，正在自动重新登录...")
    login_with_credentials(email, password)
    return _load_session()


def login_interactive():
    """交互式登录并保存 session 和凭据。"""
    email = input("Garmin Connect 账号 (手机号/邮箱): ")
    from getpass import getpass

    password = getpass("密码: ")
    cookies, csrf, user_id = _login_via_playwright(email, password)
    _save_session(cookies, csrf, user_id)
    _save_config(email, password)


def login_with_credentials(email, password):
    """用提供的凭据登录并保存 session 和凭据。"""
    cookies, csrf, user_id = _login_via_playwright(email, password)
    _save_session(cookies, csrf, user_id)
    _save_config(email, password)


# ─── 工具函数 ──────────────────────────────────────────────────────


def fmt_dur(seconds):
    """格式化秒为 Xh XXm 格式。"""
    if not seconds or not isinstance(seconds, (int, float)):
        return "N/A"
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def sg(data, *keys, default="N/A"):
    """安全地从嵌套字典中获取值。"""
    current = data
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return default
    return current if current is not None else default


# ─── 数据获取 → Pydantic 模型 ─────────────────────────────────────


def fetch_sleep_data(session, day: str) -> SleepData:
    try:
        data = _api_get(
            session, f"sleep-service/sleep/dailySleepData?date={day}&nonSleepBufferMinutes=60"
        )
    except Exception as e:
        log.warning("睡眠数据获取失败: %s", e)
        return SleepData()

    dto = sg(data, "dailySleepDTO", default={})
    if not dto:
        return SleepData()

    return SleepData(
        total_seconds=sg(dto, "sleepTimeSeconds", default=None),
        deep_seconds=sg(dto, "deepSleepSeconds", default=None),
        light_seconds=sg(dto, "lightSleepSeconds", default=None),
        rem_seconds=sg(dto, "remSleepSeconds", default=None),
        awake_seconds=sg(dto, "awakeSleepSeconds", default=None),
        score=sg(dto, "sleepScores", "overall", "value", default=None),
    )


def fetch_hrv_data(session, day: str) -> HRVData:
    try:
        data = _api_get(session, f"hrv-service/hrv/{day}")
    except Exception as e:
        log.warning("HRV 数据获取失败: %s", e)
        return HRVData()

    summary = sg(data, "hrvSummary", default={})
    if not summary:
        return HRVData()

    baseline = sg(summary, "baseline", default={})
    return HRVData(
        last_night_avg=sg(summary, "lastNightAvg", default=None),
        last_night_5min_high=sg(summary, "lastNight5MinHigh", default=None),
        weekly_avg=sg(summary, "weeklyAvg", default=None),
        baseline_low=sg(baseline, "balancedLow", default=None),
        baseline_high=sg(baseline, "balancedUpper", default=None),
        status=sg(summary, "status", default=None),
    )


def fetch_summary_data(session, user_id: str, day: str):
    """获取每日综合数据，返回 (stress, heart_rate, body_battery, spo2, respiration, activity)。"""
    try:
        s = _api_get(
            session,
            f"usersummary-service/usersummary/daily/{user_id}",
            params={"calendarDate": day},
        )
    except Exception as e:
        log.warning("综合数据获取失败: %s", e)
        return (
            StressData(),
            HeartRateData(),
            BodyBatteryData(),
            SpO2Data(),
            RespirationData(),
            ActivityData(),
        )

    stress = StressData(
        average=sg(s, "averageStressLevel", default=None),
        max=sg(s, "maxStressLevel", default=None),
        rest_seconds=sg(s, "restStressDuration", default=None),
        low_seconds=sg(s, "lowStressDuration", default=None),
        medium_seconds=sg(s, "mediumStressDuration", default=None),
        high_seconds=sg(s, "highStressDuration", default=None),
    )
    heart_rate = HeartRateData(
        resting=sg(s, "restingHeartRate", default=None),
        min=sg(s, "minHeartRate", default=None),
        max=sg(s, "maxHeartRate", default=None),
        avg7_resting=sg(s, "lastSevenDaysAvgRestingHeartRate", default=None),
    )
    body_battery = BodyBatteryData(
        highest=sg(s, "bodyBatteryHighestValue", default=None),
        lowest=sg(s, "bodyBatteryLowestValue", default=None),
        charged=sg(s, "bodyBatteryChargedValue", default=None),
        drained=sg(s, "bodyBatteryDrainedValue", default=None),
        at_wake=sg(s, "bodyBatteryAtWakeTime", default=None),
    )
    spo2 = SpO2Data(
        average=sg(s, "averageSpo2", default=None),
        lowest=sg(s, "lowestSpo2", default=None),
        latest=sg(s, "latestSpo2", default=None),
    )
    respiration = RespirationData(
        waking_avg=sg(s, "avgWakingRespirationValue", default=None),
        highest=sg(s, "highestRespirationValue", default=None),
        lowest=sg(s, "lowestRespirationValue", default=None),
    )
    activity = ActivityData(
        steps=sg(s, "totalSteps", default=None),
        distance_meters=sg(s, "totalDistanceMeters", default=None),
        active_calories=sg(s, "activeKilocalories", default=None),
        floors_ascended=sg(s, "floorsAscended", default=None),
    )
    return stress, heart_rate, body_battery, spo2, respiration, activity


def fetch_exercises_data(session, day: str) -> list[Exercise]:
    try:
        data = _api_get(
            session,
            "activitylist-service/activities/search/activities",
            params={"startDate": day, "endDate": day, "limit": 20},
        )
    except Exception as e:
        log.warning("运动数据获取失败: %s", e)
        return []

    if not data:
        return []

    exercises = []
    for act in data:
        # 解析开始时间，取 "HH:MM" 部分用于时间偏好学习
        start_time_raw = sg(act, "startTimeLocal", default=None)
        start_time_hhmm = None
        if start_time_raw and isinstance(start_time_raw, str):
            # 格式如 "2026-04-04 18:30:00" 或 "2026-04-04T18:30:00"
            time_part = start_time_raw.replace("T", " ").split(" ")[-1]
            start_time_hhmm = time_part[:5]  # "HH:MM"

        type_key = sg(act, "activityType", "typeKey", default=None)

        # 按活动类型拉取 details
        details = None
        activity_id = sg(act, "activityId", default=None)
        if activity_id and type_key and "strength" in str(type_key).lower():
            details = _fetch_exercise_sets(session, activity_id)

        exercises.append(
            Exercise(
                name=_normalize_activity_name(sg(act, "activityName", default="未知活动")),
                type_key=type_key,
                start_time=start_time_hhmm,
                distance_meters=sg(act, "distance", default=None),
                duration_seconds=sg(act, "duration", default=None),
                avg_hr=sg(act, "averageHR", default=None),
                max_hr=sg(act, "maxHR", default=None),
                avg_speed=sg(act, "averageSpeed", default=None),
                calories=sg(act, "calories", default=None),
                details=details,
            )
        )
    return exercises


def _fetch_exercise_sets(session, activity_id) -> str | None:
    """尝试从 Garmin API 拉取力量训练的具体动作组数，返回可读字符串或 None。

    API 端点：activity-service/activity/{activityId}/exerciseSets
    并非所有设备/活动类型都支持，失败时静默返回 None。
    """
    try:
        data = _api_get(
            session,
            f"activity-service/activity/{activity_id}/exerciseSets",
        )
    except Exception:
        return None

    if not data:
        return None

    # API 返回结构：{"activityId": ..., "exerciseSets": [{...}, ...]}
    # 每个 set: {"exercises": [{"category": "SQUAT", "name": "BACK_SQUAT"}], "setType": "ACTIVE",
    #            "repetitionCount": 12, "weight": 0.0, ...}
    exercise_sets = data if isinstance(data, list) else data.get("exerciseSets", [])
    if not exercise_sets:
        return None

    # 按动作名称聚合组数
    agg: dict[str, list[int]] = defaultdict(list)
    for s in exercise_sets:
        if sg(s, "setType", default="") != "ACTIVE":
            continue
        exercises_list = s.get("exercises", [])
        if exercises_list:
            name = exercises_list[0].get("name") or exercises_list[0].get("category") or "UNKNOWN"
        else:
            name = "UNKNOWN"
        reps = sg(s, "repetitionCount", default=None)
        if reps:
            agg[name].append(int(reps))

    if not agg:
        return None

    parts = []
    for name, reps_list in agg.items():
        sets_count = len(reps_list)
        avg_reps = int(sum(reps_list) / sets_count)
        parts.append(f"{name}{sets_count}组×{avg_reps}")
    return ", ".join(parts)


def fetch_daily_health(session, user_id: str, day: str) -> DailyHealth:
    """从 Garmin API 获取一天的完整健康数据，返回结构化模型。"""
    sleep = fetch_sleep_data(session, day)
    hrv = fetch_hrv_data(session, day)
    stress, heart_rate, body_battery, spo2, respiration, activity = fetch_summary_data(
        session, user_id, day
    )
    exercises = fetch_exercises_data(session, day)

    return DailyHealth(
        date=day,
        sleep=sleep,
        stress=stress,
        heart_rate=heart_rate,
        body_battery=body_battery,
        spo2=spo2,
        respiration=respiration,
        activity=activity,
        hrv=hrv,
        exercises=exercises,
    )


# ─── Pydantic → Markdown 格式化 ──────────────────────────────────


def _v(val, suffix="") -> str:
    """格式化值，None 显示为 N/A。"""
    if val is None:
        return "N/A"
    if isinstance(val, float) and val.is_integer():
        return f"{int(val)}{suffix}"
    return f"{val}{suffix}"


def daily_health_to_markdown(dh: DailyHealth) -> str:
    """将 DailyHealth 模型渲染为 Markdown 文本（与之前输出格式完全一致）。"""
    sections = [f"# {dh.date} 健康数据\n"]

    # 睡眠
    if dh.sleep.has_data:
        sections.append(
            "\n".join(
                [
                    "## 睡眠",
                    f"- 总睡眠: {fmt_dur(dh.sleep.total_seconds)}",
                    f"- 深睡: {fmt_dur(dh.sleep.deep_seconds)} | "
                    f"浅睡: {fmt_dur(dh.sleep.light_seconds)} | "
                    f"REM: {fmt_dur(dh.sleep.rem_seconds)}",
                    f"- 清醒: {fmt_dur(dh.sleep.awake_seconds)}",
                    f"- 睡眠分数: {_v(dh.sleep.score)}",
                ]
            )
        )
    else:
        sections.append("## 睡眠\n- 无数据")

    # 压力
    sections.append(
        "\n".join(
            [
                "## 压力",
                f"- 平均压力: {_v(dh.stress.average)}",
                f"- 最高压力: {_v(dh.stress.max)}",
                f"- 休息: {fmt_dur(dh.stress.rest_seconds)} | "
                f"低: {fmt_dur(dh.stress.low_seconds)} | "
                f"中: {fmt_dur(dh.stress.medium_seconds)} | "
                f"高: {fmt_dur(dh.stress.high_seconds)}",
            ]
        )
    )

    # 心率
    sections.append(
        "\n".join(
            [
                "## 心率",
                f"- 静息心率: {_v(dh.heart_rate.resting)} bpm",
                f"- 最低: {_v(dh.heart_rate.min)} bpm | 最高: {_v(dh.heart_rate.max)} bpm",
                f"- 7天平均静息心率: {_v(dh.heart_rate.avg7_resting)} bpm",
            ]
        )
    )

    # Body Battery
    sections.append(
        "\n".join(
            [
                "## Body Battery",
                f"- 最高: {_v(dh.body_battery.highest)}",
                f"- 最低: {_v(dh.body_battery.lowest)}",
                f"- 充能: +{_v(dh.body_battery.charged)} | 消耗: -{_v(dh.body_battery.drained)}",
                f"- 起床时: {_v(dh.body_battery.at_wake)}",
            ]
        )
    )

    # 血氧
    sections.append(
        "\n".join(
            [
                "## 血氧 (SpO2)",
                f"- 平均: {_v(dh.spo2.average)}%",
                f"- 最低: {_v(dh.spo2.lowest)}%",
                f"- 最新: {_v(dh.spo2.latest)}%",
            ]
        )
    )

    # 呼吸
    sections.append(
        "\n".join(
            [
                "## 呼吸",
                f"- 清醒平均: {_v(dh.respiration.waking_avg)} 次/min",
                f"- 最高: {_v(dh.respiration.highest)} 次/min | "
                f"最低: {_v(dh.respiration.lowest)} 次/min",
            ]
        )
    )

    # 活动
    steps = dh.activity.steps
    steps_str = f"{steps:,}" if isinstance(steps, int) else _v(steps)
    sections.append(
        "\n".join(
            [
                "## 活动",
                f"- 步数: {steps_str}",
                f"- 距离: {_v(dh.activity.distance_km)} km",
                f"- 活动卡路里: {_v(dh.activity.active_calories)} kcal",
                f"- 爬楼: {_v(dh.activity.floors_ascended)} 层",
            ]
        )
    )

    # HRV
    if dh.hrv.last_night_avg is not None:
        sections.append(
            "\n".join(
                [
                    "## HRV",
                    f"- 昨晚平均: {_v(dh.hrv.last_night_avg)} ms",
                    f"- 昨晚最高 (5min): {_v(dh.hrv.last_night_5min_high)} ms",
                    f"- 周平均: {_v(dh.hrv.weekly_avg)} ms",
                    f"- 基线: {_v(dh.hrv.baseline_low)}-{_v(dh.hrv.baseline_high)} ms",
                    f"- 状态: {_v(dh.hrv.status)}",
                ]
            )
        )
    else:
        sections.append("## HRV\n- 无数据")

    # 运动
    if dh.exercises:
        lines = ["## 运动"]
        for ex in dh.exercises:
            parts = [f"- **{ex.name}**"]
            if ex.distance_km:
                parts.append(f"{ex.distance_km}km")
            parts.append(fmt_dur(ex.duration_seconds))
            if ex.pace_str:
                parts.append(f"配速 {ex.pace_str}")
            if ex.avg_hr:
                parts.append(f"平均心率 {_v(ex.avg_hr)}")
            if ex.max_hr:
                parts.append(f"最高心率 {_v(ex.max_hr)}")
            if ex.calories:
                parts.append(f"{_v(ex.calories)}kcal")
            lines.append(" | ".join(parts))
        sections.append("\n".join(lines))
    else:
        sections.append("## 运动\n- 今日无运动记录")

    return "\n\n".join(sections) + "\n"


# ─── 生成与保存 ────────────────────────────────────────────────────


def save_day(session, user_id, day, retry_empty=True, skip_existing=False):
    day_str = day if isinstance(day, str) else day.isoformat()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 跳过已存在记录
    if skip_existing:
        db_path = BASE_DIR / "health.db"
        if db_path.exists():
            db.init_db(db_path)
            with db.get_conn(db_path) as conn:
                row = conn.execute(
                    "SELECT 1 FROM daily_health WHERE date = ? LIMIT 1", (day_str,)
                ).fetchone()
            if row:
                log.info("%s 数据库中已存在，跳过", day_str)
                return

    log.info("正在拉取 %s 的数据...", day_str)
    dh = None
    for attempt in range(1, 4):
        dh = fetch_daily_health(session, user_id, day_str)
        if dh.has_data:
            break
        if not retry_empty:
            log.info("%s 无有效数据，不重试", day_str)
            break
        log.warning("%s 无有效数据，30 秒后重试 (第 %d/3 次)...", day_str, attempt)
        time.sleep(30)

    # 保存 Markdown（人可读）
    md_path = OUTPUT_DIR / f"{day_str}.md"
    md_path.write_text(daily_health_to_markdown(dh), encoding="utf-8")

    # 写入 SQLite
    db_path = BASE_DIR / "health.db"
    db.init_db(db_path)
    with db.get_conn(db_path) as conn:
        db.upsert_daily_health(conn, dh)
    log.info("已写入 SQLite: %s", db_path)

    if not dh.has_data:
        log.info("已保存: %s（当天无有效健康数据，可能未佩戴手表）", md_path)
    else:
        log.info("已保存: %s", md_path)


def parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def main():
    from superhealth.log_config import setup_logging

    setup_logging()

    parser = argparse.ArgumentParser(description="从 Garmin Connect 中国区拉取健康数据")
    parser.add_argument("--login", action="store_true", help="交互式登录并保存 session")
    parser.add_argument(
        "--credentials", nargs=2, metavar=("EMAIL", "PASSWORD"), help="直接提供凭据登录"
    )
    parser.add_argument("--date", type=str, help="拉取指定日期 (YYYY-MM-DD)")
    parser.add_argument("--range", nargs=2, metavar=("START", "END"), help="拉取日期范围")
    parser.add_argument(
        "--no-retry-empty", action="store_true", help="当天无数据时不重试（适合批量补录）"
    )
    parser.add_argument(
        "--skip-existing", action="store_true", help="数据库中已存在该日期记录时跳过"
    )
    args = parser.parse_args()

    if args.login:
        login_interactive()
        return

    if args.credentials:
        login_with_credentials(args.credentials[0], args.credentials[1])

    session, user_id = _load_session()

    kwargs = dict(retry_empty=not args.no_retry_empty, skip_existing=args.skip_existing)

    if args.range:
        start = parse_date(args.range[0])
        end = parse_date(args.range[1])
        current = start
        while current <= end:
            save_day(session, user_id, current, **kwargs)
            current += timedelta(days=1)
    elif args.date:
        save_day(session, user_id, parse_date(args.date), **kwargs)
    else:
        yesterday = date.today() - timedelta(days=1)
        save_day(session, user_id, yesterday, **kwargs)

    log.info("完成!")


if __name__ == "__main__":
    main()
