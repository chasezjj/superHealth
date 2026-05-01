"""统一数据获取层 — 仪表盘所有 DB 查询在此集中。

所有返回值均为 pandas DataFrame 或 dict，方便 Streamlit 直接使用。
使用 @st.cache_data(ttl=0) 禁用缓存，每次访问都重新查询。
"""

from __future__ import annotations

import glob
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

from superhealth.database import DEFAULT_DB_PATH, get_conn, query_lab_trends_unified, query_multiple_metrics, _LIVER_KIDNEY_METRICS, query_user_profiles


# ─── 基础查询 ─────────────────────────────────────────────────────────

@st.cache_data(ttl=0)
def load_daily_health(days: int = 90) -> pd.DataFrame:
    """读取最近 N 天的 Garmin 每日数据（含今日，共 N 个日期）。"""
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    with get_conn(DEFAULT_DB_PATH) as conn:
        df = pd.read_sql_query(
            "SELECT * FROM daily_health WHERE date >= ? ORDER BY date",
            conn,
            params=(since,),
        )
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df["sleep_hours"] = df["sleep_total_seconds"] / 3600
    return df


@st.cache_data(ttl=0)
def load_vitals(days: int = 90) -> pd.DataFrame:
    """读取最近 N 天的血压/体重/体脂记录（Health Auto Export）。"""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with get_conn(DEFAULT_DB_PATH) as conn:
        df = pd.read_sql_query(
            "SELECT * FROM vitals WHERE measured_at >= ? ORDER BY measured_at",
            conn,
            params=(since,),
        )
    if not df.empty:
        # 解析带时区的时间字符串，保持本地时间不变
        df["measured_at"] = pd.to_datetime(df["measured_at"]).dt.tz_localize(None)
        df["date"] = df["measured_at"].dt.date
    return df


@st.cache_data(ttl=0)
def load_exercises(days: int = 90) -> pd.DataFrame:
    """读取最近 N 天的运动记录。"""
    since = (date.today() - timedelta(days=days)).isoformat()
    with get_conn(DEFAULT_DB_PATH) as conn:
        df = pd.read_sql_query(
            "SELECT * FROM exercises WHERE date >= ? ORDER BY date",
            conn,
            params=(since,),
        )
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df["duration_min"] = df["duration_seconds"] / 60
        df["is_high_intensity"] = df["avg_hr"].fillna(0) > 140
    return df


@st.cache_data(ttl=0)
def load_lab_results(item_name: Optional[str] = None) -> pd.DataFrame:
    """读取化验结果，可按指标名称过滤。"""
    with get_conn(DEFAULT_DB_PATH) as conn:
        if item_name:
            df = pd.read_sql_query(
                "SELECT * FROM lab_results WHERE item_name = ? ORDER BY date",
                conn,
                params=(item_name,),
            )
        else:
            df = pd.read_sql_query(
                "SELECT * FROM lab_results ORDER BY date",
                conn,
            )
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


@st.cache_data(ttl=0)
def load_eye_exams() -> pd.DataFrame:
    """读取所有眼科检查记录。"""
    with get_conn(DEFAULT_DB_PATH) as conn:
        df = pd.read_sql_query(
            "SELECT * FROM eye_exams ORDER BY date",
            conn,
        )
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


@st.cache_data(ttl=0)
def load_annual_checkups() -> pd.DataFrame:
    """读取所有年度体检记录。"""
    with get_conn(DEFAULT_DB_PATH) as conn:
        df = pd.read_sql_query(
            "SELECT * FROM annual_checkups ORDER BY checkup_date",
            conn,
        )
    if not df.empty:
        df["checkup_date"] = pd.to_datetime(df["checkup_date"])
    return df


@st.cache_data(ttl=0)
def load_appointments() -> pd.DataFrame:
    """读取就医提醒记录（status != completed）。"""
    with get_conn(DEFAULT_DB_PATH) as conn:
        df = pd.read_sql_query(
            "SELECT * FROM appointments WHERE status != 'completed' ORDER BY due_date",
            conn,
        )
    if not df.empty:
        df["due_date"] = pd.to_datetime(df["due_date"])
        df["days_until"] = (df["due_date"] - pd.Timestamp.now()).dt.days
    return df


