"""数据管理组合页。"""

from __future__ import annotations

import streamlit as st

from superhealth.dashboard.views import garmin_data, upload


def render() -> None:
    st.header("数据管理")
    view = st.radio(
        "数据管理视图",
        ["上传文档", "Garmin 数据"],
        horizontal=True,
        label_visibility="collapsed",
        key="data_management_view",
    )

    if view == "上传文档":
        upload.render()
    else:
        garmin_data.render()
