"""阶段目标页面：当前目标卡片、增删改操作、进度趋势、历史目标。"""

from __future__ import annotations

from datetime import date

import plotly.graph_objects as go
import streamlit as st

from superhealth.database import DEFAULT_DB_PATH as DB_PATH
from superhealth.goals.manager import GoalManager
from superhealth.goals.metrics import METRIC_REGISTRY

_DIRECTION_LABELS = {"decrease": "降低", "increase": "提升", "stabilize": "稳定"}

_GOAL_BASELINE_HELP = (
    "自动计算基线时，系统取创建目标当天之前7天（不含创建日）的同类指标。"
    "7日均值类指标会先按天聚合，再取这些日均值的平均；至少需要3天可用数据。"
    "填 0.0 表示自动计算；手动填写时会直接使用你输入的值作为基线。"
)

_GOAL_METRIC_HELP = (
    "目标指标决定当前值的计算方式。"
    "名称里带“7日均值”的指标，当前值是快照日期最近7天（含当天）的日均值平均。"
    "血压、体重、体脂等 vitals 指标会先把同一天多次记录合并为日均值。"
)

_GOAL_ACTION_HELPS = {
    "achieved": (
        "目标已经达到时使用。目标会进入历史目标，达成日期会记录为今天；"
        "绑定的进行中实验会自动结案为 completed，草稿实验会清理；历史进度快照保留。"
    ),
    "paused": (
        "暂时不继续推进但以后可能恢复时使用。目标会进入历史目标；"
        "绑定的进行中实验会自动回退为 reverted，草稿实验会清理；历史进度快照保留。"
    ),
    "abandoned": (
        "确认不再追这个目标时使用。目标会进入历史目标；"
        "绑定的进行中实验会自动回退为 reverted，草稿实验会清理；历史进度快照保留。"
    ),
    "deleted": (
        "只把目标从当前视图归档，不做物理删除。目标状态变为 deleted，"
        "历史进度快照会保留；如果还有未结案实验，需要先处理实验。"
    ),
}


def _metric_options() -> list[str]:
    """返回 'label (key)' 格式的指标选项列表。"""
    return [f"{spec.label} ({key})" for key, spec in METRIC_REGISTRY.items()]


def _parse_metric_option(option: str) -> str:
    """从 'label (key)' 格式中提取 key。"""
    return option.rsplit("(", 1)[-1].rstrip(")")


def render():
    st.header("阶段目标")

    mgr = GoalManager(DB_PATH)

    _render_add_form(mgr)
    _render_snapshot_tools(mgr)

    goals = mgr.list_goals(status="active")
    if not goals:
        st.info('暂无活跃目标，点击上方"新增目标"创建第一个。')
        _render_history(mgr)
        return

    for goal in goals:
        spec = METRIC_REGISTRY.get(goal["metric_key"])
        label = spec.label if spec else goal["metric_key"]

        with st.container(border=True):
            col1, col2, col3 = st.columns([3, 2, 1])
            with col1:
                st.subheader(goal['name'])
                st.caption(
                    f"{label} · {_DIRECTION_LABELS.get(goal['direction'], goal['direction'])}"
                )
            with col2:
                if goal.get("baseline_value") is not None and goal.get("target_value") is not None:
                    st.metric(
                        "基线 → 目标",
                        f"{goal['baseline_value']:.1f} → {goal['target_value']:.1f}",
                    )
            with col3:
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

            _render_goal_trend(mgr, goal)
            _render_goal_actions(mgr, goal)

    # ── 达成/异常提示 ──
    today = date.today().isoformat()
    candidates = mgr.check_achievement_candidates(today)
    if candidates:
        st.success('目标达成候选！可点击目标卡片上的"达成"按钮确认。')
        for c in candidates:
            st.write(f"  {c['goal']['name']}：{c['note']}")

    off_track = mgr.check_off_track(today)
    if off_track:
        st.warning("以下目标进展缓慢，建议调整策略：")
        for o in off_track:
            st.write(f"  {o['goal']['name']}：{o['note']}")

    _render_history(mgr)


