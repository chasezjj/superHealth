"""趋势分析组合页。"""

from __future__ import annotations

import streamlit as st

from superhealth.dashboard.views import correlations, trends


def render() -> None:
    st.header("趋势分析")
    view = st.radio(
        "趋势分析视图",
        ["状态趋势", "相关性分析"],
        horizontal=True,
        label_visibility="collapsed",
        key="analysis_view",
    )

    if view == "状态趋势":
        trends.render()
    else:
        correlations.render()
