"""SQLite 存储层：健康数据的结构化持久化。

设计原则：
- 单文件数据库，零配置，易备份
- Markdown 保留为人可读归档，SQLite 做分析引擎
- 所有写操作幂等（UPSERT），重复导入安全
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from superhealth.models import DailyHealth

DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "health.db"

# Whitelist of allowed columns for kwargs-based insert functions
_EYE_EXAM_COLS = {
    "date", "doctor", "hospital", "od_vision", "od_iop", "od_cd_ratio",
    "os_vision", "os_iop", "os_cd_ratio", "fundus_note", "prescription", "note",
}
_KIDNEY_ULTRASOUND_COLS = {
    "date", "right_length_cm", "right_finding", "left_length_cm", "left_finding",
    "right_ureter", "left_ureter", "prostate", "conclusion", "doctor",
}
_ANNUAL_CHECKUP_COLS = {
    "checkup_date", "institution", "height_cm", "weight_kg", "bmi", "systolic",
    "diastolic", "heart_rate", "uric_acid", "creatinine", "urea", "cystatin_c",
    "total_cholesterol", "triglyceride", "ldl_c", "hdl_c", "fasting_glucose",
    "hba1c", "alt", "ast", "ggt", "wbc", "rbc", "hgb", "hct", "plt", "t3", "t4",
    "tsh", "afp", "cea", "t_psa", "nse", "cyfra211", "vision_right", "vision_left",
    "iop_right", "iop_left", "cup_disc_ratio", "thyroid_note", "lung_note",
    "ultrasound_note", "abnormal_summary", "raw_text",
}
_MEDICATION_COLS = {
    "name", "condition", "start_date", "end_date", "dosage", "frequency", "note",
}
_MEDICATION_EFFECT_COLS = {
    "medication_id", "lab_result_id", "eye_exam_id", "checkup_date",
    "expected_effect", "actual_effect", "is_effective", "note",
}


def _validate_kwargs(kwargs: dict, allowed: set[str], func_name: str) -> dict:
    """Validate kwargs keys against allowed column names."""
    invalid = set(kwargs.keys()) - allowed
    if invalid:
        raise ValueError(f"{func_name}: unknown column(s): {invalid}")
    return kwargs


@contextmanager
def get_conn(db_path: Path = DEFAULT_DB_PATH):
    """获取数据库连接，自动提交/回滚。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_SCHEMA_FILE = Path(__file__).parent.parent.parent / "schema.sql"


def init_db(db_path: Path = DEFAULT_DB_PATH):
    """执行 schema.sql，幂等创建/更新所有表结构。

    云端部署后运行一次即可同步最新 schema：
        python -c "from superhealth.database import init_db; init_db()"

    schema.sql 规则：
    - CREATE TABLE IF NOT EXISTS → 直接执行，天然幂等
    - ALTER TABLE ... ADD COLUMN → 忽略"列已存在"错误，幂等
    """
    raw = _SCHEMA_FILE.read_text(encoding="utf-8")
    # 去掉注释行，避免注释内的分号干扰语句分割
    lines = [ln for ln in raw.splitlines() if not ln.strip().startswith("--")]
    schema_sql = "\n".join(lines)
    statements = [s.strip() for s in schema_sql.split(";") if s.strip()]
    with get_conn(db_path) as conn:
        for stmt in statements:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as e:
                # ALTER TABLE ADD COLUMN 在列已存在时报错，静默跳过
                if "duplicate column name" in str(e).lower():
                    continue
                raise


# ─── DailyHealth CRUD ────────────────────────────────────────────────

# effect_tracker 直接依赖的核心指标，变更时记入审计表
_AUDIT_FIELDS = {
    "sleep_score": "sleep_score",
    "hr_resting": "hr_resting",
    "hrv_last_night_avg": "hrv_last_night_avg",
    "stress_average": "stress_average",
    "bb_at_wake": "bb_at_wake",
    "steps": "steps",
}


def _audit_daily_health_change(conn: sqlite3.Connection, date_str: str, old: dict, new: dict):
    """比较新旧值，将核心指标变化写入 daily_health_audit。"""
    for field, col in _AUDIT_FIELDS.items():
        old_val = old.get(col)
        new_val = new.get(col)
        if old_val != new_val and (old_val is not None or new_val is not None):
            conn.execute(
                """INSERT INTO daily_health_audit (date, field_name, old_value, new_value)
                   VALUES (?, ?, ?, ?)""",
                (date_str, field, old_val, new_val),
            )


