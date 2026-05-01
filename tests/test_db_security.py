"""Test kwargs whitelist validation in database CRUD functions."""
import pytest

from superhealth import database as db


@pytest.fixture
def tmp_db(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    return db_path


class TestKwargsWhitelist:
    def test_insert_eye_exam_valid(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            db.insert_eye_exam(conn, date="2025-01-01", od_iop=15.0, os_iop=16.0)
            rows = conn.execute("SELECT * FROM eye_exams").fetchall()
        assert len(rows) == 1
        assert rows[0]["od_iop"] == 15.0

    def test_insert_eye_exam_invalid_col(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            with pytest.raises(ValueError, match="unknown column"):
                db.insert_eye_exam(conn, date="2025-01-01", evil_column="drop table")

    def test_insert_kidney_ultrasound_valid(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            db.insert_kidney_ultrasound(conn, date="2025-01-01", right_length_cm=10.5)
            rows = conn.execute("SELECT * FROM kidney_ultrasounds").fetchall()
        assert len(rows) == 1

    def test_insert_kidney_ultrasound_invalid(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            with pytest.raises(ValueError, match="unknown column"):
                db.insert_kidney_ultrasound(conn, date="2025-01-01", hack="evil")

    def test_upsert_annual_checkup_valid(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            db.upsert_annual_checkup(conn, checkup_date="2025-01-01", bmi=22.5, uric_acid=380)
            rows = conn.execute("SELECT * FROM annual_checkups").fetchall()
        assert len(rows) == 1
        assert rows[0]["bmi"] == 22.5

    def test_upsert_annual_checkup_invalid(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            with pytest.raises(ValueError, match="unknown column"):
                db.upsert_annual_checkup(conn, checkup_date="2025-01-01", __secret="evil")

    def test_insert_medication_valid(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            db.insert_medication(conn, name="test", condition="test", start_date="2025-01-01")
            rows = conn.execute("SELECT * FROM medications").fetchall()
        assert len(rows) == 1

    def test_insert_medication_invalid(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            with pytest.raises(ValueError, match="unknown column"):
                db.insert_medication(conn, name="test", injection="evil")

    def test_insert_medication_effect_valid(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            # Create a medication first (FK constraint)
            db.insert_medication(conn, name="test_med", condition="test", start_date="2025-01-01")
            db.insert_medication_effect(conn, medication_id=1, expected_effect="test")
            rows = conn.execute("SELECT * FROM medication_effects").fetchall()
        assert len(rows) == 1

    def test_insert_medication_effect_invalid(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            with pytest.raises(ValueError, match="unknown column"):
                db.insert_medication_effect(conn, medication_id=1, exploit="evil")

    def test_query_failed_sync_dates_validates_int(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            with pytest.raises(ValueError):
                db.query_failed_sync_dates(conn, since_days=-1)
            with pytest.raises(ValueError):
                db.query_failed_sync_dates(conn, since_days="evil")  # type: ignore
