#!/usr/bin/env python3
"""分析 Garmin 日报数据，生成恢复评分和活动建议。

优先从 JSON 文件加载结构化数据（由 fetch_garmin.py 生成），
若 JSON 不存在则回退到解析 Markdown 文件（兼容历史数据）。
"""

import argparse
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

_PKG_DIR = Path(__file__).parent.parent  # src/superhealth/
BASE_DIR = _PKG_DIR.parent.parent  # superhealth/ (project root)
DATA_DIR = BASE_DIR / "activity-data"


# ─── 数据加载 ─────────────────────────────────────────────────────


def load_day(day_str: str) -> dict[str, Any] | None:
    """加载某天的数据为扁平字典。优先 SQLite，降级到 Markdown（含 archive/）。"""
    _DB_PATH = BASE_DIR / "health.db"
    if _DB_PATH.exists():
        try:
            from superhealth import database as db

            with db.get_conn(_DB_PATH) as conn:
                flat = db.query_daily_flat(conn, day_str)
                if flat:
                    return flat
        except Exception as e:
            log.debug("SQLite 读取失败，降级: %s", e)

    # 当月文件在顶层，历史文件在 archive/YYYY-MM/
    year_month = day_str[:7]
    for md_path in [
        DATA_DIR / f"{day_str}.md",
        DATA_DIR / "archive" / year_month / f"{day_str}.md",
    ]:
        if md_path.exists():
            return _parse_markdown(md_path)

    return None


def load_recent(current_day: date, days_back: int = 5) -> list[dict[str, Any]]:
    items = []
    for i in range(days_back + 1):
        d = current_day - timedelta(days=i)
        data = load_day(d.isoformat())
        if data is not None:
            items.append(data)
    items.sort(key=lambda x: x["date"])
    return items


# ─── Markdown 解析（兼容历史数据）────────────────────────────────


def _section(text: str, title: str) -> str:
    pattern = rf"## {re.escape(title)}\n(.*?)(?=\n## |\Z)"
    m = re.search(pattern, text, re.S)
    return m.group(1).strip() if m else ""


def _m1(pattern: str, text: str, cast: Callable[[str], Any] = str, default: Any = None) -> Any:
    m = re.search(pattern, text, re.MULTILINE)
    if not m:
        return default
    try:
        return cast(m.group(1))
    except Exception:
        return default