def upsert_daily_health(conn: sqlite3.Connection, dh: DailyHealth):
    """写入或更新一天的 Garmin 健康数据，核心指标变化时记入审计表。"""
    now_iso = datetime.now().isoformat()

    # 1. 读取旧值（用于审计对比）
    old_row = conn.execute(
        """SELECT sleep_score, hr_resting, hrv_last_night_avg,
                  stress_average, bb_at_wake, steps
           FROM daily_health WHERE date = ?""",
        (dh.date,),
    ).fetchone()
    old = dict(old_row) if old_row else {}

    # 2. 执行 upsert
    conn.execute(
        """
        INSERT INTO daily_health (
            date,
            sleep_total_seconds, sleep_deep_seconds, sleep_light_seconds,
            sleep_rem_seconds, sleep_awake_seconds, sleep_score,
            stress_average, stress_max, stress_rest_seconds,
            stress_low_seconds, stress_medium_seconds, stress_high_seconds,
            hr_resting, hr_min, hr_max, hr_avg7_resting,
            bb_highest, bb_lowest, bb_charged, bb_drained, bb_at_wake,
            spo2_average, spo2_lowest, spo2_latest,
            resp_waking_avg, resp_highest, resp_lowest,
            steps, distance_meters, active_calories, floors_ascended,
            hrv_last_night_avg, hrv_last_night_5min_high, hrv_weekly_avg,
            hrv_baseline_low, hrv_baseline_high, hrv_status,
            raw_json, fetched_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        ) ON CONFLICT(date) DO UPDATE SET
            sleep_total_seconds=excluded.sleep_total_seconds,
            sleep_deep_seconds=excluded.sleep_deep_seconds,
            sleep_light_seconds=excluded.sleep_light_seconds,
            sleep_rem_seconds=excluded.sleep_rem_seconds,
            sleep_awake_seconds=excluded.sleep_awake_seconds,
            sleep_score=excluded.sleep_score,
            stress_average=excluded.stress_average,
            stress_max=excluded.stress_max,
            stress_rest_seconds=excluded.stress_rest_seconds,
            stress_low_seconds=excluded.stress_low_seconds,
            stress_medium_seconds=excluded.stress_medium_seconds,
            stress_high_seconds=excluded.stress_high_seconds,
            hr_resting=excluded.hr_resting,
            hr_min=excluded.hr_min,
            hr_max=excluded.hr_max,
            hr_avg7_resting=excluded.hr_avg7_resting,
            bb_highest=excluded.bb_highest,
            bb_lowest=excluded.bb_lowest,
            bb_charged=excluded.bb_charged,
            bb_drained=excluded.bb_drained,
            bb_at_wake=excluded.bb_at_wake,
            spo2_average=excluded.spo2_average,
            spo2_lowest=excluded.spo2_lowest,
            spo2_latest=excluded.spo2_latest,
            resp_waking_avg=excluded.resp_waking_avg,
            resp_highest=excluded.resp_highest,
            resp_lowest=excluded.resp_lowest,
            steps=excluded.steps,
            distance_meters=excluded.distance_meters,
            active_calories=excluded.active_calories,
            floors_ascended=excluded.floors_ascended,
            hrv_last_night_avg=excluded.hrv_last_night_avg,
            hrv_last_night_5min_high=excluded.hrv_last_night_5min_high,
            hrv_weekly_avg=excluded.hrv_weekly_avg,
            hrv_baseline_low=excluded.hrv_baseline_low,
            hrv_baseline_high=excluded.hrv_baseline_high,
            hrv_status=excluded.hrv_status,
            raw_json=excluded.raw_json,
            fetched_at=excluded.fetched_at
    """,
        (
            dh.date,
            dh.sleep.total_seconds,
            dh.sleep.deep_seconds,
            dh.sleep.light_seconds,
            dh.sleep.rem_seconds,
            dh.sleep.awake_seconds,
            dh.sleep.score,
            dh.stress.average,
            dh.stress.max,
            dh.stress.rest_seconds,
            dh.stress.low_seconds,
            dh.stress.medium_seconds,
            dh.stress.high_seconds,
            dh.heart_rate.resting,
            dh.heart_rate.min,
            dh.heart_rate.max,
            dh.heart_rate.avg7_resting,
            dh.body_battery.highest,
            dh.body_battery.lowest,
            dh.body_battery.charged,
            dh.body_battery.drained,
            dh.body_battery.at_wake,
            dh.spo2.average,
            dh.spo2.lowest,
            dh.spo2.latest,
            dh.respiration.waking_avg,
            dh.respiration.highest,
            dh.respiration.lowest,
            dh.activity.steps,
            dh.activity.distance_meters,
            dh.activity.active_calories,
            dh.activity.floors_ascended,
            dh.hrv.last_night_avg,
            dh.hrv.last_night_5min_high,
            dh.hrv.weekly_avg,
            dh.hrv.baseline_low,
            dh.hrv.baseline_high,
            dh.hrv.status,
            dh.model_dump_json(),
            now_iso,
        ),
    )

    # 3. 核心指标变更审计
    new = {
        "sleep_score": dh.sleep.score,
        "hr_resting": dh.heart_rate.resting,
        "hrv_last_night_avg": dh.hrv.last_night_avg,
        "stress_average": dh.stress.average,
        "bb_at_wake": dh.body_battery.at_wake,
        "steps": dh.activity.steps,
    }
    _audit_daily_health_change(conn, dh.date, old, new)

    # 运动记录：先删后插
    conn.execute("DELETE FROM exercises WHERE date = ?", (dh.date,))
    for ex in dh.exercises:
        conn.execute(
            """
            INSERT INTO exercises (date, name, type_key, start_time, distance_meters,
                duration_seconds, avg_hr, max_hr, avg_speed, calories, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                dh.date,
                ex.name,
                ex.type_key,
                ex.start_time,
                ex.distance_meters,
                ex.duration_seconds,
                ex.avg_hr,
                ex.max_hr,
                ex.avg_speed,
                ex.calories,
                ex.details,
            ),
        )


def load_daily_health_from_db(conn: sqlite3.Connection, day_str: str) -> Optional[DailyHealth]:
    """从 SQLite 加载一天的数据，返回 DailyHealth 或 None。"""
    row = conn.execute("SELECT raw_json FROM daily_health WHERE date = ?", (day_str,)).fetchone()
    if row and row["raw_json"]:
        return DailyHealth.model_validate_json(row["raw_json"])
    return None


def query_daily_flat(conn: sqlite3.Connection, day_str: str) -> Optional[dict]:
    """从 SQLite 加载一天的扁平字典（兼容 analyze_garmin.py）。"""
    row = conn.execute("SELECT * FROM daily_health WHERE date = ?", (day_str,)).fetchone()
    if not row:
        return None
    return {
        "date": row["date"],
        "sleep_total_min": row["sleep_total_seconds"] // 60 if row["sleep_total_seconds"] else None,
        "sleep_deep_min": row["sleep_deep_seconds"] // 60 if row["sleep_deep_seconds"] else None,
        "sleep_rem_min": row["sleep_rem_seconds"] // 60 if row["sleep_rem_seconds"] else None,
        "sleep_awake_min": row["sleep_awake_seconds"] // 60 if row["sleep_awake_seconds"] else None,
        "sleep_score": row["sleep_score"],
        "avg_stress": row["stress_average"],
        "max_stress": row["stress_max"],
        "resting_hr": row["hr_resting"],
        "min_hr": row["hr_min"],
        "max_hr": row["hr_max"],
        "avg7_resting_hr": row["hr_avg7_resting"],
        "body_battery_highest": row["bb_highest"],
        "body_battery_lowest": row["bb_lowest"],
        "body_battery_wake": row["bb_at_wake"],
        "spo2_avg": row["spo2_average"],
        "spo2_lowest": row["spo2_lowest"],
        "spo2_latest": row["spo2_latest"],
        "resp_waking": row["resp_waking_avg"],
        "steps": row["steps"],
        "distance_km": round(row["distance_meters"] / 1000, 1) if row["distance_meters"] else None,
        "hrv_avg": row["hrv_last_night_avg"],
        "hrv_weekly": row["hrv_weekly_avg"],
        "hrv_baseline_low": row["hrv_baseline_low"],
        "hrv_baseline_high": row["hrv_baseline_high"],
        "hrv_status": row["hrv_status"],
    }


def query_date_range(conn: sqlite3.Connection, start: str, end: str) -> list[dict]:
    """查询日期范围内的扁平字典列表。"""
    rows = conn.execute(
        "SELECT date FROM daily_health WHERE date BETWEEN ? AND ? ORDER BY date",
        (start, end),
    ).fetchall()
    results = []
    for row in rows:
        d = query_daily_flat(conn, row["date"])
        if d:
            results.append(d)
    return results


# ─── 化验结果 CRUD ───────────────────────────────────────────────────


def insert_lab_result(
    conn: sqlite3.Connection,
    *,
    date: str,
    source: str,
    item_name: str,
    item_code: str | None = None,
    value: float | None = None,
    unit: str | None = None,
    ref_low: float | None = None,
    ref_high: float | None = None,
    is_abnormal: int = 0,
    note: str | None = None,
):
    conn.execute(
        """
        INSERT INTO lab_results (date, source, item_name, item_code,
            value, unit, ref_low, ref_high, is_abnormal, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (date, source, item_name, item_code, value, unit, ref_low, ref_high, is_abnormal, note),
    )


# ─── 眼科检查 CRUD ──────────────────────────────────────────────────


def insert_eye_exam(conn: sqlite3.Connection, **kwargs):
    _validate_kwargs(kwargs, _EYE_EXAM_COLS, "insert_eye_exam")
    cols = ", ".join(f'"{k}"' for k in kwargs.keys())
    placeholders = ", ".join(["?"] * len(kwargs))
    conn.execute(
        f"INSERT INTO eye_exams ({cols}) VALUES ({placeholders})",
        tuple(kwargs.values()),
    )


# ─── 肾脏彩超 CRUD ──────────────────────────────────────────────────


def insert_kidney_ultrasound(conn: sqlite3.Connection, **kwargs):
    _validate_kwargs(kwargs, _KIDNEY_ULTRASOUND_COLS, "insert_kidney_ultrasound")
    cols = ", ".join(f'"{k}"' for k in kwargs.keys())
    placeholders = ", ".join(["?"] * len(kwargs))
    conn.execute(
        f"INSERT INTO kidney_ultrasounds ({cols}) VALUES ({placeholders})",
        tuple(kwargs.values()),
    )


# ─── 体征 CRUD ──────────────────────────────────────────────────────


def insert_vital(
    conn: sqlite3.Connection,
    *,
    measured_at: str,
    source: str = "health_auto_export",
    systolic: Optional[int] = None,
    diastolic: Optional[int] = None,
    weight_kg: Optional[float] = None,
    body_fat_pct: Optional[float] = None,
):
    """写入一条体征记录（来自 Health Auto Export）。"""
    conn.execute(
        """
        INSERT INTO vitals (measured_at, source, systolic, diastolic,
            weight_kg, body_fat_pct)
        VALUES (?, ?, ?, ?, ?, ?)
    """,
        (measured_at, source, systolic, diastolic, weight_kg, body_fat_pct),
    )


def query_vitals_by_date(conn: sqlite3.Connection, date_str: str) -> Optional[dict]:
    """查询某一天的最新体征记录（按 measured_at 倒序取第一条）。

    date_str: YYYY-MM-DD 格式
    """
    row = conn.execute(
        """SELECT measured_at, systolic, diastolic,
                  weight_kg, body_fat_pct
           FROM vitals
           WHERE measured_at LIKE ?
           ORDER BY measured_at DESC
           LIMIT 1""",
        (f"{date_str}%",),
    ).fetchone()
    if not row:
        return None
    return {
        "measured_at": row["measured_at"],
        "systolic": row["systolic"],
        "diastolic": row["diastolic"],
        "weight_kg": row["weight_kg"],
        "body_fat_pct": row["body_fat_pct"],
    }


# ─── 年度体检 CRUD ──────────────────────────────────────────────────


def upsert_annual_checkup(conn: sqlite3.Connection, **kwargs):
    """写入或更新一次年度体检记录（以 checkup_date 为唯一键）。"""
    _validate_kwargs(kwargs, _ANNUAL_CHECKUP_COLS, "upsert_annual_checkup")
    cols = ", ".join(f'"{k}"' for k in kwargs.keys())
    placeholders = ", ".join(["?"] * len(kwargs))
    updates = ", ".join(f'"{k}"=excluded."{k}"' for k in kwargs if k != "checkup_date")
    conn.execute(
        f"INSERT INTO annual_checkups ({cols}) VALUES ({placeholders})"
        f" ON CONFLICT(checkup_date) DO UPDATE SET {updates}",
        tuple(kwargs.values()),
    )


# ─── 用药 CRUD ──────────────────────────────────────────────────────


def insert_medication(conn: sqlite3.Connection, **kwargs):
    _validate_kwargs(kwargs, _MEDICATION_COLS, "insert_medication")
    cols = ", ".join(f'"{k}"' for k in kwargs.keys())
    placeholders = ", ".join(["?"] * len(kwargs))
    conn.execute(
        f"INSERT INTO medications ({cols}) VALUES ({placeholders})",
        tuple(kwargs.values()),
    )


def query_active_medications(conn: sqlite3.Connection) -> list[dict]:
    """查询当前在用的药物列表。"""
    rows = conn.execute(
        """SELECT id, name, condition, start_date, dosage, frequency, note
           FROM medications
           WHERE end_date IS NULL OR end_date = ''
           ORDER BY condition, name"""
    ).fetchall()
    return [dict(row) for row in rows]


def query_medication_by_condition(conn: sqlite3.Connection, condition: str) -> list[dict]:
    """按疾病查询用药记录。"""
    rows = conn.execute(
        """SELECT * FROM medications WHERE condition = ? ORDER BY start_date""", (condition,)
    ).fetchall()
    return [dict(row) for row in rows]


# ─── 用药效果关联 CRUD ───────────────────────────────────────────────


def insert_medication_effect(conn: sqlite3.Connection, **kwargs):
    """记录用药与检查结果的关联。

    kwargs 可包含:
    - medication_id: 药物ID（必填）
    - lab_result_id: 关联的化验结果ID
    - eye_exam_id: 关联的眼科检查ID
    - checkup_date: 关联的体检日期
    - expected_effect: 预期效果描述
    - actual_effect: 实际观察效果
    - is_effective: 1=有效, 0=无效, NULL=待评估
    - note: 备注
    """
    _validate_kwargs(kwargs, _MEDICATION_EFFECT_COLS, "insert_medication_effect")
    cols = ", ".join(f'"{k}"' for k in kwargs.keys())
    placeholders = ", ".join(["?"] * len(kwargs))
    conn.execute(
        f"INSERT INTO medication_effects ({cols}) VALUES ({placeholders})",
        tuple(kwargs.values()),
    )


def query_medication_effects(conn: sqlite3.Connection, medication_id: int) -> list[dict]:
    """查询某药物的所有效果记录。"""
    rows = conn.execute(
        """SELECT * FROM medication_effects
           WHERE medication_id = ?
           ORDER BY recorded_at DESC""",
        (medication_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def query_lab_results_with_medication(
    conn: sqlite3.Connection, item_name: str, medication_name: str
) -> list[dict]:
    """查询特定化验指标在用药前后的变化。

    返回包含化验结果和关联用药信息的列表。
    """
    rows = conn.execute(
        """SELECT lr.*, m.name as med_name, m.start_date as med_start,
                  me.expected_effect, me.is_effective
           FROM lab_results lr
           LEFT JOIN medication_effects me ON lr.id = me.lab_result_id
           LEFT JOIN medications m ON me.medication_id = m.id
           WHERE lr.item_name = ?
           ORDER BY lr.date""",
        (item_name,),
    ).fetchall()
    return [dict(row) for row in rows]


# ─── 天气 CRUD ──────────────────────────────────────────────────────


def upsert_weather(
    conn: sqlite3.Connection,
    *,
    date: str,
    condition: str | None = None,
    temperature: float | None = None,
    temp_max: float | None = None,
    temp_min: float | None = None,
    wind_scale: int | None = None,
    aqi: float | None = None,
    outdoor_ok: int | None = None,
):
    """写入或更新一天的天气数据（以 date 为唯一键）。"""
    conn.execute(
        """
        INSERT INTO weather (date, condition, temperature, temp_max, temp_min, wind_scale, aqi, outdoor_ok)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            condition=excluded.condition,
            temperature=excluded.temperature,
            temp_max=excluded.temp_max,
            temp_min=excluded.temp_min,
            wind_scale=excluded.wind_scale,
            aqi=excluded.aqi,
            outdoor_ok=excluded.outdoor_ok
    """,
        (date, condition, temperature, temp_max, temp_min, wind_scale, aqi, outdoor_ok),
    )


# ─── 日历事件 CRUD ──────────────────────────────────────────────────


def insert_calendar_events(conn: sqlite3.Connection, *, date: str, events: list[dict]):
    """写入一天的所有日历事件（先删后插，保证幂等）。"""
    conn.execute("DELETE FROM calendar_events WHERE date = ?", (date,))
    for ev in events:
        conn.execute(
            """
            INSERT INTO calendar_events
                (date, subject, start_time, end_time, duration_min,
                 is_all_day, location, organizer, is_recurring)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                date,
                ev.get("subject"),
                ev.get("start_time"),
                ev.get("end_time"),
                ev.get("duration_min"),
                ev.get("is_all_day", 0),
                ev.get("location"),
                ev.get("organizer"),
                ev.get("is_recurring", 0),
            ),
        )


def query_calendar_events(conn: sqlite3.Connection, date_str: str) -> list[dict]:
    """查询某天的所有日历事件，按开始时间排序。"""
    rows = conn.execute(
        """
        SELECT date, subject, start_time, end_time, duration_min,
               is_all_day, location, organizer, is_recurring
        FROM calendar_events
        WHERE date = ?
        ORDER BY start_time
        """,
        (date_str,),
    ).fetchall()
    return [dict(row) for row in rows]


def query_calendar_events_multi(
    conn: sqlite3.Connection, date_strs: list[str]
) -> dict[str, list[dict]]:
    """批量查询多个日期的日历事件，返回 {date_str: [events]}。

    避免在 _find_control_days() 中对每个候选日单独查询的 N+1 问题。
    """
    if not date_strs:
        return {}
    placeholders = ",".join("?" * len(date_strs))
    rows = conn.execute(
        f"""
        SELECT date, subject, start_time, end_time, duration_min,
               is_all_day, location, organizer, is_recurring
        FROM calendar_events
        WHERE date IN ({placeholders})
        ORDER BY date, start_time
        """,
        date_strs,
    ).fetchall()
    result: dict[str, list[dict]] = {}
    for row in rows:
        d = row["date"]
        result.setdefault(d, []).append(dict(row))
    return result


def query_weather(conn: sqlite3.Connection, date_str: str) -> Optional[dict]:
    """查询某天的天气记录，返回 dict 或 None。"""
    row = conn.execute("SELECT * FROM weather WHERE date = ?", (date_str,)).fetchone()
    if not row:
        return None
    col_names = row.keys()
    return {
        "date": row["date"],
        "condition": row["condition"],
        "temperature": row["temperature"],
        "temp_max": row["temp_max"] if "temp_max" in col_names else None,
        "temp_min": row["temp_min"] if "temp_min" in col_names else None,
        "wind_scale": row["wind_scale"],
        "aqi": row["aqi"],
        "outdoor_ok": bool(row["outdoor_ok"]),
    }


# ─── 反馈 CRUD（Phase 4）────────────────────────────────────────────


def insert_recommendation_feedback(
    conn: sqlite3.Connection,
    *,
    date: str,
    report_id: str,
    recommendation_type: str = None,
    recommendation_content: str = None,
    compliance: int = None,
    actual_action: str = None,
    tracked_metrics: str = None,
):
    """写入一条建议执行反馈。tracked_metrics 为 JSON 字符串。

    Args:
        compliance: 遵从度百分比，0-100 的整数（如 85 表示 85% 符合建议）
    """
    conn.execute(
        """
        INSERT INTO recommendation_feedback
            (date, report_id, recommendation_type, recommendation_content,
             compliance, actual_action, tracked_metrics, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))
    """,
        (
            date,
            report_id,
            recommendation_type,
            recommendation_content,
            compliance,
            actual_action,
            tracked_metrics,
        ),
    )


def update_recommendation_feedback(
    conn: sqlite3.Connection,
    *,
    date: str,
    recommendation_type: str = None,
    compliance: int,
    actual_action: str = None,
) -> bool:
    """更新已有反馈记录的 compliance / actual_action。

    Args:
        recommendation_type: 建议类型，None 表示更新该日期的任意类型记录
        compliance: 遵从度百分比，0-100 的整数（如 85 表示 85% 符合建议）

    返回 True 表示成功更新了至少一条记录，False 表示没有匹配记录。
    用于 auto_feedback.py 的两阶段写入（Phase 2）。
    """
    if recommendation_type:
        cursor = conn.execute(
            """
            UPDATE recommendation_feedback
            SET compliance = ?, actual_action = ?
            WHERE date = ? AND recommendation_type = ? AND compliance IS NULL
        """,
            (compliance, actual_action, date, recommendation_type),
        )
    else:
        cursor = conn.execute(
            """
            UPDATE recommendation_feedback
            SET compliance = ?, actual_action = ?
            WHERE date = ? AND compliance IS NULL
        """,
            (compliance, actual_action, date),
        )
    return cursor.rowcount > 0


def update_user_feedback(
    conn: sqlite3.Connection,
    *,
    date: str,
    recommendation_type: str = "exercise",
    user_feedback: str,
) -> bool:
    """记录用户文字反馈。

    将 user_feedback 写入 user_feedback 列。
    返回 True 表示成功更新了至少一条记录。
    """
    cursor = conn.execute(
        """
        UPDATE recommendation_feedback
        SET user_feedback = ?
        WHERE date = ? AND recommendation_type = ?
    """,
        (user_feedback, date, recommendation_type),
    )
    return cursor.rowcount > 0


def update_recommendation_quality_score(
    conn: sqlite3.Connection,
    *,
    date: str,
    recommendation_type: str = "exercise",
    quality_score: float,
) -> bool:
    """更新反馈记录的 quality_score。

    返回 True 表示成功更新了至少一条记录。
    """
    cursor = conn.execute(
        """
        UPDATE recommendation_feedback
        SET quality_score = ?
        WHERE date = ? AND recommendation_type = ?
    """,
        (round(quality_score, 4), date, recommendation_type),
    )
    return cursor.rowcount > 0


def query_feedback_by_date_range(
    conn: sqlite3.Connection, start: str, end: str, recommendation_type: str = None
) -> list[dict]:
    """查询日期范围内的反馈记录，可按类型过滤。"""
    if recommendation_type:
        rows = conn.execute(
            """SELECT * FROM recommendation_feedback
               WHERE date BETWEEN ? AND ?
                 AND recommendation_type = ?
               ORDER BY date""",
            (start, end, recommendation_type),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM recommendation_feedback
               WHERE date BETWEEN ? AND ?
               ORDER BY date""",
            (start, end),
        ).fetchall()
    return [dict(row) for row in rows]


# ─── 学习偏好 CRUD（Phase 4）────────────────────────────────────────


def upsert_learned_preference(
    conn: sqlite3.Connection,
    *,
    preference_type: str,
    preference_key: str,
    preference_value: str,
    confidence_score: float = 0.5,
    evidence_count: int = 1,
    goal_id: int = None,
    status: str = "active",
):
    """写入或更新一条学习到的偏好（以 type+key 为唯一键）。"""
    conn.execute(
        """
        INSERT INTO learned_preferences
            (preference_type, preference_key, preference_value,
             confidence_score, evidence_count, last_updated, goal_id, status)
        VALUES (?, ?, ?, ?, ?, datetime('now','localtime'), ?, ?)
        ON CONFLICT(preference_type, preference_key) DO UPDATE SET
            preference_value = excluded.preference_value,
            confidence_score = excluded.confidence_score,
            evidence_count   = excluded.evidence_count,
            last_updated     = datetime('now','localtime'),
            goal_id          = CASE WHEN excluded.goal_id IS NOT NULL
                               THEN excluded.goal_id ELSE learned_preferences.goal_id END,
            status           = excluded.status
    """,
        (
            preference_type,
            preference_key,
            preference_value,
            confidence_score,
            evidence_count,
            goal_id,
            status,
        ),
    )


def query_learned_preferences(
    conn: sqlite3.Connection, preference_type: str = None, exclude_status: str = None
) -> list[dict]:
    """查询学习偏好，可按 preference_type 和 exclude_status 过滤。

    exclude_status 为 None 时返回所有状态；传入状态值则排除该状态（同时保留 NULL 旧数据）。
    """
    if preference_type and exclude_status:
        rows = conn.execute(
            """SELECT * FROM learned_preferences
               WHERE preference_type = ? AND (status != ? OR status IS NULL)
               ORDER BY confidence_score DESC""",
            (preference_type, exclude_status),
        ).fetchall()
    elif preference_type:
        rows = conn.execute(
            """SELECT * FROM learned_preferences
               WHERE preference_type = ?
               ORDER BY confidence_score DESC""",
            (preference_type,),
        ).fetchall()
    elif exclude_status:
        rows = conn.execute(
            """SELECT * FROM learned_preferences
               WHERE status != ? OR status IS NULL
               ORDER BY preference_type, confidence_score DESC""",
            (exclude_status,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM learned_preferences
               ORDER BY preference_type, confidence_score DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


def query_avg_quality_for_preference(
    conn: sqlite3.Connection, recent_days: int = 30
) -> float | None:
    """查询近期全局平均 quality_score，作为偏好效果的代理指标。"""
    from datetime import timedelta

    since = (date.today() - timedelta(days=recent_days)).isoformat()
    row = conn.execute(
        """SELECT AVG(quality_score) as avg_q, COUNT(*) as n
           FROM recommendation_feedback
           WHERE quality_score IS NOT NULL
             AND date >= ?""",
        (since,),
    ).fetchone()
    if row and row["n"] >= 3:
        return round(row["avg_q"], 4)
    return None


def update_preference_status(
    conn: sqlite3.Connection,
    *,
    preference_type: str,
    preference_key: str,
    status: str,
    confidence_multiplier: float = 1.0,
):
    """更新偏好的生命周期状态，可选调整置信度。"""
    conn.execute(
        """UPDATE learned_preferences
           SET status = ?,
               confidence_score = ROUND(MIN(0.95, confidence_score * ?), 3),
               last_updated = datetime('now','localtime')
           WHERE preference_type = ? AND preference_key = ?""",
        (status, confidence_multiplier, preference_type, preference_key),
    )


# ─── 就医预约提醒 CRUD（Phase 6）────────────────────────────────────


def upsert_appointment(
    conn: sqlite3.Connection,
    *,
    condition: str,
    hospital: Optional[str],
    department: Optional[str],
    due_date: str,
    interval_months: int,
    source_exam_id: Optional[int] = None,
    source_table: Optional[str] = None,
    notes: Optional[str] = None,
):
    """写入或更新一条预约提醒（以 condition 为唯一键，一个病情只保留一条最新预约）。"""
    cursor = conn.execute(
        """
        UPDATE appointments
        SET due_date        = ?,
            interval_months = ?,
            source_exam_id  = ?,
            source_table    = ?,
            hospital        = ?,
            department      = ?,
            notes           = ?,
            status          = 'pending',
            updated_at      = datetime('now','localtime')
        WHERE condition = ?
    """,
        (
            due_date,
            interval_months,
            source_exam_id,
            source_table,
            hospital,
            department,
            notes,
            condition,
        ),
    )
    if cursor.rowcount == 0:
        conn.execute(
            """
            INSERT INTO appointments
                (condition, hospital, department, due_date, interval_months,
                 source_exam_id, source_table, status, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, datetime('now','localtime'))
        """,
            (
                condition,
                hospital,
                department,
                due_date,
                interval_months,
                source_exam_id,
                source_table,
                notes,
            ),
        )


def get_all_appointments(conn: sqlite3.Connection) -> list[dict]:
    """查询所有预约提醒，按 due_date 升序。"""
    rows = conn.execute("SELECT * FROM appointments ORDER BY due_date ASC").fetchall()
    return [dict(row) for row in rows]


def get_pending_appointments(conn: sqlite3.Connection) -> list[dict]:
    """查询状态为 pending / reminded_14 / reminded_7 的预约（即尚未完成的）。"""
    rows = conn.execute(
        """SELECT * FROM appointments
           WHERE status IN ('pending', 'reminded_14', 'reminded_7')
           ORDER BY due_date ASC"""
    ).fetchall()
    return [dict(row) for row in rows]


def mark_appointment_reminded(conn: sqlite3.Connection, *, appointment_id: int, days_left: int):
    """标记预约已在 days_left 天前被提醒（14 或 7）。"""
    status = f"reminded_{days_left}"
    conn.execute(
        "UPDATE appointments SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
        (status, appointment_id),
    )


def mark_appointment_completed(conn: sqlite3.Connection, appointment_id: int):
    """标记预约已完成（就诊后手动调用）。"""
    conn.execute(
        "UPDATE appointments SET status = 'completed', updated_at = datetime('now','localtime') WHERE id = ?",
        (appointment_id,),
    )


# ─── Goals CRUD（Goals 子系统）────────────────────────────────────────


def insert_goal_progress(
    conn: sqlite3.Connection,
    *,
    goal_id: int,
    date: str,
    current_value: float = None,
    delta_from_baseline: float = None,
    progress_pct: float = None,
    note: str = None,
):
    """写入一条目标每日进度快照（INSERT OR REPLACE）。"""
    conn.execute(
        """
        INSERT OR REPLACE INTO goal_progress
            (goal_id, date, current_value, delta_from_baseline, progress_pct, note)
        VALUES (?, ?, ?, ?, ?, ?)
    """,
        (goal_id, date, current_value, delta_from_baseline, progress_pct, note),
    )


def query_active_goals(conn: sqlite3.Connection) -> list[dict]:
    """查询所有 active 目标，按 priority 排序。"""
    rows = conn.execute("SELECT * FROM goals WHERE status = 'active' ORDER BY priority").fetchall()
    return [dict(row) for row in rows]


def query_goal_progress_range(
    conn: sqlite3.Connection, goal_id: int, start_date: str, end_date: str
) -> list[dict]:
    """查询指定目标在日期范围内的进度记录。"""
    rows = conn.execute(
        """SELECT * FROM goal_progress
           WHERE goal_id = ? AND date BETWEEN ? AND ?
           ORDER BY date""",
        (goal_id, start_date, end_date),
    ).fetchall()
    return [dict(row) for row in rows]


# ─── 肝肾指标统一趋势查询（合并 lab_results + annual_checkups）──────────────────

# 指标映射配置：lab_results 查询条件 → annual_checkups 列名
_LIVER_KIDNEY_METRICS = {
    "uric_acid": {
        "lab_item_names": ["尿酸", "血尿酸", "UA"],
        "checkup_column": "uric_acid",
        "unit": "μmol/L",
        "ref_low": 208,
        "ref_high": 428,
    },
    "creatinine": {
        "lab_item_names": ["肌酐", "血肌酐", "Cr", "CREA"],
        "checkup_column": "creatinine",
        "unit": "μmol/L",
        "ref_low": 44,
        "ref_high": 133,
    },
    "urea": {
        "lab_item_names": ["尿素", "尿素氮", "BUN"],
        "checkup_column": "urea",
        "unit": "mmol/L",
        "ref_low": 2.6,
        "ref_high": 7.5,
    },
    "ldl_c": {
        "lab_item_names": ["低密度脂蛋白胆固醇", "LDL-C", "LDL"],
        "checkup_column": "ldl_c",
        "unit": "mmol/L",
        "ref_low": None,
        "ref_high": 3.4,
    },
    "triglyceride": {
        "lab_item_names": ["甘油三酯", "TG", "血脂-甘油三酯"],
        "checkup_column": "triglyceride",
        "unit": "mmol/L",
        "ref_low": None,
        "ref_high": 1.7,
    },
    "hdl_c": {
        "lab_item_names": ["高密度脂蛋白胆固醇", "HDL-C", "HDL"],
        "checkup_column": "hdl_c",
        "unit": "mmol/L",
        "ref_low": 1.0,
        "ref_high": None,
    },
    "total_cholesterol": {
        "lab_item_names": ["总胆固醇", "TC", "Chol"],
        "checkup_column": "total_cholesterol",
        "unit": "mmol/L",
        "ref_low": None,
        "ref_high": 5.2,
    },
    "alt": {
        "lab_item_names": ["丙氨酸氨基转移酶", "ALT", "GPT", "谷丙转氨酶"],
        "checkup_column": "alt",
        "unit": "U/L",
        "ref_low": None,
        "ref_high": 50,
    },
    "ast": {
        "lab_item_names": ["天冬氨酸氨基转移酶", "AST", "GOT", "谷草转氨酶"],
        "checkup_column": "ast",
        "unit": "U/L",
        "ref_low": None,
        "ref_high": 40,
    },
    "ggt": {
        "lab_item_names": [
            "γ-谷氨酰转肽酶",
            "GGT",
            "谷氨酰转肽酶",
            "r-谷氨酰转肽酶",
            "r-GT",
            "γ-GT",
        ],
        "checkup_column": "ggt",
        "unit": "U/L",
        "ref_low": None,
        "ref_high": 60,
    },
    "cystatin_c": {
        "lab_item_names": ["胱抑素C", "Cystatin C", "Cys-C"],
        "checkup_column": "cystatin_c",
        "unit": "mg/L",
        "ref_low": None,
        "ref_high": 1.03,
    },
}


def query_lab_trends_unified(
    conn: sqlite3.Connection,
    metric_key: str,
    start_date: str = None,
    end_date: str = None,
) -> list[dict]:
    """统一查询肝肾/血脂指标时间序列（合并 lab_results + annual_checkups）。

    Args:
        metric_key: 指标代码，如 'uric_acid', 'creatinine', 'ldl_c' 等
        start_date: 可选，起始日期 YYYY-MM-DD
        end_date: 可选，结束日期 YYYY-MM-DD

    Returns:
        按日期排序的时间序列列表，每项包含:
        - date: 日期 YYYY-MM-DD
        - value: 数值
        - source: 'lab_results' 或 'annual_checkups'
        - unit: 单位
        - ref_low: 参考下限
        - ref_high: 参考上限
        - is_abnormal: 是否异常（基于参考范围判断，体检数据自动计算）
    """
    config = _LIVER_KIDNEY_METRICS.get(metric_key)
    if not config:
        raise ValueError(
            f"未知的指标代码: {metric_key}，支持的指标: {list(_LIVER_KIDNEY_METRICS.keys())}"
        )

    # 构建 lab_results 的查询条件
    item_names = config["lab_item_names"]
    name_conditions = " OR ".join(["item_name LIKE ?"] * len(item_names))
    lab_params = [f"%{n}%" for n in item_names]

    date_where = ""
    date_params = []
    if start_date:
        date_where += " AND date >= ?"
        date_params.append(start_date)
    if end_date:
        date_where += " AND date <= ?"
        date_params.append(end_date)

    # 查询 lab_results
    lab_sql = f"""
        SELECT date, value, unit, ref_low, ref_high, is_abnormal
        FROM lab_results
        WHERE ({name_conditions}){date_where}
        ORDER BY date
    """
    lab_rows = conn.execute(lab_sql, lab_params + date_params).fetchall()

    # 查询 annual_checkups
    checkup_col = config["checkup_column"]
    checkup_date_where = ""
    checkup_params = []
    if start_date:
        checkup_date_where += " AND checkup_date >= ?"
        checkup_params.append(start_date)
    if end_date:
        checkup_date_where += " AND checkup_date <= ?"
        checkup_params.append(end_date)

    checkup_sql = f"""
        SELECT checkup_date as date, {checkup_col} as value
        FROM annual_checkups
        WHERE {checkup_col} IS NOT NULL{checkup_date_where}
        ORDER BY checkup_date
    """
    checkup_rows = conn.execute(checkup_sql, checkup_params).fetchall()

    # 合并结果
    results = []
    ref_low = config.get("ref_low")
    ref_high = config.get("ref_high")
    unit = config.get("unit", "")

    for row in lab_rows:
        val = row["value"]
        if val is None:
            continue
        row_ref_low = row["ref_low"] if row["ref_low"] is not None else ref_low
        row_ref_high = row["ref_high"] if row["ref_high"] is not None else ref_high
        is_abnormal = row["is_abnormal"]
        # 如果数据库没标记异常，自动计算
        if is_abnormal == 0 and row_ref_low is not None and row_ref_high is not None:
            is_abnormal = 1 if (val < row_ref_low or val > row_ref_high) else 0

        results.append(
            {
                "date": row["date"],
                "value": val,
                "source": "lab_results",
                "unit": row["unit"] or unit,
                "ref_low": row_ref_low,
                "ref_high": row_ref_high,
                "is_abnormal": is_abnormal,
            }
        )

    for row in checkup_rows:
        val = row["value"]
        if val is None:
            continue
        # 体检数据自动判断异常
        is_abnormal = 0
        if ref_low is not None and ref_high is not None:
            is_abnormal = 1 if (val < ref_low or val > ref_high) else 0

        results.append(
            {
                "date": row["date"],
                "value": val,
                "source": "annual_checkups",
                "unit": unit,
                "ref_low": ref_low,
                "ref_high": ref_high,
                "is_abnormal": is_abnormal,
            }
        )

    # 按日期排序
    results.sort(key=lambda x: x["date"])
    return results


def query_multiple_metrics(
    conn: sqlite3.Connection,
    metric_keys: list[str],
    start_date: str = None,
    end_date: str = None,
) -> dict[str, list[dict]]:
    """批量查询多个指标的趋势数据。

    Args:
        metric_keys: 指标代码列表，如 ['uric_acid', 'creatinine', 'ldl_c']
        start_date: 可选，起始日期
        end_date: 可选，结束日期

    Returns:
        字典，key 为指标代码，value 为该指标的时间序列列表
    """
    return {key: query_lab_trends_unified(conn, key, start_date, end_date) for key in metric_keys}


# ─── 同步日志 CRUD ───────────────────────────────────────────────────


def insert_sync_log(
    conn: sqlite3.Connection,
    date: str,
    step: str,
    status: str,
    error_message: str | None = None,
    source: str = "garmin",
) -> int:
    """记录一次同步/流水线步骤的执行结果。"""
    cursor = conn.execute(
        """
        INSERT INTO sync_logs (date, source, step, status, error_message, created_at)
        VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))
        """,
        (date, source, step, status, error_message),
    )
    return cursor.lastrowid


def query_failed_sync_dates(
    conn: sqlite3.Connection,
    since_days: int = 7,
    step: str = "fetch",
    source: str = "garmin",
) -> list[str]:
    """查询最近 N 天内指定步骤失败的日期列表（去重，升序）。"""
    if not isinstance(since_days, int) or since_days < 0:
        raise ValueError("since_days must be a non-negative integer")
    rows = conn.execute(
        """
        SELECT DISTINCT date FROM sync_logs
        WHERE source = ?
          AND step = ?
          AND status = 'failure'
          AND created_at >= datetime('now', '-' || ? || ' days', 'localtime')
        ORDER BY date ASC
        """,
        (source, step, str(since_days)),
    ).fetchall()
    return [row["date"] for row in rows]


# ─── 用户档案 CRUD ──────────────────────────────────────────────────


def upsert_user_profile(conn: sqlite3.Connection, key: str, value: str):
    """写入/更新一条用户档案（幂等）。"""
    conn.execute(
        """
        INSERT INTO user_profile (key, value, updated_at)
        VALUES (?, ?, datetime('now','localtime'))
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (key, value),
    )


def query_user_profile(conn: sqlite3.Connection, key: str) -> Optional[str]:
    """查询单条用户档案值。"""
    row = conn.execute("SELECT value FROM user_profile WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def query_user_profiles(conn: sqlite3.Connection) -> dict:
    """查询全部用户档案，返回 {key: value} 字典。"""
    rows = conn.execute("SELECT key, value FROM user_profile").fetchall()
    return {row["key"]: row["value"] for row in rows}
