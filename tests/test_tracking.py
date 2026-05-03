"""测试 tracking/medication_tracker 的 CRUD 和分析功能。"""
from unittest.mock import patch

import pytest

from superhealth import database as db
from superhealth.tracking.medication_tracker import MedicationTracker


@pytest.fixture
def tmp_db(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    return db_path


class TestAddMedication:
    def test_add_and_return_id(self, tmp_db):
        tracker = MedicationTracker(tmp_db)
        med_id = tracker.add_medication(
            name="测试药物", condition="glaucoma", start_date="2025-01-01",
            dosage="每日1滴", frequency="每日",
        )
        assert isinstance(med_id, int)
        assert med_id > 0

    def test_add_with_optional_fields(self, tmp_db):
        tracker = MedicationTracker(tmp_db)
        med_id = tracker.add_medication(
            name="测试药物2", condition="hyperuricemia", start_date="2025-01-01",
            dosage="每日1片", end_date="2025-06-01", note="饭后服用",
        )
        with db.get_conn(tmp_db) as conn:
            row = conn.execute("SELECT * FROM medications WHERE id = ?", (med_id,)).fetchone()
        assert row["end_date"] == "2025-06-01"
        assert row["note"] == "饭后服用"


class TestGetActiveMedications:
    def test_active_only(self, tmp_db):
        tracker = MedicationTracker(tmp_db)
        tracker.add_medication(name="活跃药", condition="test", start_date="2025-01-01", dosage="1片")
        tracker.add_medication(
            name="已停药", condition="test", start_date="2024-01-01",
            dosage="1片", end_date="2024-12-01",
        )
        active = tracker.get_active_medications()
        assert len(active) == 1
        assert active[0]["name"] == "活跃药"

    def test_empty(self, tmp_db):
        tracker = MedicationTracker(tmp_db)
        assert tracker.get_active_medications() == []


class TestGetMedicationsByCondition:
    def test_filter_by_condition(self, tmp_db):
        tracker = MedicationTracker(tmp_db)
        tracker.add_medication(name="眼药A", condition="glaucoma", start_date="2025-01-01", dosage="1滴")
        tracker.add_medication(name="尿酸药", condition="hyperuricemia", start_date="2025-01-01", dosage="1片")
        glaucoma_meds = tracker.get_medications_by_condition("glaucoma")
        assert len(glaucoma_meds) == 1
        assert glaucoma_meds[0]["name"] == "眼药A"


class TestLinkToObservation:
    def test_link_creates_record(self, tmp_db):
        tracker = MedicationTracker(tmp_db)
        with db.get_conn(tmp_db) as conn:
            db.insert_medication(conn, name="test", condition="test", start_date="2025-01-01", dosage="1")
            med_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.bulk_insert_observations(conn, [
                {"obs_date": "2025-01-15", "category": "lab", "item_name": "尿酸", "value_num": 400}
            ])
            obs_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        tracker.link_to_observation(med_id, obs_id, expected_effect="降低尿酸", is_effective=1)

        with db.get_conn(tmp_db) as conn:
            rows = conn.execute("SELECT * FROM medication_effects WHERE medication_id = ?", (med_id,)).fetchall()
        assert len(rows) == 1
        assert rows[0]["expected_effect"] == "降低尿酸"
        assert rows[0]["is_effective"] == 1

    def test_link_to_lab_result_compat(self, tmp_db):
        """link_to_lab_result 向后兼容别名。"""
        tracker = MedicationTracker(tmp_db)
        with db.get_conn(tmp_db) as conn:
            db.insert_medication(conn, name="test", condition="test", start_date="2025-01-01", dosage="1")
            med_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.bulk_insert_observations(conn, [
                {"obs_date": "2025-01-15", "category": "lab", "item_name": "尿酸", "value_num": 400}
            ])
            obs_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        tracker.link_to_lab_result(med_id, obs_id, expected_effect="降低尿酸")

        with db.get_conn(tmp_db) as conn:
            rows = conn.execute("SELECT * FROM medication_effects WHERE medication_id = ?", (med_id,)).fetchall()
        assert len(rows) == 1

    def test_link_to_eye_exam_compat(self, tmp_db):
        """link_to_eye_exam 向后兼容别名。"""
        tracker = MedicationTracker(tmp_db)
        with db.get_conn(tmp_db) as conn:
            db.insert_medication(conn, name="test", condition="glaucoma", start_date="2025-01-01", dosage="1")
            med_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.bulk_insert_observations(conn, [
                {"obs_date": "2025-01-15", "category": "eye", "item_name": "右眼眼压", "value_num": 15}
            ])
            obs_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        tracker.link_to_eye_exam(med_id, obs_id, expected_effect="控制眼压")

        with db.get_conn(tmp_db) as conn:
            rows = conn.execute("SELECT * FROM medication_effects WHERE medication_id = ?", (med_id,)).fetchall()
        assert len(rows) == 1


class TestAnalyzeMedicationEffect:
    def test_no_medication(self, tmp_db):
        tracker = MedicationTracker(tmp_db)
        result = tracker.analyze_medication_effect("不存在的药", "尿酸")
        assert "error" in result

    def test_before_after_comparison(self, tmp_db):
        tracker = MedicationTracker(tmp_db)
        tracker.add_medication(name="降尿酸药", condition="hyperuricemia", start_date="2025-02-01", dosage="1片")

        with db.get_conn(tmp_db) as conn:
            db.bulk_insert_observations(conn, [
                {"obs_date": "2025-01-01", "category": "lab", "item_name": "尿酸", "value_num": 500},
                {"obs_date": "2025-01-15", "category": "lab", "item_name": "尿酸", "value_num": 480},
                {"obs_date": "2025-02-10", "category": "lab", "item_name": "尿酸", "value_num": 420},
                {"obs_date": "2025-02-20", "category": "lab", "item_name": "尿酸", "value_num": 400},
            ])

        result = tracker.analyze_medication_effect("降尿酸药", "尿酸")
        assert result["medication"] == "降尿酸药"
        assert result["indicator"] == "尿酸"
        assert result["before"]["count"] == 2
        assert result["after"]["count"] == 2
        assert result["change"] < 0
        assert "change_pct" in result


class TestGetMedicationSummary:
    def test_summary(self, tmp_db):
        tracker = MedicationTracker(tmp_db)
        tracker.add_medication(name="眼药A", condition="glaucoma", start_date="2025-01-01", dosage="1滴")
        tracker.add_medication(name="眼药B", condition="glaucoma", start_date="2025-01-01", dosage="1滴")
        tracker.add_medication(name="尿酸药", condition="hyperuricemia", start_date="2025-01-01", dosage="1片")

        summary = tracker.get_medication_summary()
        assert summary["active_count"] == 3
        assert len(summary["by_condition"]) == 2
        conditions = {r["condition"] for r in summary["by_condition"]}
        assert conditions == {"glaucoma", "hyperuricemia"}