@st.cache_data(ttl=0)
def load_weather(days: int = 90) -> pd.DataFrame:
    """读取最近 N 天的天气记录。"""
    since = (date.today() - timedelta(days=days)).isoformat()
    with get_conn(DEFAULT_DB_PATH) as conn:
        df = pd.read_sql_query(
            "SELECT * FROM weather WHERE date >= ? ORDER BY date",
            conn,
            params=(since,),
        )
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


# ─── 快捷查询（单值） ─────────────────────────────────────────────────

def get_latest_vitals() -> dict:
    """获取最新一条血压/体重/体脂记录。"""
    with get_conn(DEFAULT_DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM vitals ORDER BY measured_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else {}


def get_latest_daily_health() -> dict:
    """获取最新一天的 Garmin 数据。"""
    with get_conn(DEFAULT_DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM daily_health ORDER BY date DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else {}


def get_lab_latest(item_name: str) -> Optional[dict]:
    """获取某化验指标最新一次记录。"""
    with get_conn(DEFAULT_DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM lab_results WHERE item_name = ? ORDER BY date DESC LIMIT 1",
            (item_name,),
        ).fetchone()
    return dict(row) if row else None


def get_upcoming_appointments(within_days: int = 14) -> list[dict]:
    """获取即将到来的就医提醒（N天内）。"""
    cutoff = (date.today() + timedelta(days=within_days)).isoformat()
    today = date.today().isoformat()
    with get_conn(DEFAULT_DB_PATH) as conn:
        rows = conn.execute(
            """SELECT * FROM appointments
               WHERE due_date BETWEEN ? AND ?
               AND status != 'completed'
               ORDER BY due_date""",
            (today, cutoff),
        ).fetchall()
    return [dict(r) for r in rows]


# ─── 统一化验趋势查询（合并 lab_results + annual_checkups）────────────────

@st.cache_data(ttl=0)
def load_unified_lab_trends(metric_key: str, years: int = 10) -> pd.DataFrame:
    """加载统一化验趋势数据（合并门诊化验和年度体检）。

    Args:
        metric_key: 指标代码，如 'uric_acid', 'creatinine', 'ldl_c' 等
        years: 查询最近 N 年的数据

    Returns:
        DataFrame 包含: date, value, source, unit, ref_low, ref_high, is_abnormal
    """
    start_date = (date.today() - timedelta(days=365 * years)).isoformat()
    with get_conn(DEFAULT_DB_PATH) as conn:
        results = query_lab_trends_unified(conn, metric_key, start_date=start_date)

    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    df["date"] = pd.to_datetime(df["date"])
    return df


@st.cache_data(ttl=0)
def load_multiple_unified_trends(metric_keys: list[str], years: int = 10) -> dict[str, pd.DataFrame]:
    """批量加载多个指标的统一趋势数据。

    Returns:
        字典，key 为指标代码，value 为对应的 DataFrame
    """
    start_date = (date.today() - timedelta(days=365 * years)).isoformat()
    with get_conn(DEFAULT_DB_PATH) as conn:
        results = query_multiple_metrics(conn, metric_keys, start_date=start_date)

    dfs = {}
    for key, data in results.items():
        if data:
            df = pd.DataFrame(data)
            df["date"] = pd.to_datetime(df["date"])
            dfs[key] = df
        else:
            dfs[key] = pd.DataFrame()
    return dfs


def get_available_lab_metrics() -> dict[str, str]:
    """获取可用的化验指标列表。

    Returns:
        字典，key 为指标代码，value 为中文名称
    """
    return {
        "uric_acid": "尿酸",
        "creatinine": "肌酐",
        "urea": "尿素",
        "ldl_c": "低密度脂蛋白胆固醇 (LDL-C)",
        "triglyceride": "甘油三酯 (TG)",
        "hdl_c": "高密度脂蛋白胆固醇 (HDL-C)",
        "total_cholesterol": "总胆固醇",
        "alt": "丙氨酸氨基转移酶 (ALT)",
        "ast": "天冬氨酸氨基转移酶 (AST)",
    }


# ─── AI 摘要 ─────────────────────────────────────────────────────────

def get_latest_ai_summary(max_chars: int = 300) -> str:
    """从最新的 advanced-daily-report 文件中提取 AI 建议摘要。"""
    summary, _ = get_latest_ai_report()
    return summary


def get_latest_ai_report() -> tuple[str, str]:
    """从最新的 advanced-daily-report 文件中提取 (执行摘要, 完整报告内容)。"""
    report_dir = DEFAULT_DB_PATH.parent / "activity-data" / "reports"
    if not report_dir.exists():
        return "（暂无 AI 建议报告）", ""

    # 找最新的 advanced-daily-report .md 文件
    files = sorted(report_dir.glob("*-advanced-daily-report.md"), reverse=True)
    if not files:
        return "（暂无 AI 建议报告）", ""

    content = files[0].read_text(encoding="utf-8")

    # 提取 "执行摘要" 里的 blockquote (> ...)
    summary = ""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("> "):
            summary += stripped[2:] + "\n"
        elif stripped and summary:
            # 摘要段落结束
            break
    summary = summary.strip()

    #  fallback：若没找到 blockquote，取第一个非空非标题行
    if not summary:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                summary = stripped
                break

    return summary, content


def get_latest_weekly_report() -> tuple[str, str]:
    """从最新的周报文件中提取 (摘要, 完整内容)。"""
    report_dir = DEFAULT_DB_PATH.parent / "activity-data" / "reports"
    if not report_dir.exists():
        return "（暂无周报）", ""

    files = sorted(report_dir.glob("*-weekly-report.md"), reverse=True)
    if not files:
        return "（暂无周报）", ""

    content = files[0].read_text(encoding="utf-8")

    # 优先提取 "LLM 深度洞察" 作为摘要
    summary = ""
    in_section = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "## LLM 深度洞察":
            in_section = True
            continue
        if in_section:
            if stripped.startswith("## "):
                break
            if stripped:
                summary += stripped + "\n"
    summary = summary.strip()

    # 如果 LLM 未生成内容（不可用/未配置），视为无有效摘要
    if summary.startswith("（LLM 不可用") or summary.startswith("（未配置"):
        summary = ""

    # fallback：提取 "因果发现"
    if not summary:
        in_section = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == "## 因果发现":
                in_section = True
                continue
            if in_section:
                if stripped.startswith("## "):
                    break
                if stripped:
                    summary += stripped + "\n"
        summary = summary.strip()

    # fallback：提取第一个非空非标题段落
    if not summary:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("-"):
                summary = stripped
                break

    return summary, content


# ─── 全量加载（用于相关性分析） ──────────────────────────────────────

@st.cache_data(ttl=0)
def load_merged_for_correlation(days: int = 180) -> pd.DataFrame:
    """
    合并 daily_health + vitals（按日聚合）为宽表，用于相关性热力图。
    返回列：date, hrv, body_battery, sleep_hours, stress, resting_hr,
            systolic, diastolic, weight_kg, body_fat_pct, steps, exercise_min
    """
    dh = load_daily_health(days)
    vitals = load_vitals(days)
    ex = load_exercises(days)

    if dh.empty:
        return pd.DataFrame()

    merged = dh[["date", "hrv_last_night_avg", "bb_highest", "sleep_hours",
                  "stress_average", "hr_resting", "steps"]].copy()
    merged = merged.rename(columns={
        "hrv_last_night_avg": "心率变异",
        "bb_highest": "身体电量",
        "sleep_hours": "睡眠时长",
        "stress_average": "压力指数",
        "hr_resting": "静息心率",
        "steps": "步数",
    })
    merged["date"] = merged["date"].dt.date

    # 血压/体重：按天取均值
    if not vitals.empty:
        v_daily = vitals.groupby("date").agg(
            收缩压=("systolic", "mean"),
            舒张压=("diastolic", "mean"),
            体重=("weight_kg", "mean"),
            体脂率=("body_fat_pct", "mean"),
        ).reset_index()
        merged = merged.merge(v_daily, on="date", how="left")

    # 运动：每天总时长（分钟）
    if not ex.empty:
        ex_daily = ex.groupby(ex["date"].dt.date)["duration_min"].sum().reset_index()
        ex_daily.columns = ["date", "运动时长"]
        merged = merged.merge(ex_daily, on="date", how="left")

    return merged.dropna(how="all")


# ─── 学习偏好 ────────────────────────────────────────────────────────

@st.cache_data(ttl=0)
def load_learned_preferences(preference_type: str = None, status: str = None) -> pd.DataFrame:
    """加载已学习的个人偏好。

    Args:
        preference_type: 按类别过滤（exercise_type, intensity, timing 等）
        status: 要排除的状态。传 'reverted' 时排除 reverted，同时保留 NULL 旧数据。
    """
    from superhealth.database import query_learned_preferences
    with get_conn(DEFAULT_DB_PATH) as conn:
        rows = query_learned_preferences(conn, preference_type=preference_type, exclude_status=status)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    if "last_updated" in df.columns:
        df["last_updated"] = pd.to_datetime(df["last_updated"])
    return df


# ─── 用户档案 ────────────────────────────────────────────────────────

@st.cache_data(ttl=0)
def get_user_profile() -> dict:
    """读取用户档案（身高/性别/出生日期等），返回 {key: value} 字典。"""
    with get_conn(DEFAULT_DB_PATH) as conn:
        return query_user_profiles(conn)


# ─── 历史回顾专用查询 ─────────────────────────────────────────────────

@st.cache_data(ttl=0)
def load_recent_feedback(days: int = 3) -> pd.DataFrame:
    """读取最近 N 天（不含今天）的 recommendation_feedback 记录（含效果追踪）。"""
    today = date.today().isoformat()
    since = (date.today() - timedelta(days=days)).isoformat()
    with get_conn(DEFAULT_DB_PATH) as conn:
        df = pd.read_sql_query(
            """SELECT date, recommendation_type, recommendation_content,
                      compliance, actual_action, tracked_metrics,
                      user_feedback, user_rating
               FROM recommendation_feedback
               WHERE date >= ? AND date < ?
               ORDER BY date DESC""",
            conn,
            params=(since, today),
        )
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


@st.cache_data(ttl=0)
def load_feedback_by_range(start_date: str, end_date: str) -> pd.DataFrame:
    """读取指定日期范围的 recommendation_feedback 记录（含效果追踪）。"""
    with get_conn(DEFAULT_DB_PATH) as conn:
        df = pd.read_sql_query(
            """SELECT date, recommendation_type, recommendation_content,
                      compliance, actual_action, tracked_metrics,
                      user_feedback, user_rating
               FROM recommendation_feedback
               WHERE date >= ? AND date <= ?
               ORDER BY date DESC""",
            conn,
            params=(start_date, end_date),
        )
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


@st.cache_data(ttl=0)
def load_recent_goal_progress(days: int = 3) -> dict[int, pd.DataFrame]:
    """读取最近 N 天每个 active goal 的进度记录（含今天，与阶段性目标页保持一致）。

    Returns:
        {goal_id: DataFrame}，DataFrame 含 date, current_value, progress_pct, note
    """
    since = (date.today() - timedelta(days=days)).isoformat()
    with get_conn(DEFAULT_DB_PATH) as conn:
        goal_rows = conn.execute(
            "SELECT id FROM goals WHERE status = 'active'"
        ).fetchall()
        goal_ids = [r["id"] for r in goal_rows]

        result = {}
        for gid in goal_ids:
            df = pd.read_sql_query(
                """SELECT date, current_value, progress_pct, note
                   FROM goal_progress
                   WHERE goal_id = ? AND date >= ?
                   ORDER BY date""",
                conn,
                params=(gid, since),
            )
            if not df.empty:
                df["date"] = pd.to_datetime(df["date"]).dt.date
            result[gid] = df
    return result
