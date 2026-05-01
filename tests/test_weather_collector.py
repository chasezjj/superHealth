"""测试天气采集器的纯函数逻辑。"""
import pytest

from superhealth.collectors.weather_collector import (
    _is_outdoor_ok,
    _parse_wind_scale,
    _wind_speed_to_scale,
)


class TestParseWindScale:
    def test_single_value(self):
        assert _parse_wind_scale("3") == 3

    def test_range_value(self):
        assert _parse_wind_scale("1-3") == 3
        assert _parse_wind_scale("3-5") == 5

    def test_none(self):
        assert _parse_wind_scale(None) is None

    def test_empty(self):
        assert _parse_wind_scale("") is None
        assert _parse_wind_scale("   ") is None

    def test_invalid(self):
        assert _parse_wind_scale("abc") is None


class TestWindSpeedToScale:
    def test_calm(self):
        assert _wind_speed_to_scale(0) == 0

    def test_light_breeze(self):
        assert _wind_speed_to_scale(10) == 2

    def test_strong_wind(self):
        assert _wind_speed_to_scale(49) == 6
        assert _wind_speed_to_scale(50) == 7

    def test_hurricane(self):
        assert _wind_speed_to_scale(150) == 12

    def test_none(self):
        assert _wind_speed_to_scale(None) is None


class TestIsOutdoorOk:
    def test_sunny_good_conditions(self):
        assert _is_outdoor_ok("晴", 2, 45) is True

    def test_rain_condition(self):
        assert _is_outdoor_ok("小雨", 2, 45) is False

    def test_rain_icon_code(self):
        assert _is_outdoor_ok("多云", 2, 45, icon_code="305") is False

    def test_high_wind(self):
        assert _is_outdoor_ok("晴", 5, 45) is False

    def test_high_aqi(self):
        assert _is_outdoor_ok("晴", 2, 120) is False

    def test_wind_speed_fallback(self):
        # wind_scale=2 但 wind_speed_scale=5，取更保守的
        assert _is_outdoor_ok("晴", 2, 45, wind_speed_scale=5) is False

    def test_snow_condition(self):
        assert _is_outdoor_ok("中雪", 2, 45) is False
