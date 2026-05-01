"""P4: 相关性分析。"""

from __future__ import annotations

import streamlit as st

from superhealth.dashboard.data_loader import load_merged_for_correlation
from superhealth.dashboard.components.charts import chart_heatmap, chart_scatter

ALL_METRICS = [
    "心率变异", "身体电量", "睡眠时长", "压力指数",
    "静息心率", "步数", "收缩压", "舒张压",
    "体重", "体脂率", "运动时长",
]


def render():
    st.header("相关性分析")

    days = st.slider("分析时间跨度（天）", min_value=30, max_value=365, value=180, step=30)
    df = load_merged_for_correlation(days)

    if df.empty:
        st.warning("数据不足，请确认 health.db 已同步。")
        return

    # 可用列
    available = [m for m in ALL_METRICS if m in df.columns]
    if not available:
        st.warning("无可用指标列。")
        return

    # ── 热力图 ──────────────────────────────────────────────────────
    st.subheader("指标相关性热力图")
    selected_metrics = st.multiselect("选择参与热力图的指标", available, default=available)

    if len(selected_metrics) >= 2:
        corr = df[selected_metrics].corr(min_periods=3)
        st.plotly_chart(chart_heatmap(corr), width="stretch")
        with st.expander("查看相关系数表"):
            st.dataframe(corr.round(3))
    else:
        st.info("请至少选择2个指标。")

    st.divider()

    # ── 散点图 ──────────────────────────────────────────────────────
    st.subheader("自选散点图")
    col1, col2 = st.columns(2)
    with col1:
        x_col = st.selectbox("X 轴", available, index=0)
    with col2:
        y_col = st.selectbox("Y 轴", available,
                             index=min(1, len(available) - 1))

    if x_col != y_col:
        st.plotly_chart(chart_scatter(df, x_col, y_col), width="stretch")
    else:
        st.info("请选择不同的 X / Y 轴指标。")
