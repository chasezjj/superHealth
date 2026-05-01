"""相关性分析模块：计算健康指标间的相关性。

核心功能：
1. Pearson相关系数计算
2. 跨日滞后相关性分析（如睡眠对次日HRV的影响）
3. 相关性热力图数据生成
4. 关键指标对的相关性分析

典型分析场景：
- 睡眠质量 → 次日HRV
- 运动强度 → 次日Body Battery
- 步数 → 压力水平
- 睡眠时长 → 静息心率
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from superhealth import database as db

log = logging.getLogger(__name__)
DB_PATH = Path(__file__).parent.parent.parent.parent / "health.db"


@dataclass
class CorrelationResult:
    """相关性分析结果。"""

    metric_x: str
    metric_y: str
    n: int  # 样本数
    r: float  # Pearson相关系数
    r_squared: float  # 决定系数
    p_value: Optional[float]  # p值（如果计算）
    interpretation: str  # 解释

    def strength(self) -> str:
        """返回相关强度描述。"""
        abs_r = abs(self.r)
        if abs_r >= 0.7:
            return "强相关"
        elif abs_r >= 0.4:
            return "中等相关"
        elif abs_r >= 0.2:
            return "弱相关"
        else:
            return "几乎无关"

    def direction(self) -> str:
        """返回相关方向。"""
        return "正相关" if self.r > 0 else "负相关"


def pearson_correlation(x: list[float], y: list[float]) -> tuple[float, int]:
    """计算Pearson相关系数。

    Returns:
        (r, n) 相关系数和有效样本数
    """
    if len(x) != len(y) or len(x) < 2:
        return 0.0, 0

    n = len(x)
    mean_x = sum(x) / n
    mean_y = sum(y) / n

    numerator = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    sum_sq_x = sum((xi - mean_x) ** 2 for xi in x)
    sum_sq_y = sum((yi - mean_y) ** 2 for yi in y)

    denominator = math.sqrt(sum_sq_x * sum_sq_y)

    if denominator == 0:
        return 0.0, n

    r = numerator / denominator
    return r, n


class CorrelationAnalyzer:
    """相关性分析器。"""

    # 指标到数据库字段的映射
    FIELD_MAP = {
        "sleep_score": "sleep_score",
        "sleep_duration": "sleep_total_seconds",
        "resting_hr": "hr_resting",
        "hrv_avg": "hrv_last_night_avg",
        "avg_stress": "stress_average",
        "max_stress": "stress_max",
        "body_battery_wake": "bb_at_wake",
        "body_battery_charged": "bb_charged",
        "steps": "steps",
        "active_calories": "active_calories",
        "exercise_duration": None,  # 从exercises表计算
    }

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        db.init_db(db_path)

    def _get_conn(self):
        return db.get_conn(self.db_path)

    def get_metric_series(
        self,
        metric: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days: Optional[int] = None,
    ) -> list[dict]:
        """获取指标时间序列数据。

        Returns:
            [{"date": "2026-03-01", "value": 85.0}, ...]
        """
        field = self.FIELD_MAP.get(metric)
        if not field:
            raise ValueError(f"未知指标: {metric}")

        end = end_date or date.today().isoformat()
        if days:
            start = (datetime.fromisoformat(end) - timedelta(days=days)).isoformat()[:10]
        else:
            start = (
                start_date or (datetime.fromisoformat(end) - timedelta(days=90)).isoformat()[:10]
            )

        with self._get_conn() as conn:
            rows = conn.execute(
                f"""SELECT date, {field} as value
                    FROM daily_health
                    WHERE date BETWEEN ? AND ?
                      AND {field} IS NOT NULL
                    ORDER BY date""",
                (start, end),
            ).fetchall()

        return [{"date": r["date"], "value": r["value"]} for r in rows]

    def correlate_same_day(
        self,
        metric_x: str,
        metric_y: str,
        days: int = 90,
    ) -> CorrelationResult:
        """计算两个指标的当日相关性。"""
        series_x = self.get_metric_series(metric_x, days=days)
        series_y = self.get_metric_series(metric_y, days=days)

        # 按日期对齐
        y_by_date = {s["date"]: s["value"] for s in series_y}

        paired_x = []
        paired_y = []
        for sx in series_x:
            date_x = sx["date"]
            if date_x in y_by_date:
                paired_x.append(sx["value"])
                paired_y.append(y_by_date[date_x])

        if len(paired_x) < 7:
            return CorrelationResult(
                metric_x=metric_x,
                metric_y=metric_y,
                n=len(paired_x),
                r=0.0,
                r_squared=0.0,
                p_value=None,
                interpretation="数据不足（需至少7天）",
            )

        r, n = pearson_correlation(paired_x, paired_y)
        interpretation = f"{self._interpret_correlation(r)}（{self._interpret_direction(metric_x, metric_y, r)}）"

        return CorrelationResult(
            metric_x=metric_x,
            metric_y=metric_y,
            n=n,
            r=round(r, 3),
            r_squared=round(r**2, 3),
            p_value=None,
            interpretation=interpretation,
        )

    def correlate_with_lag(
        self,
        metric_x: str,  # 前置指标（如睡眠）
        metric_y: str,  # 后置指标（如次日HRV）
        lag_days: int = 1,
        days: int = 90,
    ) -> CorrelationResult:
        """计算滞后相关性（metric_x 对 lag_days 后的 metric_y 的影响）。

        典型用法：
        - correlate_with_lag("sleep_score", "hrv_avg", lag_days=1)  # 睡眠对次日HRV的影响
        """
        series_x = self.get_metric_series(metric_x, days=days)
        series_y = self.get_metric_series(metric_y, days=days)

        # 将y的日期减去lag_days，与x对齐
        y_by_date = {}
        for sy in series_y:
            date_y = datetime.fromisoformat(sy["date"]) - timedelta(days=lag_days)
            y_by_date[date_y.isoformat()[:10]] = sy["value"]

        paired_x = []
        paired_y = []
        for sx in series_x:
            date_x = sx["date"]
            if date_x in y_by_date:
                paired_x.append(sx["value"])
                paired_y.append(y_by_date[date_x])

        if len(paired_x) < 7:
            return CorrelationResult(
                metric_x=metric_x,
                metric_y=f"{metric_y}(t+{lag_days})",
                n=len(paired_x),
                r=0.0,
                r_squared=0.0,
                p_value=None,
                interpretation="数据不足（需至少7天）",
            )

        r, n = pearson_correlation(paired_x, paired_y)
        interpretation = f"{self._interpret_correlation(r)}（{self._interpret_direction(metric_x, metric_y, r)}）"

        return CorrelationResult(
            metric_x=metric_x,
            metric_y=f"{metric_y}(t+{lag_days})",
            n=n,
            r=round(r, 3),
            r_squared=round(r**2, 3),
            p_value=None,
            interpretation=interpretation,
        )

    def _interpret_correlation(self, r: float) -> str:
        """解释相关系数强度。"""
        abs_r = abs(r)
        if abs_r >= 0.7:
            return "强相关"
        elif abs_r >= 0.4:
            return "中等相关"
        elif abs_r >= 0.2:
            return "弱相关"
        else:
            return "几乎无关"

    def _interpret_direction(self, metric_x: str, metric_y: str, r: float) -> str:
        """解释相关方向的业务含义。"""
        if r > 0:
            return f"{metric_x}越高，{metric_y}也越高"
        elif r < 0:
            return f"{metric_x}越高，{metric_y}越低"
        else:
            return "两者无明显关系"

    def analyze_key_correlations(self, days: int = 90) -> list[CorrelationResult]:
        """分析关键指标对的相关性。

        包含预设的关键分析场景。
        """
        results = []

        # 定义关键分析场景
        analyses = [
            # 睡眠对次日恢复的影响
            ("sleep_score", "hrv_avg", 1, "睡眠评分 → 次日HRV"),
            ("sleep_score", "body_battery_wake", 1, "睡眠评分 → 次日Body Battery"),
            ("sleep_duration", "resting_hr", 1, "睡眠时长 → 次日静息心率"),
            # 运动对恢复的影响
            ("steps", "body_battery_wake", 1, "步数 → 次日Body Battery"),
            ("active_calories", "body_battery_wake", 1, "活动卡路里 → 次日Body Battery"),
            # 压力与其他指标
            ("avg_stress", "sleep_score", 0, "压力水平 ↔ 睡眠评分"),
            ("avg_stress", "hrv_avg", 0, "压力水平 ↔ HRV"),
            # 心率相关
            ("resting_hr", "hrv_avg", 0, "静息心率 ↔ HRV"),
            ("sleep_score", "resting_hr", 1, "睡眠评分 → 次日静息心率"),
        ]

        for metric_x, metric_y, lag, description in analyses:
            try:
                if lag > 0:
                    result = self.correlate_with_lag(metric_x, metric_y, lag, days)
                else:
                    result = self.correlate_same_day(metric_x, metric_y, days)

                # 添加描述
                result.interpretation = f"{description}: {result.interpretation}"
                results.append(result)
            except Exception as e:
                log.warning("分析 %s 与 %s 的相关性失败: %s", metric_x, metric_y, e)
                continue

        return results

    def get_correlation_matrix(
        self,
        metrics: list[str],
        days: int = 90,
    ) -> dict:
        """生成相关性矩阵数据。

        Returns:
            {"metrics": [...], "matrix": [[r11, r12, ...], [r21, r22, ...], ...]}
        """
        n = len(metrics)
        matrix = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]

        for i, mi in enumerate(metrics):
            for j, mj in enumerate(metrics[i + 1 :], i + 1):
                try:
                    result = self.correlate_same_day(mi, mj, days)
                    matrix[i][j] = result.r
                    matrix[j][i] = result.r
                except Exception as e:
                    log.warning("计算 %s 与 %s 相关性失败: %s", mi, mj, e)

        return {
            "metrics": metrics,
            "matrix": matrix,
        }


def generate_correlation_report(days: int = 90) -> str:
    """生成相关性分析报告（Markdown格式）。"""
    analyzer = CorrelationAnalyzer()

    lines = [f"# 健康指标相关性报告（最近{days}天）", ""]
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    # 关键相关性分析
    lines.append("## 关键指标相关性分析")
    lines.append("")
    lines.append("以下分析揭示不同健康指标之间的关联关系：")
    lines.append("")

    results = analyzer.analyze_key_correlations(days)

    # 按相关性强度排序
    results.sort(key=lambda r: abs(r.r), reverse=True)

    for result in results:
        emoji = "🔴" if abs(result.r) >= 0.5 else "🟡" if abs(result.r) >= 0.3 else "⚪"
        lines.append(f"{emoji} **{result.interpretation}**")
        lines.append(f"   - 相关系数: r = {result.r} ({result.strength()}, {result.direction()})")
        lines.append(f"   - 样本数: n = {result.n}")
        lines.append(
            f"   - 解释力: R² = {result.r_squared}（可解释{result.r_squared * 100:.1f}%的变异）"
        )
        lines.append("")

    # 洞察总结
    lines.append("## 关键洞察")
    lines.append("")

    strong_positive = [r for r in results if r.r >= 0.5]
    strong_negative = [r for r in results if r.r <= -0.5]

    if strong_positive:
        lines.append("### 强正向关联")
        for r in strong_positive:
            lines.append(f"- {r.metric_x} 与 {r.metric_y}: 同步变化趋势明显")
        lines.append("")

    if strong_negative:
        lines.append("### 强负向关联")
        for r in strong_negative:
            lines.append(f"- {r.metric_x} 与 {r.metric_y}: 此消彼长关系明显")
        lines.append("")

    # 实用建议
    lines.append("## 实用建议")
    lines.append("")

    # 根据相关性给出建议
    sleep_hrv = next(
        (r for r in results if "睡眠评分" in r.interpretation and "HRV" in r.interpretation), None
    )
    if sleep_hrv and sleep_hrv.r > 0.3:
        lines.append(
            f"1. **睡眠质量对恢复至关重要**: 睡眠评分与次日HRV呈{sleep_hrv.direction()}（r={sleep_hrv.r}），"
        )
        lines.append("   优先保证睡眠质量可显著改善次日身体恢复状态。")
        lines.append("")

    stress_hrv = next(
        (r for r in results if "压力" in r.interpretation and "HRV" in r.interpretation), None
    )
    if stress_hrv and stress_hrv.r < -0.3:
        lines.append(
            f"2. **压力管理有助恢复**: 压力水平与HRV呈{stress_hrv.direction()}（r={stress_hrv.r}），"
        )
        lines.append("   建议在高压力日增加放松活动。")
        lines.append("")

    exercise_recovery = next(
        (r for r in results if "步数" in r.interpretation or "卡路里" in r.interpretation), None
    )
    if exercise_recovery and exercise_recovery.r < -0.2:
        lines.append(
            f"3. **注意运动恢复平衡**: 运动量与次日恢复呈{exercise_recovery.direction()}（r={exercise_recovery.r}），"
        )
        lines.append("   大运动量后需确保充足恢复时间。")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    from superhealth.log_config import setup_logging

    setup_logging()
    report = generate_correlation_report(90)
    print(report)
