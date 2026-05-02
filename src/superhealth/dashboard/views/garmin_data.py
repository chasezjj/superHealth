"""Garmin 数据管理页面 — 查看、编辑、删除健康数据和运动记录。"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from superhealth.database import (
    DEFAULT_DB_PATH,
    delete_daily_health,
    delete_exercise,
    get_conn,
    update_daily_health_fields,
    update_exercise,
)
from superhealth.dashboard.data_loader import load_daily_health, load_exercises

# 用于展示的列名映射（数据库列 → 中文显示名）
_DH_DISPLAY_COLS = {
    "date": "日期",
    "sleep_hours": "睡眠时长(h)",
    "sleep_score": "睡眠评分",
    "hr_resting": "静息心率",
    "hrv_last_night_avg": "HRV",
    "bb_highest": "身体电量(最高)",
    "stress_average": "压力均值",
    "steps": "步数",
    "spo2_average": "SpO2均值",
}

_EX_DISPLAY_COLS = {
    "id": "ID",
    "date": "日期",
    "name": "运动名称",
    "type_key": "类型",
    "start_time": "开始时间",
    "duration_min": "时长(分钟)",
    "avg_hr": "平均心率",
    "max_hr": "最大心率",
    "calories": "卡路里",
}

# 健康数据字段分组（用于编辑表单）
_DH_FIELD_GROUPS = {
    "睡眠": [
        ("sleep_total_seconds", "总睡眠时长(秒)", "number"),
        ("sleep_score", "睡眠评分", "number"),
        ("sleep_deep_seconds", "深睡时长(秒)", "number"),
        ("sleep_light_seconds", "浅睡时长(秒)", "number"),
        ("sleep_rem_seconds", "REM时长(秒)", "number"),
        ("sleep_awake_seconds", "清醒时长(秒)", "number"),
    ],
    "心率": [
        ("hr_resting", "静息心率(bpm)", "number"),
        ("hr_min", "最低心率", "number"),
        ("hr_max", "最高心率", "number"),
        ("hr_avg7_resting", "7日均静息心率", "number"),
    ],
    "身体电量": [
        ("bb_highest", "最高电量", "number"),
        ("bb_lowest", "最低电量", "number"),
        ("bb_at_wake", "醒来时电量", "number"),
        ("bb_charged", "充电量", "number"),
        ("bb_drained", "消耗量", "number"),
    ],
    "HRV": [
        ("hrv_last_night_avg", "昨夜HRV均值", "number"),
        ("hrv_last_night_5min_high", "昨夜HRV5分钟峰值", "number"),
        ("hrv_weekly_avg", "周均HRV", "number"),
        ("hrv_baseline_low", "HRV基线下限", "number"),
        ("hrv_baseline_high", "HRV基线上限", "number"),
        ("hrv_status", "HRV状态", "text"),
    ],
    "压力": [
        ("stress_average", "平均压力", "number"),
        ("stress_max", "最大压力", "number"),
        ("stress_rest_seconds", "休息时长(秒)", "number"),
        ("stress_low_seconds", "低压力时长(秒)", "number"),
        ("stress_medium_seconds", "中压力时长(秒)", "number"),
        ("stress_high_seconds", "高压力时长(秒)", "number"),
    ],
    "SpO2/呼吸": [
        ("spo2_average", "SpO2均值(%)", "number"),
        ("spo2_lowest", "SpO2最低值", "number"),
        ("spo2_latest", "SpO2最新值", "number"),
        ("resp_waking_avg", "清醒平均呼吸率", "number"),
        ("resp_highest", "最高呼吸率", "number"),
        ("resp_lowest", "最低呼吸率", "number"),
    ],
    "活动": [
        ("steps", "步数", "number"),
        ("distance_meters", "距离(米)", "number"),
        ("active_calories", "活跃卡路里", "number"),
        ("floors_ascended", "爬楼层数", "number"),
    ],
}


def _load_dh_row(date_str: str) -> dict:
    """从数据库直接读取某日的原始健康数据行。"""
    with get_conn(DEFAULT_DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM daily_health WHERE date = ?", (date_str,)
        ).fetchone()
    return dict(row) if row else {}


def _load_ex_row(exercise_id: int) -> dict:
    """从数据库读取单条运动记录原始行。"""
    with get_conn(DEFAULT_DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM exercises WHERE id = ?", (exercise_id,)
        ).fetchone()
    return dict(row) if row else {}


def _num_input(label: str, value, key: str):
    """统一 number_input，处理 None 值。"""
    v = float(value) if value is not None else 0.0
    return st.number_input(label, value=v, key=key, step=1.0, format="%.4g")


def _render_dh_edit_form(date_str: str):
    """渲染健康数据编辑表单。"""
    row = _load_dh_row(date_str)
    if not row:
        st.error(f"未找到 {date_str} 的数据")
        return

    with st.form(f"edit_dh_{date_str}"):
        updated = {}
        for group_name, fields in _DH_FIELD_GROUPS.items():
            st.markdown(f"**{group_name}**")
            cols = st.columns(3)
            for i, (col_name, label, ftype) in enumerate(fields):
                with cols[i % 3]:
                    if ftype == "text":
                        val = st.text_input(
                            label,
                            value=str(row.get(col_name) or ""),
                            key=f"dh_{date_str}_{col_name}",
                        )
                        updated[col_name] = val if val else None
                    else:
                        val = _num_input(label, row.get(col_name), f"dh_{date_str}_{col_name}")
                        updated[col_name] = val if val != 0.0 else None
            st.divider()

        if st.form_submit_button("保存修改", type="primary"):
            non_null = {k: v for k, v in updated.items() if v is not None}
            if non_null:
                with get_conn(DEFAULT_DB_PATH) as conn:
                    update_daily_health_fields(conn, date_str, non_null)
                st.success(f"已更新 {date_str} 的健康数据")
                st.cache_data.clear()
                st.rerun()
            else:
                st.warning("没有可保存的非空值")


def _render_ex_edit_form(exercise_id: int):
    """渲染运动记录编辑表单。"""
    row = _load_ex_row(exercise_id)
    if not row:
        st.error(f"未找到 ID={exercise_id} 的运动记录")
        return

    with st.form(f"edit_ex_{exercise_id}"):
        c1, c2, c3 = st.columns(3)
        with c1:
            name = st.text_input("运动名称", value=str(row.get("name") or ""), key=f"ex_name_{exercise_id}")
            type_key = st.text_input("类型代码", value=str(row.get("type_key") or ""), key=f"ex_type_{exercise_id}")
            start_time = st.text_input("开始时间(HH:MM)", value=str(row.get("start_time") or ""), key=f"ex_start_{exercise_id}")
        with c2:
            duration_s = _num_input("时长(秒)", row.get("duration_seconds"), f"ex_dur_{exercise_id}")
            distance_m = _num_input("距离(米)", row.get("distance_meters"), f"ex_dist_{exercise_id}")
        with c3:
            avg_hr = _num_input("平均心率", row.get("avg_hr"), f"ex_avghr_{exercise_id}")
            max_hr = _num_input("最大心率", row.get("max_hr"), f"ex_maxhr_{exercise_id}")
            calories = _num_input("卡路里", row.get("calories"), f"ex_cal_{exercise_id}")

        if st.form_submit_button("保存修改", type="primary"):
            fields = {
                "name": name or None,
                "type_key": type_key or None,
                "start_time": start_time or None,
                "duration_seconds": int(duration_s) if duration_s else None,
                "distance_meters": distance_m if distance_m else None,
                "avg_hr": avg_hr if avg_hr else None,
                "max_hr": max_hr if max_hr else None,
                "calories": calories if calories else None,
            }
            non_null = {k: v for k, v in fields.items() if v is not None}
            if non_null:
                with get_conn(DEFAULT_DB_PATH) as conn:
                    update_exercise(conn, exercise_id, non_null)
                st.success(f"已更新运动记录 ID={exercise_id}")
                st.cache_data.clear()
                st.rerun()
            else:
                st.warning("没有可保存的非空值")


def _tab_daily_health(df_all: pd.DataFrame, start_date: date, end_date: date):
    """健康数据 Tab 内容。"""
    if df_all.empty:
        st.info("所选日期范围内没有健康数据，请先通过系统配置拉取 Garmin 数据。")
        return

    # 按日期范围过滤
    mask = (df_all["date"].dt.date >= start_date) & (df_all["date"].dt.date <= end_date)
    df = df_all[mask].copy()

    if df.empty:
        st.info(f"{start_date} 至 {end_date} 之间没有数据。")
        return

    # 汇总展示（降序）
    df = df.sort_values("date", ascending=False)
    st.caption(f"共 {len(df)} 条记录")
    display_df = df[list(_DH_DISPLAY_COLS.keys())].copy()
    display_df.columns = list(_DH_DISPLAY_COLS.values())
    display_df["日期"] = display_df["日期"].dt.strftime("%Y-%m-%d")
    for col in ["睡眠时长(h)", "睡眠评分", "静息心率", "HRV", "身体电量(最高)", "压力均值", "步数", "SpO2均值"]:
        if col in display_df.columns:
            display_df[col] = display_df[col].apply(
                lambda x: f"{x:.1f}" if pd.notna(x) and x != 0 else "—"
            )
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    st.divider()

    # 选择日期进行编辑/删除
    date_options = sorted(df["date"].dt.strftime("%Y-%m-%d").tolist(), reverse=True)
    selected_date = st.selectbox("选择日期进行编辑或删除", date_options, key="dh_select_date")

    if selected_date:
        col_edit, col_delete_area = st.columns([3, 1])

        with col_edit:
            with st.expander("编辑所选记录", expanded=False):
                _render_dh_edit_form(selected_date)

        with col_delete_area:
            st.markdown("**删除记录**")
            confirm = st.checkbox(
                "确认删除（含当天所有运动记录）",
                key=f"confirm_del_dh_{selected_date}",
            )
            if st.button(
                "删除",
                key=f"del_dh_{selected_date}",
                type="primary",
                disabled=not confirm,
            ):
                with get_conn(DEFAULT_DB_PATH) as conn:
                    delete_daily_health(conn, selected_date)
                st.success(f"已删除 {selected_date} 的健康数据及运动记录")
                st.cache_data.clear()
                st.rerun()


def _tab_exercises(df_all: pd.DataFrame, start_date: date, end_date: date):
    """运动记录 Tab 内容。"""
    if df_all.empty:
        st.info("所选日期范围内没有运动记录。")
        return

    mask = (df_all["date"].dt.date >= start_date) & (df_all["date"].dt.date <= end_date)
    df = df_all[mask].copy()

    if df.empty:
        st.info(f"{start_date} 至 {end_date} 之间没有运动记录。")
        return

    # 运动类型过滤
    all_types = sorted(df["type_key"].dropna().unique().tolist())
    selected_types = st.multiselect(
        "按运动类型过滤（留空表示全部）",
        options=all_types,
        default=[],
        key="ex_type_filter",
    )
    if selected_types:
        df = df[df["type_key"].isin(selected_types)]

    if df.empty:
        st.info("过滤后没有匹配的运动记录。")
        return

    # 降序排列
    df = df.sort_values(["date", "id"], ascending=[False, False])
    st.caption(f"共 {len(df)} 条记录")

    display_cols = [c for c in _EX_DISPLAY_COLS.keys() if c in df.columns]
    display_df = df[display_cols].copy()
    display_df.columns = [_EX_DISPLAY_COLS[c] for c in display_cols]
    display_df["日期"] = display_df["日期"].dt.strftime("%Y-%m-%d")
    if "时长(分钟)" in display_df.columns:
        display_df["时长(分钟)"] = display_df["时长(分钟)"].apply(
            lambda x: f"{x:.1f}" if pd.notna(x) else "—"
        )
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    st.divider()

    # 选择记录进行编辑/删除
    id_options = df["id"].astype(int).tolist()
    id_labels = {
        row["id"]: f"{row['date'].strftime('%Y-%m-%d')} | {row.get('name','') or ''} | {row.get('type_key','') or ''}"
        for _, row in df.iterrows()
    }
    selected_id = st.selectbox(
        "选择运动记录进行编辑或删除",
        options=id_options,
        format_func=lambda x: id_labels.get(x, str(x)),
        key="ex_select_id",
    )

    if selected_id:
        col_edit, col_delete_area = st.columns([3, 1])

        with col_edit:
            with st.expander("编辑所选记录", expanded=False):
                _render_ex_edit_form(selected_id)

        with col_delete_area:
            st.markdown("**删除记录**")
            confirm = st.checkbox(
                "确认删除",
                key=f"confirm_del_ex_{selected_id}",
            )
            if st.button(
                "删除",
                key=f"del_ex_{selected_id}",
                type="primary",
                disabled=not confirm,
            ):
                with get_conn(DEFAULT_DB_PATH) as conn:
                    delete_exercise(conn, selected_id)
                st.success(f"已删除运动记录 ID={selected_id}")
                st.cache_data.clear()
                st.rerun()


def render():
    st.header("Garmin 数据管理")

    # 日期范围选择
    col_s, col_e, col_days = st.columns([2, 2, 2])
    with col_days:
        quick_range = st.selectbox(
            "快速范围",
            ["自定义", "最近7天", "最近30天", "最近90天", "最近180天"],
            index=1,
            key="garmin_quick_range",
        )

    today = date.today()
    default_start = today - timedelta(days=6)
    if quick_range == "最近7天":
        default_start = today - timedelta(days=6)
    elif quick_range == "最近30天":
        default_start = today - timedelta(days=29)
    elif quick_range == "最近90天":
        default_start = today - timedelta(days=89)
    elif quick_range == "最近180天":
        default_start = today - timedelta(days=179)

    with col_s:
        start_date = st.date_input(
            "开始日期",
            value=default_start,
            key="garmin_start_date",
            disabled=(quick_range != "自定义"),
        )
    with col_e:
        end_date = st.date_input(
            "结束日期",
            value=today,
            key="garmin_end_date",
            disabled=(quick_range != "自定义"),
        )

    if quick_range != "自定义":
        start_date = default_start
        end_date = today

    if start_date > end_date:
        st.error("开始日期不能晚于结束日期")
        return

    # 计算需要加载的天数
    load_days = (end_date - start_date).days + 1 + 1

    # 加载数据
    df_dh = load_daily_health(days=max(load_days, 7))
    df_ex = load_exercises(days=max(load_days, 7))

    st.divider()

    tab1, tab2 = st.tabs(["📊 健康数据", "🏃 运动记录"])

    with tab1:
        _tab_daily_health(df_dh, start_date, end_date)

    with tab2:
        _tab_exercises(df_ex, start_date, end_date)
