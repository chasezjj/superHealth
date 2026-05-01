"""用药追踪模块：记录用药情况并分析药物效果。

核心功能：
1. 用药记录管理（CRUD）
2. 用药与检查结果的关联
3. 药物效果分析（用药前后指标对比）
4. 用药依从性追踪

典型使用场景：
- 青光眼药物：追踪眼压控制效果
- 降尿酸药物：追踪尿酸水平变化
- 降压药：追踪血压变化趋势
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from superhealth import database as db

log = logging.getLogger(__name__)
DB_PATH = Path(__file__).parent.parent.parent.parent / "health.db"


class MedicationTracker:
    """用药追踪器。"""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        db.init_db(db_path)

    def add_medication(
        self,
        name: str,
        condition: str,  # glaucoma / hyperuricemia / hypertension / etc
        start_date: str,
        dosage: str,
        frequency: str = "每日",
        end_date: Optional[str] = None,
        note: str = "",
    ) -> int:
        """添加用药记录，返回 medication_id。"""
        with db.get_conn(self.db_path) as conn:
            db.insert_medication(
                conn,
                name=name,
                condition=condition,
                start_date=start_date,
                end_date=end_date,
                dosage=dosage,
                frequency=frequency,
                note=note,
            )
            # 获取刚插入的ID
            row = conn.execute("SELECT last_insert_rowid()").fetchone()
            med_id = row[0]
            log.info("添加用药记录: %s (ID=%d) 用于 %s", name, med_id, condition)
            return med_id

    def get_active_medications(self) -> list[dict]:
        """获取当前在用药物列表。"""
        with db.get_conn(self.db_path) as conn:
            return db.query_active_medications(conn)

    def get_medications_by_condition(self, condition: str) -> list[dict]:
        """按疾病获取用药记录。"""
        with db.get_conn(self.db_path) as conn:
            return db.query_medication_by_condition(conn, condition)

    def link_to_lab_result(
        self,
        medication_id: int,
        lab_result_id: int,
        expected_effect: str,
        actual_effect: str = "",
        is_effective: Optional[int] = None,
        note: str = "",
    ):
        """将用药与化验结果关联。"""
        with db.get_conn(self.db_path) as conn:
            db.insert_medication_effect(
                conn,
                medication_id=medication_id,
                lab_result_id=lab_result_id,
                expected_effect=expected_effect,
                actual_effect=actual_effect,
                is_effective=is_effective,
                note=note,
            )
            log.info("关联用药(ID=%d)与化验结果(ID=%d)", medication_id, lab_result_id)

    def link_to_eye_exam(
        self,
        medication_id: int,
        eye_exam_id: int,
        expected_effect: str,
        actual_effect: str = "",
        is_effective: Optional[int] = None,
        note: str = "",
    ):
        """将用药与眼科检查关联。"""
        with db.get_conn(self.db_path) as conn:
            db.insert_medication_effect(
                conn,
                medication_id=medication_id,
                eye_exam_id=eye_exam_id,
                expected_effect=expected_effect,
                actual_effect=actual_effect,
                is_effective=is_effective,
                note=note,
            )
            log.info("关联用药(ID=%d)与眼科检查(ID=%d)", medication_id, eye_exam_id)

    def analyze_medication_effect(
        self,
        medication_name: str,
        indicator: str,  # 如 "尿酸", "眼压"
        days_before: int = 30,
        days_after: int = 30,
    ) -> dict:
        """分析药物对特定指标的效果。

        返回用药前后指标变化情况。
        """
        with db.get_conn(self.db_path) as conn:
            # 获取药物信息
            med_row = conn.execute(
                "SELECT * FROM medications WHERE name = ? ORDER BY start_date DESC LIMIT 1",
                (medication_name,),
            ).fetchone()

            if not med_row:
                return {"error": f"未找到药物: {medication_name}"}

            med = dict(med_row)
            start_date = med["start_date"]

            # 查询用药前的指标
            before_rows = conn.execute(
                """SELECT date, value, unit FROM lab_results
                   WHERE item_name = ? AND date < ?
                   ORDER BY date DESC LIMIT 3""",
                (indicator, start_date),
            ).fetchall()

            # 查询用药后的指标
            after_rows = conn.execute(
                """SELECT date, value, unit FROM lab_results
                   WHERE item_name = ? AND date >= ?
                   ORDER BY date ASC LIMIT 3""",
                (indicator, start_date),
            ).fetchall()

            before_values = [r["value"] for r in before_rows if r["value"] is not None]
            after_values = [r["value"] for r in after_rows if r["value"] is not None]

            result = {
                "medication": medication_name,
                "indicator": indicator,
                "start_date": start_date,
                "before": {
                    "count": len(before_values),
                    "values": before_values,
                    "avg": sum(before_values) / len(before_values) if before_values else None,
                },
                "after": {
                    "count": len(after_values),
                    "values": after_values,
                    "avg": sum(after_values) / len(after_values) if after_values else None,
                },
            }

            # 计算变化
            if result["before"]["avg"] and result["after"]["avg"]:
                change = result["after"]["avg"] - result["before"]["avg"]
                change_pct = (change / result["before"]["avg"]) * 100
                result["change"] = round(change, 2)
                result["change_pct"] = round(change_pct, 1)

            return result

    def get_medication_summary(self) -> dict:
        """获取用药汇总报告。"""
        with db.get_conn(self.db_path) as conn:
            # 当前用药
            active = db.query_active_medications(conn)

            # 按疾病分组统计
            conditions = conn.execute(
                """SELECT condition, COUNT(*) as count,
                          GROUP_CONCAT(DISTINCT name) as medications
                   FROM medications
                   WHERE end_date IS NULL OR end_date = ''
                   GROUP BY condition"""
            ).fetchall()

            return {
                "active_count": len(active),
                "active_medications": active,
                "by_condition": [dict(r) for r in conditions],
            }


def init_default_medications():
    """初始化默认用药记录（基于已有病历）。"""
    tracker = MedicationTracker()

    # 检查是否已有记录
    existing = tracker.get_active_medications()
    if existing:
        log.info("已有 %d 条用药记录，跳过初始化", len(existing))
        return

    # 示例用药记录（请替换为实际用药信息）
    tracker.add_medication(
        name="示例眼药水",
        condition="glaucoma",
        start_date="2023-01-01",
        dosage="每晚1滴",
        frequency="每日",
        note="用于控制眼压，青光眼标准治疗",
    )

    log.info("初始化默认用药记录完成")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_default_medications()
