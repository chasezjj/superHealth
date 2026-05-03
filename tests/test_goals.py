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


class TestGoalManagerDeleteWithExperiments:
    """删除目标时若存在未结案绑定实验，应抛 ValueError。"""

    def _seed_goal(self, tmp_db, mgr):
        with db.get_conn(tmp_db) as conn:
            for i in range(3):
                db.insert_vital(conn, measured_at=f"2025-04-0{i+1} 08:00:00", systolic=120)
        return mgr.add_goal(
            name="降压",
            metric_key="bp_systolic_mean_7d",
            direction="decrease",
            baseline_value=130,
            target=120,
        )

    def test_delete_blocked_by_draft_experiment(self, tmp_db):
        from superhealth.feedback.experiment_manager import ExperimentManager
        mgr = GoalManager(tmp_db)
        gid = self._seed_goal(tmp_db, mgr)
        exp_mgr = ExperimentManager(tmp_db)
        exp_mgr.create_draft(
            name="等长运动",
            hypothesis="降压 5 mmHg",
            goal_id=gid,
            metric_key="bp_systolic_mean_7d",
            direction="decrease",
            intervention="每日 4 组等长握力",
        )
        with pytest.raises(ValueError, match="未结案的绑定实验"):
            mgr.delete_goal(gid)
        # 目标仍然存在
        assert mgr.get_goal(gid) is not None

    def test_delete_blocked_by_active_experiment(self, tmp_db):
        from superhealth.feedback.experiment_manager import ExperimentManager
        mgr = GoalManager(tmp_db)
        gid = self._seed_goal(tmp_db, mgr)
        exp_mgr = ExperimentManager(tmp_db)
        eid = exp_mgr.create_draft(
            name="等长运动",
            hypothesis="降压 5 mmHg",
            goal_id=gid,
            metric_key="bp_systolic_mean_7d",
            direction="decrease",
            intervention="每日 4 组等长握力",
        )
        exp_mgr.activate(eid)
        with pytest.raises(ValueError, match="未结案的绑定实验"):
            mgr.delete_goal(gid)

    def test_delete_allowed_after_experiment_cancelled_and_removed(self, tmp_db):
        from superhealth.feedback.experiment_manager import ExperimentManager
        mgr = GoalManager(tmp_db)
        gid = self._seed_goal(tmp_db, mgr)
        exp_mgr = ExperimentManager(tmp_db)
        eid = exp_mgr.create_draft(
            name="等长运动",
            hypothesis="降压",
            goal_id=gid,
            metric_key="bp_systolic_mean_7d",
            direction="decrease",
            intervention="每日 4 组等长握力",
        )
        exp_mgr.activate(eid)
        exp_mgr.cancel(eid)         # active → draft
        exp_mgr.delete_draft(eid)   # 草稿删干净
        mgr.delete_goal(gid)
        assert mgr.get_goal(gid) is None

    def test_delete_allowed_with_only_historical_experiments(self, tmp_db):
        """已结案 (completed/reverted) 的实验不应阻止目标删除。"""
        from superhealth.feedback.experiment_manager import ExperimentManager
        mgr = GoalManager(tmp_db)
        gid = self._seed_goal(tmp_db, mgr)
        exp_mgr = ExperimentManager(tmp_db)
        eid = exp_mgr.create_draft(
            name="等长运动",
            hypothesis="降压",
            goal_id=gid,
            metric_key="bp_systolic_mean_7d",
            direction="decrease",
            intervention="每日 4 组等长握力",
        )
        # 直接改成 completed 模拟历史结案
        with db.get_conn(tmp_db) as conn:
            conn.execute("UPDATE experiments SET status='completed' WHERE id=?", (eid,))
        mgr.delete_goal(gid)
        assert mgr.get_goal(gid) is None

    def test_get_blocking_experiments_returns_only_open(self, tmp_db):
        from superhealth.feedback.experiment_manager import ExperimentManager
        mgr = GoalManager(tmp_db)
        gid = self._seed_goal(tmp_db, mgr)
        exp_mgr = ExperimentManager(tmp_db)
        d_eid = exp_mgr.create_draft(
            name="A",
            hypothesis="h",
            goal_id=gid,
            metric_key="bp_systolic_mean_7d",
            direction="decrease",
            intervention="i",
        )
        c_eid = exp_mgr.create_draft(
            name="B",
            hypothesis="h",
            goal_id=gid,
            metric_key="bp_systolic_mean_7d",
            direction="decrease",
            intervention="i",
        )
        with db.get_conn(tmp_db) as conn:
            conn.execute("UPDATE experiments SET status='completed' WHERE id=?", (c_eid,))
        blocking = mgr.get_blocking_experiments(gid)
        ids = {b["id"] for b in blocking}
        assert ids == {d_eid}


