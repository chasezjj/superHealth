"""测试 vitals_receiver 的数据解析函数。"""
import pytest

from superhealth.api.vitals_receiver import _normalize_ts, _parse_payload


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


class TestParsePayload:
    def test_v2_format_blood_pressure(self):
        payload = {
            "data": {
                "metrics": [
                    {
                        "name": "blood_pressure_systolic",
                        "units": "mmHg",
                        "data": [{"date": "2025-04-01 08:15:00 +0800", "qty": 128}],
                    },
                    {
                        "name": "blood_pressure_diastolic",
                        "units": "mmHg",
                        "data": [{"date": "2025-04-01 08:15:00 +0800", "qty": 82}],
                    },
                ]
            }
        }
        records = _parse_payload(payload)
        assert len(records) == 1
        assert records[0]["measured_at"] == "2025-04-01T08:15:00+08:00"
        assert records[0]["systolic"] == 128
        assert records[0]["diastolic"] == 82

    def test_v2_format_weight_and_body_fat(self):
        payload = {
            "data": {
                "metrics": [
                    {
                        "name": "weight_body_mass",
                        "units": "kg",
                        "data": [{"date": "2025-04-01 07:00:00 +0800", "qty": 68.5}],
                    },
                    {
                        "name": "body_fat_percentage",
                        "units": "%",
                        "data": [{"date": "2025-04-01 07:00:00 +0800", "qty": 22.3}],
                    },
                ]
            }
        }
        records = _parse_payload(payload)
        assert len(records) == 1
        assert records[0]["weight_kg"] == 68.5
        assert records[0]["body_fat_pct"] == 22.3

    def test_v1_format_compat(self):
        payload = {
            "metrics": [
                {
                    "name": "weight_body_mass",
                    "data": [{"date": "2025-04-01 07:00:00", "qty": 70.0}],
                }
            ]
        }
        records = _parse_payload(payload)
        assert len(records) == 1
        assert records[0]["weight_kg"] == 70.0

    def test_blood_pressure_combined_format(self):
        payload = {
            "data": {
                "metrics": [
                    {
                        "name": "blood_pressure",
                        "data": [
                            {"date": "2025-04-01 08:15:00 +0800", "systolic": 120, "diastolic": 80}
                        ],
                    }
                ]
            }
        }
        records = _parse_payload(payload)
        assert len(records) == 1
        assert records[0]["systolic"] == 120
        assert records[0]["diastolic"] == 80

    def test_multiple_timepoints(self):
        payload = {
            "data": {
                "metrics": [
                    {
                        "name": "blood_pressure_systolic",
                        "data": [
                            {"date": "2025-04-01 08:00:00 +0800", "qty": 120},
                            {"date": "2025-04-01 20:00:00 +0800", "qty": 115},
                        ],
                    }
                ]
            }
        }
        records = _parse_payload(payload)
        assert len(records) == 2
        assert records[0]["measured_at"] == "2025-04-01T08:00:00+08:00"
        assert records[1]["measured_at"] == "2025-04-01T20:00:00+08:00"

    def test_ignore_unknown_metrics(self):
        payload = {
            "data": {
                "metrics": [
                    {
                        "name": "heart_rate",
                        "data": [{"date": "2025-04-01 08:00:00", "qty": 70}],
                    },
                    {
                        "name": "unknown_metric",
                        "data": [{"date": "2025-04-01 08:00:00", "qty": 999}],
                    },
                ]
            }
        }
        records = _parse_payload(payload)
        assert len(records) == 0

    def test_empty_payload(self):
        assert _parse_payload({}) == []
        assert _parse_payload({"data": {}}) == []
        assert _parse_payload({"data": {"metrics": []}}) == []

    def test_missing_qty_skipped(self):
        payload = {
            "data": {
                "metrics": [
                    {
                        "name": "weight_body_mass",
                        "data": [{"date": "2025-04-01 07:00:00"}],  # 没有 qty
                    }
                ]
            }
        }
        records = _parse_payload(payload)
        assert len(records) == 0

    def test_body_mass_alias(self):
        payload = {
            "data": {
                "metrics": [
                    {
                        "name": "body_mass",
                        "data": [{"date": "2025-04-01 07:00:00", "qty": 65.0}],
                    }
                ]
            }
        }
        records = _parse_payload(payload)
        assert len(records) == 1
        assert records[0]["weight_kg"] == 65.0
