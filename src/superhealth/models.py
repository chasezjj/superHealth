"""Pydantic 数据模型：Garmin 健康数据的结构化表示。

所有字段均为 Optional，因为用户不一定每天佩戴手表，
API 返回空数据是正常情况，不应视为错误。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from pydantic import BaseModel, Field


class SleepData(BaseModel):
    total_seconds: Optional[int] = None
    deep_seconds: Optional[int] = None
    light_seconds: Optional[int] = None
    rem_seconds: Optional[int] = None
    awake_seconds: Optional[int] = None
    score: Optional[float] = None

    @property
    def total_minutes(self) -> Optional[int]:
        return self.total_seconds // 60 if self.total_seconds is not None else None

    @property
    def has_data(self) -> bool:
        return self.total_seconds is not None and self.total_seconds > 0


class StressData(BaseModel):
    average: Optional[float] = None
    max: Optional[float] = None
    rest_seconds: Optional[int] = None
    low_seconds: Optional[int] = None
    medium_seconds: Optional[int] = None
    high_seconds: Optional[int] = None


class HeartRateData(BaseModel):
    resting: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    avg7_resting: Optional[float] = None


class BodyBatteryData(BaseModel):
    highest: Optional[float] = None
    lowest: Optional[float] = None
    charged: Optional[float] = None
    drained: Optional[float] = None
    at_wake: Optional[float] = None


class SpO2Data(BaseModel):
    average: Optional[float] = None
    lowest: Optional[float] = None
    latest: Optional[float] = None


class RespirationData(BaseModel):
    waking_avg: Optional[float] = None
    highest: Optional[float] = None
    lowest: Optional[float] = None


class ActivityData(BaseModel):
    steps: Optional[int] = None
    distance_meters: Optional[float] = None
    active_calories: Optional[float] = None
    floors_ascended: Optional[float] = None

    @property
    def distance_km(self) -> Optional[float]:
        if self.distance_meters is not None:
            return round(self.distance_meters / 1000, 1)
        return None


class HRVData(BaseModel):
    last_night_avg: Optional[float] = None
    last_night_5min_high: Optional[float] = None
    weekly_avg: Optional[float] = None
    baseline_low: Optional[float] = None
    baseline_high: Optional[float] = None
    status: Optional[str] = None  # BALANCED / UNBALANCED / LOW


class Exercise(BaseModel):
    name: str = "未知活动"
    type_key: Optional[str] = None
    start_time: Optional[str] = None  # "HH:MM" 本地时间
    distance_meters: Optional[float] = None
    duration_seconds: Optional[float] = None
    avg_hr: Optional[float] = None
    max_hr: Optional[float] = None
    avg_speed: Optional[float] = None
    calories: Optional[float] = None
    details: Optional[str] = None  # 具体动作明细，如 "俯卧撑3组×15, 深蹲3组×20"

    @property
    def distance_km(self) -> Optional[float]:
        if self.distance_meters and self.distance_meters > 0:
            return round(self.distance_meters / 1000, 2)
        return None

    @property
    def pace_str(self) -> Optional[str]:
        """配速（仅跑步/步行类活动）。"""
        if (self.avg_speed and self.avg_speed > 0
                and self.type_key in ("running", "trail_running", "walking")):
            pace_sec = 1000 / self.avg_speed
            pace_min = int(pace_sec // 60)
            pace_s = int(pace_sec % 60)
            return f"{pace_min}:{pace_s:02d}/km"
        return None


class DailyHealth(BaseModel):
    """一天的完整健康数据。所有子模块均可为空（未佩戴手表）。"""
    date: str  # YYYY-MM-DD
    sleep: SleepData = Field(default_factory=SleepData)
    stress: StressData = Field(default_factory=StressData)
    heart_rate: HeartRateData = Field(default_factory=HeartRateData)
    body_battery: BodyBatteryData = Field(default_factory=BodyBatteryData)
    spo2: SpO2Data = Field(default_factory=SpO2Data)
    respiration: RespirationData = Field(default_factory=RespirationData)
    activity: ActivityData = Field(default_factory=ActivityData)
    hrv: HRVData = Field(default_factory=HRVData)
    exercises: list[Exercise] = Field(default_factory=list)

    @property
    def has_data(self) -> bool:
        """判断当天是否有有效数据（至少有心率或睡眠）。"""
        return (self.sleep.has_data
                or self.heart_rate.resting is not None
                or self.body_battery.at_wake is not None)

    def to_flat_dict(self) -> dict[str, Any]:
        """转为扁平字典，兼容 analyze_garmin.py 的 score_state 等函数。"""
        return {
            'date': self.date,
            'sleep_total_min': self.sleep.total_minutes,
            'sleep_score': self.sleep.score,
            'avg_stress': self.stress.average,
            'max_stress': self.stress.max,
            'resting_hr': self.heart_rate.resting,
            'min_hr': self.heart_rate.min,
            'max_hr': self.heart_rate.max,
            'avg7_resting_hr': self.heart_rate.avg7_resting,
            'body_battery_highest': self.body_battery.highest,
            'body_battery_lowest': self.body_battery.lowest,
            'body_battery_wake': self.body_battery.at_wake,
            'spo2_avg': self.spo2.average,
            'spo2_lowest': self.spo2.lowest,
            'spo2_latest': self.spo2.latest,
            'resp_waking': self.respiration.waking_avg,
            'steps': self.activity.steps,
            'distance_km': self.activity.distance_km,
            'hrv_avg': self.hrv.last_night_avg,
            'hrv_weekly': self.hrv.weekly_avg,
            'hrv_baseline_low': self.hrv.baseline_low,
            'hrv_baseline_high': self.hrv.baseline_high,
            'hrv_status': self.hrv.status,
        }
