#!/usr/bin/env python3
"""综合健康日报生成器。

整合以下数据源：
1. Garmin 数据（睡眠、压力、心率、HRV、Body Battery、运动）
2. 体征数据（血压、体重、体脂率）
3. 高级分析（趋势分析、个人基线对比、多日异常模式识别）

生成包含当日状态评估和运动建议的综合报告。
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any, Optional

from superhealth import database as db
from superhealth.analysis.trends import TrendAnalyzer
from superhealth.core.assessment_models import RecoveryModel
from superhealth.core.health_profile_builder import HealthProfile

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent.parent / "health.db"
DATA_DIR = Path(__file__).parent.parent.parent.parent / "activity-data"


@dataclass
class VitalStats:
    """体征统计数据。"""
    latest_systolic: Optional[int] = None
    latest_diastolic: Optional[int] = None
    latest_weight: Optional[float] = None
    latest_body_fat: Optional[float] = None
    avg_7d_systolic: Optional[float] = None
    avg_7d_diastolic: Optional[float] = None
    avg_7d_weight: Optional[float] = None
    avg_30d_weight: Optional[float] = None
    weight_change_7d: Optional[float] = None
    recent_high_bp_readings: list[dict] = None  # 近期高血压记录


@dataclass
class RecoveryAssessment:
    """恢复状态评估。"""
    overall_score: int  # 0-100
    level: str  # "优秀"/"良好"/"一般"/"需恢复"
    sleep_quality: str
    hrv_status: str
    body_battery_status: str
    readiness: str  # "适合高强度"/"适合中等强度"/"建议轻度活动"/"建议休息"


@dataclass
class ExerciseRecommendation:
    """运动建议。"""
    intensity: str  # "高强度"/"中等强度"/"轻度"/"休息"
    duration: str
    type_suggestion: str
    cautions: list[str]


class DailyReportGenerator:
    """综合日报生成器。"""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.trend_analyzer = TrendAnalyzer(db_path)

    def _get_conn(self):
        return db.get_conn(self.db_path)

    def load_garmin_data(self, day_str: str) -> Optional[dict]:
        """加载 Garmin 数据。"""
        with self._get_conn() as conn:
            return db.query_daily_flat(conn, day_str)

    def load_vitals_stats(self, day_str: str) -> VitalStats:
        """加载体征统计数据。"""
        stats = VitalStats()
        stats.recent_high_bp_readings = []
        end_date = day_str
        start_7d = (datetime.fromisoformat(day_str) - timedelta(days=7)).isoformat()[:10]
        start_30d = (datetime.fromisoformat(day_str) - timedelta(days=30)).isoformat()[:10]

        with self._get_conn() as conn:
            # 最新值
            latest = db.query_vitals_by_date(conn, day_str)
            if latest:
                stats.latest_systolic = latest.get('systolic')
                stats.latest_diastolic = latest.get('diastolic')
                stats.latest_weight = latest.get('weight_kg')
                stats.latest_body_fat = latest.get('body_fat_pct')

            # 7天平均
            rows = conn.execute(
                """SELECT systolic, diastolic, weight_kg
                   FROM vitals
                   WHERE measured_at >= ? AND measured_at < ? || 'T23:59:59'
                     AND systolic IS NOT NULL""",
                (start_7d + 'T00:00:00', end_date)
            ).fetchall()

            if rows:
                stats.avg_7d_systolic = mean([r['systolic'] for r in rows if r['systolic']])
                stats.avg_7d_diastolic = mean([r['diastolic'] for r in rows if r['diastolic']])
                weights = [r['weight_kg'] for r in rows if r['weight_kg']]
                if weights:
                    stats.avg_7d_weight = mean(weights)

            # 30天平均体重（用于对比）
            rows = conn.execute(
                """SELECT weight_kg FROM vitals
                   WHERE measured_at >= ? AND measured_at < ? || 'T23:59:59'
                     AND weight_kg IS NOT NULL""",
                (start_30d + 'T00:00:00', end_date)
            ).fetchall()
            if rows:
                weights = [r['weight_kg'] for r in rows]
                stats.avg_30d_weight = mean(weights)

            # 7天体重变化
            if stats.avg_7d_weight and stats.avg_30d_weight:
                stats.weight_change_7d = stats.avg_7d_weight - stats.avg_30d_weight

            # 近期异常血压记录（最近7天内收缩压>=130或舒张压>=85）
            rows = conn.execute(
                """SELECT measured_at, systolic, diastolic
                   FROM vitals
                   WHERE measured_at >= ? AND measured_at < ? || 'T23:59:59'
                     AND (systolic >= 130 OR diastolic >= 85)
                   ORDER BY measured_at DESC""",
                (start_7d + 'T00:00:00', end_date)
            ).fetchall()
            for row in rows:
                stats.recent_high_bp_readings.append({
                    'measured_at': row['measured_at'][:10],
                    'systolic': row['systolic'],
                    'diastolic': row['diastolic']
                })

        return stats

    def get_metric_trend_analysis(self, metric: str, day_str: str) -> tuple[Optional[float], Optional[str]]:
        """获取指标趋势分析。

        Returns: (当日值, 趋势描述)
        """
        try:
            trend_data = self.trend_analyzer.calculate_rolling_averages(metric, 30, day_str)
            if not trend_data:
                return None, None

            # 当日值
            today_value = None
            for d in trend_data:
                if d['date'] == day_str:
                    today_value = d['value']
                    break

            if today_value is None:
                return None, None

            # 计算30天平均
            avg_30d = trend_data[-1].get('avg_30d')

            # 获取阈值用于判断偏离程度
            thresholds = {
                "sleep_score": 5,
                "resting_hr": 3,
                "hrv_avg": 5,
                "avg_stress": 5,
                "body_battery_wake": 10,
            }
            threshold = thresholds.get(metric, 0)

            if avg_30d:
                diff = today_value - avg_30d
                if abs(diff) < threshold * 0.5:
                    trend = f"接近30天平均({avg_30d:.0f})"
                elif diff > 0:
                    if metric in ["resting_hr", "avg_stress"]:
                        trend = f"⚠️ 高于30天平均({avg_30d:.0f})"
                    else:
                        trend = f"📈 高于30天平均({avg_30d:.0f})"
                else:
                    if metric in ["resting_hr", "avg_stress"]:
                        trend = f"📉 低于30天平均({avg_30d:.0f})"
                    else:
                        trend = f"⚠️ 低于30天平均({avg_30d:.0f})"
            else:
                trend = None

            return today_value, trend
        except Exception:
            return None, None

    def assess_recovery(self, garmin_data: dict) -> RecoveryAssessment:
        """综合评估恢复状态。统一调用 RecoveryModel 避免双轨制评分。"""
        profile = HealthProfile()  # 空画像，使用 fallback 绝对阈值
        result = RecoveryModel().assess(garmin_data, {}, profile)

        # 从 garmin_data 计算展示标签（与 RecoveryModel 内部一致）
        sleep_score = garmin_data.get('sleep_score')
        if sleep_score:
            if sleep_score >= 85:
                sleep_quality = "优秀"
            elif sleep_score >= 75:
                sleep_quality = "良好"
            elif sleep_score >= 65:
                sleep_quality = "一般"
            elif sleep_score >= 50:
                sleep_quality = "较差"
            else:
                sleep_quality = "严重不足"
        else:
            sleep_quality = "无数据"

        hrv_status = (garmin_data.get('hrv_status') or '').upper()
        if hrv_status == 'BALANCED':
            hrv_desc = "平衡"
        elif hrv_status == 'UNBALANCED':
            hrv_desc = "波动"
        elif hrv_status == 'LOW':
            hrv_desc = "偏低"
        else:
            hrv_desc = "未知"

        bb_wake = garmin_data.get('body_battery_wake')
        if bb_wake:
            if bb_wake >= 80:
                bb_desc = "充足"
            elif bb_wake >= 65:
                bb_desc = "良好"
            elif bb_wake >= 50:
                bb_desc = "一般"
            elif bb_wake >= 35:
                bb_desc = "偏低"
            else:
                bb_desc = "严重不足"
        else:
            bb_desc = "无数据"

        # 从 AssessmentResult tags 中提取 readiness
        readiness = "建议休息"
        for tag in result.tags:
            if tag.startswith("readiness:"):
                readiness = tag.split(":", 1)[1]
                break

        return RecoveryAssessment(
            overall_score=result.score,
            level=result.status,
            sleep_quality=sleep_quality,
            hrv_status=hrv_desc,
            body_battery_status=bb_desc,
            readiness=readiness,
        )

    def generate_exercise_recommendation(
        self,
        assessment: RecoveryAssessment,
        garmin_data: dict,
        vitals: VitalStats
    ) -> ExerciseRecommendation:
        """生成运动建议。"""
        cautions = []

        # 基于恢复状态确定强度
        if assessment.readiness == "适合高强度":
            intensity = "高强度"
            duration = "45-60分钟"
            type_suggestion = "间歇跑、力量训练、或长距离有氧"
        elif assessment.readiness == "适合中等强度":
            intensity = "中等强度"
            duration = "30-45分钟"
            type_suggestion = "慢跑、骑车、游泳或中等强度力量训练"
        elif assessment.readiness == "建议轻度活动":
            intensity = "轻度"
            duration = "20-30分钟"
            type_suggestion = "散步、轻松骑车、瑜伽或拉伸"
        else:
            intensity = "休息"
            duration = "0-15分钟"
            type_suggestion = "完全休息或轻度拉伸、散步"

        # 血压检查
        if vitals.latest_systolic and vitals.latest_diastolic:
            if vitals.latest_systolic >= 140 or vitals.latest_diastolic >= 90:
                cautions.append(f"血压偏高({vitals.latest_systolic}/{vitals.latest_diastolic})，避免高强度运动")
                if intensity == "高强度":
                    intensity = "中等强度"
                    type_suggestion = "改为中等强度有氧，避免爆发力动作"
            elif vitals.latest_systolic >= 130 or vitals.latest_diastolic >= 85:
                cautions.append(f"血压偏高({vitals.latest_systolic}/{vitals.latest_diastolic})，监控运动强度")

        # 近期异常血压提醒
        if vitals.recent_high_bp_readings and len(vitals.recent_high_bp_readings) > 0:
            # 排除今天的记录
            other_days = [r for r in vitals.recent_high_bp_readings if r['measured_at'] != date.today().isoformat()]
            if other_days:
                recent = other_days[0]
                cautions.append(f"{recent['measured_at']}有血压偏高记录({recent['systolic']}/{recent['diastolic']})，注意监测")

        # 体重变化检查
        if vitals.weight_change_7d:
            if vitals.weight_change_7d > 0.5:
                cautions.append(f"近7天体重上升{vitals.weight_change_7d:.1f}kg，注意饮食控制")
            elif vitals.weight_change_7d < -0.5:
                cautions.append(f"近7天体重下降{abs(vitals.weight_change_7d):.1f}kg，确保充足营养")

        # HRV 检查
        if garmin_data.get('hrv_status', '').upper() == 'LOW':
            cautions.append("HRV偏低，身体可能处于疲劳状态，建议降低训练强度")

        # 睡眠检查
        sleep_score = garmin_data.get('sleep_score')
        if sleep_score and sleep_score < 70:
            cautions.append(f"睡眠不足({sleep_score})，优先考虑补觉而非高强度训练")

        return ExerciseRecommendation(
            intensity=intensity,
            duration=duration,
            type_suggestion=type_suggestion,
            cautions=cautions
        )

    def get_trend_insights(self, day_str: str) -> list[str]:
        """获取趋势洞察。"""
        insights = []

        # 睡眠趋势
        try:
            sleep_trend = self.trend_analyzer.analyze_trend("sleep_score", 30)
            if sleep_trend.trend_direction == "down":
                insights.append("📉 睡眠评分近7天呈下降趋势，建议调整作息")
            elif sleep_trend.trend_direction == "up":
                insights.append("📈 睡眠评分近7天呈上升趋势，保持当前习惯")
        except Exception:
            pass

        # HRV趋势
        try:
            hrv_trend = self.trend_analyzer.analyze_trend("hrv_avg", 30)
            if hrv_trend.is_anomaly and hrv_trend.z_score and hrv_trend.z_score < -1.5:
                insights.append("⚠️ HRV显著低于个人基线，身体可能处于疲劳状态")
        except Exception:
            pass

        # 静息心率趋势
        try:
            rhr_trend = self.trend_analyzer.analyze_trend("resting_hr", 30)
            if rhr_trend.trend_direction == "up":
                insights.append("📈 静息心率近7天上升，注意恢复质量")
        except Exception:
            pass

        return insights

    def generate_report(self, day_str: str) -> str:
        """生成综合日报。"""
        lines = []

        # 加载数据
        garmin = self.load_garmin_data(day_str)
        if not garmin:
            return f"# {day_str} 健康日报\n\n未找到 Garmin 数据。"

        vitals = self.load_vitals_stats(day_str)

        # 评估恢复状态
        assessment = self.assess_recovery(garmin)

        # 生成运动建议
        recommendation = self.generate_exercise_recommendation(assessment, garmin, vitals)

        # 获取趋势洞察
        insights = self.get_trend_insights(day_str)

        # === 报告头部 ===
        lines.append(f"# {day_str} 健康日报")
        lines.append("")

        # === 恢复状态总览 ===
        lines.append("## 🎯 今日恢复状态")
        lines.append("")

        # 使用 emoji 表示状态
        status_emoji = {"优秀": "🟢", "良好": "🟢", "一般": "🟡", "需恢复": "🔴"}
        emoji = status_emoji.get(assessment.level, "⚪")

        lines.append(f"{emoji} **综合评分: {assessment.overall_score}/100** ({assessment.level})")
        lines.append(f"- 准备状态: **{assessment.readiness}**")
        lines.append(f"- 睡眠质量: {assessment.sleep_quality}")
        lines.append(f"- HRV状态: {assessment.hrv_status}")
        lines.append(f"- Body Battery: {assessment.body_battery_status}")
        lines.append("")

        # === Garmin 数据详情（带高级分析） ===
        lines.append("## 📊 Garmin 数据")
        lines.append("")

        # 睡眠（带趋势）
        sleep_min = garmin.get('sleep_total_min')
        sleep_score = garmin.get('sleep_score')
        sleep_val, sleep_trend = self.get_metric_trend_analysis("sleep_score", day_str)
        sleep_line = f"- 睡眠: {f'{sleep_min//60}小时{sleep_min%60}分' if sleep_min else 'N/A'} | 评分: {sleep_score or 'N/A'}"
        if sleep_trend:
            sleep_line += f" ({sleep_trend})"
        lines.append(sleep_line)

        # HRV（带趋势）
        hrv = garmin.get('hrv_avg')
        hrv_status = garmin.get('hrv_status')
        hrv_val, hrv_trend = self.get_metric_trend_analysis("hrv_avg", day_str)
        hrv_line = f"- HRV: {f'{hrv:.0f} ms' if hrv else 'N/A'} ({hrv_status or 'N/A'})"
        if hrv_trend:
            hrv_line += f" ({hrv_trend})"
        lines.append(hrv_line)

        # 静息心率（带趋势）
        rhr = garmin.get('resting_hr')
        rhr_val, rhr_trend = self.get_metric_trend_analysis("resting_hr", day_str)
        rhr_line = f"- 静息心率: {f'{rhr:.0f} bpm' if rhr else 'N/A'}"
        if rhr_trend:
            rhr_line += f" ({rhr_trend})"
        lines.append(rhr_line)

        # Body Battery（带趋势）
        bb = garmin.get('body_battery_wake')
        bb_val, bb_trend = self.get_metric_trend_analysis("body_battery_wake", day_str)
        bb_line = f"- Body Battery: {f'{bb:.0f}' if bb else 'N/A'} (起床时)"
        if bb_trend:
            bb_line += f" ({bb_trend})"
        lines.append(bb_line)

        # 压力（带趋势）
        stress = garmin.get('avg_stress')
        stress_val, stress_trend = self.get_metric_trend_analysis("avg_stress", day_str)
        stress_line = f"- 平均压力: {f'{stress:.0f}' if stress else 'N/A'}"
        if stress_trend:
            stress_line += f" ({stress_trend})"
        lines.append(stress_line)

        # 步数
        steps = garmin.get('steps')
        lines.append(f"- 步数: {f'{steps:,}' if steps else 'N/A'}")

        lines.append("")

        # === 体征数据 ===
        if vitals.latest_systolic or vitals.latest_weight:
            lines.append("## 🩺 体征数据")
            lines.append("")

            if vitals.latest_systolic and vitals.latest_diastolic:
                bp_status = ""
                if vitals.latest_systolic >= 140 or vitals.latest_diastolic >= 90:
                    bp_status = " ⚠️ 偏高"
                elif vitals.latest_systolic >= 130 or vitals.latest_diastolic >= 85:
                    bp_status = " ⚡ 注意"
                lines.append(f"- 血压: {vitals.latest_systolic}/{vitals.latest_diastolic} mmHg{bp_status}")
                if vitals.avg_7d_systolic:
                    lines.append(f"  (7天平均: {vitals.avg_7d_systolic:.0f}/{vitals.avg_7d_diastolic:.0f})")

                # 近期异常血压提醒
                if vitals.recent_high_bp_readings and len(vitals.recent_high_bp_readings) > 0:
                    other_days = [r for r in vitals.recent_high_bp_readings if r['measured_at'] != day_str]
                    if other_days:
                        lines.append(f"- ⚠️ 近期血压偏高记录:")
                        for r in other_days[:3]:
                            lines.append(f"  - {r['measured_at']}: {r['systolic']}/{r['diastolic']} mmHg")

            if vitals.latest_weight:
                lines.append(f"- 体重: {vitals.latest_weight:.1f} kg")
                if vitals.avg_7d_weight:
                    lines.append(f"  (7天平均: {vitals.avg_7d_weight:.1f} kg)")
                if vitals.weight_change_7d:
                    change_emoji = "📈" if vitals.weight_change_7d > 0 else "📉"
                    lines.append(f"  {change_emoji} 较30天平均: {vitals.weight_change_7d:+.1f} kg")

            if vitals.latest_body_fat:
                lines.append(f"- 体脂率: {vitals.latest_body_fat:.1f}%")

            lines.append("")

        # === 趋势洞察 ===
        if insights:
            lines.append("## 📈 趋势洞察")
            lines.append("")
            for insight in insights:
                lines.append(f"- {insight}")
            lines.append("")

        # === 今日建议 ===
        lines.append("## 💡 今日建议")
        lines.append("")

        # 运动建议（合并类型和注意事项）
        lines.append(f"**运动**: {recommendation.intensity}，{recommendation.duration}")
        lines.append("")

        # 注意事项（包含类型建议和警告）
        if recommendation.cautions:
            lines.append("⚠️ **注意**:")
            for caution in recommendation.cautions:
                lines.append(f"- {caution}")
            lines.append("")

        # 个性化建议
        if assessment.readiness == "适合高强度":
            lines.append("- 身体状态良好，可以挑战高强度训练")
            lines.append("- 注意训练后的营养补充和拉伸放松")
        elif assessment.readiness == "适合中等强度":
            lines.append("- 身体恢复良好，适合中等强度运动")
            lines.append("- 避免过度训练，保留体力")
        elif assessment.readiness == "建议轻度活动":
            lines.append("- 身体恢复一般，建议轻度活动为主")
            lines.append("- 关注今晚的睡眠质量，为明天储备能量")
        else:
            lines.append("- 身体需要休息，避免高强度运动")
            lines.append("- 重点关注睡眠和营养恢复")

        lines.append("")

        return "\n".join(lines)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    ap = argparse.ArgumentParser(description="生成综合健康日报")
    ap.add_argument("--date", type=str, help="日期 (YYYY-MM-DD)，默认今天")
    args = ap.parse_args()

    day_str = args.date or date.today().isoformat()

    generator = DailyReportGenerator()
    report = generator.generate_report(day_str)

    # 保存报告
    output_path = DATA_DIR / "reports" / f"{day_str}-daily-report.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    log.info("已生成日报: %s", output_path)

    # 同时打印到控制台
    print(report)


if __name__ == "__main__":
    main()
