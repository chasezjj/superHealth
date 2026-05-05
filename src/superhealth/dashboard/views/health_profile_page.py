"""健康档案组合页。"""

from __future__ import annotations

import streamlit as st

from superhealth.dashboard.views import health_record_page, profile_page


def render() -> None:
    st.header("健康档案")
    view = st.radio(
        "健康档案视图",
        ["基本信息", "病情管理"],
        horizontal=True,
        label_visibility="collapsed",
        key="health_profile_view",
    )

    if view == "基本信息":
        profile_page.render()
    else:
        health_record_page.render()
