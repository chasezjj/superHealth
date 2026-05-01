"""圆形仪表盘组件 — 用于风险评分和 KPI 展示。"""

from __future__ import annotations

import plotly.graph_objects as go


def risk_gauge(score: float, title: str) -> go.Figure:
    """
    0~100 风险评分圆形仪表盘。
    绿 <40 / 黄 40-70 / 红 >70
    """
    fig = go.Figure(
        go.Indicator(
            mode="gauge",
            value=score,
            title={"text": title, "font": {"size": 16}},
            gauge={
                "axis": {"range": [0, 100], "tickwidth": 1},
                "bar": {"color": _score_color(score)},
                "steps": [
                    {"range": [0, 40], "color": "rgba(80,200,120,0.3)"},
                    {"range": [40, 70], "color": "rgba(255,200,60,0.3)"},
                    {"range": [70, 100], "color": "rgba(220,80,80,0.3)"},
                ],
                "threshold": {
                    "line": {"color": "black", "width": 3},
                    "thickness": 0.75,
                    "value": score,
                },
            },
            domain={"x": [0, 1], "y": [0.1, 1]},
        )
    )
    fig.add_annotation(
        x=0.5,
        y=0.18,
        text=f"<b>{score:.1f}分</b>",
        font={"size": 26, "color": _score_color(score)},
        showarrow=False,
        xref="paper",
        yref="paper",
    )
    fig.update_layout(height=300, margin=dict(t=60, b=20, l=50, r=50))
    return fig


def _score_color(score: float) -> str:
    if score < 40:
        return "#50C878"
    elif score < 70:
        return "#FFC83C"
    else:
        return "#DC5050"


def score_label(score: float, labels: list[str] | None = None) -> str:
    """根据分数返回文字标签。默认：低风险/中风险/高风险。"""
    labels = labels or ["低风险", "中等风险", "高风险"]
    if score < 40:
        return labels[0]
    elif score < 70:
        return labels[1]
    else:
        return labels[2]


def factor_bar_chart(factors: dict[str, float], title: str = "各因子贡献") -> go.Figure:
    """
    各因子贡献横向条形图。
    factors: {因子名: 贡献分值(0~100)}
    """
    names = list(factors.keys())
    values = list(factors.values())
    colors = [_score_color(v) for v in values]

    fig = go.Figure(
        go.Bar(
            x=values,
            y=names,
            orientation="h",
            marker_color=colors,
            text=[f"{v:.1f}" for v in values],
            textposition="outside",
        )
    )
    fig.update_layout(
        title=title,
        xaxis=dict(range=[0, 100], title="风险贡献（归一化）"),
        height=max(200, 40 * len(names) + 80),
        margin=dict(l=140, r=40, t=50, b=30),
    )
    return fig
