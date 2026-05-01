"""测试 vitals_receiver 的数据解析函数。"""
import pytest

from superhealth.api.vitals_receiver import _normalize_ts


class TestNormalizeTs:
    def test_iso_format_with_tz(self):
        assert _normalize_ts("2025-04-01T08:30:00+08:00") == "2025-04-01T08:30:00+08:00"

    def test_iso_format_without_tz_colon(self):
        assert _normalize_ts("2025-04-01T08:30:00+0800") == "2025-04-01T08:30:00+08:00"

    def test_standard_format_with_tz(self):
        assert _normalize_ts("2025-04-01 08:30:00 +0800") == "2025-04-01T08:30:00+08:00"

    def test_standard_format_without_tz(self):
        assert _normalize_ts("2025-04-01 08:30:00") == "2025-04-01T08:30:00"

    def test_empty_string(self):
        assert _normalize_ts("") == ""

    def test_none(self):
        assert _normalize_ts(None) == ""

    def test_invalid_string_fallback(self):
        assert _normalize_ts("not-a-date") == "not-a-date"
