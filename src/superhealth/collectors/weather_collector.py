"""天气数据采集器：调用和风天气 API 获取当日天气，判断是否适合户外运动。

判定规则（以下任意一条 → outdoor_ok=False）：
- 降水：任何降水（小雨及以上）
- 风力：风力 ≥ 4 级
- AQI：中国标准 AQI ≥ 100（轻度污染及以上）

API：和风天气 QWeather（付费版，私有 API host）
    天气：/v7/weather/now（实时天气）
    空气质量：/airquality/v1/daily/{lat}/{lon}（每日 AQI，中国标准 cn-mee）

config.toml 配置示例：
    [weather]
    api_key = "your_qweather_key"
    city = "YourCity"
    location_id = "YOUR_LOCATION_ID"   # QWeather 城市ID
    api_host = "xxxx.re.qweatherapi.com"  # 私有 API host（付费账号专属域名）

命令行：
    python -m superhealth.collectors.weather_collector --date 2026-04-05
"""

from __future__ import annotations

import argparse
import gzip
import json as _json
import logging
import ssl
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.error import URLError
from urllib.request import urlopen

_SSL_CTX = ssl.create_default_context()
# 保留系统默认 SSL 验证，不全局禁用证书检查

from superhealth import database as db
from superhealth.config import get_db_path
from superhealth.config import load as load_config

log = logging.getLogger(__name__)

DB_PATH = get_db_path()


class SSLVerificationError(Exception):
    """SSL 证书验证失败，重试无用。"""


# 降水类型代码（和风天气 icon code 映射）
# 参考：https://dev.qweather.com/docs/resource/icons/
_RAIN_CODES = {
    "300",
    "301",
    "302",
    "303",
    "304",
    "305",
    "306",
    "307",
    "308",
    "309",
    "310",
    "311",
    "312",
    "313",
    "314",
    "315",
    "316",
    "317",
    "318",
    "399",  # 雨
    "400",
    "401",
    "402",
    "403",
    "404",
    "405",
    "406",
    "407",
    "408",
    "409",
    "410",
    "499",  # 雪
    "500",
    "501",
    "502",
    "503",
    "504",
    "507",
    "508",
    "509",
    "510",
    "511",
    "512",
    "513",
    "514",
    "515",  # 雾/霾/沙尘
}

# 包含降水的天气描述关键字（fallback 文字匹配）
_RAIN_KEYWORDS = ("雨", "雪", "雷", "冰雹", "冻")


@dataclass
class WeatherData:
    date: str
    condition: str  # 天气状况描述（白天预报，fallback 实时）
    temperature: Optional[float]  # °C（实时，仅作 fallback 显示）
    temp_max: Optional[float]  # 全天最高气温 °C（3日预报）
    temp_min: Optional[float]  # 全天最低气温 °C（3日预报）
    wind_scale: Optional[int]  # 风力级别 0-12（白天预报，fallback 实时）
    aqi: Optional[float]  # 中国标准 AQI（全天日预报，0-500）
    outdoor_ok: bool  # 综合判定

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "condition": self.condition,
            "temperature": self.temperature,
            "temp_max": self.temp_max,
            "temp_min": self.temp_min,
            "wind_scale": self.wind_scale,
            "aqi": self.aqi,
            "outdoor_ok": self.outdoor_ok,
        }


def _parse_wind_scale(value: Optional[str]) -> Optional[int]:
    """解析风力字符串，支持范围值如 '1-3'、'3-4'，取最大值（保守判定）。"""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    # 范围值：取最大值
    if "-" in value:
        parts = value.split("-")
        try:
            return max(int(p.strip()) for p in parts if p.strip())
        except (ValueError, TypeError):
            return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _wind_speed_to_scale(wind_speed_kmh: Optional[float]) -> Optional[int]:
    """根据风速(km/h)换算蒲福风级，作为 windScale 的交叉验证。"""
    if wind_speed_kmh is None:
        return None
    # 蒲福风级（km/h 边界）
    boundaries = [1, 6, 12, 20, 29, 39, 50, 62, 75, 89, 103, 118]
    for scale, boundary in enumerate(boundaries, start=0):
        if wind_speed_kmh < boundary:
            return scale
    return 12