def _render_add_form(mgr: GoalManager):
    """新增目标表单。同一时间只能有一个活跃目标。"""
    if mgr.list_goals(status="active"):
        st.info("当前已有活跃目标，需先达成、暂停、废弃或归档后才能新建。下一步可以去实验追踪里选择适合的行动建议。")
        return
    with st.expander("＋ 新增目标"):
        with st.form("add_goal_form"):
            metric_option = st.selectbox(
                "追踪指标 *",
                options=_metric_options(),
                key="add_goal_metric",
                help=_GOAL_METRIC_HELP,
            )
            direction_label = st.selectbox(
                "方向 *",
                options=list(_DIRECTION_LABELS.values()),
                key="add_goal_direction",
                help="降低表示数值下降是好事；提升表示数值上升是好事；稳定表示保持在基线附近是好事。",
            )
            target_value = st.number_input(
                "目标值 *",
                value=0.0,
                step=0.1,
                key="add_goal_target",
                help="进度会用当前值、基线值、目标值和方向一起计算。",
            )
            baseline_value = st.number_input(
                "基线值（留空则自动计算）",
                value=0.0,
                step=0.1,
                help=_GOAL_BASELINE_HELP,
                key="add_goal_baseline",
            )

            submitted = st.form_submit_button("创建目标")
            if submitted:
                direction = [k for k, v in _DIRECTION_LABELS.items() if v == direction_label][0]
                metric_key = _parse_metric_option(metric_option)
                spec = METRIC_REGISTRY.get(metric_key)
                name = f"{spec.label if spec else metric_key} {_DIRECTION_LABELS[direction]}"
                bv = baseline_value if baseline_value != 0.0 else None
                try:
                    goal_id = mgr.add_goal(
                        name=name,
                        metric_key=metric_key,
                        direction=direction,
                        target=target_value,
                        baseline_value=bv,
                    )
                    for _k in (
                        "add_goal_direction",
                        "add_goal_metric",
                        "add_goal_target",
                        "add_goal_baseline",
                    ):
                        st.session_state.pop(_k, None)
                    st.success(f"目标已创建（id={goal_id}）")
                    st.rerun()
                except ValueError as e:
                    msg = str(e)
                    if "数据不足" in msg or "baseline" in msg.lower():
                        st.error('近 7 天该指标数据不足，无法自动计算基线。请在"基线值"字段手动填入一个参考值后重新提交。')
                    else:
                        st.error(msg)


def _render_goal_actions(mgr: GoalManager, goal: dict):
    """每个活跃目标的操作按钮行。"""
    gid = goal["id"]
    cols = st.columns([1, 1, 1, 1, 5])

    with cols[0]:
        if st.button(
            "达成",
            key=f"btn_achieve_{gid}",
            use_container_width=True,
            help=_GOAL_ACTION_HELPS["achieved"],
        ):
            mgr.update_status(gid, "achieved")
            st.rerun()

    with cols[1]:
        if st.button(
            "暂停",
            key=f"btn_pause_{gid}",
            use_container_width=True,
            help=_GOAL_ACTION_HELPS["paused"],
        ):
            mgr.update_status(gid, "paused")
            st.rerun()

    with cols[2]:
        if st.button(
            "废弃",
            key=f"btn_abandon_{gid}",
            use_container_width=True,
            help=_GOAL_ACTION_HELPS["abandoned"],
        ):
            mgr.update_status(gid, "abandoned")
            st.rerun()

    with cols[3]:
        confirm_key = f"confirm_delete_{gid}"
        blocking = mgr.get_blocking_experiments(gid)
        if blocking:
            st.button(
                "归档",
                key=f"btn_delete_{gid}",
                use_container_width=True,
                disabled=True,
                help=(
                    _GOAL_ACTION_HELPS["deleted"]
                    + " 当前存在未结案的绑定实验，请先到实验追踪页取消或删除后再来归档。"
                ),
            )
            # 状态残留清理
            st.session_state.pop(confirm_key, None)
        elif st.session_state.get(confirm_key):
            if st.button(
                "确认归档？",
                key=f"btn_confirm_del_{gid}",
                use_container_width=True,
                type="primary",
                help=_GOAL_ACTION_HELPS["deleted"],
            ):
                try:
                    mgr.delete_goal(gid)
                except ValueError as e:
                    st.error(str(e))
                st.session_state.pop(confirm_key, None)
                st.rerun()
        else:
            if st.button(
                "归档",
                key=f"btn_delete_{gid}",
                use_container_width=True,
                help=_GOAL_ACTION_HELPS["deleted"],
            ):
                st.session_state[confirm_key] = True
                st.rerun()

    if blocking:
        names = "、".join(f"{e['name']}（{e['status']}）" for e in blocking)
        st.warning(f"该目标仍有未结案的绑定实验：{names}。请先到「实验追踪」页取消或删除后再归档目标。")


