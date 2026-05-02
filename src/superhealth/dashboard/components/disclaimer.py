"""医疗免责声明组件 — 用于 Dashboard 各页面底部。"""

from __future__ import annotations

import streamlit as st


def render() -> None:
    """在页面底部渲染医疗免责声明。"""
    st.divider()
    st.info(
        "本内容仅为健康管理参考，不构成医疗诊断和治疗方案，疾病请及时就医。",
        icon="⚠️",
    )