def _is_outdoor_ok(
    condition: str,
    wind_scale: Optional[int],
    aqi: Optional[float],
    icon_code: str = "",
    wind_speed_scale: Optional[int] = None,
) -> bool:
    """综合判定是否适合户外运动。"""
    # 1. 降水判定（图标代码优先，文字匹配兜底）
    if icon_code and icon_code in _RAIN_CODES:
        return False
    if any(kw in condition for kw in _RAIN_KEYWORDS):
        return False
    # 2. 风力判定：windScale 与 windSpeed 换算值取更保守（更大）的
    effective_scale = wind_scale
    if wind_speed_scale is not None:
        if effective_scale is None:
            effective_scale = wind_speed_scale
        else:
            effective_scale = max(effective_scale, wind_speed_scale)
    if effective_scale is not None and effective_scale >= 4:
        return False
    # 3. PM2.5 判定（≥ 100 不适合户外）
    if aqi is not None and aqi >= 100:
        return False
    return True


def _fetch_json(url: str, timeout: int = 10) -> Optional[dict]:
    """发送 GET 请求，返回解析的 JSON dict，自动解压 gzip，失败返回 None。"""
    try:
        with urlopen(url, timeout=timeout, context=_SSL_CTX) as resp:
            raw = resp.read()
            # 自动解压 gzip（响应头 Content-Encoding: gzip 或魔数 1f 8b）
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            return _json.loads(raw.decode("utf-8"))  # type: ignore[no-any-return]
    except URLError as e:
        reason = str(e.reason) if hasattr(e, "reason") else str(e)
        if "CERTIFICATE_VERIFY_FAILED" in reason or "SSL" in reason:
            log.warning("天气 API SSL 证书验证失败，跳过请求: %s", url.split("?")[0])
            raise SSLVerificationError(f"SSL 证书验证失败: {reason}") from e
        log.warning("天气 API 请求失败: %s — %s", url.split("?")[0], e)
        return None
    except (_json.JSONDecodeError, Exception) as e:
        log.warning("天气 API 请求失败: %s — %s", url.split("?")[0], e)
        return None


