"""个人偏好页面：展示策略学习引擎从运动反馈中学到的偏好。"""

from __future__ import annotations

import streamlit as st

from superhealth.dashboard.data_loader import load_learned_preferences

# 显示名 → 实际值映射
TYPE_OPTIONS = {
    "全部": None,
    "运动类型偏好": "exercise_type",
    "场景化运动推荐": "context_exercise",
    "强度与时长偏好": "intensity",
    "运动时间偏好": "timing",
    "恢复特征": "recovery_pattern",
    "实验建议": "experiment_suggestion",
}

# 反查：preference_type → 中文标签（用于分组展示）
TYPE_LABELS = {v: k for k, v in TYPE_OPTIONS.items() if v is not None}

# 内部数据类型，不在个人偏好页面展示
_HIDDEN_TYPES = {"goal_interventions", "active_experiment"}

STATUS_OPTIONS = {
    "全部（排除已回退）": "all_exclude_reverted",
    "活跃 + 已确认": "active",
    "仅已确认": "committed",
    "仅已回退": "reverted",
}

STATUS_BADGES = {
    "active": "🟡 活跃",
    "committed": "🟢 已确认",
    "reverted": "🔴 已回退",
}


def render():
    st.header("个人偏好")

    type_label = st.selectbox("偏好类别", list(TYPE_OPTIONS.keys()))
    status_label = st.selectbox("状态", list(STATUS_OPTIONS.keys()))
    min_confidence = st.slider("最低置信度", 0.0, 1.0, 0.0, step=0.1)

    pref_type = TYPE_OPTIONS[type_label]
    status_mode = STATUS_OPTIONS[status_label]

    # ── 加载数据 ──
    exclude_status = "reverted" if status_mode in ("all_exclude_reverted", "active") else None
    df = load_learned_preferences(preference_type=pref_type, status=exclude_status)
    has_any_visible_preferences = _has_any_visible_preferences()

    if status_mode == "reverted":
        df_all = load_learned_preferences(preference_type=pref_type, status=None)
        df = df_all[df_all["status"] == "reverted"] if not df_all.empty else df_all
    elif status_mode == "committed" and not df.empty:
        df = df[df["status"] == "committed"]

    # 置信度过滤
    if not df.empty and min_confidence > 0:
        df = df[df["confidence_score"] >= min_confidence]

    # ── 过滤隐藏类型（统计与展示保持一致）──
    if not df.empty:
        df = df[~df["preference_type"].isin(_HIDDEN_TYPES)]

    # ── 统计栏 ──
    if df.empty:
        if has_any_visible_preferences:
            st.info(
                "当前筛选条件下没有匹配的个人偏好。可降低最低置信度，或切换偏好类别、状态后再查看。"
            )
        else:
            st.info(
                "个人偏好还在学习中：策略学习至少需要 8 条运动反馈才会启动，且各偏好维度还需要足够的有效运动与效果追踪样本。请继续积累数据；样本足够后每日流水线会自动更新，也可运行 `python -m superhealth.feedback.strategy_learner` 手动分析。"
            )
    else:
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("偏好总数", len(df))
        with col2:
            high_conf = len(df[df["confidence_score"] >= 0.8])
            st.metric("高置信度 (≥0.8)", high_conf)
        with col3:
            status_counts = df["status"].value_counts().to_dict()
            st.metric(
                "状态分布",
                " / ".join(f"{STATUS_BADGES.get(s, s)} {c}" for s, c in status_counts.items()),
            )

        st.divider()

        # ── 按类别分组展示 ──
        for ptype, group in df.groupby("preference_type"):
            label = TYPE_LABELS.get(ptype, ptype)
            with st.expander(f"{label}（{len(group)} 条）", expanded=True):
                for _, row in group.iterrows():
                    _render_preference_card(row)



def _has_any_visible_preferences() -> bool:
    """是否存在页面会展示的偏好类型，用于区分空数据和筛选无结果。"""
    all_df = load_learned_preferences(preference_type=None, status=None)
    if all_df.empty:
        return False
    return not all_df[~all_df["preference_type"].isin(_HIDDEN_TYPES)].empty


def _render_preference_card(row):
    """渲染单条偏好。"""
    key_label = row["preference_key"].replace("_", " ").title()
    status = row.get("status", "active")
    badge = STATUS_BADGES.get(status, status)
    conf = row["confidence_score"]
    evidence = row.get("evidence_count", 0)
    updated = row.get("last_updated", "")

    # 置信度颜色
    if conf >= 0.8:
        conf_color = "green"
    elif conf >= 0.5:
        conf_color = "orange"
    else:
        conf_color = "red"

    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown(f"**{key_label}** → {row['preference_value']}")
    with col2:
        st.markdown(
            f"<span style='color:{conf_color}; font-weight:bold'>{conf:.0%}</span>",
            unsafe_allow_html=True,
        )

    st.progress(conf)
    caption_parts = [badge, f"证据: {evidence} 条"]
    if updated:
        if hasattr(updated, "strftime"):
            caption_parts.append(f"更新: {updated.strftime('%Y-%m-%d')}")
        else:
            caption_parts.append(f"更新: {updated}")
    st.caption(" · ".join(caption_parts))
