"""P3: 化验趋势（统一视图 - 合并门诊化验 + 年度体检）。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from superhealth.dashboard.components.charts import (
    chart_medical_timeline,
    chart_unified_lab_trend,
)
from superhealth.dashboard.data_loader import (
    load_annual_checkups,
    load_eye_exams,
    load_lab_results,
    load_multiple_unified_trends,
    load_unified_lab_trends,
)
from superhealth.database import _LIVER_KIDNEY_METRICS

# 指标显示配置（用于Dashboard）
METRIC_DISPLAY_CONFIG = {
    "uric_acid": {
        "label": "尿酸",
        "icon": "🧪",
        "color": "#E05C5C",
        "priority": 1,
    },
    "creatinine": {
        "label": "肌酐",
        "icon": "🫘",
        "color": "#4F8EF7",
        "priority": 2,
    },
    "urea": {
        "label": "尿素",
        "icon": "💧",
        "color": "#5CA8E0",
        "priority": 3,
    },
    "ldl_c": {
        "label": "低密度脂蛋白 (LDL-C)",
        "icon": "🩸",
        "color": "#F7A84F",
        "priority": 4,
    },
    "triglyceride": {
        "label": "甘油三酯 (TG)",
        "icon": "🍔",
        "color": "#F7D94F",
        "priority": 5,
    },
    "hdl_c": {
        "label": "高密度脂蛋白 (HDL-C)",
        "icon": "🛡️",
        "color": "#5CB85C",
        "priority": 6,
    },
    "total_cholesterol": {
        "label": "总胆固醇",
        "icon": "📊",
        "color": "#C875D1",
        "priority": 7,
    },
    "alt": {
        "label": "谷丙转氨酶 (ALT)",
        "icon": "🔥",
        "color": "#E74C3C",
        "priority": 8,
    },
    "ast": {
        "label": "谷草转氨酶 (AST)",
        "icon": "⚡",
        "color": "#E67E22",
        "priority": 9,
    },
    "ggt": {
        "label": "γ-谷氨酰转肽酶 (GGT)",
        "icon": "🍺",
        "color": "#9B59B6",
        "priority": 10,
    },
    "cystatin_c": {
        "label": "胱抑素C",
        "icon": "🔬",
        "color": "#1ABC9C",
        "priority": 11,
    },
}


def render():
    st.header("化验趋势")
    st.caption("数据来源于门诊化验记录和年度体检，自动合并显示")

    # 加载数据
    df_lab = load_lab_results()
    df_eye = load_eye_exams()
    df_checkup = load_annual_checkups()

    # 时间范围选择
    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        time_range = st.selectbox(
            "时间范围",
            options=["全部", "1年", "3年", "5年", "10年"],
            index=3,  # 默认5年
        )
    years_map = {"全部": 50, "1年": 1, "3年": 3, "5年": 5, "10年": 10}
    years = years_map[time_range]

    with col2:
        view_mode = st.radio(
            "显示模式",
            options=["合并视图", "分指标查看", "多指标对比"],
            horizontal=True,
            index=0,
        )

    # 根据显示模式渲染不同视图
    if view_mode == "合并视图":
        _render_merged_view(years)
    elif view_mode == "分指标查看":
        _render_single_metric_view(years)
    else:  # 多指标对比
        _render_multi_metric_view(years)

    # 眼压（独立显示）
    st.divider()
    col_eye1, col_eye2 = st.columns([1, 3])
    with col_eye1:
        show_eye = st.checkbox("显示眼压趋势", value=True)
    with col_eye2:
        if show_eye and not df_eye.empty:
            import plotly.graph_objects as go

            fig = go.Figure()
            fig.add_hrect(
                y0=0,
                y1=21,
                fillcolor="rgba(100,200,100,0.08)",
                line_width=0,
                annotation_text="正常 <21 mmHg",
                annotation_position="top left",
            )
            if df_eye["od_iop"].notna().any():
                fig.add_trace(
                    go.Scatter(
                        x=df_eye["date"],
                        y=df_eye["od_iop"],
                        name="右眼 IOP",
                        mode="lines+markers",
                        line=dict(color="#4F8EF7"),
                    )
                )
            if df_eye["os_iop"].notna().any():
                fig.add_trace(
                    go.Scatter(
                        x=df_eye["date"],
                        y=df_eye["os_iop"],
                        name="左眼 IOP",
                        mode="lines+markers",
                        line=dict(color="#F7A84F"),
                    )
                )
            fig.update_layout(
                title="眼压（IOP）", height=280, yaxis_title="mmHg", hovermode="x unified"
            )
            st.plotly_chart(fig, width="stretch")

    # 就医时间线
    st.divider()
    st.subheader("📅 就医时间线")
    fig_tl = chart_medical_timeline(df_checkup, df_eye, df_lab)
    st.plotly_chart(fig_tl, width="stretch")

    # 数据说明
    with st.expander("ℹ️ 数据说明"):
        st.markdown("""
        **数据来源说明：**
        - 🔵 **门诊化验**（菱形标记）：来自门诊化验复查
        - 🟠 **年度体检**（圆形标记）：来自每年定期体检报告

        **指标参考范围：**
        | 指标 | 正常范围 | 单位 |
        |------|----------|------|
        | 尿酸 | 208-428 | μmol/L |
        | 肌酐 | 44-133 | μmol/L |
        | 尿素 | 2.6-7.5 | mmol/L |
        | LDL-C | <3.4 | mmol/L |
        | 甘油三酯 | <1.7 | mmol/L |
        | HDL-C | >1.0 | mmol/L |
        | ALT | <50 | U/L |
        | AST | <40 | U/L |

        **提示：** 异常值会用红色圆圈标注，参考范围用绿色背景显示。
        """)



def _render_merged_view(years: int):
    """合并视图：三列显示关键指标。"""
    # 加载关键指标数据
    key_metrics = [
        "uric_acid",
        "creatinine",
        "urea",
        "cystatin_c",
        "alt",
        "ast",
        "ggt",
        "ldl_c",
        "triglyceride",
        "total_cholesterol",
    ]
    data = {}
    for metric in key_metrics:
        df = load_unified_lab_trends(metric, years)
        if not df.empty:
            data[metric] = df

    if not data:
        st.warning("暂无化验数据。")
        return

    # 计算全局时间范围，让所有图表 x 轴对齐
    all_dates = pd.concat([df["date"] for df in data.values()])
    x_min = all_dates.min()
    x_max = all_dates.max()
    span_years = (x_max - x_min).days / 365
    # x 轴范围：从起始年前一年末到结束年年末，保证起始年刻度不被裁剪
    range_start = pd.Timestamp(f"{x_min.year - 1}-12-31")
    range_end = pd.Timestamp(f"{x_max.year + 1}-01-01")
    x_range = (range_start, range_end)
    # 跨度>6年时隔年显示刻度，从起始年开始
    step = 2 if span_years > 6 else 1
    x_tickvals = [pd.Timestamp(f"{y}-07-01") for y in range(x_min.year, x_max.year + 1, step)]

    # 分三列显示
    col1, col2, col3 = st.columns(3)

    with col1:
        st.subheader("🫘 肾功能指标")
        if "uric_acid" in data:
            config = _LIVER_KIDNEY_METRICS["uric_acid"]
            display = METRIC_DISPLAY_CONFIG["uric_acid"]
            fig = chart_unified_lab_trend(
                data["uric_acid"],
                title=f"{display['icon']} {display['label']}",
                ref_low=config.get("ref_low"),
                ref_high=config.get("ref_high"),
                unit=config.get("unit", ""),
                x_range=x_range,
                x_tickvals=x_tickvals,
            )
            st.plotly_chart(fig, use_container_width=True)

        if "creatinine" in data:
            config = _LIVER_KIDNEY_METRICS["creatinine"]
            display = METRIC_DISPLAY_CONFIG["creatinine"]
            fig = chart_unified_lab_trend(
                data["creatinine"],
                title=f"{display['icon']} {display['label']}",
                ref_low=config.get("ref_low"),
                ref_high=config.get("ref_high"),
                unit=config.get("unit", ""),
                x_range=x_range,
                x_tickvals=x_tickvals,
            )
            st.plotly_chart(fig, use_container_width=True)

        if "urea" in data:
            config = _LIVER_KIDNEY_METRICS["urea"]
            display = METRIC_DISPLAY_CONFIG["urea"]
            fig = chart_unified_lab_trend(
                data["urea"],
                title=f"{display['icon']} {display['label']}",
                ref_low=config.get("ref_low"),
                ref_high=config.get("ref_high"),
                unit=config.get("unit", ""),
                x_range=x_range,
                x_tickvals=x_tickvals,
            )
            st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("🔥 肝功能指标")
        liver_metrics = ["alt", "ast", "ggt"]
        for metric in liver_metrics:
            if metric in data:
                config = _LIVER_KIDNEY_METRICS[metric]
                display = METRIC_DISPLAY_CONFIG[metric]
                fig = chart_unified_lab_trend(
                    data[metric],
                    title=f"{display['icon']} {display['label']}",
                    ref_low=config.get("ref_low"),
                    ref_high=config.get("ref_high"),
                    unit=config.get("unit", ""),
                    x_range=x_range,
                    x_tickvals=x_tickvals,
                )
                st.plotly_chart(fig, use_container_width=True)

    with col3:
        st.subheader("🩸 血脂指标")
        if "ldl_c" in data:
            config = _LIVER_KIDNEY_METRICS["ldl_c"]
            display = METRIC_DISPLAY_CONFIG["ldl_c"]
            fig = chart_unified_lab_trend(
                data["ldl_c"],
                title=f"{display['icon']} {display['label']}",
                ref_low=config.get("ref_low"),
                ref_high=config.get("ref_high"),
                unit=config.get("unit", ""),
                x_range=x_range,
                x_tickvals=x_tickvals,
            )
            st.plotly_chart(fig, use_container_width=True)

        if "triglyceride" in data:
            config = _LIVER_KIDNEY_METRICS["triglyceride"]
            display = METRIC_DISPLAY_CONFIG["triglyceride"]
            fig = chart_unified_lab_trend(
                data["triglyceride"],
                title=f"{display['icon']} {display['label']}",
                ref_low=config.get("ref_low"),
                ref_high=config.get("ref_high"),
                unit=config.get("unit", ""),
                x_range=x_range,
                x_tickvals=x_tickvals,
            )
            st.plotly_chart(fig, use_container_width=True)

        if "total_cholesterol" in data:
            config = _LIVER_KIDNEY_METRICS["total_cholesterol"]
            display = METRIC_DISPLAY_CONFIG["total_cholesterol"]
            fig = chart_unified_lab_trend(
                data["total_cholesterol"],
                title=f"{display['icon']} {display['label']}",
                ref_low=config.get("ref_low"),
                ref_high=config.get("ref_high"),
                unit=config.get("unit", ""),
                x_range=x_range,
                x_tickvals=x_tickvals,
            )
            st.plotly_chart(fig, use_container_width=True)


def _render_single_metric_view(years: int):
    """分指标查看：用户选择一个指标详细查看。"""
    # 指标选择
    available_metrics = list(METRIC_DISPLAY_CONFIG.keys())
    metric_labels = [
        f"{METRIC_DISPLAY_CONFIG[m]['icon']} {METRIC_DISPLAY_CONFIG[m]['label']}"
        for m in available_metrics
    ]

    selected_idx = st.selectbox(
        "选择指标",
        options=range(len(available_metrics)),
        format_func=lambda i: metric_labels[i],
        index=0,
    )
    selected_metric = available_metrics[selected_idx]

    # 加载数据
    df = load_unified_lab_trends(selected_metric, years)

    if df.empty:
        st.warning(f"暂无 {METRIC_DISPLAY_CONFIG[selected_metric]['label']} 数据。")
        return

    # 显示大图
    config = _LIVER_KIDNEY_METRICS[selected_metric]
    display = METRIC_DISPLAY_CONFIG[selected_metric]

    fig = chart_unified_lab_trend(
        df,
        title=f"{display['icon']} {display['label']} 趋势",
        ref_low=config.get("ref_low"),
        ref_high=config.get("ref_high"),
        unit=config.get("unit", ""),
    )
    st.plotly_chart(fig, use_container_width=True)

    # 统计信息
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("最新值", f"{df.iloc[-1]['value']:.2f}")
    with col2:
        st.metric("平均值", f"{df['value'].mean():.2f}")
    with col3:
        delta = df.iloc[-1]["value"] - df.iloc[0]["value"] if len(df) > 1 else 0
        st.metric("总变化", f"{delta:+.2f}")
    with col4:
        abnormal_count = df["is_abnormal"].sum() if "is_abnormal" in df.columns else 0
        st.metric("异常记录", f"{int(abnormal_count)}/{len(df)}")

    # 数据表格
    with st.expander("📋 查看原始数据"):
        display_df = df.copy()
        display_df["date"] = display_df["date"].dt.strftime("%Y-%m-%d")
        source_label = {
            "lab": "门诊化验", "outpatient": "门诊病历",
            "annual_checkup": "年度体检", "imaging": "影像报告",
            "discharge": "出院小结", "other": "其他",
        }
        display_df["source"] = display_df["source"].map(
            lambda s: source_label.get(s, s)
        )
        st.dataframe(display_df, use_container_width=True)


def _render_multi_metric_view(years: int):
    """多指标对比：同时显示多个指标。"""
    # 让用户选择要对比的指标
    available_metrics = list(METRIC_DISPLAY_CONFIG.keys())
    default_selection = ["uric_acid", "creatinine"]

    selected = st.multiselect(
        "选择要对比的指标（最多4个）",
        options=available_metrics,
        default=default_selection,
        format_func=lambda x: (
            f"{METRIC_DISPLAY_CONFIG[x]['icon']} {METRIC_DISPLAY_CONFIG[x]['label']}"
        ),
        max_selections=4,
    )

    if not selected:
        st.info("请至少选择一个指标进行对比")
        return

    if len(selected) == 1:
        # 只选一个时，直接显示单指标视图
        _render_single_metric_view(years)
        return

    # 加载数据
    data = load_multiple_unified_trends(selected, years)

    # 创建对比图表
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    # 每个指标一个子图
    fig = make_subplots(
        rows=len(selected),
        cols=1,
        subplot_titles=[
            f"{METRIC_DISPLAY_CONFIG[m]['icon']} {METRIC_DISPLAY_CONFIG[m]['label']}"
            for m in selected
        ],
        vertical_spacing=0.1,
    )

    for i, metric in enumerate(selected, 1):
        df = data.get(metric, pd.DataFrame())
        if df.empty:
            continue

        config = _LIVER_KIDNEY_METRICS[metric]
        color = METRIC_DISPLAY_CONFIG[metric]["color"]

        # 分离不同数据源
        lab_data = df[~df["source"].isin(["annual_checkup"])]
        checkup_data = df[df["source"] == "annual_checkup"]

        # 年度体检
        if not checkup_data.empty:
            fig.add_trace(
                go.Scatter(
                    x=checkup_data["date"],
                    y=checkup_data["value"],
                    mode="lines+markers",
                    name=f"{METRIC_DISPLAY_CONFIG[metric]['label']} - 体检",
                    line=dict(color=color, width=2),
                    marker=dict(size=10, symbol="circle"),
                    showlegend=False,
                ),
                row=i,
                col=1,
            )

        # 门诊化验
        if not lab_data.empty:
            fig.add_trace(
                go.Scatter(
                    x=lab_data["date"],
                    y=lab_data["value"],
                    mode="lines+markers",
                    name=f"{METRIC_DISPLAY_CONFIG[metric]['label']} - 门诊",
                    line=dict(color=color, width=2, dash="dot"),
                    marker=dict(size=9, symbol="diamond"),
                    showlegend=False,
                ),
                row=i,
                col=1,
            )

        # 参考范围
        if config.get("ref_high"):
            y0 = config.get("ref_low", 0)
            fig.add_hrect(
                y0=y0,
                y1=config["ref_high"],
                fillcolor="rgba(100,200,100,0.08)",
                line_width=0,
                row=i,
                col=1,
            )

        # Y轴标题
        fig.update_yaxes(title_text=config.get("unit", ""), row=i, col=1)

    fig.update_layout(
        height=300 * len(selected),
        hovermode="x unified",
        title="多指标趋势对比",
    )
    st.plotly_chart(fig, use_container_width=True)
