"""测试今日概览页的纯辅助逻辑。"""

import pandas as pd

from superhealth import database as db
from superhealth.dashboard.views.historical_review import _estimate_goal_progress_today
from superhealth.dashboard.views.overview import _latest_feedback_for_today


def test_latest_feedback_for_today_returns_none_for_empty_frame():
    df = pd.DataFrame()

    assert _latest_feedback_for_today(df) is None


def test_latest_feedback_for_today_returns_first_row():
    df = pd.DataFrame(
        [
            {"date": "2026-05-04", "user_feedback": "最新", "user_rating": 5},
            {"date": "2026-05-04", "user_feedback": "较旧", "user_rating": 3},
        ]
    )

    row = _latest_feedback_for_today(df)

    assert row["user_feedback"] == "最新"
    assert row["user_rating"] == 5


def test_estimate_goal_progress_today_uses_current_diastolic_data(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    with db.get_conn(db_path) as conn:
        db.insert_vital(conn, measured_at="2025-04-05 08:00:00", diastolic=80)
        db.insert_vital(conn, measured_at="2025-04-06 08:00:00", diastolic=78)
        db.insert_vital(conn, measured_at="2025-04-07 08:00:00", diastolic=76)

    goal = {
        "metric_key": "bp_diastolic_mean_7d",
        "direction": "decrease",
        "baseline_value": 82.0,
        "target_value": 72.0,
    }

    estimate = _estimate_goal_progress_today(goal, ref_date="2025-04-07", db_path=db_path)

    assert estimate is not None
    assert estimate["current_value"] == 78.0
    assert estimate["progress_pct"] == 40.0
    assert estimate["is_estimate"] is True


def test_estimate_goal_progress_today_returns_none_without_data(tmp_path):
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    goal = {
        "metric_key": "bp_diastolic_mean_7d",
        "direction": "decrease",
        "baseline_value": 82.0,
        "target_value": 72.0,
    }

    assert _estimate_goal_progress_today(goal, ref_date="2025-04-07", db_path=db_path) is None
