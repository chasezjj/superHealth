"""Plotly 图表封装 — 仪表盘复用的折线/散点/热力图组件。"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


# ─── 颜色常量 ─────────────────────────────────────────────────────────

COLOR_HRV = "#4F8EF7"
COLOR_BB = "#F7A84F"
COLOR_SYSTOLIC = "#E05C5C"
COLOR_DIASTOLIC = "#5CA8E0"
COLOR_PULSE = "#8E8E8E"
COLOR_WEIGHT = "#5CB85C"
COLOR_FAT = "#F7D94F"
COLOR_STRESS = "#C875D1"
COLOR_ANOMALY = "#FF3333"
REFERENCE_BAND_COLOR = "rgba(100,200,100,0.08)"


def _add_7day_avg(fig: go.Figure, df: pd.DataFrame, col: str,
                  name: str, color: str, row: int = 1, col_idx: int = 1,
                  secondary_y: bool = False):
    """在图上叠加7日移动均线（虚线）。"""
    avg = df[col].rolling(7, min_periods=1).mean()
    trace = go.Scatter(
        x=df["date"], y=avg, name=f"{name} 7日均",
        line=dict(color=color, dash="dash", width=1.5),
        opacity=0.6, showlegend=False,
    )
    # 只有 make_subplots 图才能传 row/col/secondary_y
    if secondary_y or row != 1 or col_idx != 1:
        fig.add_trace(trace, row=row, col=col_idx, secondary_y=secondary_y)
    else:
        fig.add_trace(trace)


def chart_hrv_bb(df: pd.DataFrame, anomaly_dates: list | None = None) -> go.Figure:
    """图1: HRV + Body Battery 双 Y 轴折线。"""
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    fig.add_trace(go.Scatter(
        x=df["date"], y=df["hrv_last_night_avg"],
        name="心率变异", line=dict(color=COLOR_HRV, width=2),
    ), secondary_y=False)
    _add_7day_avg(fig, df, "hrv_last_night_avg", "心率变异", COLOR_HRV)

    fig.add_trace(go.Scatter(
        x=df["date"], y=df["bb_highest"],
        name="身体电量", line=dict(color=COLOR_BB, width=2),
    ), secondary_y=True)
    _add_7day_avg(fig, df, "bb_highest", "身体电量", COLOR_BB, secondary_y=True)

    if anomaly_dates:
        for ad in anomaly_dates:
            hrv_val = df.loc[df["date"] == ad, "hrv_last_night_avg"]
            if not hrv_val.empty:
                fig.add_trace(go.Scatter(
                    x=[ad], y=[hrv_val.iloc[0]],
                    mode="markers", marker=dict(color=COLOR_ANOMALY, size=10, symbol="x"),
                    showlegend=False, name="异常",
                ), secondary_y=False)

    fig.update_layout(title="心率变异 & 身体电量趋势", hovermode="x unified", height=320)
    fig.update_yaxes(title_text="心率变异 (ms)", secondary_y=False)
    fig.update_yaxes(title_text="身体电量", secondary_y=True)
    return fig


def chart_bp(df_vitals: pd.DataFrame) -> go.Figure:
    """图2: 血压趋势（收缩压/舒张压/脉率）+ 参考区间带。"""
    fig = go.Figure()

    # 参考区间：收缩压<120，舒张压<80
    if not df_vitals.empty:
        fig.add_hrect(y0=0, y1=120, fillcolor=REFERENCE_BAND_COLOR,
                      line_width=0, annotation_text="正常收缩压", annotation_position="top left")
        fig.add_hrect(y0=0, y1=80, fillcolor="rgba(100,100,200,0.05)",
                      line_width=0)

    # 过滤掉血压为空的记录，确保连线连续
    df_bp = df_vitals.dropna(subset=["systolic"])
    if not df_bp.empty:
        fig.add_trace(go.Scatter(
            x=df_bp["measured_at"], y=df_bp["systolic"],
            name="收缩压", line=dict(color=COLOR_SYSTOLIC, width=2),
            mode="lines+markers",
        ))
        fig.add_trace(go.Scatter(
            x=df_bp["measured_at"], y=df_bp["diastolic"],
            name="舒张压", line=dict(color=COLOR_DIASTOLIC, width=2),
            mode="lines+markers",
        ))
    if "heart_rate" in df_vitals.columns and df_vitals["heart_rate"].notna().any():
        df_hr = df_vitals.dropna(subset=["heart_rate"])
        fig.add_trace(go.Scatter(
            x=df_hr["measured_at"], y=df_hr["heart_rate"],
            name="脉率", line=dict(color=COLOR_PULSE, width=1.5, dash="dot"),
            mode="lines+markers",
        ))

    fig.update_layout(title="血压趋势", hovermode="x unified", height=320,
                      yaxis_title="mmHg")
    return fig


def chart_bp_mini(df_vitals: pd.DataFrame, title: str = "") -> go.Figure:
    """血压迷你趋势图（概览页用，无脉率）。"""
    fig = go.Figure()

    df_bp = df_vitals.dropna(subset=["systolic"])
    if not df_bp.empty:
        fig.add_trace(go.Scatter(
            x=df_bp["measured_at"], y=df_bp["systolic"],
            name="收缩压", line=dict(color=COLOR_SYSTOLIC, width=2),
            mode="lines+markers",
        ))
        fig.add_trace(go.Scatter(
            x=df_bp["measured_at"], y=df_bp["diastolic"],
            name="舒张压", line=dict(color=COLOR_DIASTOLIC, width=2),
            mode="lines+markers",
        ))

    fig.update_layout(
        title=title,
        hovermode="x unified",
        height=200,
        showlegend=False,
        margin=dict(t=5, b=10, l=10, r=10),
        yaxis_title="mmHg",
    )
    return fig


def chart_weight_fat(df_vitals: pd.DataFrame, title: str = "体重 & 体脂率趋势") -> go.Figure:
    """图3: 体重 + 体脂率双 Y 轴。"""
    fig = go.Figure()

    # 过滤掉体重为空的记录，确保连线连续
    df_weight = df_vitals.dropna(subset=["weight_kg"])
    if not df_weight.empty:
        fig.add_trace(go.Scatter(
            x=df_weight["measured_at"], y=df_weight["weight_kg"],
            name="体重", line=dict(color=COLOR_WEIGHT, width=2),
            mode="lines+markers",
        ))

    df_fat = df_vitals.dropna(subset=["body_fat_pct"])
    if not df_fat.empty:
        fig.add_trace(go.Scatter(
            x=df_fat["measured_at"], y=df_fat["body_fat_pct"],
            name="体脂率", line=dict(color=COLOR_FAT, width=2),
            yaxis="y2",
            mode="lines+markers",
        ))

    fig.update_layout(
        title=title,
        hovermode="x unified",
        height=320,
        yaxis=dict(title="体重 (kg)"),
        yaxis2=dict(title="体脂率 (%)", overlaying="y", side="right"),
    )
    return fig


def chart_stress(df: pd.DataFrame) -> go.Figure:
    """图4: 压力指数趋势，标注高压区间 >50。"""
    fig = go.Figure()
    fig.add_hrect(y0=50, y1=100, fillcolor="rgba(200,100,100,0.08)",
                  line_width=0, annotation_text="高压区间 >50",
                  annotation_position="top left")

    fig.add_trace(go.Scatter(
        x=df["date"], y=df["stress_average"],
        name="压力指数", line=dict(color=COLOR_STRESS, width=2),
        fill="tozeroy", fillcolor="rgba(200,117,209,0.1)",
    ))
    _add_7day_avg(fig, df, "stress_average", "压力", COLOR_STRESS)

    fig.update_layout(title="压力指数趋势", hovermode="x unified", height=320,
                      yaxis_title="压力指数")
    return fig


def chart_exercise_gantt(df_ex: pd.DataFrame) -> go.Figure:
    """图5: 运动记录甘特图（每日运动类型×时长，颜色区分类型）。"""
    if df_ex.empty:
        fig = go.Figure()
        fig.update_layout(title="运动记录（暂无数据）", height=280)
        return fig

    # 为每种运动类型分配颜色
    types = df_ex["name"].unique()
    palette = [
        "#4F8EF7", "#F7A84F", "#5CB85C", "#C875D1",
        "#E05C5C", "#5CA8E0", "#F7D94F", "#8E8E8E",
    ]
    color_map = {t: palette[i % len(palette)] for i, t in enumerate(types)}

    fig = go.Figure()
    for etype, grp in df_ex.groupby("name"):
        fig.add_trace(go.Bar(
            x=grp["date"], y=grp["duration_min"],
            name=etype, marker_color=color_map[etype],
        ))

    fig.update_layout(
        title="运动记录（时长/天）", barmode="stack",
        hovermode="x unified", height=320,
        yaxis_title="时长（分钟）",
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right", x=1,
            font=dict(size=11),
        ),
        margin=dict(b=50),
    )
    return fig


def chart_lab_item(df: pd.DataFrame, item_name: str,
                   ref_low: float | None = None, ref_high: float | None = None,
                   unit: str = "", visit_dates: list | None = None) -> go.Figure:
    """化验趋势折线 + 参考区间填充。"""
    sub = df[df["item_name"] == item_name].copy()
    fig = go.Figure()

    if ref_high is not None:
        fig.add_hrect(y0=ref_low or 0, y1=ref_high,
                      fillcolor=REFERENCE_BAND_COLOR, line_width=0,
                      annotation_text="参考范围", annotation_position="top left")

    fig.add_trace(go.Scatter(
        x=sub["date"], y=sub["value"],
        mode="lines+markers", name=item_name,
        line=dict(width=2), marker=dict(size=7),
    ))

    if visit_dates:
        for vd in visit_dates:
            fig.add_vline(x=vd, line_dash="dot", line_color="grey",
                          annotation_text="复诊", annotation_position="top")

    fig.update_layout(
        title=item_name,
        yaxis_title=unit,
        hovermode="x unified", height=280,
    )
    return fig


def chart_heatmap(corr: pd.DataFrame) -> go.Figure:
    """相关性热力图。"""
    fig = go.Figure(go.Heatmap(
        z=corr.values,
        x=corr.columns.tolist(),
        y=corr.index.tolist(),
        colorscale="RdBu_r",
        zmin=-1, zmax=1,
        text=corr.round(2).values,
        texttemplate="%{text}",
        hoverongaps=False,
    ))
    fig.update_layout(title="指标相关性热力图", height=500)
    return fig


def chart_scatter(df: pd.DataFrame, x_col: str, y_col: str) -> go.Figure:
    """散点图，按周几着色 + 趋势回归线。"""
    import numpy as np
    from sklearn.linear_model import LinearRegression

    sub = df[[x_col, y_col, "date"]].dropna()
    if sub.empty:
        fig = go.Figure()
        fig.update_layout(title="数据不足", height=350)
        return fig

    sub = sub.copy()
    sub["weekday"] = pd.to_datetime(sub["date"]).dt.weekday

    fig = go.Figure(go.Scatter(
        x=sub[x_col], y=sub[y_col],
        mode="markers",
        name=y_col,
        marker=dict(color=sub["weekday"], colorscale="Viridis",
                    showscale=True, colorbar=dict(title="周几"),
                    size=8),
        text=sub["date"].astype(str),
        hovertemplate=f"{x_col}: %{{x}}<br>{y_col}: %{{y}}<br>日期: %{{text}}",
    ))

    # 回归线
    X = sub[x_col].values.reshape(-1, 1)
    y = sub[y_col].values
    model = LinearRegression().fit(X, y)
    x_line = np.linspace(X.min(), X.max(), 100)
    y_line = model.predict(x_line.reshape(-1, 1))
    r2 = model.score(X, y)
    fig.add_trace(go.Scatter(
        x=x_line, y=y_line, mode="lines",
        line=dict(color="red", dash="dash", width=1.5),
        name=f"趋势线 R²={r2:.2f}",
    ))

    fig.update_layout(
        title=f"{x_col} vs {y_col}",
        xaxis_title=x_col, yaxis_title=y_col, height=400,
        legend=dict(orientation="h", x=0, y=-0.18, xanchor="left", yanchor="top"),
        margin=dict(r=100, b=80),
    )
    return fig


def chart_medical_timeline(
    checkups: pd.DataFrame,
    eye_exams: pd.DataFrame,
    lab_results: pd.DataFrame,
) -> go.Figure:
    """就医时间线（年度体检 + 眼科 + 化验）。"""
    import plotly.express as px

    records = []
    for _, row in checkups.iterrows():
        d = str(row["checkup_date"])[:10]
        records.append(dict(
            Task="年度体检", Start=d,
            Finish=str(pd.Timestamp(d) + pd.Timedelta(days=1))[:10], Resource="体检",
        ))
    for _, row in eye_exams.iterrows():
        d = str(row["date"])[:10]
        records.append(dict(
            Task="眼科随访", Start=d,
            Finish=str(pd.Timestamp(d) + pd.Timedelta(days=1))[:10], Resource="眼科",
        ))

    if not records:
        fig = go.Figure()
        fig.update_layout(title="就医时间线（暂无数据）", height=200)
        return fig

    df_tl = pd.DataFrame(records)
    fig = px.timeline(df_tl, x_start="Start", x_end="Finish",
                      y="Task", color="Resource", title="就医时间线")
    fig.update_layout(height=250)
    return fig


def chart_trend_prediction(
    hist_dates, hist_values, pred_dates, pred_values,
    pred_upper, pred_lower, title: str, unit: str = ""
) -> go.Figure:
    """实线（历史）+ 虚线（预测）+ 置信带。"""
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=list(hist_dates), y=list(hist_values),
        name="历史", line=dict(color="#4F8EF7", width=2),
    ))
    fig.add_trace(go.Scatter(
        x=list(pred_dates), y=list(pred_values),
        name="预测", line=dict(color="#F7A84F", width=2, dash="dash"),
    ))
    fig.add_trace(go.Scatter(
        x=list(pred_dates) + list(pred_dates)[::-1],
        y=list(pred_upper) + list(pred_lower)[::-1],
        fill="toself", fillcolor="rgba(247,168,79,0.15)",
        line=dict(color="rgba(255,255,255,0)"),
        name="置信带", showlegend=True,
    ))

    fig.update_layout(title=title, yaxis_title=unit,
                      hovermode="x unified", height=320)
    return fig


# ─── 统一化验趋势图表（支持多数据源）────────────────────────────────────

COLOR_LAB_RESULTS = "#4F8EF7"      # 门诊化验 - 蓝色
COLOR_ANNUAL_CHECKUP = "#F7A84F"   # 年度体检 - 橙色
COLOR_ABNORMAL = "#E05C5C"         # 异常点 - 红色


def chart_unified_lab_trend(
    df: pd.DataFrame,
    title: str,
    ref_low: float | None = None,
    ref_high: float | None = None,
    unit: str = "",
    show_source: bool = True,
    x_range: tuple | None = None,
    x_dtick: str = "M12",
    x_tickvals: list | None = None,
) -> go.Figure:
    """统一化验趋势图表（合并门诊化验 + 年度体检）。

    Args:
        df: DataFrame 包含 date, value, source, is_abnormal 列
        title: 图表标题
        ref_low: 参考下限
        ref_high: 参考上限
        unit: 单位
        show_source: 是否按数据来源分组显示（不同颜色）
        x_range: 统一 x 轴范围
        x_dtick: x 轴刻度间隔，默认每年（"M12"），隔年传 "M24"

    Returns:
        Plotly Figure
    """
    fig = go.Figure()

    if df.empty:
        fig.update_layout(title=f"{title}（暂无数据）", height=280)
        return fig

    # 添加参考区间背景
    if ref_high is not None:
        y0 = ref_low if ref_low is not None else 0
        fig.add_hrect(
            y0=y0, y1=ref_high,
            fillcolor=REFERENCE_BAND_COLOR, line_width=0,
            annotation_text="参考范围", annotation_position="top left"
        )

    if show_source and "source" in df.columns:
        # 按数据来源分组显示
        lab_data = df[df["source"] == "lab_results"]
        checkup_data = df[df["source"] == "annual_checkups"]

        # 年度体检 - 橙色圆点
        if not checkup_data.empty:
            fig.add_trace(go.Scatter(
                x=checkup_data["date"],
                y=checkup_data["value"],
                mode="lines+markers",
                name="年度体检",
                line=dict(color=COLOR_ANNUAL_CHECKUP, width=2),
                marker=dict(size=10, symbol="circle"),
                hovertemplate="%{y:.2f} " + unit + "<br>%{x}<br>来源: 年度体检<extra></extra>",
            ))

        # 门诊化验 - 蓝色菱形
        if not lab_data.empty:
            fig.add_trace(go.Scatter(
                x=lab_data["date"],
                y=lab_data["value"],
                mode="lines+markers",
                name="门诊化验",
                line=dict(color=COLOR_LAB_RESULTS, width=2, dash="dot"),
                marker=dict(size=9, symbol="diamond"),
                hovertemplate="%{y:.2f} " + unit + "<br>%{x}<br>来源: 门诊化验<extra></extra>",
            ))

        # 异常点标注
        abnormal = df[df.get("is_abnormal", False) == True]
        if not abnormal.empty:
            fig.add_trace(go.Scatter(
                x=abnormal["date"],
                y=abnormal["value"],
                mode="markers",
                name="异常",
                marker=dict(
                    color="rgba(0,0,0,0)",
                    size=14,
                    symbol="circle",
                    line=dict(color=COLOR_ABNORMAL, width=2),
                ),
                showlegend=True,
                hoverinfo="skip",
            ))
    else:
        # 不区分来源，统一显示
        fig.add_trace(go.Scatter(
            x=df["date"],
            y=df["value"],
            mode="lines+markers",
            name=title,
            line=dict(width=2),
            marker=dict(size=8),
        ))

    fig.update_layout(
        title=title,
        yaxis_title=unit,
        hovermode="x unified",
        height=320,
        margin=dict(l=50, r=30, t=50, b=80),
        legend=dict(orientation="h", yanchor="top", y=-0.18, xanchor="left", x=0),
    )
    if x_range is not None:
        if x_tickvals is not None:
            fig.update_xaxes(range=x_range, tickvals=x_tickvals, tickformat="%Y")
        else:
            fig.update_xaxes(range=x_range, dtick=x_dtick, tickformat="%Y")
    return fig


def chart_multi_metric_trends(
    data_dict: dict[str, pd.DataFrame],
    metric_configs: list[dict],
) -> go.Figure:
    """多指标叠加趋势图（支持双Y轴）。

    Args:
        data_dict: key 为指标代码，value 为 DataFrame
        metric_configs: 每个指标的配置列表，每项包含:
            - key: 指标代码（对应 data_dict 的 key）
            - label: 显示名称
            - color: 颜色
            - secondary_y: 是否使用右侧Y轴
            - ref_low, ref_high: 参考范围（可选，用于背景带）

    Returns:
        Plotly Figure（双Y轴）
    """
    has_secondary = any(cfg.get("secondary_y") for cfg in metric_configs)

    if has_secondary:
        fig = make_subplots(specs=[[{"secondary_y": True}]])
    else:
        fig = go.Figure()

    for cfg in metric_configs:
        key = cfg["key"]
        label = cfg["label"]
        color = cfg.get("color", "#4F8EF7")
        use_secondary = cfg.get("secondary_y", False)
        df = data_dict.get(key, pd.DataFrame())

        if df.empty:
            continue

        trace = go.Scatter(
            x=df["date"],
            y=df["value"],
            mode="lines+markers",
            name=label,
            line=dict(color=color, width=2),
            marker=dict(size=8),
        )

        if has_secondary:
            fig.add_trace(trace, secondary_y=use_secondary)
        else:
            fig.add_trace(trace)

        # 添加参考范围背景带（仅主Y轴第一个指标）
        if cfg.get("ref_high") and not use_secondary:
            y0 = cfg.get("ref_low", 0)
            fig.add_hrect(
                y0=y0, y1=cfg["ref_high"],
                fillcolor=REFERENCE_BAND_COLOR, line_width=0,
                annotation_text=f"{label}参考范围",
                annotation_position="top left",
            )

    fig.update_layout(
        title="多指标趋势对比",
        hovermode="x unified",
        height=400,
        margin=dict(b=80),
        legend=dict(orientation="h", yanchor="top", y=-0.18, xanchor="left", x=0),
    )

    if has_secondary:
        fig.update_yaxes(title_text="主指标", secondary_y=False)
        fig.update_yaxes(title_text="次指标", secondary_y=True)

    return fig
