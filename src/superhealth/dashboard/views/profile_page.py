"""用户基本档案设置页面。

读写 data/profile/profile.md，存储姓名、出生日期、性别、身高等基本信息。
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from superhealth.user_profile import PROFILE_PATH, read_profile, write_profile

_GENDER_OPTIONS = ["male", "female"]
_GENDER_LABELS = {"male": "男", "female": "女"}
_GENDER_VALUES = {"男": "male", "女": "female"}


def _migrate_from_db() -> dict | None:
    """如果 profile.md 不存在，尝试从旧 user_profile 表读取数据。"""
    try:
        from superhealth import database as db
        from superhealth.collectors.fetch_garmin import BASE_DIR

        db_path = BASE_DIR / "health.db"
        with db.get_conn(db_path) as conn:
            rows = conn.execute("SELECT key, value FROM user_profile").fetchall()
            if rows:
                return {row["key"]: row["value"] for row in rows}
    except Exception:
        pass
    return None


def render() -> None:
    st.title("个人档案")
    st.caption("设置基本健康档案，保存后用于风险预测、BMI 计算等功能。")

    existing = read_profile()

    # 迁移提示：profile.md 不存在但旧表有数据
    if not PROFILE_PATH.exists():
        old_data = _migrate_from_db()
        if old_data:
            st.info("检测到旧档案数据，已从数据库预填，请确认后点击保存。")
            existing = old_data

    # ── 表单 ──────────────────────────────────────────────────────────
    with st.form("profile_form"):
        name = st.text_input(
            "姓名（可选）",
            value=existing.get("name", ""),
            key="prof_name",
        )

        # 出生日期
        bd_str = existing.get("birthdate", "")
        try:
            bd_default = date.fromisoformat(bd_str) if bd_str else date(1990, 1, 1)
        except ValueError:
            bd_default = date(1990, 1, 1)
        birthdate = st.date_input(
            "出生日期",
            value=bd_default,
            min_value=date(1900, 1, 1),
            max_value=date.today(),
            key="prof_birthdate",
        )

        # 性别
        current_gender_label = _GENDER_LABELS.get(existing.get("gender", ""), "男")
        gender_label = st.selectbox(
            "性别",
            options=["男", "女"],
            index=["男", "女"].index(current_gender_label),
            key="prof_gender",
        )

        # 身高
        try:
            height_default = float(existing.get("height_cm", 170))
        except (ValueError, TypeError):
            height_default = 170.0
        height_cm = st.number_input(
            "身高（cm）",
            value=height_default,
            min_value=50.0,
            max_value=250.0,
            step=0.5,
            format="%.1f",
            key="prof_height",
        )

        submitted = st.form_submit_button("保存", type="primary")

    if submitted:
        data: dict = {
            "birthdate": birthdate.isoformat(),
            "gender": _GENDER_VALUES[gender_label],
            "height_cm": f"{height_cm:.1f}",
        }
        if name.strip():
            data["name"] = name.strip()
        write_profile(data)
        st.success("档案已保存")
        st.rerun()

    # ── 当前文件预览 ──────────────────────────────────────────────────
    if PROFILE_PATH.exists():
        with st.expander("查看 profile.md 内容", expanded=False):
            st.code(PROFILE_PATH.read_text(encoding="utf-8"), language="yaml")
            st.caption(f"文件路径：`{PROFILE_PATH}`")
