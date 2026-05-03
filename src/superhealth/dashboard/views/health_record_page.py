"""健康档案页面 —— 病情管理。

展示并编辑 medical_conditions 表，重点是配置各病情的复诊间隔
（follow_up_months / follow_up_hospital / follow_up_department），
appointment_scheduler 会依据这些字段自动推算下次应诊日期。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from superhealth import database as db
from superhealth.collectors.fetch_garmin import BASE_DIR

DB_PATH = BASE_DIR / "health.db"

_STATUS_LABELS = {"active": "在治/随访", "resolved": "已缓解", "suspected": "疑似"}
_STATUS_VALUES = {v: k for k, v in _STATUS_LABELS.items()}
_STATUS_OPTIONS = list(_STATUS_LABELS.values())


def _load_conditions() -> pd.DataFrame:
    with db.get_conn(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, name, status, onset_date, follow_up_months, "
            "follow_up_hospital, follow_up_department, notes "
            "FROM medical_conditions ORDER BY status, name"
        ).fetchall()
    if not rows:
        return pd.DataFrame(columns=[
            "id", "name", "status", "onset_date",
            "follow_up_months", "follow_up_hospital", "follow_up_department", "notes",
        ])
    df = pd.DataFrame([dict(r) for r in rows])
    df["status"] = df["status"].map(_STATUS_LABELS).fillna(df["status"])
    return df


def _save_condition(
    conn,
    *,
    name: str,
    status: str,
    onset_date: str | None,
    follow_up_months: int | None,
    follow_up_hospital: str | None,
    follow_up_department: str | None,
    notes: str | None,
) -> None:
    db.upsert_medical_condition(
        conn,
        name=name,
        status=_STATUS_VALUES.get(status, status),
        onset_date=onset_date or None,
        follow_up_months=follow_up_months or None,
        follow_up_hospital=follow_up_hospital or None,
        follow_up_department=follow_up_department or None,
        notes=notes or None,
    )


def render() -> None:
    st.title("健康档案")
    st.caption("管理病情清单，并配置各病情的复诊间隔，定时任务将自动推算下次应诊日期。")

    # ── 添加新病情 ────────────────────────────────────────────────────
    with st.expander("添加新病情", expanded=False):
        with st.form("add_condition_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            new_name = c1.text_input("病情名称 *", placeholder="如：原发性开角型青光眼")
            new_status = c2.selectbox("状态", _STATUS_OPTIONS, index=0)
            c3, c4 = st.columns(2)
            new_onset = c3.text_input("发病/确诊日期", placeholder="YYYY-MM-DD")
            new_months = c4.number_input(
                "复诊间隔（月）", min_value=1, max_value=60, value=6, step=1
            )
            c5, c6 = st.columns(2)
            new_hospital = c5.text_input("复诊医院", placeholder="如：同仁医院")
            new_dept = c6.text_input("复诊科室", placeholder="如：眼科")
            new_notes = st.text_area("备注", height=80)
            submitted = st.form_submit_button("添加", type="primary")

        if submitted:
            if not new_name.strip():
                st.error("病情名称不能为空")
            else:
                try:
                    with db.get_conn(DB_PATH) as conn:
                        _save_condition(
                            conn,
                            name=new_name.strip(),
                            status=new_status,
                            onset_date=new_onset.strip() or None,
                            follow_up_months=int(new_months),
                            follow_up_hospital=new_hospital.strip() or None,
                            follow_up_department=new_dept.strip() or None,
                            notes=new_notes.strip() or None,
                        )
                    st.success(f"已添加：{new_name.strip()}")
                    st.rerun()
                except Exception as e:
                    st.error(f"保存失败：{e}")

    st.divider()

    # ── 病情列表（可内联编辑）────────────────────────────────────────
    st.subheader("病情清单")

    df = _load_conditions()
    if df.empty:
        st.info("暂无病情记录，点击上方「添加新病情」开始录入。")
        return

    edited = st.data_editor(
        df.drop(columns=["id"]),
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        column_config={
            "name": st.column_config.TextColumn("病情名称", disabled=True, width="medium"),
            "status": st.column_config.SelectboxColumn(
                "状态", options=_STATUS_OPTIONS, width="small"
            ),
            "onset_date": st.column_config.TextColumn("确诊日期", width="small"),
            "follow_up_months": st.column_config.NumberColumn(
                "复诊间隔（月）", min_value=1, max_value=60, step=1, width="small"
            ),
            "follow_up_hospital": st.column_config.TextColumn("复诊医院", width="medium"),
            "follow_up_department": st.column_config.TextColumn("复诊科室", width="small"),
            "notes": st.column_config.TextColumn("备注", width="large"),
        },
        key="conditions_editor",
    )

    col_save, col_del = st.columns([1, 3])

    if col_save.button("保存修改", type="primary", key="btn_save_conditions"):
        try:
            with db.get_conn(DB_PATH) as conn:
                for i, row in edited.iterrows():
                    original_name = df.iloc[i]["name"]
                    _save_condition(
                        conn,
                        name=original_name,
                        status=row["status"],
                        onset_date=row.get("onset_date") or None,
                        follow_up_months=int(row["follow_up_months"]) if pd.notna(row.get("follow_up_months")) else None,
                        follow_up_hospital=row.get("follow_up_hospital") or None,
                        follow_up_department=row.get("follow_up_department") or None,
                        notes=row.get("notes") or None,
                    )
            st.success("已保存")
            st.rerun()
        except Exception as e:
            st.error(f"保存失败：{e}")

    # ── 删除 ─────────────────────────────────────────────────────────
    st.divider()
    with st.expander("删除病情", expanded=False):
        del_name = st.selectbox(
            "选择要删除的病情",
            options=df["name"].tolist(),
            key="del_condition_select",
        )
        if st.button("确认删除", type="secondary", key="btn_del_condition"):
            with db.get_conn(DB_PATH) as conn:
                conn.execute("DELETE FROM medical_conditions WHERE name = ?", (del_name,))
            st.success(f"已删除：{del_name}")
            st.rerun()
