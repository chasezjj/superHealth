"""测试 Goals 子系统。"""
from unittest.mock import MagicMock, patch

import pytest

from superhealth import database as db
from superhealth.goals.manager import GoalManager
from superhealth.goals.metrics import GoalMetricRegistry, METRIC_REGISTRY
from superhealth.goals.models import VALID_DIRECTIONS, VALID_STATUSES, Goal, GoalProgress


@pytest.fixture
def tmp_db(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    return db_path


class TestGoalModel:
    def test_priority_validation(self):
        g = Goal(name="test", priority=1, metric_key="steps_mean_7d", direction="increase", start_date="2025-01-01")
        assert g.priority == 1

    def test_invalid_priority(self):
        with pytest.raises(ValueError):
            Goal(name="test", priority=5, metric_key="steps_mean_7d", direction="increase", start_date="2025-01-01")


class TestGoalMetricRegistryComputeProgress:
    def test_decrease_progress(self):
        reg = GoalMetricRegistry()
        # baseline=80, target=70, current=75 → 降了一半 → 50%
        pct = reg.compute_progress(75, 80, 70, "decrease")
        assert pct == 50.0

    def test_increase_progress(self):
        reg = GoalMetricRegistry()
        # baseline=8000, target=10000, current=9000 → 升了一半 → 50%
        pct = reg.compute_progress(9000, 8000, 10000, "increase")
        assert pct == 50.0

    def test_stabilize_within_tolerance(self):
        reg = GoalMetricRegistry()
        # baseline=100, current=102 (within 5%) → 100%
        pct = reg.compute_progress(102, 100, 100, "stabilize")
        assert pct == 100.0

    def test_stabilize_outside_tolerance(self):
        reg = GoalMetricRegistry()
        # baseline=100, current=125 (≥25% deviation -> max_dev reached) → 0%
        pct = reg.compute_progress(125, 100, 100, "stabilize")
        assert pct == 0.0

    def test_stabilize_partial(self):
        reg = GoalMetricRegistry()
        # baseline=100, current=110 (5-20% deviation)
        pct = reg.compute_progress(110, 100, 100, "stabilize")
        assert 0 < pct < 100

    def test_none_returns_none(self):
        reg = GoalMetricRegistry()
        assert reg.compute_progress(None, 100, 100, "decrease") is None

    def test_zero_total_decrease(self):
        reg = GoalMetricRegistry()
        pct = reg.compute_progress(80, 80, 80, "decrease")
        assert pct == 100.0

    def test_zero_total_increase(self):
        reg = GoalMetricRegistry()
        pct = reg.compute_progress(80, 80, 80, "increase")
        assert pct == 100.0


class TestGoalManagerAddGoal:
    def test_add_goal_success(self, tmp_db):
        mgr = GoalManager(tmp_db)
        # seed vitals data for baseline calculation
        with db.get_conn(tmp_db) as conn:
            for i in range(3):
                db.insert_vital(conn, measured_at=f"2025-04-0{i+1} 08:00:00", systolic=120)
        goal_id = mgr.add_goal(
            name="降压目标", priority=1, metric_key="bp_systolic_mean_7d",
            direction="decrease", baseline_value=130, target=120,
        )
        assert goal_id is not None

    def test_add_goal_invalid_metric(self, tmp_db):
        mgr = GoalManager(tmp_db)
        with pytest.raises(ValueError, match="不支持的指标 key"):
            mgr.add_goal(name="test", priority=1, metric_key="invalid_key", direction="decrease")

    def test_add_goal_invalid_direction(self, tmp_db):
        mgr = GoalManager(tmp_db)
        with pytest.raises(ValueError, match="direction 必须是"):
            mgr.add_goal(name="test", priority=1, metric_key="steps_mean_7d", direction="up")

    def test_add_goal_invalid_priority(self, tmp_db):
        mgr = GoalManager(tmp_db)
        with pytest.raises(ValueError, match="priority 必须是 1-3"):
            mgr.add_goal(name="test", priority=5, metric_key="steps_mean_7d", direction="increase")


class TestGoalManagerLifecycle:
    def test_list_goals(self, tmp_db):
        mgr = GoalManager(tmp_db)
        with db.get_conn(tmp_db) as conn:
            for i in range(3):
                db.insert_vital(conn, measured_at=f"2025-04-0{i+1} 08:00:00", systolic=120)
        mgr.add_goal(name="目标A", priority=1, metric_key="bp_systolic_mean_7d", direction="decrease", baseline_value=130)
        goals = mgr.list_goals()
        assert len(goals) == 1
        assert goals[0]["name"] == "目标A"

    def test_get_goal(self, tmp_db):
        mgr = GoalManager(tmp_db)
        with db.get_conn(tmp_db) as conn:
            for i in range(3):
                db.insert_vital(conn, measured_at=f"2025-04-0{i+1} 08:00:00", systolic=120)
        gid = mgr.add_goal(name="目标B", priority=1, metric_key="bp_systolic_mean_7d", direction="decrease", baseline_value=130)
        g = mgr.get_goal(gid)
        assert g["name"] == "目标B"

    def test_update_status(self, tmp_db):
        mgr = GoalManager(tmp_db)
        with db.get_conn(tmp_db) as conn:
            for i in range(3):
                db.insert_vital(conn, measured_at=f"2025-04-0{i+1} 08:00:00", systolic=120)
        gid = mgr.add_goal(name="目标C", priority=1, metric_key="bp_systolic_mean_7d", direction="decrease", baseline_value=130)
        mgr.update_status(gid, "achieved")
        g = mgr.get_goal(gid)
        assert g["status"] == "achieved"
        assert g["achieved_date"] is not None

    def test_update_status_invalid(self, tmp_db):
        mgr = GoalManager(tmp_db)
        with pytest.raises(ValueError, match="status 必须是"):
            mgr.update_status(1, "invalid_status")

    def test_get_active_goals(self, tmp_db):
        mgr = GoalManager(tmp_db)
        with db.get_conn(tmp_db) as conn:
            for i in range(3):
                db.insert_vital(conn, measured_at=f"2025-04-0{i+1} 08:00:00", systolic=120)
        mgr.add_goal(name="活跃目标", priority=1, metric_key="bp_systolic_mean_7d", direction="decrease", baseline_value=130)
        active = mgr.get_active_goals()
        assert len(active) == 1


class TestGoalManagerTrackProgress:
    def test_track_daily_progress(self, tmp_db):
        mgr = GoalManager(tmp_db)
        with db.get_conn(tmp_db) as conn:
            for i in range(3):
                db.insert_vital(conn, measured_at=f"2025-04-0{i+1} 08:00:00", systolic=120)
        gid = mgr.add_goal(name="降压", priority=1, metric_key="bp_systolic_mean_7d", direction="decrease", baseline_value=130, target=110)
        mgr.track_daily_progress("2025-04-03")
        progress = mgr.get_goal_progress(gid)
        assert len(progress) == 1


class TestGoalManagerChecks:
    def test_value_meets_target_decrease(self):
        assert GoalManager._value_meets_target(120, 130, "decrease") is True
        assert GoalManager._value_meets_target(135, 130, "decrease") is False

    def test_value_meets_target_increase(self):
        assert GoalManager._value_meets_target(9000, 8000, "increase") is True
        assert GoalManager._value_meets_target(7000, 8000, "increase") is False

    def test_value_meets_target_stabilize(self):
        assert GoalManager._value_meets_target(102, 100, "stabilize") is True
        assert GoalManager._value_meets_target(110, 100, "stabilize") is False


class TestMetricRegistry:
    def test_valid_keys(self):
        assert "steps_mean_7d" in METRIC_REGISTRY
        assert "bp_systolic_mean_7d" in METRIC_REGISTRY

    def test_metric_spec_fields(self):
        spec = METRIC_REGISTRY["steps_mean_7d"]
        assert spec.table == "daily_health"
        assert spec.column == "steps"
        assert spec.aggregation == "mean_7d"
