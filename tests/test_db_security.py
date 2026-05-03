"""Test kwargs whitelist validation in database CRUD functions."""
import pytest

from superhealth import database as db


@pytest.fixture
def tmp_db(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    return db_path


class TestKwargsWhitelist:
    def test_insert_medical_document_valid(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            doc_id = db.insert_medical_document(
                conn, doc_date="2025-01-01", doc_type="lab", markdown_path="data/lab/2025-01-01.md"
            )
            rows = conn.execute("SELECT * FROM medical_documents").fetchall()
        assert len(rows) == 1
        assert doc_id == 1

    def test_bulk_insert_observations_valid(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            obs = [{"obs_date": "2025-01-01", "category": "lab", "item_name": "尿酸", "value_num": 420.0}]
            db.bulk_insert_observations(conn, obs)
            rows = conn.execute("SELECT * FROM medical_observations").fetchall()
        assert len(rows) == 1
        assert rows[0]["value_num"] == 420.0

    def test_upsert_medical_condition_valid(self, tmp_db):
        with db.get_conn(tmp_db) as conn:
            db.upsert_medical_condition(conn, name="高尿酸血症", status="active")
            rows = conn.execute("SELECT * FROM medical_conditions").fetchall()
        assert len(rows) == 1
        assert rows[0]["name"] == "高尿酸血症"

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
