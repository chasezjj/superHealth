"""测试 recommendation_feedback 的手动反馈写入。"""

import pytest

from superhealth import database as db
from superhealth.feedback.feedback_collector import submit_feedback


@pytest.fixture
def tmp_db(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    return db_path


def _feedback_rows(db_path):
    with db.get_conn(db_path) as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM recommendation_feedback ORDER BY id"
            ).fetchall()
        ]


def test_submit_feedback_updates_existing_record(tmp_db):
    with db.get_conn(tmp_db) as conn:
        db.insert_recommendation_feedback(
            conn,
            date="2026-05-04",
            report_id="2026-05-04-advanced-daily-report",
            recommendation_type="exercise",
            recommendation_content="跑步 30 分钟",
            compliance=None,
            actual_action=None,
            tracked_metrics=None,
        )

    assert submit_feedback(
        "2026-05-04",
        "强度合适，已完成。",
        rating=5,
        db_path=tmp_db,
    )

    rows = _feedback_rows(tmp_db)
    assert len(rows) == 1
    assert rows[0]["user_feedback"] == "强度合适，已完成。"
    assert rows[0]["user_rating"] == 5
    assert rows[0]["recommendation_content"] == "跑步 30 分钟"


def test_submit_feedback_creates_record_when_missing(tmp_db):
    assert submit_feedback(
        "2026-05-04",
        "今天不方便运动。",
        recommendation_type="non-exercise",
        rating=3,
        db_path=tmp_db,
    )

    rows = _feedback_rows(tmp_db)
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-05-04"
    assert rows[0]["report_id"] == "2026-05-04-advanced-daily-report"
    assert rows[0]["recommendation_type"] == "non-exercise"
    assert rows[0]["user_feedback"] == "今天不方便运动。"
    assert rows[0]["user_rating"] == 3


def test_submit_feedback_allows_no_rating(tmp_db):
    submit_feedback("2026-05-04", "只记录文字。", rating=None, db_path=tmp_db)

    rows = _feedback_rows(tmp_db)
    assert rows[0]["user_feedback"] == "只记录文字。"
    assert rows[0]["user_rating"] is None


@pytest.mark.parametrize("rating", [0, 6])
def test_submit_feedback_rejects_invalid_rating(tmp_db, rating):
    with pytest.raises(ValueError, match="rating must be between 1 and 5"):
        submit_feedback("2026-05-04", "bad", rating=rating, db_path=tmp_db)
