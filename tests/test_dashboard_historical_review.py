"""测试历史回顾页的纯辅助逻辑。"""

import math

from superhealth.dashboard.views.historical_review import _goal_delta_color, _rating_stars


def test_rating_stars_accepts_float_rating_from_pandas():
    assert _rating_stars(5.0) == "⭐⭐⭐⭐⭐"


def test_rating_stars_ignores_empty_rating():
    assert _rating_stars(None) == ""
    assert _rating_stars(math.nan) == ""


def test_rating_stars_ignores_out_of_range_rating():
    assert _rating_stars(0) == ""
    assert _rating_stars(6) == ""


def test_goal_delta_color_follows_goal_direction():
    assert _goal_delta_color("decrease") == "inverse"
    assert _goal_delta_color("increase") == "normal"
    assert _goal_delta_color("stabilize") == "off"
