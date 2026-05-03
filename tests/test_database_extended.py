"""扩展测试 database.py 中未覆盖的 CRUD 函数。"""
from pathlib import Path

import pytest

from superhealth import database as db
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


@pytest.fixture
def tmp_db(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    return db_path


class TestUpsertDailyHealth:
    def test_insert_new_record(self, tmp_db):
        dh = DailyHealth(
            date="2025-04-01",
            sleep=SleepData(total_seconds=28800, score=85),
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
            exercises=[
                Exercise(
                    name="跑步",
                    type_key="running",
                    start_time="07:00",
                    distance_meters=5000,
                    duration_seconds=1800,
                    avg_hr=140,
                    max_hr=160,
                    avg_speed=2.78,
                    calories=300,
                )
            ],
        )
        with db.get_conn(tmp_db) as conn:
            db.upsert_daily_health(conn, dh)
            row = conn.execute("SELECT * FROM daily_health WHERE date = ?", ("2025-04-01",)).fetchone()
        assert row is not None
        assert row["sleep_score"] == 85
        assert row["hr_resting"] == 55
        assert row["steps"] == 8000

    def test_update_existing_record(self, tmp_db):
        dh1 = DailyHealth(date="2025-04-01", sleep=SleepData(score=80))
        dh2 = DailyHealth(date="2025-04-01", sleep=SleepData(score=90))
        with db.get_conn(tmp_db) as conn:
            db.upsert_daily_health(conn, dh1)
            db.upsert_daily_health(conn, dh2)
            row = conn.execute("SELECT sleep_score FROM daily_health WHERE date = ?", ("2025-04-01",)).fetchone()
        assert row["sleep_score"] == 90

    def test_audit_on_change(self, tmp_db):
        dh1 = DailyHealth(date="2025-04-01", sleep=SleepData(score=80), heart_rate=HeartRateData(resting=55))
        dh2 = DailyHealth(date="2025-04-01", sleep=SleepData(score=85), heart_rate=HeartRateData(resting=55))
        with db.get_conn(tmp_db) as conn:
            db.upsert_daily_health(conn, dh1)
            db.upsert_daily_health(conn, dh2)
            audits = conn.execute(
                "SELECT * FROM daily_health_audit WHERE date = ? AND field_name = 'sleep_score'",
                ("2025-04-01",),
            ).fetchall()
        # Initial insert + update both create audit records
        assert len(audits) >= 1
        new_values = [a["new_value"] for a in audits]
        assert 85 in new_values


class TestQueryDailyFlat:
    def test_query_existing(self, tmp_db):
        dh = DailyHealth(
            date="2025-04-01",
            sleep=SleepData(total_seconds=3660, score=85),
            activity=ActivityData(distance_meters=5250),
        )
        with db.get_conn(tmp_db) as conn:
            db.upsert_daily_health(conn, dh)
            flat = db.query_daily_flat(conn, "2025-04-01")
        assert flat is not None
        assert flat["sleep_score"] == 85
        assert flat["sleep_total_min"] == 61
        assert flat["distance_km"] == 5.2  # round(5.25, 1) == 5.2 in Python

    def test_query_missing(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            flat = db.query_daily_flat(conn, "2025-04-01")
        assert flat is None


class TestQueryDateRange:
    def test_range_returns_list(self, tmp_db):
        for d in ["2025-04-01", "2025-04-02", "2025-04-03"]:
            dh = DailyHealth(date=d, sleep=SleepData(score=80))
            with db.get_conn(tmp_db) as conn:
                db.upsert_daily_health(conn, dh)
        with db.get_conn(tmp_db) as conn:
            results = db.query_date_range(conn, "2025-04-01", "2025-04-02")
        assert len(results) == 2
        assert results[0]["date"] == "2025-04-01"


class TestObservationCRUD:
    def test_bulk_insert_observations(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            obs = [
                {
                    "obs_date": "2025-01-01",
                    "category": "lab",
                    "item_name": "尿酸",
                    "item_code": "UA",
                    "value_num": 420,
                    "unit": "μmol/L",
                    "ref_low": 208,
                    "ref_high": 428,
                    "is_abnormal": 0,
                }
            ]
            db.bulk_insert_observations(conn, obs)
            rows = conn.execute("SELECT * FROM medical_observations").fetchall()
        assert len(rows) == 1
        assert rows[0]["value_num"] == 420


class TestWeatherCRUD:
    def test_upsert_weather(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            db.upsert_weather(conn, date="2025-04-01", condition="晴", temperature=22.5, aqi=45)
            row = conn.execute("SELECT * FROM weather WHERE date = ?", ("2025-04-01",)).fetchone()
        assert row["condition"] == "晴"
        assert row["temperature"] == 22.5

    def test_upsert_weather_update(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            db.upsert_weather(conn, date="2025-04-01", condition="晴", temperature=22)
            db.upsert_weather(conn, date="2025-04-01", condition="多云", temperature=20)
            row = conn.execute("SELECT condition FROM weather WHERE date = ?", ("2025-04-01",)).fetchone()
        assert row["condition"] == "多云"


class TestCalendarCRUD:
    def test_insert_and_query(self, tmp_db):
        events = [
            {"subject": "会议A", "start_time": "09:00", "end_time": "10:00", "duration_min": 60},
            {"subject": "会议B", "start_time": "14:00", "end_time": "15:00", "duration_min": 60},
        ]
        with db.get_conn(tmp_db) as conn:
            db.insert_calendar_events(conn, date="2025-04-01", events=events)
            result = db.query_calendar_events(conn, "2025-04-01")
        assert len(result) == 2
        assert result[0]["subject"] == "会议A"

    def test_query_multi(self, tmp_db):
        events = [{"subject": "会议", "start_time": "09:00", "duration_min": 60}]
        with db.get_conn(tmp_db) as conn:
            db.insert_calendar_events(conn, date="2025-04-01", events=events)
            db.insert_calendar_events(conn, date="2025-04-02", events=events)
            multi = db.query_calendar_events_multi(conn, ["2025-04-01", "2025-04-02"])
        assert len(multi) == 2
        assert "2025-04-01" in multi

    def test_insert_idempotent(self, tmp_db):
        events = [{"subject": "会议", "start_time": "09:00", "duration_min": 60}]
        with db.get_conn(tmp_db) as conn:
            db.insert_calendar_events(conn, date="2025-04-01", events=events)
            db.insert_calendar_events(conn, date="2025-04-01", events=events)
            result = db.query_calendar_events(conn, "2025-04-01")
        assert len(result) == 1


class TestSyncLog:
    def test_insert_and_query_failed(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            db.insert_sync_log(conn, date="2025-04-01", step="fetch", status="failure", error_message="timeout")
            db.insert_sync_log(conn, date="2025-04-02", step="fetch", status="failure")
            db.insert_sync_log(conn, date="2025-04-03", step="fetch", status="success")
            failed = db.query_failed_sync_dates(conn, since_days=7, step="fetch")
        assert len(failed) == 2
        assert "2025-04-01" in failed
        assert "2025-04-02" in failed

    def test_query_failed_validates_since_days(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            with pytest.raises(ValueError):
                db.query_failed_sync_dates(conn, since_days=-1)
            with pytest.raises(ValueError):
                db.query_failed_sync_dates(conn, since_days="evil")


class TestUserProfile:
    def test_write_and_read(self, tmp_path, monkeypatch):
        import superhealth.user_profile as up
        monkeypatch.setattr(up, "PROFILE_DIR", tmp_path)
        monkeypatch.setattr(up, "PROFILE_PATH", tmp_path / "profile.md")
        up.write_profile({"height_cm": "175", "gender": "male"})
        result = up.read_profile()
        assert result["height_cm"] == "175"
        assert result["gender"] == "male"

    def test_read_missing(self, tmp_path, monkeypatch):
        import superhealth.user_profile as up
        monkeypatch.setattr(up, "PROFILE_PATH", tmp_path / "profile.md")
        assert up.read_profile() == {}

    def test_write_multiple_keys(self, tmp_path, monkeypatch):
        import superhealth.user_profile as up
        monkeypatch.setattr(up, "PROFILE_DIR", tmp_path)
        monkeypatch.setattr(up, "PROFILE_PATH", tmp_path / "profile.md")
        up.write_profile({"birthdate": "1985-06-15", "gender": "female", "height_cm": "162.0"})
        result = up.read_profile()
        assert result == {"birthdate": "1985-06-15", "gender": "female", "height_cm": "162.0"}


class TestGoalProgress:
    def test_insert_and_query(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            conn.execute(
                "INSERT INTO goals (name, priority, metric_key, direction, baseline_value, start_date) VALUES (?, ?, ?, ?, ?, ?)",
                ("测试目标", 1, "steps", "increase", 8000, "2025-01-01"),
            )
            goal_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.insert_goal_progress(
                conn, goal_id=goal_id, date="2025-04-01", current_value=120, progress_pct=80
            )
            rows = db.query_goal_progress_range(conn, goal_id=goal_id, start_date="2025-04-01", end_date="2025-04-01")
        assert len(rows) == 1
        assert rows[0]["current_value"] == 120


class TestQueryLabTrends:
    def test_unified_trends(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            doc_id = db.insert_medical_document(
                conn, doc_date="2025-03-01", doc_type="annual_checkup",
                markdown_path="data/checkup-reports/2025-03-01.md"
            )
            obs = [
                {"obs_date": "2025-01-01", "category": "lab", "item_name": "尿酸", "value_num": 400, "unit": "μmol/L"},
                {"obs_date": "2025-02-01", "category": "lab", "item_name": "尿酸", "value_num": 420, "unit": "μmol/L"},
                {"obs_date": "2025-03-01", "category": "lab", "item_name": "尿酸", "value_num": 430, "document_id": doc_id},
            ]
            db.bulk_insert_observations(conn, obs)
            results = db.query_lab_trends_unified(conn, "uric_acid")
        assert len(results) == 3
        sources = {r["source"] for r in results}
        assert "annual_checkup" in sources

    def test_multiple_metrics(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            obs = [
                {"obs_date": "2025-01-01", "category": "lab", "item_name": "尿酸", "value_num": 400},
                {"obs_date": "2025-01-01", "category": "lab", "item_name": "肌酐", "value_num": 80},
            ]
            db.bulk_insert_observations(conn, obs)
            multi = db.query_multiple_metrics(conn, ["uric_acid", "creatinine"])
        assert len(multi) == 2
        assert "uric_acid" in multi
        assert "creatinine" in multi

    def test_unknown_metric_raises(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            with pytest.raises(ValueError, match="未知的指标代码"):
                db.query_lab_trends_unified(conn, "unknown_metric")


class TestLoadDailyHealthFromDb:
    def test_roundtrip(self, tmp_db):
        dh = DailyHealth(
            date="2025-04-01",
            sleep=SleepData(total_seconds=28800, score=85),
            heart_rate=HeartRateData(resting=55),
        )
        with db.get_conn(tmp_db) as conn:
            db.upsert_daily_health(conn, dh)
            loaded = db.load_daily_health_from_db(conn, "2025-04-01")
        assert loaded is not None
        assert loaded.date == "2025-04-01"
        assert loaded.sleep.score == 85
        assert loaded.heart_rate.resting == 55

    def test_missing_returns_none(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            loaded = db.load_daily_health_from_db(conn, "2025-04-01")
        assert loaded is None
