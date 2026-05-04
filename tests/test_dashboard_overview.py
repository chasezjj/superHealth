"""测试今日概览页的纯辅助逻辑。"""

import pandas as pd

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
