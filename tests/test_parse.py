from superhealth.analysis.analyze_garmin import (
    fmt_minutes_hm,
    fmt_val,
    has_meaningful_data,
)


class TestHasMeaningfulData:
    def test_with_real_data(self):
        assert has_meaningful_data({"sleep_score": 63, "resting_hr": 57, "hrv_avg": 35}) is True

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
