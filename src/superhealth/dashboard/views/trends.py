"""P2: 状态趋势。"""

from __future__ import annotations

import streamlit as st

from superhealth.analysis.trends import TrendAnalyzer
from superhealth.dashboard.components.charts import (
    chart_bp,
    chart_exercise_gantt,
    chart_hrv_bb,
    chart_stress,
    chart_weight_fat,
)
from superhealth.dashboard.data_loader import load_daily_health, load_exercises, load_vitals

RANGE_OPTIONS = {
    "最近30天": 30,
    "最近90天": 90,
    "最近半年": 180,
    "全部": 730,
}


def render():
    st.header("状态趋势")

    period_label = st.selectbox("时间范围", list(RANGE_OPTIONS.keys()), index=0)
    days = RANGE_OPTIONS[period_label]

    df_dh = load_daily_health(days)
    df_vitals = load_vitals(days)
    df_ex = load_exercises(days)

    if df_dh.empty and df_vitals.empty:
        st.warning("暂无健康数据，请确认 health.db 已同步。")
        return

    # 图1：HRV + Body Battery（含异常点标注）
    if not df_dh.empty:
        try:
            analyzer = TrendAnalyzer()
            anomalies = analyzer.detect_anomalies("hrv_avg", z_threshold=2.0)
            anomaly_dates = [a["date"] for a in anomalies]
        except Exception:
            anomaly_dates = []
        st.plotly_chart(chart_hrv_bb(df_dh, anomaly_dates=anomaly_dates), width="stretch")
    else:
        st.info("无 Garmin 数据")

    # 图2：压力指数
    if not df_dh.empty and df_dh["stress_average"].notna().any():
        st.plotly_chart(chart_stress(df_dh), width="stretch")
    else:
        st.info("无压力数据")

    # 图3：血压
    if not df_vitals.empty and df_vitals["systolic"].notna().any():
        st.plotly_chart(chart_bp(df_vitals), width="stretch")
    else:
        st.info("无血压记录")

    # 图4：体重 + 体脂率
    if not df_vitals.empty and df_vitals["weight_kg"].notna().any():
        st.plotly_chart(chart_weight_fat(df_vitals), width="stretch")
    else:
        st.info("无体重记录")

    # 图5：运动甘特图
    st.plotly_chart(chart_exercise_gantt(df_ex), width="stretch")
