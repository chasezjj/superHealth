"""测试 reminders 子系统。"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from superhealth import database as db
from superhealth.reminders.appointment_scheduler import _query_last_exam_date, refresh_appointments
from superhealth.reminders.reminder_config import REMINDER_RULES, ReminderRule


@pytest.fixture
def tmp_db(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    return db_path


class TestReminderRule:
    def test_glaucoma_rule(self):
        rule = next(r for r in REMINDER_RULES if r.condition == "glaucoma")
        assert rule.interval_months == 3
        assert rule.source_table == "eye_exams"

    def test_hyperuricemia_rule(self):
        rule = next(r for r in REMINDER_RULES if r.condition == "hyperuricemia")
        assert rule.interval_months == 6
        assert rule.item_filter == {"item_name": "尿酸"}


class TestQueryLastExamDate:
    def test_with_filter(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            db.insert_lab_result(conn, date="2025-01-01", source="hospital", item_name="尿酸", value=400)
            db.insert_lab_result(conn, date="2025-02-01", source="hospital", item_name="尿酸", value=420)
            rule = ReminderRule(
                condition="hyperuricemia", label="高尿酸", hospital=None, department=None,
                interval_months=6, source_table="lab_results", date_field="date",
                item_filter={"item_name": "尿酸"},
            )
            last_date, exam_id = _query_last_exam_date(conn, rule)
        assert last_date == "2025-02-01"

    def test_without_filter(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            db.insert_eye_exam(conn, date="2025-03-01", od_iop=15, os_iop=16)
            rule = ReminderRule(
                condition="glaucoma", label="青光眼", hospital=None, department=None,
                interval_months=3, source_table="eye_exams", date_field="date",
            )
            last_date, exam_id = _query_last_exam_date(conn, rule)
        assert last_date == "2025-03-01"

    def test_no_records(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            rule = ReminderRule(
                condition="glaucoma", label="青光眼", hospital=None, department=None,
                interval_months=3, source_table="eye_exams", date_field="date",
            )
            last_date, exam_id = _query_last_exam_date(conn, rule)
        assert last_date is None
        assert exam_id is None


class TestRefreshAppointments:
    def test_dry_run(self, tmp_db, capsys):
        with db.get_conn(tmp_db) as conn:
            db.insert_eye_exam(conn, date="2025-01-01", od_iop=15, os_iop=16)

        with db.get_conn(tmp_db) as conn:
            rule = ReminderRule(
                condition="glaucoma", label="青光眼", hospital=None, department=None,
                interval_months=3, source_table="eye_exams", date_field="date",
            )
            last_date, _ = _query_last_exam_date(conn, rule)
        assert last_date is not None
        due = date.fromisoformat(last_date)
        from dateutil.relativedelta import relativedelta
        expected_due = (due + relativedelta(months=3)).isoformat()

        with patch(
            "superhealth.reminders.appointment_scheduler.get_conn",
            side_effect=lambda: db.get_conn(tmp_db),
        ):
            results = refresh_appointments(dry_run=True)

        # Should have at least glaucoma result
        glaucoma = next((r for r in results if r["condition"] == "glaucoma"), None)
        assert glaucoma is not None
        assert glaucoma["due_date"] == expected_due
