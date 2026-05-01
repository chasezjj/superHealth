"""测试 Pydantic 数据模型的属性和方法。"""
import pytest

from superhealth.models import (
    ActivityData,
    BodyBatteryData,
    DailyHealth,
    Exercise,
    HeartRateData,
    HRVData,
    RespirationData,
    SleepData,
    SpO2Data,
    StressData,
)


class TestSleepData:
    def test_total_minutes(self):
        s = SleepData(total_seconds=3660)
        assert s.total_minutes == 61

    def test_total_minutes_none(self):
        s = SleepData()
        assert s.total_minutes is None

    def test_has_data_with_total_seconds(self):
        s = SleepData(total_seconds=3600)
        assert s.has_data is True

    def test_has_data_empty(self):
        s = SleepData()
        assert s.has_data is False


class TestActivityData:
    def test_distance_km(self):
        a = ActivityData(distance_meters=5250)
        assert a.distance_km == 5.2  # round(5.25, 1) == 5.2 in Python

    def test_distance_km_none(self):
        a = ActivityData()
        assert a.distance_km is None


class TestExercise:
    def test_distance_km_with_value(self):
        e = Exercise(distance_meters=42195)
        assert e.distance_km == 42.2

    def test_distance_km_zero(self):
        e = Exercise(distance_meters=0)
        assert e.distance_km is None

    def test_distance_km_none(self):
        e = Exercise()
        assert e.distance_km is None

    def test_pace_str_running(self):
        e = Exercise(type_key="running", avg_speed=2.78)
        # 1000/2.78 = 359.7s → 5:59/km
        assert e.pace_str == "5:59/km"

    def test_pace_str_walking(self):
        e = Exercise(type_key="walking", avg_speed=1.39)
        # 1000/1.39 = 719.4s → 11:59/km
        assert e.pace_str == "11:59/km"

    def test_pace_str_no_speed(self):
        e = Exercise(type_key="running")
        assert e.pace_str is None

    def test_pace_str_not_applicable(self):
        e = Exercise(type_key="strength", avg_speed=5.0)
        assert e.pace_str is None


class TestDailyHealth:
    def test_has_data_with_sleep(self):
        dh = DailyHealth(date="2025-04-01", sleep=SleepData(total_seconds=3600))
        assert dh.has_data is True

    def test_has_data_with_hr(self):
        dh = DailyHealth(
            date="2025-04-01", heart_rate=HeartRateData(resting=55)
        )
        assert dh.has_data is True

    def test_has_data_with_body_battery(self):
        dh = DailyHealth(
            date="2025-04-01", body_battery=BodyBatteryData(at_wake=80)
        )
        assert dh.has_data is True

    def test_has_data_empty(self):
        dh = DailyHealth(date="2025-04-01")
        assert dh.has_data is False

    def test_to_flat_dict_keys(self):
        dh = DailyHealth(
            date="2025-04-01",
            sleep=SleepData(total_seconds=3600, score=85),
            stress=StressData(average=25),
            heart_rate=HeartRateData(resting=55, avg7_resting=53),
            body_battery=BodyBatteryData(at_wake=75),
            spo2=SpO2Data(average=95, lowest=88, latest=94),
            respiration=RespirationData(waking_avg=14),
            activity=ActivityData(steps=8000, distance_meters=5000),
            hrv=HRVData(
                last_night_avg=40,
                baseline_low=35,
                baseline_high=45,
                status="BALANCED",
            ),
        )
        flat = dh.to_flat_dict()
        assert flat["date"] == "2025-04-01"
        assert flat["sleep_total_min"] == 60
        assert flat["sleep_score"] == 85
        assert flat["avg_stress"] == 25
        assert flat["resting_hr"] == 55
        assert flat["avg7_resting_hr"] == 53
        assert flat["body_battery_wake"] == 75
        assert flat["spo2_avg"] == 95
        assert flat["spo2_lowest"] == 88
        assert flat["spo2_latest"] == 94
        assert flat["resp_waking"] == 14
        assert flat["steps"] == 8000
        assert flat["distance_km"] == 5.0
        assert flat["hrv_avg"] == 40
        assert flat["hrv_baseline_low"] == 35
        assert flat["hrv_baseline_high"] == 45
        assert flat["hrv_status"] == "BALANCED"

    def test_to_flat_dict_empty(self):
        dh = DailyHealth(date="2025-04-01")
        flat = dh.to_flat_dict()
        assert flat["date"] == "2025-04-01"
        assert flat["sleep_score"] is None
        assert flat["steps"] is None
