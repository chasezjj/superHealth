"""指标白名单注册表：预定义可自动追踪的指标及其聚合逻辑。

metric_key 后缀编码聚合方式：
- _mean_7d：取 date 前 7 天（含 date）的均值
- _latest：最近一次检测值（低频指标）

低频指标不参与每日 progress 快照，仅检测到新数据时触发评估。
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

log = logging.getLogger(__name__)

# 需要至少 3 天数据才能计算基线
MIN_BASELINE_DAYS = 3


@dataclass
class MetricSpec:
    """指标规格。"""

    key: str
    label: str
    table: str
    column: str
    frequency: str  # daily / low_freq
    aggregation: str  # mean_7d / latest


# 指标白名单
METRIC_REGISTRY: dict[str, MetricSpec] = {
    "bp_systolic_mean_7d": MetricSpec(
        key="bp_systolic_mean_7d",
        label="7天收缩压均值",
        table="vitals",
        column="systolic",
        frequency="daily",
        aggregation="mean_7d",
    ),
    "bp_diastolic_mean_7d": MetricSpec(
        key="bp_diastolic_mean_7d",
        label="7天舒张压均值",
        table="vitals",
        column="diastolic",
        frequency="daily",
        aggregation="mean_7d",
    ),
    "body_battery_wake_mean_7d": MetricSpec(
        key="body_battery_wake_mean_7d",
        label="晨起 Body Battery 7日均值",
        table="daily_health",
        column="bb_at_wake",
        frequency="daily",
        aggregation="mean_7d",
    ),
    "sleep_score_mean_7d": MetricSpec(
        key="sleep_score_mean_7d",
        label="睡眠分 7日均值",
        table="daily_health",
        column="sleep_score",
        frequency="daily",
        aggregation="mean_7d",
    ),
    "hrv_mean_7d": MetricSpec(
        key="hrv_mean_7d",
        label="HRV 7日均值",
        table="daily_health",
        column="hrv_last_night_avg",
        frequency="daily",
        aggregation="mean_7d",
    ),
    "resting_hr_mean_7d": MetricSpec(
        key="resting_hr_mean_7d",
        label="静息心率 7日均值",
        table="daily_health",
        column="hr_resting",
        frequency="daily",
        aggregation="mean_7d",
    ),
    "weight_kg_mean_7d": MetricSpec(
        key="weight_kg_mean_7d",
        label="体重 7日均值",
        table="vitals",
        column="weight_kg",
        frequency="daily",
        aggregation="mean_7d",
    ),
    "body_fat_pct_mean_7d": MetricSpec(
        key="body_fat_pct_mean_7d",
        label="体脂率 7日均值",
        table="vitals",
        column="body_fat_pct",
        frequency="daily",
        aggregation="mean_7d",
    ),
    "steps_mean_7d": MetricSpec(
        key="steps_mean_7d",
        label="步数 7日均值",
        table="daily_health",
        column="steps",
        frequency="daily",
        aggregation="mean_7d",
    ),
    "stress_mean_7d": MetricSpec(
        key="stress_mean_7d",
        label="压力 7日均值",
        table="daily_health",
        column="stress_average",
        frequency="daily",
        aggregation="mean_7d",
    ),
    "uric_acid_latest": MetricSpec(
        key="uric_acid_latest",
        label="最近化验尿酸值",
        table="lab_results",
        column="value",
        frequency="low_freq",
        aggregation="latest",
    ),
    "iop_mean_recent": MetricSpec(
        key="iop_mean_recent",
        label="眼压均值（青光眼目标）",
        table="eye_exams",
        column="od_iop",
        frequency="low_freq",
        aggregation="latest",
    ),
}

VALID_METRIC_KEYS = set(METRIC_REGISTRY.keys())


class GoalMetricRegistry:
    """指标聚合器：根据 metric_key 查询数据库并计算聚合值。"""

    def get_current_value(
        self, conn: sqlite3.Connection, metric_key: str, ref_date: str
    ) -> Optional[float]:
        """取 ref_date 前 7 天（含 ref_date）的聚合值。

        对于低频指标（_latest），取最近一次有数据的值（不限日期）。
        """
        spec = METRIC_REGISTRY.get(metric_key)
        if not spec:
            raise ValueError(f"未知的指标 key: {metric_key}")

        if spec.aggregation == "mean_7d":
            return self._query_mean_7d(conn, spec, ref_date)
        elif spec.aggregation == "latest":
            return self._query_latest(conn, spec, ref_date)
        return None

    def get_baseline(
        self, conn: sqlite3.Connection, metric_key: str, start_date: str
    ) -> Optional[float]:
        """取 start_date 前 7 天（不含 start_date）的聚合值作为基线。

        需要至少 MIN_BASELINE_DAYS 天数据，否则返回 None。
        """
        spec = METRIC_REGISTRY.get(metric_key)
        if not spec:
            raise ValueError(f"未知的指标 key: {metric_key}")

        if spec.aggregation == "mean_7d":
            end = (date.fromisoformat(start_date) - timedelta(days=1)).isoformat()
            start = (date.fromisoformat(start_date) - timedelta(days=7)).isoformat()
            return self._query_mean_range(conn, spec, start, end)
        elif spec.aggregation == "latest":
            # 低频指标：取 start_date 之前最近一次检测值
            return self._query_latest_before(conn, spec, start_date)
        return None

    def _query_mean_7d(
        self, conn: sqlite3.Connection, spec: MetricSpec, ref_date: str
    ) -> Optional[float]:
        """查询 ref_date 前 7 天（含）的均值。"""
        start = (date.fromisoformat(ref_date) - timedelta(days=6)).isoformat()
        return self._query_mean_range(conn, spec, start, ref_date)

    def _query_mean_range(
        self, conn: sqlite3.Connection, spec: MetricSpec, start: str, end: str
    ) -> Optional[float]:
        """查询 [start, end] 区间内的均值。"""
        if spec.table == "vitals":
            # vitals 表可能有每天多条记录（多次测量），按日期聚合
            rows = conn.execute(
                f"""
                SELECT AVG(day_avg) FROM (
                    SELECT DATE(measured_at) AS d, AVG({spec.column}) AS day_avg
                    FROM vitals
                    WHERE {spec.column} IS NOT NULL
                      AND DATE(measured_at) BETWEEN ? AND ?
                    GROUP BY d
                )
            """,
                (start, end),
            ).fetchone()
        elif spec.table == "lab_results":
            rows = conn.execute(
                f"""
                SELECT AVG({spec.column}) FROM {spec.table}
                WHERE {spec.column} IS NOT NULL
                  AND date BETWEEN ? AND ?
                  AND item_name IN ('尿酸', '血尿酸', 'UA')
            """,
                (start, end),
            ).fetchone()
        elif spec.table == "eye_exams":
            rows = conn.execute(
                f"""
                SELECT AVG((od_iop + os_iop) / 2.0) FROM {spec.table}
                WHERE od_iop IS NOT NULL AND os_iop IS NOT NULL
                  AND date BETWEEN ? AND ?
            """,
                (start, end),
            ).fetchone()
        else:
            rows = conn.execute(
                f"""
                SELECT AVG({spec.column}) FROM {spec.table}
                WHERE {spec.column} IS NOT NULL
                  AND date BETWEEN ? AND ?
            """,
                (start, end),
            ).fetchone()

        return rows[0] if rows and rows[0] is not None else None

    def _query_latest(
        self, conn: sqlite3.Connection, spec: MetricSpec, ref_date: str
    ) -> Optional[float]:
        """查询 ref_date 及之前的最近一次检测值。"""
        if spec.table == "lab_results":
            row = conn.execute(
                f"""
                SELECT {spec.column} FROM {spec.table}
                WHERE {spec.column} IS NOT NULL
                  AND date <= ?
                  AND item_name IN ('尿酸', '血尿酸', 'UA')
                ORDER BY date DESC LIMIT 1
            """,
                (ref_date,),
            ).fetchone()
        elif spec.table == "eye_exams":
            row = conn.execute(
                """
                SELECT (od_iop + os_iop) / 2.0 FROM eye_exams
                WHERE od_iop IS NOT NULL AND os_iop IS NOT NULL
                  AND date <= ?
                ORDER BY date DESC LIMIT 1
            """,
                (ref_date,),
            ).fetchone()
        else:
            row = conn.execute(
                f"""
                SELECT {spec.column} FROM {spec.table}
                WHERE {spec.column} IS NOT NULL
                  AND date <= ?
                ORDER BY date DESC LIMIT 1
            """,
                (ref_date,),
            ).fetchone()

        return row[0] if row and row[0] is not None else None

    def _query_latest_before(
        self, conn: sqlite3.Connection, spec: MetricSpec, before_date: str
    ) -> Optional[float]:
        """查询 before_date 之前（不含）的最近一次检测值。"""
        if spec.table == "lab_results":
            row = conn.execute(
                f"""
                SELECT {spec.column} FROM {spec.table}
                WHERE {spec.column} IS NOT NULL
                  AND date < ?
                  AND item_name IN ('尿酸', '血尿酸', 'UA')
                ORDER BY date DESC LIMIT 1
            """,
                (before_date,),
            ).fetchone()
        elif spec.table == "eye_exams":
            row = conn.execute(
                """
                SELECT (od_iop + os_iop) / 2.0 FROM eye_exams
                WHERE od_iop IS NOT NULL AND os_iop IS NOT NULL
                  AND date < ?
                ORDER BY date DESC LIMIT 1
            """,
                (before_date,),
            ).fetchone()
        else:
            row = conn.execute(
                f"""
                SELECT {spec.column} FROM {spec.table}
                WHERE {spec.column} IS NOT NULL
                  AND date < ?
                ORDER BY date DESC LIMIT 1
            """,
                (before_date,),
            ).fetchone()

        return row[0] if row and row[0] is not None else None

    def compute_progress(
        self,
        current: Optional[float],
        baseline: Optional[float],
        target: Optional[float],
        direction: str,
    ) -> Optional[float]:
        """计算达成进度百分比（可负可超100）。

        direction=decrease：从 baseline 往 target 降，当前>baseline 时进度为负
        direction=increase：从 baseline 往 target 升，当前<baseline 时进度为负
        direction=stabilize：当前值在 baseline ±5% 范围内算 100%
        """
        if current is None or baseline is None or target is None:
            return None

        if direction == "stabilize":
            tolerance = abs(baseline) * 0.05
            if abs(current - baseline) <= tolerance:
                return 100.0
            deviation = abs(current - baseline) - tolerance
            max_dev = abs(baseline) * 0.20
            return max(0.0, (1 - deviation / max_dev) * 100)

        if direction == "decrease":
            total = baseline - target
            if total == 0:
                return 100.0 if current <= target else 0.0
            return (baseline - current) / total * 100

        if direction == "increase":
            total = target - baseline
            if total == 0:
                return 100.0 if current >= target else 0.0
            return (current - baseline) / total * 100

        return None
