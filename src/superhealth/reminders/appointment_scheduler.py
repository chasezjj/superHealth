"""就医预约推算器（Phase 6）

根据各病情规则，从数据库最近一次检查记录自动推算下次应诊日期，
并将结果写入 appointments 表（幂等，可反复执行）。

使用方式：
    python -m superhealth.reminders.appointment_scheduler
    python -m superhealth.reminders.appointment_scheduler --dry-run
"""

from __future__ import annotations

import argparse
from datetime import date
from typing import Optional

from dateutil.relativedelta import relativedelta

from superhealth.database import get_conn, upsert_appointment
from superhealth.reminders.reminder_config import REMINDER_RULES, ReminderRule


def _query_last_exam_date(conn, rule: ReminderRule) -> tuple[Optional[str], Optional[int]]:
    """查询某规则对应数据表的最近一条记录，返回 (date_str, record_id)。"""
    table = rule.source_table
    date_col = rule.date_field

    if rule.item_filter:
        # 带字段过滤（如 lab_results 按 item_name 筛选）
        filter_col, filter_val = next(iter(rule.item_filter.items()))
        row = conn.execute(
            f"SELECT id, {date_col} FROM {table} WHERE {filter_col} = ? ORDER BY {date_col} DESC LIMIT 1",
            (filter_val,),
        ).fetchone()
    else:
        row = conn.execute(
            f"SELECT id, {date_col} FROM {table} ORDER BY {date_col} DESC LIMIT 1"
        ).fetchone()

    if row:
        return row[date_col], row["id"]
    return None, None


def refresh_appointments(dry_run: bool = False) -> list[dict]:
    """推算所有病情的下次应诊日期，写入 appointments 表。

    返回推算结果列表（无论 dry_run 与否均返回）。
    """
    results = []
    with get_conn() as conn:
        for rule in REMINDER_RULES:
            last_date_str, exam_id = _query_last_exam_date(conn, rule)
            if last_date_str is None:
                print(f"[scheduler] {rule.label}：数据库无记录，跳过")
                continue

            last_date = date.fromisoformat(last_date_str)
            due = last_date + relativedelta(months=rule.interval_months)
            days_left = (due - date.today()).days

            result = {
                "condition": rule.condition,
                "label": rule.label,
                "hospital": rule.hospital,
                "department": rule.department,
                "last_exam_date": last_date_str,
                "due_date": due.isoformat(),
                "days_left": days_left,
                "interval_months": rule.interval_months,
                "source_exam_id": exam_id,
                "source_table": rule.source_table,
            }
            results.append(result)

            if dry_run:
                print(
                    f"[dry-run] {rule.label}：上次 {last_date_str} → "
                    f"下次 {due.isoformat()}（距今 {days_left} 天）"
                )
            else:
                upsert_appointment(
                    conn,
                    condition=rule.condition,
                    hospital=rule.hospital,
                    department=rule.department,
                    due_date=due.isoformat(),
                    interval_months=rule.interval_months,
                    source_exam_id=exam_id,
                    source_table=rule.source_table,
                )
                print(
                    f"[scheduler] {rule.label}：上次 {last_date_str} → "
                    f"下次 {due.isoformat()}（距今 {days_left} 天）已写入"
                )

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="推算并写入就医预约日期")
    parser.add_argument("--dry-run", action="store_true", help="只打印结果，不写入数据库")
    args = parser.parse_args()
    refresh_appointments(dry_run=args.dry_run)
