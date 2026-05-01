"""阶段性目标页面：当前目标卡片、进度趋势、历史目标。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

from superhealth.goals.manager import GoalManager

DB_PATH = Path(__file__).parent.parent.parent.parent.parent / "health.db"


def _get_conn():
    from superhealth import database as db

    return db.get_conn(DB_PATH)


def render():
    st.header("阶段性目标")

    from superhealth.goals.manager import GoalManager
    from superhealth.goals.metrics import METRIC_REGISTRY

    mgr = GoalManager(DB_PATH)

    # ── 活跃目标 ──
    goals = mgr.list_goals(status="active")
    if not goals:
        st.info("暂无活跃目标。通过 CLI 添加：`python -m superhealth.goals add ...`")
        _render_history(mgr)
        return

    priority_labels = {1: "P1 主要", 2: "P2 次要", 3: "P3 辅助"}
    direction_labels = {"decrease": "降低", "increase": "提升", "stabilize": "稳定"}

    for goal in goals:
        spec = METRIC_REGISTRY.get(goal["metric_key"])
        label = spec.label if spec else goal["metric_key"]

        with st.container(border=True):
            col1, col2, col3 = st.columns([3, 2, 1])
            with col1:
                p_label = priority_labels.get(goal["priority"], f"P{goal['priority']}")
                st.subheader(f"{p_label}：{goal['name']}")
                st.caption(
                    f"{label} · {direction_labels.get(goal['direction'], goal['direction'])}"
                )
            with col2:
                if goal.get("baseline_value") is not None and goal.get("target_value") is not None:
                    st.metric(
                        "基线 → 目标",
                        f"{goal['baseline_value']:.1f} → {goal['target_value']:.1f}",
                    )
            with col3:
                # 获取最新进度
                progress = mgr.get_goal_progress(goal["id"], days=1)
                if progress and progress[0].get("progress_pct") is not None:
                    pct = progress[0]["progress_pct"]
                    st.metric("进度", f"{pct:.0f}%")
                    if pct < 0:
                        st.error("方向走反")
                    else:
                        st.progress(max(0.0, min(pct / 100.0, 1.0)))
                else:
                    st.metric("进度", "N/A")

            # 趋势图
            _render_goal_trend(mgr, goal)

    # ── 达成/异常提示 ──
    today = date.today().isoformat()
    candidates = mgr.check_achievement_candidates(today)
    if candidates:
        st.success("目标达成候选！请在 CLI 确认：`python -m superhealth.goals achieve <id>`")
        for c in candidates:
            st.write(f"  {c['goal']['name']}：{c['note']}")

    off_track = mgr.check_off_track(today)
    if off_track:
        st.warning("以下目标进展缓慢，建议调整策略：")
        for o in off_track:
            st.write(f"  {o['goal']['name']}：{o['note']}")

    # ── 历史目标 ──
    _render_history(mgr)


def _render_goal_trend(mgr: "GoalManager", goal: dict):
    """渲染目标进度趋势图。"""
    progress = mgr.get_goal_progress(goal["id"], days=30)
    if len(progress) < 2:
        return

    dates = [p["date"] for p in reversed(progress)]
    values = [p["current_value"] for p in reversed(progress)]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=values,
            mode="lines+markers",
            name="当前值",
            line=dict(width=2),
        )
    )

    # 基线和目标参考线
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
        height=250,
        margin=dict(l=40, r=20, t=20, b=30),
        xaxis_title="",
        yaxis_title="",
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_history(mgr: "GoalManager"):
    """渲染历史已达成/已暂停目标。"""
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
                + f" · {g['start_date']} ~ {g.get('target_date', '—')}"
            )
