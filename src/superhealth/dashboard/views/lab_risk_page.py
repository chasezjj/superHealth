"""化验与风险组合页。"""

from __future__ import annotations

import streamlit as st

from superhealth.dashboard.views import lab_results, prediction


def render() -> None:
    st.header("化验与风险")
    view = st.radio(
        "化验与风险视图",
        ["化验趋势", "风险评估"],
        horizontal=True,
        label_visibility="collapsed",
        key="lab_risk_view",
    )

    if view == "化验趋势":
        lab_results.render()
    else:
        prediction.render()
