"""测试数据库核心操作。"""
from pathlib import Path

import pytest

from superhealth import database as db
from superhealth.goals.manager import GoalManager


@pytest.fixture
def tmp_db(tmp_path):
    """创建临时数据库并初始化 schema。"""
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    return db_path


class TestDatabaseInit:
    def test_init_creates_tables(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = {r["name"] for r in tables}
        assert "daily_health" in table_names
        assert "vitals" in table_names
        assert "medical_observations" in table_names
        assert "medications" in table_names
        assert "goals" in table_names


class TestVitalsOperations:
    def test_insert_and_query_vitals(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            db.insert_vital(conn, measured_at="2025-04-01 08:00:00", systolic=120, diastolic=80)
            rows = conn.execute("SELECT * FROM vitals").fetchall()
        assert len(rows) == 1
        assert rows[0]["systolic"] == 120
        assert rows[0]["diastolic"] == 80


class TestMedicationOperations:
    def test_insert_medication(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            db.insert_medication(
                conn,
                name="示例药物",
                condition="test",
                start_date="2025-01-01",
                dosage="每日1片",
            )
            rows = conn.execute("SELECT * FROM medications").fetchall()
        assert len(rows) == 1
        assert rows[0]["name"] == "示例药物"

    def test_query_active_medications(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            db.insert_medication(
                conn,
                name="活跃药物",
                condition="test",
                start_date="2025-01-01",
                dosage="每日1片",
            )
            active = db.query_active_medications(conn)
        assert len(active) == 1
        assert active[0]["name"] == "活跃药物"


class TestGoalOperations:
    def test_add_goal(self, tmp_db):
        mgr = GoalManager(tmp_db)
        goal_id = mgr.add_goal(
            name="测试目标",
            metric_key="steps_mean_7d",
            direction="increase",
            baseline_value=8000,
        )
        assert goal_id is not None
        goals = mgr.list_goals()
        assert len(goals) == 1
        assert goals[0]["name"] == "测试目标"
