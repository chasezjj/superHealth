"""历史回顾页面：周报摘要 + 建议执行回顾。"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from superhealth.dashboard.data_loader import (
    get_latest_weekly_report,
    load_feedback_by_range,
    load_recent_goal_progress,
)
from superhealth.database import DEFAULT_DB_PATH, get_conn, query_active_goals
from superhealth.goals.manager import GoalManager
from superhealth.goals.metrics import METRIC_REGISTRY


def _compliance_label(compliance: int | None) -> tuple[str, str]:
    """返回 (颜色标签, 文字描述)。"""
    if compliance is None:
        return "gray", "待评估"
    if compliance >= 80:
        return "green", f"{compliance}% 优秀"
    if compliance >= 50:
        return "orange", f"{compliance}% 一般"
    return "red", f"{compliance}% 需改进"


def _assessment_badge(assessment: str | None) -> str:
    """返回评估结论的 emoji 前缀。"""
    if assessment == "positive":
        return "🟢 恢复良好"
    if assessment == "negative":
        return "🔴 恢复不佳"
    if assessment == "neutral":
        return "🟡 基本持平"
    return "⚪ 暂无评估"


def render():
    st.header("历史回顾")

    # ── 本周历史回顾 ──
    st.subheader("本周历史回顾")
    _render_weekly_review()

    st.divider()

    # ── 第三部分：建议执行回顾 ──
    st.subheader("建议执行回顾")

    # 日期范围选择
    col_range1, col_range2 = st.columns(2)
    with col_range1:
        start_date = st.date_input("开始日期", value=date.today() - timedelta(days=3))
    with col_range2:
        end_date = st.date_input("结束日期", value=date.today() - timedelta(days=1))

    if start_date > end_date:
        st.warning("开始日期不能晚于结束日期。")
        df_feedback = pd.DataFrame()
    else:
        df_feedback = load_feedback_by_range(start_date.isoformat(), end_date.isoformat())

    if df_feedback.empty:
        st.info(f"{start_date} ~ {end_date} 暂无建议记录。")
    else:
        day_count = df_feedback["date"].nunique()
        st.caption(f"共 {day_count} 天记录（{start_date} ~ {end_date}）")
        for day_date, day_rows in df_feedback.groupby("date", sort=False):
            _render_feedback_day_card(day_date, day_rows)



def _render_weekly_review():
    """渲染本周周报摘要卡片。"""
    summary, full_report = get_latest_weekly_report()
    if summary.startswith("（暂无"):
        st.info(summary)
        return

    # 将 LLM 返回的 markdown 列表解析为标题+正文卡片
    sections = _parse_weekly_summary(summary)
    if not sections:
        st.markdown(summary)
    else:
        with st.container(border=True):
            for title, body in sections:
                st.markdown(f"**{title}**")
                st.markdown(body)

    if full_report:
        with st.expander("查看完整周报"):
            st.markdown(full_report)


def _parse_weekly_summary(summary: str) -> list[tuple[str, str]]:
    """解析周报摘要，提取 (标题, 正文) 列表。

    输入格式示例：
        - 本周发现：正文内容...
        - 习惯调整：正文内容...
    返回：[("本周发现", "正文内容..."), ...]
    """
    sections: list[tuple[str, str]] = []
    current_title = ""
    current_body: list[str] = []

    for line in summary.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # 检测列表项开头："- 标题：正文" 或 "- 标题：正文"
        if stripped.startswith("- "):
            # 保存上一段
            if current_title and current_body:
                sections.append((current_title, " ".join(current_body)))

            content = stripped[2:].strip()  # 去掉 "- "
            # 尝试按第一个全角/半角冒号分割标题和正文
            for sep in ("：", ": "):
                if sep in content:
                    idx = content.index(sep)
                    current_title = content[:idx].strip()
                    current_body = [content[idx + len(sep) :].strip()]
                    break
            else:
                # 没有冒号，整段作为正文，标题留空（fallback）
                current_title = ""
                current_body = [content]
        else:
            # 续行，追加到当前正文
            if current_body is not None:
                current_body.append(stripped)

    # 保存最后一段
    if current_title and current_body:
        sections.append((current_title, " ".join(current_body)))

    return sections


def _render_feedback_day_card(day_date, day_rows: pd.DataFrame):
    """将同一天的所有建议类型合并渲染为一张卡片。"""
    day_str = str(day_date)
    compliances = day_rows["compliance"].dropna()
    avg_compliance = int(compliances.mean()) if not compliances.empty else None
    color, label = _compliance_label(avg_compliance)

    with st.container(border=True):
        col_title, col_badge = st.columns([3, 1])
        with col_title:
            st.markdown(f"**{day_str}**")
        with col_badge:
            if color == "green":
                st.success(label)
            elif color == "orange":
                st.warning(label)
            elif color == "red":
                st.error(label)
            else:
                st.info(label)
            if avg_compliance is not None:
                st.progress(min(avg_compliance / 100.0, 1.0))

        type_labels = {"exercise": "运动", "non-exercise": "非运动"}
        last_idx = day_rows.index[-1]
        for idx, row in day_rows.iterrows():
            rtype = row.get("recommendation_type") or ""
            type_label = type_labels.get(rtype, rtype) if rtype else ""
            prefix = f"**[{type_label}]** " if type_label else ""
            content = row["recommendation_content"]
            actual = row["actual_action"]
            if isinstance(content, str) and content.lstrip().startswith("【运动建议】"):
                st.markdown(f"{content}  \n**实际执行：** {actual or '—'}")
            else:
                st.markdown(f"{prefix}**建议：** {content or '—'}  \n**实际执行：** {actual or '—'}")
            if rtype == "non-exercise" and not row.get("compliance"):
                st.info("💡 非运动类建议无法自动计算合规度")

            user_fb = row["user_feedback"]
            rating = row["user_rating"]
            if user_fb or rating:
                parts = []
                if rating:
                    parts.append("⭐" * rating)
                if user_fb:
                    parts.append(user_fb)
                st.caption("用户反馈：" + " | ".join(parts))

            tracked = row["tracked_metrics"]
            if tracked:
                try:
                    data = json.loads(tracked) if isinstance(tracked, str) else tracked
                except json.JSONDecodeError:
                    data = None
                if data:
                    _render_tracked_metrics(data)

            if idx != last_idx:
                st.divider()


def _render_feedback_card(row: pd.Series):
    """渲染单日建议执行卡片。"""
    day_str = str(row["date"])
    compliance = row["compliance"]
    color, label = _compliance_label(compliance)

    with st.container(border=True):
        # 顶部：日期 + compliance 标签与进度条
        col_title, col_badge = st.columns([3, 1])
        with col_title:
            st.markdown(f"**{day_str}**")
        with col_badge:
            if color == "green":
                st.success(label)
            elif color == "orange":
                st.warning(label)
            elif color == "red":
                st.error(label)
            else:
                st.info(label)
            if compliance is not None and pd.notna(compliance):
                st.progress(min(float(compliance) / 100.0, 1.0))

        # 建议 & 实际执行
        content = row["recommendation_content"]
        actual = row["actual_action"]
        if isinstance(content, str) and content.lstrip().startswith("【运动建议】"):
            st.markdown(f"{content}  \n**实际执行：** {actual or '—'}")
        else:
            st.markdown(f"**建议：** {content or '—'}  \n**实际执行：** {actual or '—'}")

        # 用户反馈
        user_fb = row["user_feedback"]
        rating = row["user_rating"]
        if user_fb or rating:
            parts = []
            if rating:
                parts.append("⭐" * rating)
            if user_fb:
                parts.append(user_fb)
            st.caption("用户反馈：" + " | ".join(parts))

        # 效果追踪
        tracked = row["tracked_metrics"]
        if tracked:
            try:
                data = json.loads(tracked) if isinstance(tracked, str) else tracked
            except json.JSONDecodeError:
                data = None
            if data:
                _render_tracked_metrics(data)


def _render_tracked_metrics(data: dict):
    """渲染效果追踪摘要。"""
    assessment = data.get("assessment")
    pos = data.get("positive_signals", 0)
    neg = data.get("negative_signals", 0)
    skipped = data.get("skipped_negative", 0)
    crs = data.get("composite_score_avg")

    badge = _assessment_badge(assessment)
    st.divider()

    cols = st.columns(4)
    with cols[0]:
        st.metric("评估", badge)
    with cols[1]:
        st.metric("正向信号", pos)
    with cols[2]:
        st.metric("负向信号", neg)
    with cols[3]:
        crs_str = f"{crs:.3f}" if crs is not None else "—"
        st.metric("复合恢复评分", crs_str)

    if skipped:
        st.caption(f"注：{skipped} 个负向信号因 detected contaminated 日被排除")

    details = data.get("details", [])
    if details:
        with st.expander("详细追踪记录"):
            for d in details:
                st.markdown(f"- {d}")


def _render_goal_progress():
    """渲染各活跃目标的进度快照（含30天趋势、优先级、达成判定）。"""
    from superhealth.goals.manager import GoalManager

    mgr = GoalManager(DEFAULT_DB_PATH)
    goal_progress = load_recent_goal_progress(days=30)

    with get_conn(DEFAULT_DB_PATH) as conn:
        goals = query_active_goals(conn)

    if not goals:
        st.info('当前无活跃目标。请前往侧边栏"阶段目标"页面新增。')
        _render_goal_history(mgr)
        return

    direction_labels = {"decrease": "降低", "increase": "提升", "stabilize": "稳定"}

    for goal in goals:
        gid = goal["id"]
        df = goal_progress.get(gid, pd.DataFrame())

        spec = METRIC_REGISTRY.get(goal["metric_key"])
        metric_label = spec.label if spec else goal["metric_key"]

        with st.container(border=True):
            # 标题行：名称 + 指标 + 方向
            st.markdown(
                f"**{goal['name']}**  "
                f"（{metric_label} · {direction_labels.get(goal['direction'], goal['direction'])}）"
            )
            if goal.get("baseline_value") is not None and goal.get("target_value") is not None:
                st.caption(f"基线 {goal['baseline_value']:.1f} → 目标 {goal['target_value']:.1f}")

            # metric 卡片：最新值 + 相比基线变化 + 进度
            col1, col2, col3 = st.columns([3, 2, 2])
            if not df.empty and len(df) >= 1:
                latest = df.iloc[-1]
                current = latest["current_value"]
                baseline = goal.get("baseline_value")
                pct = latest["progress_pct"]

                with col1:
                    delta = None
                    if baseline is not None and current is not None:
                        delta = round(current - baseline, 2)
                    st.metric(
                        label="当前值",
                        value=f"{current:.1f}" if current is not None else "—",
                        delta=delta,
                    )

                with col2:
                    st.metric(
                        label="进度",
                        value=f"{pct:.0f}%" if pct is not None else "—",
                    )

                with col3:
                    if pct is not None and pct < 0:
                        st.error("方向走反")
                    elif pct is not None:
                        st.progress(min(pct / 100.0, 1.0))
                    else:
                        st.caption("待评估")

            # 30天趋势图
            if not df.empty and len(df) >= 2:
                fig = go.Figure()
                fig.add_trace(
                    go.Scatter(
                        x=df["date"].astype(str),
                        y=df["current_value"],
                        mode="lines+markers",
                        name="当前值",
                        line=dict(width=2),
                    )
                )
                if goal.get("baseline_value") is not None:
                    fig.add_hline(
                        y=goal["baseline_value"],
                        line_dash="dash",
                        line_color="gray",
                        annotation_text="基线",
                    )
                if goal.get("target_value") is not None:
                    fig.add_hline(
                        y=goal["target_value"],
                        line_dash="dash",
                        line_color="green",
                        annotation_text="目标",
                    )
                fig.update_layout(
                    height=220,
                    margin=dict(l=40, r=20, t=20, b=30),
                    xaxis_title="",
                    yaxis_title="",
                    showlegend=False,
                )
                st.plotly_chart(fig, width="stretch")
            elif not df.empty:
                st.caption("数据点不足，无法绘制趋势图。")
            else:
                st.caption("暂无进度数据。")

    # ── 达成/异常提示 ──
    today = date.today().isoformat()
    candidates = mgr.check_achievement_candidates(today)
    if candidates:
        st.success('目标达成候选！请前往"阶段目标"页点击"达成"按钮确认。')
        for c in candidates:
            st.write(f"  {c['goal']['name']}：{c['note']}")

    off_track = mgr.check_off_track(today)
    if off_track:
        st.warning("以下目标进展缓慢，建议调整策略：")
        for o in off_track:
            st.write(f"  {o['goal']['name']}：{o['note']}")

    # ── 历史目标 ──
    _render_goal_history(mgr)


def _render_goal_history(mgr: "GoalManager"):
    """渲染历史已达成/已暂停/已放弃目标。"""
    achieved = mgr.list_goals(status="achieved")
    paused = mgr.list_goals(status="paused")
    abandoned = mgr.list_goals(status="abandoned")

    history = achieved + paused + abandoned
    if not history:
        return

    with st.expander("历史目标"):
        for g in history:
            status_icon = {"achieved": "✓", "paused": "⏸", "abandoned": "✗"}.get(g["status"], "?")
            st.write(
                f"{status_icon} {g['name']}（{g['status']}）"
                + (f" · 达成日：{g['achieved_date']}" if g.get("achieved_date") else "")
                + f" · 始于 {g['start_date']}"
            )
