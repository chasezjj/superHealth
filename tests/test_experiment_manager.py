"""测试 experiment_manager 的生命周期管理和纯函数。"""
from unittest.mock import MagicMock, patch

import pytest

from superhealth import database as db
from superhealth.feedback.experiment_manager import (
    ExperimentManager,
    _METRIC_LABELS,
    _METRIC_TO_CAUSAL_KEY,
)


@pytest.fixture
def tmp_db(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    return db_path


class TestDirectionMatches:
    def test_increase(self):
        mgr = ExperimentManager()
        assert mgr._direction_matches("increase", 5) is True
        assert mgr._direction_matches("increase", -2) is False

    def test_decrease(self):
        mgr = ExperimentManager()
        assert mgr._direction_matches("decrease", -3) is True
        assert mgr._direction_matches("decrease", 2) is False

    def test_stabilize_within_tolerance(self):
        mgr = ExperimentManager()
        assert mgr._direction_matches("stabilize", 2, baseline=100) is True

    def test_stabilize_outside_tolerance(self):
        mgr = ExperimentManager()
        assert mgr._direction_matches("stabilize", 15, baseline=100) is False


class TestExperimentManagerCRUD:
    def test_create_draft(self, tmp_db):
        mgr = ExperimentManager(tmp_db)
        exp_id = mgr.create_draft(
            name="测试实验", hypothesis="假设", goal_id=None,
            metric_key="sleep_score_mean_7d", direction="increase",
            intervention="每天早睡1小时",
        )
        assert exp_id is not None
        exp = mgr.get_experiment(exp_id)
        assert exp["name"] == "测试实验"
        assert exp["status"] == "draft"

    def test_create_draft_duplicate_raises(self, tmp_db):
        mgr = ExperimentManager(tmp_db)
        mgr.create_draft(name="同名实验", hypothesis="h", goal_id=1, metric_key="steps_mean_7d", direction="increase", intervention="test")
        with pytest.raises(ValueError, match="已有同名草稿"):
            mgr.create_draft(name="同名实验", hypothesis="h", goal_id=1, metric_key="steps_mean_7d", direction="increase", intervention="test")

    def test_list_experiments(self, tmp_db):
        mgr = ExperimentManager(tmp_db)
        mgr.create_draft(goal_id=None, name="实验A", hypothesis="h", metric_key="steps_mean_7d", direction="increase", intervention="test")
        mgr.create_draft(goal_id=None, name="实验B", hypothesis="h", metric_key="steps_mean_7d", direction="increase", intervention="test")
        all_exps = mgr.list_experiments()
        assert len(all_exps) == 2

    def test_list_by_status(self, tmp_db):
        mgr = ExperimentManager(tmp_db)
        mgr.create_draft(goal_id=None, name="草稿", hypothesis="h", metric_key="steps_mean_7d", direction="increase", intervention="test")
        drafts = mgr.list_experiments(status="draft")
        assert len(drafts) == 1
        active = mgr.list_experiments(status="active")
        assert len(active) == 0

    def test_delete_draft(self, tmp_db):
        mgr = ExperimentManager(tmp_db)
        exp_id = mgr.create_draft(goal_id=None, name="待删", hypothesis="h", metric_key="steps_mean_7d", direction="increase", intervention="test")
        mgr.delete_draft(exp_id)
        assert mgr.get_experiment(exp_id) is None

    def test_delete_non_draft_raises(self, tmp_db):
        mgr = ExperimentManager(tmp_db)
        exp_id = mgr.create_draft(goal_id=None, name="活跃实验", hypothesis="h", metric_key="steps_mean_7d", direction="increase", intervention="test")
        mgr.activate(exp_id)
        with pytest.raises(ValueError, match="只有 draft 可以删除"):
            mgr.delete_draft(exp_id)


class TestExperimentManagerLifecycle:
    def test_activate_and_cancel(self, tmp_db):
        mgr = ExperimentManager(tmp_db)
        exp_id = mgr.create_draft(goal_id=None, name="激活实验", hypothesis="h", metric_key="steps_mean_7d", direction="increase", intervention="test")
        mgr.activate(exp_id)
        exp = mgr.get_experiment(exp_id)
        assert exp["status"] == "active"
        assert exp["start_date"] is not None

        # Cancel should revert to draft
        mgr.cancel(exp_id)
        exp = mgr.get_experiment(exp_id)
        assert exp["status"] == "draft"
        assert exp["start_date"] is None

    def test_activate_duplicate_raises(self, tmp_db):
        mgr = ExperimentManager(tmp_db)
        exp1 = mgr.create_draft(goal_id=None, name="实验1", hypothesis="h", metric_key="steps_mean_7d", direction="increase", intervention="test")
        exp2 = mgr.create_draft(goal_id=None, name="实验2", hypothesis="h", metric_key="steps_mean_7d", direction="increase", intervention="test")
        mgr.activate(exp1)
        with pytest.raises(ValueError, match="已有活跃实验"):
            mgr.activate(exp2)

    def test_get_active_experiment(self, tmp_db):
        mgr = ExperimentManager(tmp_db)
        assert mgr.get_active_experiment() is None
        exp_id = mgr.create_draft(goal_id=None, name="活跃", hypothesis="h", metric_key="steps_mean_7d", direction="increase", intervention="test")
        mgr.activate(exp_id)
        active = mgr.get_active_experiment()
        assert active is not None
        assert active["id"] == exp_id


class TestMetricMappings:
    def test_metric_labels(self):
        assert "steps_mean_7d" in _METRIC_LABELS
        assert _METRIC_LABELS["steps_mean_7d"] == "步数（7日均值）"

    def test_causal_key_mapping(self):
        assert _METRIC_TO_CAUSAL_KEY["sleep_score_mean_7d"] == "sleep_score"
        assert _METRIC_TO_CAUSAL_KEY["hrv_mean_7d"] == "hrv_avg"