def fetch_weather(target_date: str | None = None, db_path: Path = DB_PATH) -> Optional[WeatherData]:
    """采集天气数据并写入 DB，返回 WeatherData 或 None（API 不可用时）。

    target_date: YYYY-MM-DD，默认今天。
    注意：免费 API 只支持当天实时天气，历史天气需付费接口。
    对于非今天的日期，仅查询 DB 已有记录，不发起 API 请求。
    """
    today = date.today().isoformat()
    day_str = target_date or today

    cfg = load_config()
    weather_cfg = cfg.weather

    # 非今天日期：只从 DB 读，不请求 API
    if day_str != today:
        with db.get_conn(db_path) as conn:
            existing = db.query_weather(conn, day_str)
        if existing:
            return WeatherData(
                date=existing["date"],
                condition=existing["condition"],
                temperature=existing.get("temperature"),
                temp_max=existing.get("temp_max"),
                temp_min=existing.get("temp_min"),
                wind_scale=existing.get("wind_scale"),
                aqi=existing.get("aqi"),
                outdoor_ok=existing["outdoor_ok"],
            )
        log.info("天气采集：%s 非今天且 DB 无记录，跳过 API 请求", day_str)
        return None

    # 今天日期：先尝试从 DB 读取已有数据
    with db.get_conn(db_path) as conn:
        existing = db.query_weather(conn, day_str)
    if existing and existing.get("temp_max") is not None:
        # 已有包含全天预报的完整数据，直接返回
        return WeatherData(
            date=existing["date"],
            condition=existing["condition"],
            temperature=existing.get("temperature"),
            temp_max=existing.get("temp_max"),
            temp_min=existing.get("temp_min"),
            wind_scale=existing.get("wind_scale"),
            aqi=existing.get("aqi"),
            outdoor_ok=existing["outdoor_ok"],
        )

    # DB 无数据，尝试 API 采集
    if not weather_cfg.is_complete():
        log.warning("天气 API key 未配置（~/.superhealth/config.toml [weather] api_key），跳过天气采集")
        return None

    location_id = weather_cfg.location_id
    api_key = weather_cfg.api_key
    host = weather_cfg.api_host.strip() if weather_cfg.api_host else "devapi.qweather.com"
    base = f"https://{host}/v7"

    MAX_RETRIES = 3
    weather_data: Optional[WeatherData] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # 1. 实时天气（用于 fallback condition/temperature/outdoor_ok 判定）
            weather_url = f"{base}/weather/now?location={location_id}&key={api_key}"
            weather_resp = _fetch_json(weather_url)
            if not weather_resp or weather_resp.get("code") != "200":
                raise RuntimeError(f"天气 API 返回异常: {weather_resp}")

            now = weather_resp.get("now", {})
            condition = now.get("text", "未知")
            icon_code = now.get("icon", "")
            try:
                temperature = float(now.get("temp", 0))
            except (TypeError, ValueError):
                temperature = None
            wind_scale_now = _parse_wind_scale(now.get("windScale"))
            try:
                wind_speed_now = float(now.get("windSpeed", 0))
            except (TypeError, ValueError):
                wind_speed_now = None

            # 2. 3日预报（取 today 对应的白天预报，获取全天温度区间和白天风力）
            temp_max: Optional[float] = None
            temp_min: Optional[float] = None
            wind_scale: Optional[int] = wind_scale_now  # fallback 到实时
            wind_speed: Optional[float] = wind_speed_now
            forecast_url = f"{base}/weather/3d?location={location_id}&key={api_key}"
            forecast_resp = _fetch_json(forecast_url)
            if forecast_resp and forecast_resp.get("code") == "200":
                daily_list = forecast_resp.get("daily", [])
                # daily[0] 为今日预报（fxDate == today）
                today_fc = next(
                    (d for d in daily_list if d.get("fxDate") == today),
                    daily_list[0] if daily_list else None,
                )
                if today_fc:
                    # 白天天气描述覆盖实时（更代表全天）
                    fc_text = today_fc.get("textDay") or today_fc.get("textNight", "")
                    if fc_text:
                        condition = fc_text
                        icon_code = today_fc.get("iconDay", icon_code)
                    try:
                        temp_max = float(today_fc["tempMax"])
                    except (KeyError, TypeError, ValueError):
                        pass
                    try:
                        temp_min = float(today_fc["tempMin"])
                    except (KeyError, TypeError, ValueError):
                        pass
                    wind_scale = _parse_wind_scale(today_fc.get("windScaleDay"))
                    if wind_scale is None:
                        wind_scale = wind_scale_now
                    try:
                        wind_speed = float(today_fc.get("windSpeedDay", wind_speed_now or 0))
                    except (TypeError, ValueError):
                        wind_speed = wind_speed_now
            else:
                log.debug("3日预报 API 无响应，使用实时天气数据作为 fallback")
                wind_scale = wind_scale_now

            # 3. 每日空气质量（AQI）—— 接口：/airquality/v1/daily/{lat}/{lon}
            # 注意：该接口不提供 PM2.5 原始值，使用中国标准 AQI（cn-mee）作为替代
            # AQI < 100 = 优/良（适合户外），≥ 100 = 轻度污染及以上（不适合户外）
            aqi: Optional[float] = None  # 实际存储 AQI 值
            lat = f"{weather_cfg.latitude:.2f}"
            lon = f"{weather_cfg.longitude:.2f}"
            air_url = f"https://{host}/airquality/v1/daily/{lat}/{lon}?key={api_key}"
            air_resp = _fetch_json(air_url)
            if air_resp:
                # 响应结构：{"days": [{"indexes": [{"code": "cn-mee", "aqi": 88, ...}], ...}]}
                # days[0] 对应今日（UTC 时间段，北京时间当天）
                days_list = air_resp.get("days", [])
                if days_list:
                    indexes = days_list[0].get("indexes", [])
                    for idx in indexes:
                        if idx.get("code") == "cn-mee":
                            try:
                                aqi = float(idx.get("aqi", 0) or 0)
                            except (TypeError, ValueError):
                                pass
                            break
            if aqi is not None:
                log.debug(
                    "空气质量 AQI=%s category=%s",
                    aqi,
                    days_list[0]["indexes"][0].get("category", "")
                    if air_resp and days_list
                    else "",
                )
            else:
                log.debug("空气质量 API 无响应或解析失败，跳过 AQI")

            wind_speed_scale = _wind_speed_to_scale(wind_speed)
            outdoor_ok = _is_outdoor_ok(condition, wind_scale, aqi, icon_code, wind_speed_scale)

            weather_data = WeatherData(
                date=day_str,
                condition=condition,
                temperature=temperature,
                temp_max=temp_max,
                temp_min=temp_min,
                wind_scale=wind_scale,
                aqi=aqi,
                outdoor_ok=outdoor_ok,
            )
            break
        except SSLVerificationError:
            return None
        except Exception as e:
            if attempt == MAX_RETRIES:
                log.error("天气采集失败，已重试 %d 次: %s", MAX_RETRIES, e)
                return None
            sleep_sec = 2 ** (attempt - 1)
            log.warning("天气采集第 %d 次尝试失败: %s，%d 秒后重试...", attempt, e, sleep_sec)
            time.sleep(sleep_sec)

    # 写入 DB
    with db.get_conn(db_path) as conn:
        db.upsert_weather(
            conn,
            date=day_str,
            condition=condition,
            temperature=temperature,
            temp_max=temp_max,
            temp_min=temp_min,
            wind_scale=wind_scale,
            aqi=aqi,
            outdoor_ok=1 if outdoor_ok else 0,
        )

    temp_display = (
        f"{temp_min:.0f}~{temp_max:.0f}°C"
        if temp_max is not None and temp_min is not None
        else f"{temperature}°C"
    )
    log.info(
        "天气采集完成: %s %s %s 风力%s级 AQI=%s outdoor_ok=%s",
        day_str,
        condition,
        temp_display,
        wind_scale,
        aqi,
        outdoor_ok,
    )
    return weather_data


