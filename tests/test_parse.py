"""测试 Markdown 解析（使用 fixture 文件）。"""
from pathlib import Path

import pytest

from superhealth.analysis.analyze_garmin import (
    _parse_markdown, has_meaningful_data, fmt_val, fmt_minutes_hm
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_0425 = FIXTURE_DIR / "2025-04-25.md"
FIXTURE_0426 = FIXTURE_DIR / "2025-04-26.md"


@pytest.fixture
def data_0425():
    """2025-04-25 的解析结果。"""
    return _parse_markdown(FIXTURE_0425)


@pytest.fixture
def data_0426():
    """2025-04-26 的解析结果。"""
    return _parse_markdown(FIXTURE_0426)


class TestParseMarkdown:
    def test_date_field(self, data_0425):
        assert data_0425["date"] == "2025-04-25"

    def test_sleep_total_minutes(self, data_0425):
        # 6h 47m = 407 分钟
        assert data_0425["sleep_total_min"] == 407

    def test_sleep_score(self, data_0425):
        assert data_0425["sleep_score"] == 63.0

    def test_avg_stress(self, data_0425):
        assert data_0425["avg_stress"] == 30.0

    def test_resting_hr(self, data_0425):
        assert data_0425["resting_hr"] == 57.0

    def test_avg7_resting_hr(self, data_0425):
        assert data_0425["avg7_resting_hr"] == 53.0

    def test_body_battery_wake(self, data_0425):
        assert data_0425["body_battery_wake"] == 51.0

    def test_spo2_avg(self, data_0425):
        assert data_0425["spo2_avg"] == 94.0

    def test_spo2_lowest(self, data_0425):
        assert data_0425["spo2_lowest"] == 87.0

    def test_resp_waking(self, data_0425):
        assert data_0425["resp_waking"] == 17.0

    def test_hrv_avg(self, data_0425):
        assert data_0425["hrv_avg"] == 35.0

    def test_hrv_baseline(self, data_0425):
        assert data_0425["hrv_baseline_low"] == 34.0
        assert data_0425["hrv_baseline_high"] == 42.0

    def test_hrv_status(self, data_0425):
        assert data_0425["hrv_status"] == "UNBALANCED"


class TestHasMeaningfulData:
    def test_with_real_data(self, data_0425):
        assert has_meaningful_data(data_0425) is True

    def test_with_empty_dict(self):
        assert has_meaningful_data({}) is False

    def test_with_sleep_score_only(self):
        assert has_meaningful_data({"sleep_score": 75}) is True

    def test_with_resting_hr_only(self):
        assert has_meaningful_data({"resting_hr": 55}) is True

    def test_with_body_battery_only(self):
        assert has_meaningful_data({"body_battery_wake": 60}) is True


class TestFmtHelpers:
    def test_fmt_val_none(self):
        assert fmt_val(None) == "N/A"

    def test_fmt_val_integer_float(self):
        assert fmt_val(63.0) == "63"

    def test_fmt_val_decimal(self):
        assert fmt_val(63.5) == "63.5"

    def test_fmt_val_with_suffix(self):
        assert fmt_val(55.0, " bpm") == "55 bpm"

    def test_fmt_minutes_hm_none(self):
        assert fmt_minutes_hm(None) == "N/A"

    def test_fmt_minutes_hm_over_hour(self):
        assert fmt_minutes_hm(407) == "6小时47分"

    def test_fmt_minutes_hm_minutes_only(self):
        assert fmt_minutes_hm(45) == "45分"
