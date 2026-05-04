"""P1: 今日概览。"""

from __future__ import annotations

from datetime import date

import streamlit as st

from superhealth.dashboard.data_loader import (
    DEFAULT_DB_PATH,
    get_latest_ai_report,
    get_latest_daily_health,
    get_upcoming_appointments,
    load_daily_health,
    load_feedback_by_range,
)
from superhealth.dashboard.views.historical_review import GOAL_PROGRESS_HELP, _render_goal_progress


def _latest_feedback_for_today(df_feedback):
    """返回当天最新反馈记录，供页面预填表单。"""
    if df_feedback.empty:
        return None
    return df_feedback.iloc[0]


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
    # 默认列名，确保在 df_7d 为空时 df_90d 块也能访问
    bb_col = "bb_highest"

    if not df_7d.empty:
        bb_col = (
            "bb_highest"
            if df_7d.get("bb_highest") is not None and df_7d["bb_highest"].notna().any()
            else "bb_at_wake"
        )
        if bb_col in df_7d.columns:
            avg_bb = df_7d[bb_col].dropna().mean()
        avg_rhr = df_7d["hr_resting"].dropna().mean() if "hr_resting" in df_7d.columns else None
        avg_hrv = (
            df_7d["hrv_last_night_avg"].dropna().mean()
            if "hrv_last_night_avg" in df_7d.columns
            else None
        )
        avg_sleep = (
            df_7d["sleep_total_seconds"].dropna().mean() / 3600
            if "sleep_total_seconds" in df_7d.columns
            else None
        )
        avg_stress = (
            df_7d["stress_average"].dropna().mean() if "stress_average" in df_7d.columns else None
        )

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
        emoji = (
            "🟢"
            if (z > 0 and higher_is_better) or (z < 0 and not higher_is_better)
            else "🔴"
            if abs(z) >= 0.5
            else "⚪"
        )
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
    st.subheader("目标进度快照", help=GOAL_PROGRESS_HELP)
    _render_goal_progress()

    st.divider()

    # ── 就医提醒 ──────────────────────────────────────────────────────
    appts = get_upcoming_appointments(within_days=14)
    if appts:
        st.subheader("即将到来的就医提醒")
        for a in appts:
            days_left = (date.fromisoformat(str(a["due_date"])[:10]) - date.today()).days
            label = "🔴" if days_left <= 7 else "🟡"
            st.warning(
                f"{label} **{a['condition']}**  —  {a.get('hospital', '')} "
                f"{a.get('department', '')}  |  应诊日期：{str(a['due_date'])[:10]}"
                f"（还有 {days_left} 天）"
            )

    # ── AI 建议摘要 ───────────────────────────────────────────────────
    if "generating_report" not in st.session_state:
        st.session_state.generating_report = False
    if "report_generate_error" not in st.session_state:
        st.session_state.report_generate_error = ""
    if "report_just_generated" not in st.session_state:
        st.session_state.report_just_generated = False
    if "user_daily_context" not in st.session_state:
        st.session_state.user_daily_context = ""
    if "user_daily_context_input" not in st.session_state:
        st.session_state.user_daily_context_input = st.session_state.user_daily_context
    if "pending_user_context" not in st.session_state:
        st.session_state.pending_user_context = ""
    if "daily_feedback_saved" not in st.session_state:
        st.session_state.daily_feedback_saved = False
    if "daily_feedback_error" not in st.session_state:
        st.session_state.daily_feedback_error = ""
    st.session_state.user_daily_context = st.session_state.get("user_daily_context_input", "")

    report_dir = DEFAULT_DB_PATH.parent / "data" / "daily-reports"
    today_str = date.today().isoformat()
    today_report_file = report_dir / f"{today_str}-advanced-daily-report.md"
    has_today_report = today_report_file.exists()

    st.subheader("AI 建议摘要")
    btn_label = "重新生成高级健康日报" if has_today_report else "生成高级健康日报"
    btn_help = "调用 LLM 分析今日健康数据并生成个性化建议" if not has_today_report else "重新调用 LLM 生成今日健康日报"

    if st.session_state.generating_report:
        with st.spinner("正在调用 LLM 分析今日健康数据，请稍候..."):
            try:
                from superhealth.reports.advanced_daily_report import AdvancedDailyReportGenerator

                generator = AdvancedDailyReportGenerator(db_path=DEFAULT_DB_PATH)
                report_text = generator.generate_report(
                    today_str,
                    save=True,
                    test_mode=False,
                    user_context=st.session_state.get("pending_user_context", ""),
                )
                st.session_state.generating_report = False
                if "未找到 Garmin 数据" in report_text:
                    st.session_state.report_generate_error = "今日暂无 Garmin 数据，无法生成日报。请先去系统配置同步 Garmin 数据。"
                else:
                    st.session_state.report_just_generated = True
                st.rerun()
            except Exception as e:
                st.session_state.generating_report = False
                st.session_state.report_generate_error = str(e)
                st.rerun()

    if st.session_state.report_just_generated:
        st.success("高级健康日报生成成功！")
        st.session_state.report_just_generated = False

    if st.session_state.report_generate_error:
        st.error(f"生成失败: {st.session_state.report_generate_error}")
        st.session_state.report_generate_error = ""

    summary, full_report = get_latest_ai_report()
    if summary.startswith("（暂无"):
        st.info(summary)
    else:
        st.markdown(f"> {summary}")
        if full_report:
            with st.expander("查看完整 AI 建议报告"):
                st.markdown(full_report)

    user_daily_context = st.text_area(
        "告诉 AI 今天的特殊情况（可选）",
        placeholder="例如：今天下午有重要会议，晚上有聚餐，不方便剧烈运动...",
        key="user_daily_context_input",
        height=80,
    )
    st.session_state.user_daily_context = user_daily_context
    _, col_generate = st.columns([3, 1])
    with col_generate:
        if st.session_state.generating_report:
            st.button(
                "⏳ 生成中...",
                disabled=True,
                key="generating_daily_report_btn",
                use_container_width=True,
            )
        elif st.button(
            btn_label,
            key="generate_daily_report_btn",
            help=btn_help,
            use_container_width=True,
        ):
            st.session_state.generating_report = True
            st.session_state.report_generate_error = ""
            st.session_state.report_just_generated = False
            st.session_state.pending_user_context = st.session_state.user_daily_context
            st.rerun()

    st.divider()
    st.subheader("用户反馈与评级")
    if st.session_state.daily_feedback_saved:
        st.success("反馈已保存。")
        st.session_state.daily_feedback_saved = False
    if st.session_state.daily_feedback_error:
        st.error(f"保存失败: {st.session_state.daily_feedback_error}")
        st.session_state.daily_feedback_error = ""

    df_feedback = load_feedback_by_range(today_str, today_str)
    feedback_row = _latest_feedback_for_today(df_feedback)
    existing_feedback = ""
    existing_rating = None
    recommendation_type = "exercise"
    if feedback_row is not None:
        existing_feedback = feedback_row.get("user_feedback") or ""
        existing_rating = feedback_row.get("user_rating")
        recommendation_type = feedback_row.get("recommendation_type") or "exercise"
        if existing_rating is not None and existing_rating == existing_rating:
            existing_rating = int(existing_rating)
        else:
            existing_rating = None

    rating_options = ["不评分", 1, 2, 3, 4, 5]
    rating_index = rating_options.index(existing_rating) if existing_rating in rating_options else 0

    with st.form("daily_recommendation_feedback_form"):
        feedback_text = st.text_area(
            "今天的建议对你是否有帮助？",
            value=existing_feedback,
            placeholder="例如：运动建议可执行，但强度略高；晚间安排不方便落实...",
            height=90,
        )
        rating_value = st.radio(
            "建议评分",
            rating_options,
            index=rating_index,
            horizontal=True,
            format_func=lambda v: "不评分" if v == "不评分" else f"{v} 星",
        )
        submitted = st.form_submit_button("保存反馈")

    if submitted:
        from superhealth.feedback.feedback_collector import submit_feedback

        rating = None if rating_value == "不评分" else int(rating_value)
        try:
            submit_feedback(
                target_date=today_str,
                feedback=feedback_text.strip(),
                recommendation_type=recommendation_type,
                rating=rating,
                db_path=DEFAULT_DB_PATH,
            )
            load_feedback_by_range.clear()
            st.session_state.daily_feedback_saved = True
            st.rerun()
        except Exception as e:
            st.session_state.daily_feedback_error = str(e)
            st.rerun()