def test_connection() -> tuple[bool, str]:
    """测试和风天气 API 连通性。返回 (ok, message)。"""
    cfg = load_config().weather
    if not cfg.is_complete():
        return False, "天气配置不完整，请填写 API Key 和 Location ID"

    api_key = cfg.api_key
    location_id = cfg.location_id
    host = cfg.api_host.strip() if cfg.api_host else "devapi.qweather.com"
    url = f"https://{host}/v7/weather/now?location={location_id}&key={api_key}"

    resp = _fetch_json(url, timeout=10)
    if not resp:
        return False, "天气 API 请求失败，请检查网络或 API Host 配置"
    if resp.get("code") != "200":
        return False, f"天气 API 返回异常: code={resp.get('code')}, {resp.get('message', '未知错误')}"

    now = resp.get("now", {})
    city = now.get("text", "未知")
    return True, f"天气 API 连接成功（当前天气: {city}）"


def main():
    from superhealth.log_config import setup_logging

    setup_logging()
    ap = argparse.ArgumentParser(description="采集当日天气数据")
    ap.add_argument("--date", type=str, help="目标日期 YYYY-MM-DD，默认今天")
    args = ap.parse_args()

    result = fetch_weather(target_date=args.date)
    if result:
        print(f"日期: {result.date}")
        print(f"天气: {result.condition}  {result.temperature}°C  风力{result.wind_scale}级")
        print(f"AQI（中国标准）: {result.aqi:.0f}" if result.aqi is not None else "AQI: N/A")
        print(f"适合户外运动: {'是' if result.outdoor_ok else '否'}")
    else:
        print("天气数据获取失败（API 未配置或请求失败）")


if __name__ == "__main__":
    main()