class TestGoalStatusAutoClosesExperiments:
    """目标进入终态时，自动处理绑定的 active/draft 实验。"""

    def _seed_goal(self, tmp_db, mgr):
        with db.get_conn(tmp_db) as conn:
            for i in range(3):
                db.insert_vital(conn, measured_at=f"2025-04-0{i+1} 08:00:00", systolic=120)
        return mgr.add_goal(
            name="降压",
            metric_key="bp_systolic_mean_7d",
            direction="decrease",
            baseline_value=130,
            target=120,
        )

    def _create_exp(self, exp_mgr, goal_id, name="等长运动"):
        return exp_mgr.create_draft(
            name=name,
            hypothesis="降压",
            goal_id=goal_id,
            metric_key="bp_systolic_mean_7d",
            direction="decrease",
            intervention="每日 4 组等长握力",
        )

    def test_achieved_completes_active_experiment(self, tmp_db):
        from superhealth.feedback.experiment_manager import ExperimentManager
        mgr = GoalManager(tmp_db)
        gid = self._seed_goal(tmp_db, mgr)
        exp_mgr = ExperimentManager(tmp_db)
        eid = self._create_exp(exp_mgr, gid)
        exp_mgr.activate(eid)

        mgr.update_status(gid, "achieved")

        exp = exp_mgr.get_experiment(eid)
        assert exp["status"] == "completed"
        assert "自动结案" in exp["conclusion"]
        assert exp["conclusion_date"] is not None
        assert exp_mgr.get_active_experiment() is None

    def test_abandoned_reverts_active_experiment(self, tmp_db):
        from superhealth.feedback.experiment_manager import ExperimentManager
        mgr = GoalManager(tmp_db)
        gid = self._seed_goal(tmp_db, mgr)
        exp_mgr = ExperimentManager(tmp_db)
        eid = self._create_exp(exp_mgr, gid)
        exp_mgr.activate(eid)

        mgr.update_status(gid, "abandoned")

        exp = exp_mgr.get_experiment(eid)
        assert exp["status"] == "reverted"
        assert "自动回退" in exp["conclusion"]
        assert exp_mgr.get_active_experiment() is None

    def test_paused_reverts_active_experiment(self, tmp_db):
        from superhealth.feedback.experiment_manager import ExperimentManager
        mgr = GoalManager(tmp_db)
        gid = self._seed_goal(tmp_db, mgr)
        exp_mgr = ExperimentManager(tmp_db)
        eid = self._create_exp(exp_mgr, gid)
        exp_mgr.activate(eid)

        mgr.update_status(gid, "paused")

        exp = exp_mgr.get_experiment(eid)
        assert exp["status"] == "reverted"
        assert "自动回退" in exp["conclusion"]
        assert exp_mgr.get_active_experiment() is None

    def test_terminal_status_deletes_drafts(self, tmp_db):
        from superhealth.feedback.experiment_manager import ExperimentManager
        mgr = GoalManager(tmp_db)
        gid = self._seed_goal(tmp_db, mgr)
        exp_mgr = ExperimentManager(tmp_db)
        draft_id = self._create_exp(exp_mgr, gid, name="草稿实验")

        mgr.update_status(gid, "achieved")

        assert exp_mgr.get_experiment(draft_id) is None
        all_exps = exp_mgr.list_experiments()
        assert len(all_exps) == 0

    def test_terminal_status_does_not_affect_other_goals_experiments(self, tmp_db):
        from superhealth.feedback.experiment_manager import ExperimentManager
        mgr = GoalManager(tmp_db)
        gid1 = self._seed_goal(tmp_db, mgr)
        # 第二个目标不重复插 vitals，避免 unique 冲突
        gid2 = mgr.add_goal(
            name="降压2",
            metric_key="bp_systolic_mean_7d",
            direction="decrease",
            baseline_value=130,
            target=120,
        )
        exp_mgr = ExperimentManager(tmp_db)

        e1 = self._create_exp(exp_mgr, gid1, name="实验A")
        e2 = self._create_exp(exp_mgr, gid2, name="实验B")
        exp_mgr.activate(e2)

        mgr.update_status(gid1, "achieved")

        # gid2 的实验应保持不变
        exp2 = exp_mgr.get_experiment(e2)
        assert exp2["status"] == "active"
        exp1 = exp_mgr.get_experiment(e1)
        assert exp1 is None  # gid1 的 draft 被删

    def test_active_exp_preference_cleaned_on_auto_close(self, tmp_db):
        from superhealth.feedback.experiment_manager import ExperimentManager
        mgr = GoalManager(tmp_db)
        gid = self._seed_goal(tmp_db, mgr)
        exp_mgr = ExperimentManager(tmp_db)
        eid = self._create_exp(exp_mgr, gid)
        exp_mgr.activate(eid)

        mgr.update_status(gid, "achieved")

        with db.get_conn(tmp_db) as conn:
            row = conn.execute(
                "SELECT 1 FROM learned_preferences WHERE preference_key = ?",
                (f"active_exp_{eid}",),
            ).fetchone()
        assert row is None