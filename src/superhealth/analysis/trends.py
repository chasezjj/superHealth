"""趋势分析模块：长期健康数据的趋势计算与异常检测。

核心功能：
1. 滚动均值计算（7/30/90天）
2. 个人基线计算（30天均值±标准差）
3. 异常检测（偏离基线2σ报警）
4. 同比/环比分析

使用SQL窗口函数实现高效计算，适用于SQLite。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from superhealth import database as db

log = logging.getLogger(__name__)
DB_PATH = Path(__file__).parent.parent.parent.parent / "health.db"


@dataclass
class TrendResult:
    """趋势分析结果。"""

    metric: str
    current_value: Optional[float]
    avg_7d: Optional[float]
    avg_30d: Optional[float]
    avg_90d: Optional[float]
    baseline_mean: Optional[float]
    baseline_std: Optional[float]
    z_score: Optional[float]  # 偏离基线的标准差数
    is_anomaly: bool  # 是否异常（|z_score| > 2）
    trend_direction: str  # "up", "down", "stable"


class TrendAnalyzer:
    """趋势分析器。"""

    # 指标名称 → daily_health SQL 字段映射
    FIELD_MAP = {
        "sleep_score": "sleep_score",
        "resting_hr": "hr_resting",
        "hrv_avg": "hrv_last_night_avg",
        "avg_stress": "stress_average",
        "body_battery_wake": "bb_at_wake",
        "steps": "steps",
    }

    # 各指标的正常波动阈值（用于趋势方向判断）
    THRESHOLDS = {
        "sleep_score": 5,
        "resting_hr": 3,
        "hrv_avg": 5,
        "avg_stress": 5,
        "body_battery_wake": 10,
        "steps": 1000,
        "systolic": 5,
        "diastolic": 5,
        "weight_kg": 0.5,
        "body_fat_pct": 0.5,
    }

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        db.init_db(db_path)

    def _get_conn(self):
        return db.get_conn(self.db_path)

    def calculate_rolling_averages(
        self,
        metric: str,
        days: int = 90,
        end_date: Optional[str] = None,
    ) -> list[dict]:
        """计算滚动均值。

        Args:
            metric: 指标名称（如 "sleep_score", "resting_hr"）
            days: 查询天数
            end_date: 结束日期（默认今天）

        Returns:
            每日数据及滚动均值列表
        """
        end = end_date or date.today().isoformat()
        start = (datetime.fromisoformat(end) - timedelta(days=days)).isoformat()[:10]

        field = self.FIELD_MAP.get(metric)
        if not field:
            raise ValueError(f"未知指标: {metric}")

        with self._get_conn() as conn:
            rows = conn.execute(
                f"""SELECT
                        date,
                        {field} as value,
                        AVG({field}) OVER (
                            ORDER BY date
                            ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
                        ) as avg_7d,
                        AVG({field}) OVER (
                            ORDER BY date
                            ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
                        ) as avg_30d,
                        AVG({field}) OVER (
                            ORDER BY date
                            ROWS BETWEEN 89 PRECEDING AND CURRENT ROW
                        ) as avg_90d
                    FROM daily_health
                    WHERE date BETWEEN ? AND ?
                      AND {field} IS NOT NULL
                    ORDER BY date""",
                (start, end),
            ).fetchall()

        return [dict(r) for r in rows]

    def calculate_personal_baseline(
        self,
        metric: str,
        days: int = 90,
        end_date: Optional[str] = None,
    ) -> dict:
        """计算个人基线（均值±标准差）。

        Returns:
            {"mean": float, "std": float, "n": int, "min": float, "max": float}
        """
        end = end_date or date.today().isoformat()
        start = (datetime.fromisoformat(end) - timedelta(days=days)).isoformat()[:10]

        field_map = {
            "sleep_score": "sleep_score",
            "resting_hr": "hr_resting",
            "hrv_avg": "hrv_last_night_avg",
            "avg_stress": "stress_average",
            "body_battery_wake": "bb_at_wake",
            "steps": "steps",
        }

        field = field_map.get(metric)
        if not field:
            raise ValueError(f"未知指标: {metric}")

        with self._get_conn() as conn:
            # SQLite 没有内置 STDDEV，手动计算
            rows = conn.execute(
                f"""SELECT {field} as value
                    FROM daily_health
                    WHERE date BETWEEN ? AND ?
                      AND {field} IS NOT NULL""",
                (start, end),
            ).fetchall()

            values = [r["value"] for r in rows]
            if not values:
                return {"mean": None, "std": None, "n": 0, "min": None, "max": None}

            n = len(values)
            mean_val = sum(values) / n
            variance = sum((v - mean_val) ** 2 for v in values) / n
            std_val = variance**0.5

            return {
                "mean": mean_val,
                "std": std_val,
                "n": n,
                "min": min(values),
                "max": max(values),
            }

    def detect_anomalies(
        self,
        metric: str,
        z_threshold: float = 2.0,
        baseline_days: int = 90,
    ) -> list[dict]:
        """检测异常值（偏离基线超过z_threshold个标准差）。

        Returns:
            异常日期列表，包含实际值、预期值、z_score
        """
        # 先计算基线
        baseline = self.calculate_personal_baseline(metric, baseline_days)
        mean = baseline.get("mean")
        std = baseline.get("std")

        if not mean or not std or std == 0:
            return []

        # 查询近期数据
        field_map = {
            "sleep_score": "sleep_score",
            "resting_hr": "hr_resting",
            "hrv_avg": "hrv_last_night_avg",
            "avg_stress": "stress_average",
            "body_battery_wake": "bb_at_wake",
            "steps": "steps",
        }
        field = field_map.get(metric)

        with self._get_conn() as conn:
            rows = conn.execute(
                f"""SELECT date, {field} as value
                    FROM daily_health
                    WHERE date >= date('now', '-{baseline_days} days')
                      AND {field} IS NOT NULL""",
            ).fetchall()

        anomalies = []
        for row in rows:
            value = row["value"]
            z_score = (value - mean) / std
            if abs(z_score) > z_threshold:
                anomalies.append(
                    {
                        "date": row["date"],
                        "value": value,
                        "expected": round(mean, 2),
                        "z_score": round(z_score, 2),
                        "direction": "high" if z_score > 0 else "low",
                    }
                )

        return anomalies

    def analyze_trend(
        self,
        metric: str,
        days: int = 90,
        end_date: Optional[str] = None,
    ) -> TrendResult:
        """综合分析指标趋势。

        Args:
            end_date: 报告截止日期。传入后 current_value 取该日及之前最新值，
                      滚动均值和基线也以此日期为终点，避免周报"当前"值超前。

        返回完整的趋势分析结果。
        """
        # 获取最新值（限制在 end_date 及之前）
        field_map = {
            "sleep_score": "sleep_score",
            "resting_hr": "hr_resting",
            "hrv_avg": "hrv_last_night_avg",
            "avg_stress": "stress_average",
            "body_battery_wake": "bb_at_wake",
            "steps": "steps",
        }
        field = field_map.get(metric)

        with self._get_conn() as conn:
            if end_date:
                row = conn.execute(
                    f"""SELECT {field} as value, date
                        FROM daily_health
                        WHERE {field} IS NOT NULL
                          AND date <= ?
                        ORDER BY date DESC LIMIT 1""",
                    (end_date,),
                ).fetchone()
            else:
                row = conn.execute(
                    f"""SELECT {field} as value, date
                        FROM daily_health
                        WHERE {field} IS NOT NULL
                        ORDER BY date DESC LIMIT 1"""
                ).fetchone()

        current_value = row["value"] if row else None

        # 计算滚动均值
        rolling = self.calculate_rolling_averages(metric, days, end_date=end_date)
        latest = rolling[-1] if rolling else {}

        # 计算基线
        baseline = self.calculate_personal_baseline(metric, 90, end_date=end_date)
        mean = baseline.get("mean")
        std = baseline.get("std")

        # 计算z_score
        z_score = None
        if current_value is not None and mean is not None and std:
            z_score = (current_value - mean) / std

        # 判断趋势方向
        trend_direction = "stable"
        if rolling and len(rolling) >= 7:
            recent = [r["value"] for r in rolling[-7:] if r["value"] is not None]
            if len(recent) >= 3:
                first_half = sum(recent[: len(recent) // 2]) / (len(recent) // 2)
                second_half = sum(recent[len(recent) // 2 :]) / (len(recent) - len(recent) // 2)
                threshold = self.THRESHOLDS.get(metric, 0)
                if second_half - first_half > threshold:
                    trend_direction = "up"
                elif first_half - second_half > threshold:
                    trend_direction = "down"

        return TrendResult(
            metric=metric,
            current_value=current_value,
            avg_7d=latest.get("avg_7d"),
            avg_30d=latest.get("avg_30d"),
            avg_90d=latest.get("avg_90d"),
            baseline_mean=mean,
            baseline_std=std,
            z_score=round(z_score, 2) if z_score is not None else None,
            is_anomaly=abs(z_score) > 2 if z_score is not None else False,
            trend_direction=trend_direction,
        )

    def compare_periods(
        self,
        metric: str,
        period1_start: str,
        period1_end: str,
        period2_start: str,
        period2_end: str,
    ) -> dict:
        """对比两个时期的指标变化。

        用于同比分析（如今年6月 vs 去年6月）。
        """
        field_map = {
            "sleep_score": "sleep_score",
            "resting_hr": "hr_resting",
            "hrv_avg": "hrv_last_night_avg",
            "avg_stress": "stress_average",
            "body_battery_wake": "bb_at_wake",
            "steps": "steps",
        }
        field = field_map.get(metric)

        with self._get_conn() as conn:
            p1 = conn.execute(
                f"""SELECT AVG({field}) as mean, COUNT(*) as n
                    FROM daily_health
                    WHERE date BETWEEN ? AND ?
                      AND {field} IS NOT NULL""",
                (period1_start, period1_end),
            ).fetchone()

            p2 = conn.execute(
                f"""SELECT AVG({field}) as mean, COUNT(*) as n
                    FROM daily_health
                    WHERE date BETWEEN ? AND ?
                      AND {field} IS NOT NULL""",
                (period2_start, period2_end),
            ).fetchone()

        m1 = p1["mean"] if p1 else None
        m2 = p2["mean"] if p2 else None

        result = {
            "metric": metric,
            "period1": {
                "start": period1_start,
                "end": period1_end,
                "mean": m1,
                "n": p1["n"] if p1 else 0,
            },
            "period2": {
                "start": period2_start,
                "end": period2_end,
                "mean": m2,
                "n": p2["n"] if p2 else 0,
            },
        }

        if m1 and m2:
            result["change"] = round(m2 - m1, 2)
            result["change_pct"] = round((m2 - m1) / m1 * 100, 1)

        return result


def generate_trend_report(days: int = 30) -> str:
    """生成趋势分析报告（Markdown格式）。"""
    analyzer = TrendAnalyzer()

    lines = [f"# 健康趋势报告（最近{days}天）", ""]
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    # 核心指标趋势
    core_metrics = [
        ("sleep_score", "睡眠评分"),
        ("resting_hr", "静息心率"),
        ("hrv_avg", "HRV"),
        ("avg_stress", "平均压力"),
        ("body_battery_wake", "起床Body Battery"),
        ("steps", "步数"),
    ]

    lines.append("## 核心指标趋势")
    lines.append("")

    for metric, name in core_metrics:
        try:
            result = analyzer.analyze_trend(metric, days)
            lines.append(f"### {name}")
            lines.append(f"- 当前值: {result.current_value}")
            lines.append(f"- 7天均值: {round(result.avg_7d, 1) if result.avg_7d else 'N/A'}")
            lines.append(f"- 30天均值: {round(result.avg_30d, 1) if result.avg_30d else 'N/A'}")
            lines.append(
                f"- 个人基线: {round(result.baseline_mean, 1) if result.baseline_mean else 'N/A'} ± {round(result.baseline_std, 1) if result.baseline_std else 'N/A'}"
            )

            if result.is_anomaly:
                direction = "偏高" if result.z_score > 0 else "偏低"
                lines.append(
                    f"- ⚠️ **异常**: 当前值{direction}（偏离基线 {abs(result.z_score):.1f} 个标准差）"
                )

            trend_emoji = {"up": "📈", "down": "📉", "stable": "➡️"}
            lines.append(
                f"- 趋势: {trend_emoji.get(result.trend_direction, '')} {result.trend_direction}"
            )
            lines.append("")
        except Exception as e:
            log.warning("分析 %s 趋势失败: %s", metric, e)
            continue

    # 异常检测
    lines.append("## 近期异常检测")
    lines.append("")

    has_anomalies = False
    for metric, name in core_metrics:
        try:
            anomalies = analyzer.detect_anomalies(metric, z_threshold=2.0)
            if anomalies:
                has_anomalies = True
                lines.append(f"### {name} 异常日期")
                for a in anomalies[-5:]:  # 只显示最近5个
                    direction = "偏高" if a["direction"] == "high" else "偏低"
                    lines.append(f"- {a['date']}: {a['value']} ({direction}，偏离{a['z_score']})")
                lines.append("")
        except Exception:
            continue

    if not has_anomalies:
        lines.append("最近30天未发现明显异常。")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    report = generate_trend_report(30)
    print(report)
