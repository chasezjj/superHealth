"""短期趋势预测 — 线性外推未来7天。

对 HRV、体重、收缩压分别用近14天数据做 LinearRegression，
输出预测值 + 95% 置信区间，供仪表盘绘图。
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
from sklearn.linear_model import LinearRegression

from superhealth.dashboard.data_loader import load_daily_health, load_vitals

PREDICT_DAYS = 7
HISTORY_DAYS = 14


def _predict_series(
    dates: list[date], values: list[float], predict_days: int = PREDICT_DAYS
) -> dict:
    """
    对给定时间序列做线性外推。

    Returns:
        {
            "hist_dates": list[date],
            "hist_values": list[float],
            "pred_dates": list[date],
            "pred_values": list[float],
            "pred_upper": list[float],
            "pred_lower": list[float],
        }
    """
    if len(values) < 2:
        return {}

    x = np.arange(len(values)).reshape(-1, 1)
    y = np.array(values)
    model = LinearRegression().fit(x, y)

    # 残差标准差 → 95% 置信区间
    residuals = y - model.predict(x)
    std = residuals.std()
    z95 = 1.96

    last_idx = len(values) - 1
    pred_x = np.arange(last_idx + 1, last_idx + 1 + predict_days).reshape(-1, 1)
    pred_y = model.predict(pred_x)
    last_date = dates[-1]

    pred_dates = [last_date + timedelta(days=i + 1) for i in range(predict_days)]

    return {
        "hist_dates": dates,
        "hist_values": values,
        "pred_dates": pred_dates,
        "pred_values": pred_y.tolist(),
        "pred_upper": (pred_y + z95 * std).tolist(),
        "pred_lower": (pred_y - z95 * std).tolist(),
    }


def predict_hrv() -> dict:
    """预测未来7天 HRV。"""
    df = load_daily_health(HISTORY_DAYS)
    sub = df[["date", "hrv_last_night_avg"]].dropna()
    if sub.empty:
        return {}
    dates = [d.date() if hasattr(d, "date") else d for d in sub["date"]]
    return _predict_series(dates, sub["hrv_last_night_avg"].tolist())


def predict_weight() -> dict:
    """预测未来7天体重。"""
    df = load_vitals(HISTORY_DAYS * 2)
    sub = df[["measured_at", "weight_kg"]].dropna()
    if sub.empty:
        return {}
    sub = sub.sort_values("measured_at")
    # 按天取均值
    sub["date"] = sub["measured_at"].dt.date
    daily = sub.groupby("date")["weight_kg"].mean().reset_index()
    if len(daily) < 2:
        return {}
    return _predict_series(daily["date"].tolist(), daily["weight_kg"].tolist())


def predict_systolic() -> dict:
    """预测未来7天收缩压。"""
    df = load_vitals(HISTORY_DAYS * 2)
    sub = df[["measured_at", "systolic"]].dropna()
    if sub.empty:
        return {}
    sub = sub.sort_values("measured_at")
    sub["date"] = sub["measured_at"].dt.date
    daily = sub.groupby("date")["systolic"].mean().reset_index()
    if len(daily) < 2:
        return {}
    return _predict_series(daily["date"].tolist(), daily["systolic"].tolist())


def predict_diastolic() -> dict:
    """预测未来7天舒张压。"""
    df = load_vitals(HISTORY_DAYS * 2)
    sub = df[["measured_at", "diastolic"]].dropna()
    if sub.empty:
        return {}
    sub = sub.sort_values("measured_at")
    sub["date"] = sub["measured_at"].dt.date
    daily = sub.groupby("date")["diastolic"].mean().reset_index()
    if len(daily) < 2:
        return {}
    return _predict_series(daily["date"].tolist(), daily["diastolic"].tolist())


def predict_all() -> dict:
    """返回 HRV、体重、收缩压、舒张压四项预测结果。"""
    return {
        "hrv": predict_hrv(),
        "weight": predict_weight(),
        "systolic": predict_systolic(),
        "diastolic": predict_diastolic(),
    }
