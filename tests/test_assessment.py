"""Test assessment models: RecoveryModel scoring logic."""
import pytest

from superhealth.core.assessment_models import RecoveryModel, AssessmentResult


class MockProfile:
    """Minimal mock HealthProfile for testing."""

    def __init__(self, **trends):
        self.trends = trends
        self.conditions = []
        self.goals = []


def _profile(**kw):
    return MockProfile(**kw)


class TestRecoveryModel:
    def setup_method(self):
        self.model = RecoveryModel()

    def test_perfect_recovery(self):
        result = self.model.assess(
            daily_data={
                "sleep_score": 90,
                "hrv_status": "BALANCED",
                "body_battery_wake": 85,
                "resting_hr": 50,
                "avg7_resting_hr": 55,
                "avg_stress": 20,
            },
            vitals_data={},
            profile=_profile(),
        )
        assert isinstance(result, AssessmentResult)
        assert result.model_name == "RecoveryModel"
        assert result.score > 70
        assert result.status in ("优秀", "良好")

    def test_poor_recovery(self):
        result = self.model.assess(
            daily_data={
                "sleep_score": 40,
                "hrv_status": "LOW",
                "body_battery_wake": 20,
                "resting_hr": 70,
                "avg7_resting_hr": 55,
                "avg_stress": 50,
            },
            vitals_data={},
            profile=_profile(),
        )
        assert result.score < 40
        assert result.status in ("一般", "需关注", "需恢复")

    def test_empty_data_low_score(self):
        result = self.model.assess(
            daily_data={},
            vitals_data={},
            profile=_profile(),
        )
        assert result.score <= 40

    def test_zscore_personalization(self):
        """With personal baseline, same absolute value should score differently."""
        # Without baseline: uses absolute thresholds
        r1 = self.model.assess(
            daily_data={"sleep_score": 75},
            vitals_data={},
            profile=_profile(),
        )

        # With high baseline: 75 is below baseline, should score lower
        r2 = self.model.assess(
            daily_data={"sleep_score": 75},
            vitals_data={},
            profile=_profile(sleep_90d_avg=85, sleep_90d_std=5),
        )

        # With low baseline: 75 is above baseline, should score higher
        r3 = self.model.assess(
            daily_data={"sleep_score": 75},
            vitals_data={},
            profile=_profile(sleep_90d_avg=65, sleep_90d_std=5),
        )

        assert r3.score >= r1.score
        assert r1.score >= r2.score

    def test_hrv_status_variants(self):
        statuses = {"BALANCED": None, "UNBALANCED": None, "LOW": None, "NO_DATA": None}
        for status in statuses:
            result = self.model.assess(
                daily_data={"hrv_status": status},
                vitals_data={},
                profile=_profile(),
            )
            assert isinstance(result, AssessmentResult)

    def test_result_has_required_fields(self):
        result = self.model.assess(
            daily_data={"sleep_score": 80},
            vitals_data={},
            profile=_profile(),
        )
        assert result.model_name
        assert result.label
        assert 0 <= result.score <= 100
        assert result.status in ("优秀", "良好", "一般", "需关注", "需恢复")
        assert result.summary
