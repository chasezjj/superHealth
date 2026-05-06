"""测试天气采集器的纯函数逻辑。"""

import ssl
from unittest.mock import patch

from superhealth.collectors.weather_collector import (
    _build_ssl_context,
    _error_message_from_resp,
    _is_outdoor_ok,
    _parse_wind_scale,
    _resolve_weather_location,
    _weather_headers,
    _weather_host,
    _wind_speed_to_scale,
    test_connection as weather_test_connection,
)
from superhealth.config import AppConfig, WeatherConfig


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


class TestWeatherConnection:
    def test_build_ssl_context_skips_verification(self):
        fake_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with patch("ssl._create_unverified_context", return_value=fake_ctx) as mock_create:
            ctx = _build_ssl_context()

        assert ctx is fake_ctx
        assert ctx.check_hostname is False
        mock_create.assert_called_once_with()

    def test_weather_headers_use_api_key_header(self):
        headers = _weather_headers("token-123")
        assert headers["X-QW-Api-Key"] == "token-123"
        assert headers["Accept-Encoding"] == "gzip"

    def test_weather_host_is_stripped(self):
        assert _weather_host(" abc.qweatherapi.com ") == "abc.qweatherapi.com"

    def test_error_message_from_v2_response(self):
        resp = {
            "error": {
                "status": 403,
                "title": "Invalid Host",
                "detail": "Request denied due to invalid API Host.",
            }
        }
        msg = _error_message_from_resp(resp)
        assert msg == "HTTP 403 - Invalid Host - Request denied due to invalid API Host."

    def test_resolve_weather_location(self):
        fake_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with patch(
            "superhealth.collectors.weather_collector._fetch_json",
            return_value={
                "code": "200",
                "location": [
                    {"id": "101010100", "name": "北京", "lat": "39.90", "lon": "116.40"}
                ],
            },
        ):
            loc = _resolve_weather_location("北京", "abc.qweatherapi.com", "k", fake_ctx)

        assert loc.location_id == "101010100"
        assert loc.city_name == "北京"
        assert loc.latitude == 39.90
        assert loc.longitude == 116.40

    def test_test_connection_returns_detailed_error(self):
        fake_config = AppConfig(
            weather=WeatherConfig(
                api_key="k",
                city="北京",
                api_host="abc.qweatherapi.com",
            )
        )
        with (
            patch("superhealth.collectors.weather_collector.load_config", return_value=fake_config),
            patch(
                "superhealth.collectors.weather_collector._resolve_weather_location",
                return_value=type(
                    "Loc",
                    (),
                    {
                        "location_id": "101010100",
                        "city_name": "北京",
                        "latitude": 39.90,
                        "longitude": 116.40,
                    },
                )(),
            ),
            patch(
                "superhealth.collectors.weather_collector._fetch_json",
                return_value={
                    "error": {
                        "status": 403,
                        "title": "Invalid Host",
                        "detail": "Request denied due to invalid API Host.",
                    }
                },
            ),
        ):
            ok, msg = weather_test_connection()

        assert ok is False
        assert "Invalid Host" in msg
