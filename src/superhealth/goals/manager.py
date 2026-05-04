"""目标管理器：CRUD + 生命周期 + 每日进度追踪。"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date
from pathlib import Path
from typing import Optional

from superhealth import database as db
from superhealth.goals.metrics import (
    METRIC_REGISTRY,
    MIN_BASELINE_DAYS,
    VALID_METRIC_KEYS,
    GoalMetricRegistry,
)
from superhealth.goals.models import VALID_DIRECTIONS, VALID_STATUSES

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent.parent / "health.db"

_GOALS_COMPAT_COLUMNS = {
    "status": "TEXT NOT NULL DEFAULT 'active'",
    "target_value": "REAL",
    "target_date": "TEXT",
    "achieved_date": "TEXT",
    "notes": "TEXT",
    "created_at": "TIMESTAMP",
    "updated_at": "TIMESTAMP",
}


class GoalManager:
    """阶段性目标管理：CRUD、进度追踪、达成/异常判定。"""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.metric_registry = GoalMetricRegistry()
        self._ensure_goals_schema()

    def _get_conn(self):
        return db.get_conn(self.db_path)

    def _ensure_goals_schema(self) -> None:
        """补齐旧数据库中的 goals 兼容列。

        部分本地库是在 goals 表补齐 lifecycle/target 字段前创建的。
        Dashboard/CLI 直接实例化 GoalManager 时可能没有先运行完整 schema 初始化，
        因此在管理器入口做一次轻量列迁移，避免新增目标或更新状态时报 no column。
        """
        with self._get_conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'goals'"
            ).fetchone()
            if not exists:
                return

            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(goals)").fetchall()
            }
            for col_name, col_type in _GOALS_COMPAT_COLUMNS.items():
                if col_name in columns:
                    continue
                conn.execute(f"ALTER TABLE goals ADD COLUMN {col_name} {col_type}")

    # ── CRUD ──────────────────────────────────────────────────────────

    def add_goal(
        self,
        *,
        name: str,
        metric_key: str,
        direction: str,
        target: Optional[float] = None,
        target_date: Optional[str] = None,
        baseline_value: Optional[float] = None,
    ) -> int:
        """添加新目标，自动计算基线。同一时间只能有一个活跃目标。

        Returns:
            新目标的 ID。
        """
        if metric_key not in VALID_METRIC_KEYS:
            raise ValueError(f"不支持的指标 key: {metric_key}，可选: {sorted(VALID_METRIC_KEYS)}")
        if direction not in VALID_DIRECTIONS:
            raise ValueError(f"direction 必须是 {VALID_DIRECTIONS} 之一")

        today = date.today().isoformat()

        # 自动计算基线（如果未手动指定）
        if baseline_value is None:
            with self._get_conn() as conn:
                baseline_value = self.metric_registry.get_baseline(conn, metric_key, today)
            if baseline_value is None:
                raise ValueError(
                    f"无法自动计算基线：{metric_key} 在 {today} 前 7 天数据不足 {MIN_BASELINE_DAYS} 天。"
                    "请使用 --baseline 手动指定基线值。"
                )

        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO goals
                    (name, status, metric_key, direction,
                     baseline_value, target_value, start_date, target_date)
                VALUES (?, 'active', ?, ?, ?, ?, ?, ?)
            """,
                (
                    name,
                    metric_key,
                    direction,
                    baseline_value,
                    target,
                    today,
                    target_date,
                ),
            )
            goal_id = cursor.lastrowid or 0
            self._write_goal_progress(
                conn,
                {
                    "id": goal_id,
                    "name": name,
                    "metric_key": metric_key,
                    "baseline_value": baseline_value,
                    "target_value": target,
                    "direction": direction,
                },
                today,
            )
            log.info(
                "GOAL_CREATED id=%d name=%s metric=%s baseline=%.1f target=%s",
                goal_id,
                name,
                metric_key,
                baseline_value,
                target,
            )
            return goal_id

    def list_goals(self, status: Optional[str] = None) -> list[dict]:
        """列出目标，可按 status 过滤。"""
        with self._get_conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM goals WHERE status = ? ORDER BY start_date DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM goals ORDER BY status != 'active', start_date DESC"
                ).fetchall()
            return [dict(row) for row in rows]

    def get_goal(self, goal_id: int) -> Optional[dict]:
        """获取单个目标。"""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
            return dict(row) if row else None

    def update_status(self, goal_id: int, status: str, notes: Optional[str] = None):
        """更新目标状态（用户 CLI 触发）。

        若目标进入 achieved / paused / abandoned，会自动处理绑定的实验：
        - achieved：active 实验标记为 completed（随目标结案）
        - paused / abandoned：active 实验标记为 reverted（随目标回退）
        - 同时清理该目标下所有 draft 实验
        """
        if status not in VALID_STATUSES:
            raise ValueError(f"status 必须是 {VALID_STATUSES} 之一")
        with self._get_conn() as conn:
            updates = ["status = ?", "updated_at = datetime('now','localtime')"]
            params: list = [status]
            if status == "achieved":
                updates.append("achieved_date = ?")
                params.append(date.today().isoformat())
            if notes:
                updates.append("notes = ?")
                params.append(notes)
            params.append(goal_id)
            conn.execute(f"UPDATE goals SET {', '.join(updates)} WHERE id = ?", params)

            # ── 目标结案/暂停/废弃时，自动处理绑定实验 ──
            if status in ("achieved", "paused", "abandoned"):
                self._close_bound_experiments(conn, goal_id, status)

            log.info("GOAL_STATUS_CHANGED id=%d status=%s", goal_id, status)

    def _close_bound_experiments(
        self, conn: sqlite3.Connection, goal_id: int, goal_status: str
    ) -> None:
        """目标进入终态时，自动完成/回退绑定的 active 实验，并清理 draft 实验。"""
        # active 实验 → completed 或 reverted
        active_rows = conn.execute(
            "SELECT id FROM experiments WHERE goal_id = ? AND status = 'active'",
            (goal_id,),
        ).fetchall()
        for row in active_rows:
            exp_id = row["id"]
            if goal_status == "achieved":
                conclusion = "目标已达成，实验随目标自动结案"
                new_status = "completed"
            else:
                conclusion = f"目标已{'废弃' if goal_status == 'abandoned' else '暂停'}，实验随目标自动回退"
                new_status = "reverted"
            conn.execute(
                """UPDATE experiments
                   SET status=?, conclusion=?, conclusion_date=date('now','localtime'),
                       updated_at=datetime('now','localtime')
                   WHERE id=?""",
                (new_status, conclusion, exp_id),
            )
            # 清理 active_experiment preference
            conn.execute(
                "DELETE FROM learned_preferences WHERE preference_key = ?",
                (f"active_exp_{exp_id}",),
            )
            log.info("EXPERIMENT_AUTO_%s id=%d goal_status=%s", new_status.upper(), exp_id, goal_status)

        # draft 实验直接删除
        draft_rows = conn.execute(
            "SELECT id FROM experiments WHERE goal_id = ? AND status = 'draft'",
            (goal_id,),
        ).fetchall()
        for row in draft_rows:
            conn.execute("DELETE FROM experiments WHERE id = ?", (row["id"],))
            log.info("EXPERIMENT_AUTO_DELETED id=%d goal_id=%d", row["id"], goal_id)

    # 删除目标时禁止存在的实验状态：仍在生命周期内、未结案
    BLOCKING_EXPERIMENT_STATUSES = ("draft", "active", "evaluating")

    def get_blocking_experiments(self, goal_id: int) -> list[dict]:
        """返回阻止删除目标的实验：状态为 draft/active/evaluating 的绑定实验。"""
        with self._get_conn() as conn:
            rows = conn.execute(
                f"""SELECT id, name, status FROM experiments
                    WHERE goal_id = ?
                      AND status IN ({",".join("?" * len(self.BLOCKING_EXPERIMENT_STATUSES))})
                    ORDER BY id""",
                (goal_id, *self.BLOCKING_EXPERIMENT_STATUSES),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_goal(self, goal_id: int) -> None:
        """删除目标及其所有 progress 记录（CASCADE）。

        若存在状态为 draft/active/evaluating 的绑定实验，会抛 ValueError，
        要求调用方先到实验追踪页面取消或删除这些实验。
        """
        blocking = self.get_blocking_experiments(goal_id)
        if blocking:
            details = "、".join(f"{e['name']}（{e['status']}）" for e in blocking)
            raise ValueError(
                f"该目标仍有未结案的绑定实验：{details}。请先到实验追踪页取消或删除后再删除目标。"
            )
        with self._get_conn() as conn:
            conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
            log.info("GOAL_DELETED id=%d", goal_id)

    def get_goal_progress(self, goal_id: int, days: int = 30) -> list[dict]:
        """获取目标的历史进度。"""
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM goal_progress
                   WHERE goal_id = ?
                   ORDER BY date DESC LIMIT ?""",
                (goal_id, days),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_active_goals(self, conn: sqlite3.Connection | None = None) -> list[dict]:
        """获取所有 active 目标（供 HealthProfile 等模块调用）。"""

        def _query(c) -> list[dict]:  # type: ignore[no-untyped-def]
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM goals WHERE status = 'active' ORDER BY start_date DESC"
                ).fetchall()
            ]

        if conn:
            return _query(conn)
        with self._get_conn() as c:
            return _query(c)

    # ── 每日进度追踪（daily_pipeline 调用）─────────────────────────────

    def track_daily_progress(self, ref_date: str):
        """对每个 active goal 计算当天 progress 并写入 goal_progress。

        低频指标跳过每日快照。
        """
        with self._get_conn() as conn:
            goals = [
                dict(r)
                for r in conn.execute("SELECT * FROM goals WHERE status = 'active'").fetchall()
            ]

            for goal in goals:
                spec = METRIC_REGISTRY.get(goal["metric_key"])
                if not spec:
                    continue
                # 低频指标不做每日快照
                if spec.frequency == "low_freq":
                    continue

                self._write_goal_progress(conn, goal, ref_date)

    def _write_goal_progress(
        self, conn: sqlite3.Connection, goal: dict, ref_date: str
    ) -> bool:
        """计算并写入单个目标在 ref_date 的进度；无可用当前值时跳过。"""
        try:
            current = self.metric_registry.get_current_value(conn, goal["metric_key"], ref_date)
        except sqlite3.OperationalError as e:
            log.warning("GOAL_PROGRESS_SKIPPED goal=%s date=%s error=%s", goal["name"], ref_date, e)
            return False
        if current is None:
            return False

        baseline = goal["baseline_value"]
        target = goal["target_value"]
        direction = goal["direction"]

        delta = round(current - baseline, 2) if baseline is not None else None
        progress_pct = self.metric_registry.compute_progress(current, baseline, target, direction)

        conn.execute(
            """
            INSERT OR REPLACE INTO goal_progress
                (goal_id, date, current_value, delta_from_baseline, progress_pct)
            VALUES (?, ?, ?, ?, ?)
        """,
            (
                goal["id"],
                ref_date,
                round(current, 2),
                delta,
                round(progress_pct, 2) if progress_pct is not None else None,
            ),
        )

        log.info(
            "GOAL_PROGRESS goal=%s date=%s current=%.1f delta=%s pct=%s",
            goal["name"],
            ref_date,
            current,
            delta,
            progress_pct,
        )
        return True

    # ── 达成/异常判定 ─────────────────────────────────────────────────

    def check_achievement_candidates(self, ref_date: str) -> list[dict]:
        """检查是否有目标满足达成候选条件。

        条件：有数据的连续 7 个非空日满足 target_value。
        低频指标：最近一次检测值满足 target 即标为候选。

        Returns:
            达成候选列表，每项包含 goal 信息 + note。
            注意：不会自动改 status，仅返回候选供日报提示。
        """
        candidates = []
        with self._get_conn() as conn:
            goals = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM goals WHERE status = 'active' AND target_value IS NOT NULL"
                ).fetchall()
            ]

            for goal in goals:
                spec = METRIC_REGISTRY.get(goal["metric_key"])
                if not spec:
                    continue

                target = goal["target_value"]
                direction = goal["direction"]

                if spec.frequency == "low_freq":
                    # 低频指标：最近一次检测值满足 target
                    current = self.metric_registry.get_current_value(
                        conn, goal["metric_key"], ref_date
                    )
                    if current is not None and self._value_meets_target(current, target, direction):
                        candidates.append(
                            {
                                "goal": goal,
                                "current": current,
                                "note": f"低频指标达标：当前值 {current:.1f}，目标 {target:.1f}",
                            }
                        )
                    continue

                # 高频指标：检查最近 7 个有数据的非空日是否全部达标
                rows = conn.execute(
                    """SELECT date, current_value FROM goal_progress
                       WHERE goal_id = ? AND current_value IS NOT NULL
                       ORDER BY date DESC LIMIT 7""",
                    (goal["id"],),
                ).fetchall()

                if len(rows) < 7:
                    continue

                all_met = all(
                    self._value_meets_target(row["current_value"], target, direction)
                    for row in rows
                )
                if all_met:
                    candidates.append(
                        {
                            "goal": goal,
                            "current": rows[0]["current_value"],
                            "note": f"连续 {len(rows)} 个非空日达标",
                        }
                    )

        return candidates

    def check_off_track(self, ref_date: str) -> list[dict]:
        """检查是否有目标已 30 天无明显进展（通过最近 30 天趋势判断）。

        Returns:
            off_track 目标列表。不会自动改 status，仅返回供日报提示。
        """
        results = []
        with self._get_conn() as conn:
            goals = [
                dict(r)
                for r in conn.execute(
                    """SELECT * FROM goals WHERE status = 'active'
                   AND start_date <= date(?, '-30 days')""",
                    (ref_date,),
                ).fetchall()
            ]

            for goal in goals:
                if goal["baseline_value"] is None or goal["target_value"] is None:
                    continue

                # 获取最近 30 天的进度数据，分析趋势
                rows = conn.execute(
                    """SELECT date, current_value FROM goal_progress
                       WHERE goal_id = ? AND current_value IS NOT NULL
                       ORDER BY date DESC LIMIT 30""",
                    (goal["id"],),
                ).fetchall()

                if len(rows) < 7:
                    continue

                direction = goal["direction"]
                baseline = goal["baseline_value"]

                # 按时间顺序排列，计算趋势变化
                rows_asc = list(reversed(rows))
                first_val = rows_asc[0]["current_value"]
                last_val = rows_asc[-1]["current_value"]
                trend_diff = last_val - first_val

                going_wrong = False
                if direction == "decrease":
                    # 应该下降，但趋势在上升
                    if trend_diff > 0:
                        going_wrong = True
                elif direction == "increase":
                    # 应该上升，但趋势在下降
                    if trend_diff < 0:
                        going_wrong = True
                elif direction == "stabilize":
                    tolerance = abs(baseline) * 0.20
                    if abs(last_val - baseline) > tolerance:
                        going_wrong = True

                if going_wrong:
                    results.append(
                        {
                            "goal": goal,
                            "current": last_val,
                            "note": f"30 天趋势走反：基线 {baseline:.1f}，最近 {last_val:.1f}，趋势 {trend_diff:+.1f}",
                        }
                    )

        return results

    @staticmethod
    def _value_meets_target(value: float, target: float, direction: str) -> bool:
        """判断当前值是否满足目标。"""
        if direction == "decrease":
            return value <= target
        elif direction == "increase":
            return value >= target
        else:  # stabilize
            return abs(value - target) <= abs(target) * 0.05
