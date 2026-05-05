"""目标闭环组合页。"""

from __future__ import annotations

import streamlit as st

from superhealth.dashboard.views import (
    experiment_page,
    goals_page,
    historical_review,
    preferences_page,
)


def render() -> None:
    st.header("目标闭环")
    view = st.radio(
        "目标闭环视图",
        ["阶段目标", "实验追踪", "执行回顾", "个人偏好"],
        horizontal=True,
        label_visibility="collapsed",
        key="goal_loop_view",
    )

    if view == "阶段目标":
        goals_page.render()
    elif view == "实验追踪":
        experiment_page.render()
    elif view == "执行回顾":
        historical_review.render()
    else:
        preferences_page.render()
