"""测试 effect_tracker 的可测试纯函数逻辑。"""
import math

import pytest

from superhealth.feedback.effect_tracker import EffectTracker


class TestPickPrimaryExercise:
    def test_empty_list(self):
        assert EffectTracker._pick_primary_exercise([]) is None

    def test_single_exercise(self):
        rows = [{"duration_seconds": 1800, "avg_hr": 140}]
        winner = EffectTracker._pick_primary_exercise(rows)
        assert winner["duration_seconds"] == 1800

    def test_picks_highest_score(self):
        rows = [
            {"duration_seconds": 600, "avg_hr": 100},   # score = 60000
            {"duration_seconds": 1800, "avg_hr": 140},  # score = 252000
            {"duration_seconds": 300, "avg_hr": 120},   # score = 36000
        ]
        winner = EffectTracker._pick_primary_exercise(rows)
        assert winner["duration_seconds"] == 1800

    def test_no_hr_uses_baseline(self):
        rows = [
            {"duration_seconds": 600, "avg_hr": None},   # score = 600 * 60 = 36000
            {"duration_seconds": 300, "avg_hr": 140},    # score = 300 * 140 = 42000
        ]
        winner = EffectTracker._pick_primary_exercise(rows)
        assert winner["duration_seconds"] == 300

    def test_returns_dict_for_sqlite_row(self):
        class FakeRow:
            def __getitem__(self, key):
                return {"duration_seconds": 1800, "avg_hr": 140}.get(key)
        winner = EffectTracker._pick_primary_exercise([FakeRow()])
        assert winner["duration_seconds"] == 1800


class TestMetricEffectScore:
    def test_positive_hrv(self):
        # hrv up = good
        score = EffectTracker._metric_effect_score("hrv_avg", 5, 5)
        assert score > 0
        assert score <= 1

    def test_negative_hrv(self):
        score = EffectTracker._metric_effect_score("hrv_avg", -5, 5)
        assert score < 0
        assert score >= -1

    def test_resting_hr_reversed(self):
        # resting hr up = bad (reversed)
        score_up = EffectTracker._metric_effect_score("resting_hr", 5, 5)
        assert score_up < 0
        score_down = EffectTracker._metric_effect_score("resting_hr", -5, 5)
        assert score_down > 0

    def test_stress_reversed(self):
        score_up = EffectTracker._metric_effect_score("avg_stress", 5, 5)
        assert score_up < 0

    def test_zero_std_fallback(self):
        score = EffectTracker._metric_effect_score("hrv_avg", 5, 0)
        assert score == math.tanh(5 / 1.5)

    def test_none_std_fallback(self):
        score = EffectTracker._metric_effect_score("hrv_avg", 5, None)
        assert score == math.tanh(5 / 1.5)


class TestComputeGoalAlignedScore:
    def test_no_goals_returns_none(self):
        tracker = EffectTracker()
        assert tracker.compute_goal_aligned_score({}, []) is None

    def test_no_matching_metrics_returns_none(self):
        tracker = EffectTracker()
        goals = [{"metric_key": "unknown_metric"}]
        assert tracker.compute_goal_aligned_score({"net_effects": {"hrv_avg": 0.5}}, goals) is None

    def test_with_matching_goal(self):
        tracker = EffectTracker()
        goals = [{"metric_key": "hrv_mean_7d"}]
        result = tracker.compute_goal_aligned_score(
            {"net_effects": {"hrv_avg": 0.5}}, goals
        )
        assert result is not None
        assert isinstance(result, float)

    def test_without_net_effects_returns_none(self):
        tracker = EffectTracker()
        goals = [{"metric_key": "hrv_mean_7d"}]
        assert tracker.compute_goal_aligned_score({"net_effects": {}}, goals) is None
