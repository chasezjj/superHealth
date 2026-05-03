"""就医预约推算器（Phase 6）

从 medical_conditions 表读取全部 active 病情，按各自的 follow_up_months /
follow_up_department 字段自动推算下次应诊日期，并写入 appointments 表（幂等）。

新增或修改病情的复诊配置：
    UPDATE medical_conditions
    SET follow_up_months=3, follow_up_department='眼科', follow_up_hospital='同仁医院'
    WHERE name='原发性开角型青光眼';

使用方式：
    python -m superhealth.reminders.appointment_scheduler
    python -m superhealth.reminders.appointment_scheduler --dry-run
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import asdict, is_dataclass
from datetime import date
from typing import Any

from dateutil.relativedelta import relativedelta

from superhealth.database import get_conn, query_active_conditions, upsert_appointment
from superhealth.reminders.reminder_config import REMINDER_RULES

log = logging.getLogger(__name__)


def _as_mapping(obj: Any) -> dict:
    if isinstance(obj, dict):
        return obj
    if is_dataclass(obj):
        return asdict(obj)
    return dict(obj)


def _query_last_exam_date(conn, cond: dict) -> tuple[str | None, int | None]:
    """返回与该病情相关的最近一次就诊日期和文档 ID。

    优先按 follow_up_department 匹配 medical_documents；
    无 department 时退回使用 source_document_id（首次诊断文档）。
    """
    cond = _as_mapping(cond)

    source_table = cond.get("source_table")
    date_field = cond.get("date_field")
    if source_table and date_field:
        if source_table not in {"medical_observations", "medical_documents"}:
            return None, None
        filters = cond.get("item_filter") or {}
        where = []
        params: list[Any] = []
        for key, value in filters.items():
            where.append(f"{key} = ?")
            params.append(value)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        id_field = "id"
        row = conn.execute(
            f"SELECT {id_field}, {date_field} FROM {source_table} {where_sql} "
            f"ORDER BY {date_field} DESC, {id_field} DESC LIMIT 1",
            params,
        ).fetchone()
        if row:
            return row[date_field], row[id_field]

    dept = cond.get("follow_up_department")
    if dept:
        row = conn.execute(
            "SELECT id, doc_date FROM medical_documents WHERE department = ? ORDER BY doc_date DESC, id DESC LIMIT 1",
            (dept,),
        ).fetchone()
        if row:
            return row["doc_date"], row["id"]

    # 退回：用记录病情来源的诊断文档日期
    src_id = cond.get("source_document_id")
    if src_id:
        row = conn.execute(
            "SELECT id, doc_date FROM medical_documents WHERE id = ?",
            (src_id,),
        ).fetchone()
        if row:
            return row["doc_date"], row["id"]

    return None, None


def refresh_appointments(dry_run: bool = False) -> list[dict]:
    """推算所有 active 病情的下次应诊日期，写入 appointments 表。"""
    results = []
    with get_conn() as conn:
        conditions = query_active_conditions(conn) or REMINDER_RULES
        if not conditions:
            log.info("[scheduler] medical_conditions 表无 active 记录")
            return results

        for cond_obj in conditions:
            cond = _as_mapping(cond_obj)
            name = cond.get("name") or cond.get("condition")
            label = cond.get("label") or name

            interval_months = cond.get("follow_up_months") or cond.get("interval_months")
            if not interval_months:
                log.info("[scheduler] %s：未配置复诊间隔（follow_up_months），跳过", name)
                continue

            last_date_str, doc_id = _query_last_exam_date(conn, cond)
            if last_date_str is None:
                log.info("[scheduler] %s：无相关就诊记录，跳过", name)
                continue

            last_date = date.fromisoformat(last_date_str)
            due = last_date + relativedelta(months=interval_months)
            days_left = (due - date.today()).days

            result = {
                "condition": name,
                "label": label,
                "hospital": cond.get("follow_up_hospital") or cond.get("hospital"),
                "department": cond.get("follow_up_department") or cond.get("department"),
                "last_exam_date": last_date_str,
                "due_date": due.isoformat(),
                "days_left": days_left,
                "interval_months": interval_months,
                "source_exam_id": doc_id,
                "source_table": cond.get("source_table") or "medical_documents",
            }
            results.append(result)

            if dry_run:
                log.info(
                    "[dry-run] %s：上次 %s → 下次 %s（距今 %d 天）",
                    name, last_date_str, due.isoformat(), days_left,
                )
            else:
                upsert_appointment(
                    conn,
                    condition=name,
                    hospital=cond.get("follow_up_hospital") or cond.get("hospital"),
                    department=cond.get("follow_up_department") or cond.get("department"),
                    due_date=due.isoformat(),
                    interval_months=interval_months,
                    source_exam_id=doc_id,
                    source_table=cond.get("source_table") or "medical_documents",
                )
                log.info(
                    "[scheduler] %s：上次 %s → 下次 %s（距今 %d 天）已写入",
                    name, last_date_str, due.isoformat(), days_left,
                )

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="推算并写入就医预约日期")
    parser.add_argument("--dry-run", action="store_true", help="只打印结果，不写入数据库")
    args = parser.parse_args()
    refresh_appointments(dry_run=args.dry_run)