def _render_snapshot_tools(mgr: GoalManager):
    """渲染目标快照维护工具。"""
    with st.expander("目标快照维护", expanded=False):
        col_days, col_btn = st.columns([1, 2])
        with col_days:
            days = st.number_input(
                "重建天数",
                min_value=1,
                max_value=180,
                value=30,
                step=1,
                key="goal_snapshot_rebuild_days",
            )
        with col_btn:
            st.write("")
            st.write("")
            if st.button("重建目标快照", key="btn_rebuild_goal_snapshots"):
                today = date.today().isoformat()
                mgr.track_daily_progress(today, backfill_days=int(days))
                st.success(f"已重建最近 {int(days)} 天目标快照")
                st.rerun()


def _render_goal_trend(mgr: GoalManager, goal: dict):
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


def _render_history(mgr: GoalManager):
    """渲染历史已达成/已暂停/已废弃目标，含删除操作。"""
    achieved = mgr.list_goals(status="achieved")
    paused = mgr.list_goals(status="paused")
    abandoned = mgr.list_goals(status="abandoned")
    deleted = mgr.list_goals(status="deleted")

    history = achieved + paused + abandoned + deleted
    if not history:
        return

    with st.expander("历史目标"):
        for g in history:
            gid = g["id"]
            status_icon = {"achieved": "✓", "paused": "⏸", "abandoned": "✗", "deleted": "归档"}.get(g["status"], "?")
            label = (
                f"{status_icon} {g['name']}（{g['status']}）"
                + (f" · 达成日：{g['achieved_date']}" if g.get("achieved_date") else "")
                + f" · 始于 {g['start_date']}"
            )
            blocking = mgr.get_blocking_experiments(gid)
            col_text, col_del = st.columns([8, 1])
            with col_text:
                st.write(label)
                if blocking:
                    names = "、".join(f"{e['name']}（{e['status']}）" for e in blocking)
                    st.caption(f"⚠️ 仍有未结案实验：{names}，请先到实验追踪页处理")
            with col_del:
                confirm_key = f"confirm_delete_hist_{gid}"
                if blocking:
                    st.button("归档", key=f"btn_del_hist_{gid}", disabled=True, help="存在未结案的绑定实验")
                    st.session_state.pop(confirm_key, None)
                elif st.session_state.get(confirm_key):
                    if st.button("确认", key=f"btn_confirm_del_hist_{gid}"):
                        try:
                            mgr.delete_goal(gid)
                        except ValueError as e:
                            st.error(str(e))
                        st.session_state.pop(confirm_key, None)
                        st.rerun()
                else:
                    if st.button("归档", key=f"btn_del_hist_{gid}", disabled=g["status"] == "deleted"):
                        st.session_state[confirm_key] = True
                        st.rerun()
