"""P1: 今日概览。"""

from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from superhealth.dashboard.data_loader import (
    get_latest_ai_report,
    get_latest_daily_health,
    get_upcoming_appointments,
    load_daily_health,
)
from superhealth.dashboard.views.historical_review import _render_goal_progress


def _trend_arrow(current, prev) -> str:
    if current is None or prev is None:
        return ""
    if current > prev * 1.02:
        return " ↑"
    elif current < prev * 0.98:
        return " ↓"
    return " →"


def _metric_delta(current, avg_7d, higher_is_better=True):
    """计算 st.metric delta（趋势 vs 7日均）。

    返回 (delta_str, delta_color)。
    delta_color: 'normal'（正绿负红）或 'inverse'（正红负绿）。
    """
    if current is None or avg_7d is None:
        return None, "off"
    diff = current - avg_7d
    if abs(diff) < 0.01:
        return "→", "off"

    arrow = "↑" if diff > 0 else "↓"
    color = "normal" if higher_is_better else "inverse"

    if abs(diff) >= 1:
        num = f"{diff:+.0f}"
    else:
        num = f"{diff:+.1f}".rstrip("0").rstrip(".")
    return f"{num} {arrow}", color


def render():
    st.header("今日概览")

    today = get_latest_daily_health()

    # ── 7日均值 + 90天基线（μ±σ）────────────────────────────────────
    df_7d = load_daily_health(7)
    df_90d = load_daily_health(90)
    avg_bb = avg_rhr = avg_hrv = avg_sleep = avg_stress = None
    base_bb = base_rhr = base_hrv = base_sleep = base_stress = None
    std_bb = std_rhr = std_hrv = std_sleep = std_stress = None

    if not df_7d.empty:
        bb_col = (
            "bb_highest"
            if df_7d.get("bb_highest") is not None and df_7d["bb_highest"].notna().any()
            else "bb_at_wake"
        )
        if bb_col in df_7d.columns:
            avg_bb = df_7d[bb_col].dropna().mean()
        avg_rhr = df_7d["hr_resting"].dropna().mean() if "hr_resting" in df_7d.columns else None
        avg_hrv = df_7d["hrv_last_night_avg"].dropna().mean() if "hrv_last_night_avg" in df_7d.columns else None
        avg_sleep = df_7d["sleep_total_seconds"].dropna().mean() / 3600 if "sleep_total_seconds" in df_7d.columns else None
        avg_stress = df_7d["stress_average"].dropna().mean() if "stress_average" in df_7d.columns else None

    if not df_90d.empty:
        if bb_col in df_90d.columns:
            s = df_90d[bb_col].dropna()
            if len(s) >= 10:
                base_bb, std_bb = s.mean(), s.std()
        s = df_90d["hr_resting"].dropna()
        if len(s) >= 10:
            base_rhr, std_rhr = s.mean(), s.std()
        s = df_90d["hrv_last_night_avg"].dropna()
        if len(s) >= 10:
            base_hrv, std_hrv = s.mean(), s.std()
        s = df_90d["sleep_total_seconds"].dropna()
        if len(s) >= 10:
            base_sleep, std_sleep = s.mean() / 3600, s.std() / 3600
        s = df_90d["stress_average"].dropna()
        if len(s) >= 10:
            base_stress, std_stress = s.mean(), s.std()

    # ── KPI 卡（5列） ────────────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)

    bb = today.get("bb_highest") or today.get("bb_at_wake")
    hrv = today.get("hrv_last_night_avg")
    sleep_h = (today.get("sleep_total_seconds") or 0) / 3600
    stress = today.get("stress_average")
    rhr = today.get("hr_resting")

    def _z_caption(val, base, std, unit="", higher_is_better=True):
        if val is None or base is None or not std:
            return ""
        z = (val - base) / std
        emoji = "🟢" if (z > 0 and higher_is_better) or (z < 0 and not higher_is_better) else "🔴" if abs(z) >= 0.5 else "⚪"
        return f"{emoji} 基线 {base:.0f}±{std:.0f}{unit} ({z:+.1f}σ)"

    with c1:
        d, dc = _metric_delta(bb, avg_bb, higher_is_better=True)
        st.metric("身体电量", f"{bb:.0f}" if bb else "—", delta=d, delta_color=dc, help="最高值")
        if avg_bb is not None:
            st.caption(f"7日均 {avg_bb:.0f}")
        if base_bb is not None:
            st.caption(_z_caption(bb, base_bb, std_bb, "", higher_is_better=True))
    with c2:
        rhr_str = f"{rhr:.0f} bpm" if rhr else "—"
        d, dc = _metric_delta(rhr, avg_rhr, higher_is_better=False)
        st.metric("静息心率", rhr_str, delta=d, delta_color=dc)
        if avg_rhr is not None:
            st.caption(f"7日均 {avg_rhr:.0f} bpm")
        if base_rhr is not None:
            st.caption(_z_caption(rhr, base_rhr, std_rhr, " bpm", higher_is_better=False))
    with c3:
        hrv_str = f"{hrv:.0f} ms" if hrv else "—"
        d, dc = _metric_delta(hrv, avg_hrv, higher_is_better=True)
        st.metric("心率变异", hrv_str, delta=d, delta_color=dc)
        if avg_hrv is not None:
            st.caption(f"7日均 {avg_hrv:.0f} ms")
        if base_hrv is not None:
            st.caption(_z_caption(hrv, base_hrv, std_hrv, " ms", higher_is_better=True))
    with c4:
        d, dc = _metric_delta(sleep_h, avg_sleep, higher_is_better=True)
        st.metric("睡眠时长", f"{sleep_h:.1f} h" if sleep_h else "—", delta=d, delta_color=dc)
        if avg_sleep is not None:
            st.caption(f"7日均 {avg_sleep:.1f} h")
        if base_sleep is not None:
            st.caption(_z_caption(sleep_h, base_sleep, std_sleep, "h", higher_is_better=True))
    with c5:
        stress_str = f"{stress:.0f}" if stress else "—"
        d, dc = _metric_delta(stress, avg_stress, higher_is_better=False)
        st.metric("压力指数", stress_str, delta=d, delta_color=dc, help="Garmin 压力均值（0-100）")
        if avg_stress is not None:
            st.caption(f"7日均 {avg_stress:.0f}")
        if base_stress is not None:
            st.caption(_z_caption(stress, base_stress, std_stress, "", higher_is_better=False))

    st.divider()

    # ── 目标进度快照 ──────────────────────────────────────────────────
    st.subheader("目标进度快照")
    _render_goal_progress()

    st.divider()

    # ── 就医提醒 ──────────────────────────────────────────────────────
    appts = get_upcoming_appointments(within_days=14)
    if appts:
        st.subheader("即将到来的就医提醒")
        for a in appts:
            days_left = (
                date.fromisoformat(str(a["due_date"])[:10]) - date.today()
            ).days
            label = "🔴" if days_left <= 7 else "🟡"
            st.warning(
                f"{label} **{a['condition']}**  —  {a.get('hospital', '')} "
                f"{a.get('department', '')}  |  应诊日期：{str(a['due_date'])[:10]}"
                f"（还有 {days_left} 天）"
            )

    # ── AI 建议摘要 ───────────────────────────────────────────────────
    st.subheader("AI 建议摘要")
    summary, full_report = get_latest_ai_report()
    if summary.startswith("（暂无"):
        st.info(summary)
    else:
        st.markdown(f"> {summary}")
        if full_report:
            with st.expander("查看完整 AI 建议报告"):
                st.markdown(full_report)