def _parse_hm(s: str) -> Optional[int]:
    if not s:
        return None
    m = re.match(r"(\d+)h\s*(\d+)m", s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.match(r"(\d+)m", s)
    if m:
        return int(m.group(1))
    return None


def _parse_range(s: str) -> tuple[Optional[float], Optional[float]]:
    if not s:
        return (None, None)
    m = re.match(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", s)
    if not m:
        return (None, None)
    return (float(m.group(1)), float(m.group(2)))


def _parse_markdown(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    sec_sleep = _section(text, "睡眠")
    sec_stress = _section(text, "压力")
    sec_hr = _section(text, "心率")
    sec_bb = _section(text, "Body Battery")
    sec_spo2 = _section(text, "血氧 (SpO2)")
    sec_resp = _section(text, "呼吸")
    sec_act = _section(text, "活动")
    sec_hrv = _section(text, "HRV")

    out = {
        "date": path.stem,
        "sleep_total_min": _parse_hm(_m1(r"- 总睡眠: ([^\n]+)", sec_sleep, str, "")),
        "sleep_score": _m1(r"- 睡眠分数: ([^\n]+)", sec_sleep, float),
        "avg_stress": _m1(r"- 平均压力: ([^\n]+)", sec_stress, float),
        "max_stress": _m1(r"- 最高压力: ([^\n]+)", sec_stress, float),
        "resting_hr": _m1(r"- 静息心率: ([^ ]+)", sec_hr, float),
        "min_hr": _m1(r"- 最低: ([^ ]+) bpm", sec_hr, float),
        "max_hr": _m1(r"最高: ([^ ]+) bpm", sec_hr, float),
        "avg7_resting_hr": _m1(r"- 7天平均静息心率: ([^ ]+)", sec_hr, float),
        "body_battery_highest": _m1(r"- 最高: ([^\n]+)", sec_bb, float),
        "body_battery_lowest": _m1(r"- 最低: ([^\n]+)", sec_bb, float),
        "body_battery_wake": _m1(r"- 起床时: ([^\n]+)", sec_bb, float),
        "spo2_avg": _m1(r"- 平均: ([^%\n]+)%", sec_spo2, float),
        "spo2_lowest": _m1(r"- 最低: ([^%\n]+)%", sec_spo2, float),
        "spo2_latest": _m1(r"- 最新: ([^%\n]+)%", sec_spo2, float),
        "resp_waking": _m1(r"- 清醒平均: ([^ ]+)", sec_resp, float),
        "steps": _m1(r"- 步数: ([0-9,]+)", sec_act, lambda x: int(x.replace(",", ""))),
        "distance_km": _m1(r"- 距离: ([^ ]+) km", sec_act, float),
        "hrv_avg": _m1(r"- 昨晚平均: ([^ ]+)", sec_hrv, float),
        "hrv_weekly": _m1(r"- 周平均: ([^ ]+)", sec_hrv, float),
        "hrv_baseline_raw": _m1(r"- 基线: ([^ ]+)", sec_hrv, str),
        "hrv_status": _m1(r"- 状态: ([^\n]+)", sec_hrv, str),
    }
    lo, hi = _parse_range(out.pop("hrv_baseline_raw", ""))
    out["hrv_baseline_low"] = lo
    out["hrv_baseline_high"] = hi
    return out


# ─── 格式化工具 ───────────────────────────────────────────────────


def fmt_val(v: Any, suffix: str = "") -> str:
    if v is None:
        return "N/A"
    if isinstance(v, float) and v.is_integer():
        return f"{int(v)}{suffix}"
    return f"{v}{suffix}"


def fmt_minutes_hm(v: Optional[int]) -> str:
    if v is None:
        return "N/A"
    total = int(v)
    h = total // 60
    m = total % 60
    if h > 0:
        return f"{h}小时{m}分"
    return f"{m}分"


def fmt_delta(cur: Optional[float], prev: Optional[float], suffix: str = "") -> str:
    if cur is None or prev is None:
        return "无可比数据"
    d = cur - prev
    sign = "+" if d > 0 else ""
    if isinstance(d, float) and d.is_integer():
        d = int(d)
    return f"{sign}{d}{suffix}"


# ─── 评分与建议 ───────────────────────────────────────────────────


def score_state(
    cur: dict[str, Any], baselines: dict[str, dict] | None = None
) -> tuple[int, str, list[str]]:
    score = 0
    notes = []
    bl = baselines or {}

    def _z(val: Optional[float], key: str) -> Optional[float]:
        if val is None or key not in bl:
            return None
        b = bl[key]
        s = b.get("std", 0)
        if not s:
            return None
        return float(val - b["mean"]) / float(s)

    # ── 睡眠 ──
    st = cur.get("sleep_score")
    st_z = _z(st, "sleep_score")
    if st is not None:
        if st_z is not None:
            if st_z >= 1.0:
                score += 2
                notes.append(f"睡眠评分较好（高于个人基线{st_z:.1f}σ）")
            elif st_z >= 0.5:
                score += 1
                notes.append(f"睡眠评分尚可（高于个人基线{st_z:.1f}σ）")
            else:
                score -= 1
                notes.append(f"睡眠恢复一般（低于个人基线{abs(st_z):.1f}σ）")
        else:
            if st >= 85:
                score += 2
                notes.append("睡眠评分较好")
            elif st >= 75:
                score += 1
                notes.append("睡眠评分尚可")
            else:
                score -= 1
                notes.append("睡眠恢复一般")

    # ── Body Battery ──
    bb = cur.get("body_battery_wake")
    bb_z = _z(bb, "body_battery_wake")
    if bb is not None:
        if bb_z is not None:
            if bb_z >= 1.0:
                score += 2
                notes.append(f"起床 Body Battery 较高（高于个人基线{bb_z:.1f}σ）")
            elif bb_z >= 0.5:
                score += 1
                notes.append(f"起床 Body Battery 中等（高于个人基线{bb_z:.1f}σ）")
            elif bb_z <= -1.0:
                score -= 2
                notes.append(f"起床 Body Battery 偏低（低于个人基线{abs(bb_z):.1f}σ）")
            else:
                score -= 1
                notes.append(f"起床 Body Battery 偏弱（低于个人基线{abs(bb_z):.1f}σ）")
        else:
            if bb >= 75:
                score += 2
                notes.append("起床 Body Battery 较高")
            elif bb >= 55:
                score += 1
                notes.append("起床 Body Battery 中等")
            elif bb < 40:
                score -= 2
                notes.append("起床 Body Battery 偏低")
            else:
                score -= 1
                notes.append("起床 Body Battery 偏弱")

    # ── HRV ──
    hrv_status = (cur.get("hrv_status") or "").upper()
    if hrv_status == "BALANCED":
        score += 2
        notes.append("HRV 状态平衡")
    elif hrv_status == "UNBALANCED":
        notes.append("HRV 有所波动")
    elif hrv_status == "LOW":
        score -= 2
        notes.append("HRV 偏低")

    hrv_avg = cur.get("hrv_avg")
    hrv_low = cur.get("hrv_baseline_low")
    if hrv_avg is not None and hrv_low is not None:
        if hrv_avg >= hrv_low:
            score += 1
            notes.append("HRV 回到或接近基线")
        else:
            score -= 1
            notes.append("HRV 低于个人基线")

    # ── 静息心率 ──
    rhr = cur.get("resting_hr")
    rhr_z = _z(rhr, "resting_hr")
    if rhr is not None:
        if rhr_z is not None:
            if rhr_z <= 0:
                score += 1
                notes.append(f"静息心率未高于个人基线（{rhr_z:+.1f}σ）")
            elif rhr_z >= 1.0:
                score -= 1
                notes.append(f"静息心率偏高（高于个人基线{rhr_z:.1f}σ）")
        else:
            rhr7 = cur.get("avg7_resting_hr")
            if rhr7 is not None:
                if rhr <= rhr7:
                    score += 1
                    notes.append("静息心率未高于近期平均")
                elif rhr >= rhr7 + 3:
                    score -= 1
                    notes.append("静息心率偏高")

    # ── 压力 ──
    stress = cur.get("avg_stress")
    stress_z = _z(stress, "avg_stress")
    if stress is not None:
        if stress_z is not None:
            if stress_z <= -0.5:
                score += 1
                notes.append(f"平均压力不高（低于个人基线{abs(stress_z):.1f}σ）")
            elif stress_z >= 1.0:
                score -= 1
                notes.append(f"平均压力偏高（高于个人基线{stress_z:.1f}σ）")
        else:
            if stress < 25:
                score += 1
                notes.append("平均压力不高")
            elif stress > 35:
                score -= 1
                notes.append("平均压力偏高")

    # ── 呼吸 ──
    resp = cur.get("resp_waking")
    if resp is not None:
        if resp <= 15:
            score += 1
            notes.append("呼吸频率平稳")
        elif resp >= 18:
            score -= 1
            notes.append("呼吸频率偏高")

    if score >= 5:
        level = "恢复较好"
    elif score >= 2:
        level = "恢复中等"
    else:
        level = "恢复偏弱"

    return score, level, notes


def recommend(cur: dict[str, Any], level: str) -> tuple[str, list[str], list[str]]:
    spo2_latest = cur.get("spo2_latest")
    cautions = []
    if spo2_latest is not None and spo2_latest < 90:
        cautions.append("血氧单次读数偏低，腕表血氧可能有误差；若反复偏低或伴胸闷气短，应线下排查")

    if level == "恢复较好":
        plan = [
            "今天可做中等强度训练为主。",
            "优先建议：30–45 分钟二区有氧（快走/慢跑/骑车/椭圆机）。",
            "如果主观感觉也不错，可加 15–25 分钟轻到中等强度力量训练。",
            "不建议一上来做极限冲刺或很高强度间歇。",
        ]
        intensity = "中等强度，可少量上探到中高强度"
    elif level == "恢复中等":
        plan = [
            "今天更适合恢复偏训练日。",
            "优先建议：25–40 分钟低到中等强度有氧 + 10 分钟拉伸。",
            "力量训练可以做，但控制容量，不要练到很透支。",
            "今天重点是把身体启动起来，而不是追求表现。",
        ]
        intensity = "低到中等强度"
    else:
        plan = [
            "今天建议恢复优先。",
            "优先建议：20–30 分钟散步、轻松骑车或拉伸活动。",
            "不建议安排高强度训练，也不建议大容量力量课表。",
            "核心目标是补恢复、补睡眠、降低系统负荷。",
        ]
        intensity = "轻强度恢复"

    return intensity, plan, cautions


def avg_of(items: list[dict[str, Any]], key: str) -> Optional[float]:
    vals: list[float] = [float(x[key]) for x in items if x.get(key) is not None]
    return mean(vals) if vals else None


def _load_baselines(day_str: str, days: int = 90) -> dict[str, dict]:
    """加载最近N天的个人基线（mean ± std），用于日报评分个性化。"""
    _DB_PATH = BASE_DIR / "health.db"
    if not _DB_PATH.exists():
        return {}
    try:
        from statistics import mean, pstdev

        from superhealth import database as db

        start = (datetime.strptime(day_str, "%Y-%m-%d") - timedelta(days=days)).isoformat()[:10]
        with db.get_conn(_DB_PATH) as conn:
            rows = conn.execute(
                """SELECT sleep_score, stress_average, bb_at_wake, hr_resting
                   FROM daily_health
                   WHERE date >= ? AND date < ?""",
                (start, day_str),
            ).fetchall()
        result: dict[str, dict] = {}
        for key, field in [
            ("sleep_score", "sleep_score"),
            ("avg_stress", "stress_average"),
            ("body_battery_wake", "bb_at_wake"),
            ("resting_hr", "hr_resting"),
        ]:
            vals = [r[field] for r in rows if r[field] is not None]
            if len(vals) >= 10:  # 至少10天数据才有统计意义
                result[key] = {
                    "mean": mean(vals),
                    "std": pstdev(vals) if len(vals) > 1 else 0.0,
                }
        return result
    except Exception as e:
        log.debug("基线加载失败: %s", e)
        return {}


def has_meaningful_data(data: dict[str, Any]) -> bool:
    """判断数据是否有意义（至少有睡眠或心率数据）。"""
    return (
        data.get("sleep_score") is not None
        or data.get("resting_hr") is not None
        or data.get("body_battery_wake") is not None
    )


def _load_vitals(day_str: str) -> Optional[dict]:
    """从 SQLite 加载某天的体征数据。"""
    _DB_PATH = BASE_DIR / "health.db"
    if not _DB_PATH.exists():
        return None
    try:
        from superhealth import database as db

        with db.get_conn(_DB_PATH) as conn:
            return db.query_vitals_by_date(conn, day_str)
    except Exception as e:
        log.debug("体征数据读取失败: %s", e)
        return None


def _bp_status(systolic: int, diastolic: int) -> str:
    """根据血压值返回状态标记。"""
    if systolic >= 140 or diastolic >= 90:
        return "⚠️ 偏高"
    elif systolic >= 130 or diastolic >= 85:
        return "⚡ 偏高（注意）"
    elif systolic < 90 or diastolic < 60:
        return "⚠️ 偏低"
    return "✓ 正常"


# ─── 主函数 ───────────────────────────────────────────────────────


def main() -> None:
    from superhealth.log_config import setup_logging

    setup_logging()

    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    args = ap.parse_args()
    day = datetime.strptime(args.date, "%Y-%m-%d").date()

    current = load_day(day.isoformat())
    if current is None:
        raise SystemExit(f"未找到数据文件: {day.isoformat()}（JSON 和 Markdown 均不存在）")

    # 未佩戴手表时，生成简短的"无数据"分析报告
    if not has_meaningful_data(current):
        lines = [
            f"# {day.isoformat()} Garmin 日报分析",
            "",
            "## 今日状态判断",
            "- 恢复评级：**无数据**",
            "- 今日未检测到有效健康数据，可能未佩戴手表。",
            "- 建议保持日常运动习惯，注意休息。",
        ]
        out = DATA_DIR / f"{day.isoformat()}-analysis.md"
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log.info("已生成（无数据）: %s", out)
        return

    recent = load_recent(day, 5)
    prev = next((x for x in reversed(recent) if x["date"] < day.isoformat()), None)
    prev3 = [x for x in recent if x["date"] < day.isoformat()][-3:]

    baselines = _load_baselines(day.isoformat())
    score, level, notes = score_state(current, baselines)
    intensity, plan, cautions = recommend(current, level)

    lines = []
    lines.append(f"# {day.isoformat()} Garmin 日报分析")
    lines.append("")
    lines.append("## 今日状态判断")
    lines.append(f"- 恢复评级：**{level}**")
    lines.append(f"- 建议活动强度：**{intensity}**")
    lines.append(f"- 恢复综合分（内部打分）：**{score}**")
    lines.append("")
    lines.append("## 今日关键指标")
    lines.append(
        f"- 睡眠：{fmt_minutes_hm(current.get('sleep_total_min'))} | 睡眠分数 {fmt_val(current.get('sleep_score'))}"
    )
    lines.append(f"- 起床 Body Battery：{fmt_val(current.get('body_battery_wake'))}")
    lines.append(
        f"- HRV：{fmt_val(current.get('hrv_avg'), ' ms')} | 状态 {fmt_val(current.get('hrv_status'))}"
    )
    lines.append(
        f"- 静息心率：{fmt_val(current.get('resting_hr'), ' bpm')} | 7天均值 {fmt_val(current.get('avg7_resting_hr'), ' bpm')}"
    )
    lines.append(f"- 平均压力：{fmt_val(current.get('avg_stress'))}")
    lines.append(f"- 清醒呼吸：{fmt_val(current.get('resp_waking'), ' 次/分')}")
    lines.append("")

    # 体征数据小节（Phase 3.4）
    vitals = _load_vitals(day.isoformat())
    if vitals:
        lines.append("## 今日体征")
        measured_time = vitals.get("measured_at", "")
        if measured_time and "T" in measured_time:
            # 提取时间部分 HH:MM
            time_part = measured_time.split("T")[1][:5]
            lines.append(f"- 测量时间：{time_part}")
        if vitals.get("systolic") and vitals.get("diastolic"):
            bp_status = _bp_status(vitals["systolic"], vitals["diastolic"])
            lines.append(f"- 血压：{vitals['systolic']}/{vitals['diastolic']} mmHg {bp_status}")
        if vitals.get("weight_kg"):
            lines.append(f"- 体重：{vitals['weight_kg']:.1f} kg")
        if vitals.get("body_fat_pct"):
            lines.append(f"- 体脂率：{vitals['body_fat_pct']:.1f}%")
        lines.append("")

    if prev:
        lines.append("## 与前一天对比")
        lines.append(
            f"- 睡眠分数：今天 {fmt_val(current.get('sleep_score'))} vs 昨天 {fmt_val(prev.get('sleep_score'))}（{fmt_delta(current.get('sleep_score'), prev.get('sleep_score'))}）"
        )
        lines.append(
            f"- 起床 Body Battery：今天 {fmt_val(current.get('body_battery_wake'))} vs 昨天 {fmt_val(prev.get('body_battery_wake'))}（{fmt_delta(current.get('body_battery_wake'), prev.get('body_battery_wake'))}）"
        )
        lines.append(
            f"- HRV：今天 {fmt_val(current.get('hrv_avg'))} vs 昨天 {fmt_val(prev.get('hrv_avg'))}（{fmt_delta(current.get('hrv_avg'), prev.get('hrv_avg'), ' ms')}）"
        )
        lines.append(
            f"- 静息心率：今天 {fmt_val(current.get('resting_hr'))} vs 昨天 {fmt_val(prev.get('resting_hr'))}（{fmt_delta(current.get('resting_hr'), prev.get('resting_hr'), ' bpm')}）"
        )
        lines.append(
            f"- 平均压力：今天 {fmt_val(current.get('avg_stress'))} vs 昨天 {fmt_val(prev.get('avg_stress'))}（{fmt_delta(current.get('avg_stress'), prev.get('avg_stress'))}）"
        )
        lines.append("")

    if prev3:
        avg_sleep_score = avg_of(prev3, "sleep_score")
        avg_bb = avg_of(prev3, "body_battery_wake")
        avg_hrv = avg_of(prev3, "hrv_avg")
        lines.append("## 与前几天（最多3天）对比")
        lines.append(
            f"- 近3日平均睡眠分数：{fmt_val(avg_sleep_score)}；今天 {fmt_val(current.get('sleep_score'))}"
        )
        lines.append(
            f"- 近3日起床 Body Battery：{fmt_val(avg_bb)}；今天 {fmt_val(current.get('body_battery_wake'))}"
        )
        lines.append(
            f"- 近3日 HRV：{fmt_val(avg_hrv)} ms；今天 {fmt_val(current.get('hrv_avg'))} ms"
        )
        lines.append("")

    lines.append("## 今天为什么这样判断")
    for n in notes:
        lines.append(f"- {n}")
    lines.append("")
    lines.append("## 今日活动建议")
    for p in plan:
        lines.append(f"- {p}")
    if cautions:
        lines.append("")
        lines.append("## 提醒")
        for c in cautions:
            lines.append(f"- {c}")

    out = DATA_DIR / f"{day.isoformat()}-analysis.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("已生成: %s", out)


if __name__ == "__main__":
    main()
