"""评估模型库：统一接口的各健康维度评分模型。

将 daily_report.py 中的 assess_recovery() 提取为 RecoveryModel，
并新增其他专科维度模型。

统一接口：
    model.assess(daily_data, vitals_data, profile) -> AssessmentResult
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from superhealth.core.health_profile_builder import HealthProfile

log = logging.getLogger(__name__)


def _zscore(value: Any, mean: Any, std: Any) -> Optional[float]:
    """计算 z-score（偏离个人基线的标准差数）。数据不足时返回 None。"""
    if value is None or mean is None or not std:
        return None
    return (float(value) - float(mean)) / float(std)


def _calc_steps_ratio(
    steps_7d: Any, steps_90d: Any, details: list[str]
) -> Optional[float]:
    """计算步数 ratio（7天 vs 90天基线，上限10000）。数据不足时返回 None，并自动追加 details。"""
    if steps_7d is not None and steps_90d is not None:
        effective_baseline = min(float(steps_90d), 10_000)
        ratio = float(steps_7d) / effective_baseline
        details.append(f"近7天均值步数 {steps_7d:,}（90天基线 {steps_90d:,}）")
        return ratio
    if steps_7d is not None:
        details.append(f"近7天均值步数 {steps_7d:,}")
    return None


@dataclass
class AssessmentResult:
    """某维度的评估结果。"""

    model_name: str
    label: str  # 中文模型名
    score: int  # 0-100
    status: str  # 状态标签：优秀/良好/一般/需关注
    summary: str  # 一句话总结
    details: list[str] = field(default_factory=list)  # 详细说明
    tags: list[str] = field(default_factory=list)  # 标签（用于 LLM prompt）


class AssessmentModel(ABC):
    """评估模型抽象基类。"""

    @abstractmethod
    def assess(
        self, daily_data: dict, vitals_data: dict, profile: "HealthProfile"
    ) -> AssessmentResult:
        """返回该维度的评估结果。"""


# ─── 恢复评估模型 ─────────────────────────────────────────────────────


class RecoveryModel(AssessmentModel):
    """恢复状态评估（从 daily_report.py assess_recovery() 提取）。

    权重分配：
    - 睡眠评分：30%
    - HRV 状态：25%
    - Body Battery：20%
    - 静息心率：15%
    - 压力水平：10%
    """

    def assess(
        self, daily_data: dict, vitals_data: dict, profile: "HealthProfile"
    ) -> AssessmentResult:
        score = 0
        details = []
        tags = []
        negative_factors = 0.0

        # ── 睡眠（30分） ──
        sleep_score = daily_data.get("sleep_score")
        sleep_critical = False
        sleep_z = _zscore(
            sleep_score,
            profile.trends.get("sleep_90d_avg"),
            profile.trends.get("sleep_90d_std"),
        )
        if sleep_score:
            if sleep_z is not None:
                # 基于个人90天基线评分（越高越好）
                if sleep_z >= 1.0:
                    score += 30
                elif sleep_z >= 0.5:
                    score += 24
                elif sleep_z >= -0.5:
                    score += 18
                elif sleep_z >= -1.0:
                    score += 8
                    negative_factors += 1
                    tags.append("sleep_poor")
                else:
                    score += 0
                    sleep_critical = True
                    negative_factors += 2
                    tags.append("sleep_critical")
                details.append(
                    f"睡眠评分 {sleep_score}（个人基线 {profile.trends['sleep_90d_avg']:.0f}±{profile.trends['sleep_90d_std']:.0f}，z={sleep_z:+.1f}）"
                )
            else:
                # fallback：绝对阈值
                if sleep_score >= 85:
                    score += 30
                elif sleep_score >= 75:
                    score += 22
                elif sleep_score >= 65:
                    score += 15
                elif sleep_score >= 50:
                    score += 5
                    negative_factors += 1
                    tags.append("sleep_poor")
                else:
                    score += 0
                    sleep_critical = True
                    negative_factors += 2
                    tags.append("sleep_critical")
        else:
            sleep_critical = True

        # ── HRV（25分） ──
        hrv_status = (daily_data.get("hrv_status") or "").upper()
        hrv_critical = False
        if hrv_status == "BALANCED":
            score += 25
        elif hrv_status == "UNBALANCED":
            score += 15
            negative_factors += 0.5
        elif hrv_status == "LOW":
            score += 5
            hrv_critical = True
            negative_factors += 1.5
            tags.append("hrv_low")
        else:
            score += 15

        hrv_avg = daily_data.get("hrv_avg")
        hrv_baseline_low = daily_data.get("hrv_baseline_low")
        if hrv_avg and hrv_baseline_low and hrv_avg < hrv_baseline_low and hrv_status != "LOW":
            score -= 5
            negative_factors += 0.5

        # 额外：HRV vs 90天个人基线
        hrv_z = _zscore(
            hrv_avg,
            profile.trends.get("hrv_90d_avg"),
            profile.trends.get("hrv_90d_std"),
        )
        if hrv_z is not None and hrv_status not in ("LOW",):
            if hrv_z >= 1.0:
                score += 3
                details.append(f"HRV {hrv_avg:.0f} ms，显著高于个人基线（z={hrv_z:+.1f}）")
            elif hrv_z <= -1.0 and hrv_status != "UNBALANCED":
                score -= 3
                negative_factors += 0.5
                details.append(f"HRV {hrv_avg:.0f} ms，显著低于个人基线（z={hrv_z:+.1f}）")

        # ── Body Battery（20分） ──
        bb_wake = daily_data.get("body_battery_wake")
        bb_critical = False
        bb_z = _zscore(
            bb_wake,
            profile.trends.get("bb_90d_avg"),
            profile.trends.get("bb_90d_std"),
        )
        if bb_wake:
            if bb_z is not None:
                if bb_z >= 1.0:
                    score += 20
                elif bb_z >= 0.5:
                    score += 15
                elif bb_z >= -0.5:
                    score += 10
                elif bb_z >= -1.0:
                    score += 4
                    negative_factors += 1
                    tags.append("bb_low")
                else:
                    score += 0
                    bb_critical = True
                    negative_factors += 2
                    tags.append("bb_critical")
                details.append(
                    f"Body Battery {bb_wake:.0f}（个人基线 {profile.trends['bb_90d_avg']:.0f}±{profile.trends['bb_90d_std']:.0f}，z={bb_z:+.1f}）"
                )
            else:
                if bb_wake >= 80:
                    score += 20
                elif bb_wake >= 65:
                    score += 15
                elif bb_wake >= 50:
                    score += 10
                elif bb_wake >= 35:
                    score += 3
                    negative_factors += 1
                    tags.append("bb_low")
                else:
                    score += 0
                    bb_critical = True
                    negative_factors += 2
                    tags.append("bb_critical")
        else:
            bb_critical = True

        # ── 静息心率（15分，越低越好） ──
        rhr = daily_data.get("resting_hr")
        rhr_z = _zscore(
            rhr,
            profile.trends.get("rhr_90d_avg"),
            profile.trends.get("rhr_90d_std"),
        )
        if rhr:
            if rhr_z is not None:
                if rhr_z <= 0:
                    score += 15
                elif rhr_z <= 0.5:
                    score += 10
                elif rhr_z <= 1.0:
                    score += 5
                    negative_factors += 0.5
                else:
                    score += 0
                    negative_factors += 1
                details.append(
                    f"静息心率 {rhr:.0f} bpm（个人基线 {profile.trends['rhr_90d_avg']:.0f}±{profile.trends['rhr_90d_std']:.0f}，z={rhr_z:+.1f}）"
                )
            else:
                # fallback：7天均值
                avg7_rhr = daily_data.get("avg7_resting_hr")
                if avg7_rhr:
                    diff = rhr - avg7_rhr
                    if diff <= 0:
                        score += 15
                    elif diff <= 3:
                        score += 10
                    elif diff <= 5:
                        score += 5
                        negative_factors += 0.5
                    else:
                        score += 0
                        negative_factors += 1
                else:
                    score += 10
        else:
            score += 10

        # ── 压力（10分，越低越好） ──
        avg_stress = daily_data.get("avg_stress")
        stress_z = _zscore(
            avg_stress,
            profile.trends.get("stress_90d_avg"),
            profile.trends.get("stress_90d_std"),
        )
        if avg_stress:
            if stress_z is not None:
                if stress_z <= -1.0:
                    score += 10
                elif stress_z <= -0.5:
                    score += 8
                elif stress_z <= 0.5:
                    score += 5
                elif stress_z <= 1.0:
                    score += 2
                    negative_factors += 0.5
                else:
                    score += 0
                    negative_factors += 1.5
                    tags.append("stress_high")
                details.append(
                    f"平均压力 {avg_stress:.0f}（个人基线 {profile.trends['stress_90d_avg']:.0f}±{profile.trends['stress_90d_std']:.0f}，z={stress_z:+.1f}）"
                )
            else:
                if avg_stress < 25:
                    score += 10
                elif avg_stress < 35:
                    score += 7
                elif avg_stress < 45:
                    score += 3
                    negative_factors += 0.5
                else:
                    score += 0
                    negative_factors += 1.5
                    tags.append("stress_high")
        else:
            score += 7

        # 多重负面因素惩罚
        if negative_factors >= 3:
            score = int(score * 0.7)
        elif negative_factors >= 2:
            score = int(score * 0.85)

        score = min(100, max(0, score))

        critical_count = sum([sleep_critical, hrv_critical, bb_critical])
        if score > 90 and critical_count == 0 and negative_factors < 1:
            status = "优秀"
            readiness = "适合高强度"
        elif score > 75 and critical_count == 0 and negative_factors < 2:
            status = "良好"
            readiness = "适合中等强度"
        elif score > 60 and critical_count <= 1 and negative_factors < 3:
            status = "一般"
            readiness = "建议轻度活动"
        else:
            status = "需恢复"
            readiness = "建议休息"

        if sleep_critical and readiness in ("适合高强度", "适合中等强度"):
            readiness = "建议轻度活动"
            if status in ("优秀", "良好"):
                status = "一般"

        tags.append(f"readiness:{readiness}")

        if not details:
            if sleep_score:
                details.append(f"睡眠评分 {sleep_score}")
            if hrv_avg:
                details.append(f"HRV {hrv_avg:.0f} ms ({hrv_status or 'N/A'})")
            if bb_wake:
                details.append(f"Body Battery {bb_wake:.0f}（起床时）")

        return AssessmentResult(
            model_name="RecoveryModel",
            label="恢复评估",
            score=score,
            status=status,
            summary=f"恢复{status}，{readiness}",
            details=details,
            tags=tags,
        )


# ─── 睡眠质量模型 ─────────────────────────────────────────────────────


class SleepQualityModel(AssessmentModel):
    """睡眠质量多维评估。

    权重分配：
    - Garmin 睡眠评分（比例）：70 分
    - 睡眠时长：15 分
    - 睡眠分期（深睡+REM 占比）：10 分
    - SpO2 最低值：5 分
    趋势对比可额外扣分（最多 -15）。
    """

    def assess(
        self, daily_data: dict, vitals_data: dict, profile: "HealthProfile"
    ) -> AssessmentResult:
        sleep_score = daily_data.get("sleep_score")
        sleep_total_min = daily_data.get("sleep_total_min")
        sleep_deep_min = daily_data.get("sleep_deep_min")
        sleep_rem_min = daily_data.get("sleep_rem_min")
        spo2_lowest = daily_data.get("spo2_lowest")
        details = []
        tags = []

        if sleep_score is None:
            return AssessmentResult("SleepQualityModel", "睡眠质量", 50, "无数据", "无睡眠数据")

        # ── 1. Garmin 评分（70分，比例映射）─────────────────────────────
        garmin_part = round(sleep_score * 0.70)

        # ── 2. 睡眠时长（15分）───────────────────────────────────────────
        duration_part = 0
        if sleep_total_min:
            hours = sleep_total_min / 60
            details.append(f"睡眠时长 {hours:.1f}h")
            if 7.5 <= hours <= 9.0:
                duration_part = 15
                tags.append("sleep_duration_good")
            elif 7.0 <= hours < 7.5 or 9.0 < hours <= 9.5:
                duration_part = 11
            elif 6.0 <= hours < 7.0 or 9.5 < hours <= 10.0:
                duration_part = 6
            else:
                duration_part = 0
                tags.append("sleep_duration_short")

        # ── 3. 睡眠分期（10分）：深睡 + REM 占总睡眠时长比例 ─────────────
        stage_part = 5  # 无数据时给中间值
        if sleep_total_min and sleep_deep_min is not None and sleep_rem_min is not None:
            quality_ratio = (sleep_deep_min + sleep_rem_min) / sleep_total_min
            details.append(
                f"深睡 {sleep_deep_min}min + REM {sleep_rem_min}min（优质睡眠占比 {quality_ratio:.0%}）"
            )
            if quality_ratio >= 0.50:
                stage_part = 10
            elif quality_ratio >= 0.40:
                stage_part = 7
            elif quality_ratio >= 0.30:
                stage_part = 4
            else:
                stage_part = 1
                tags.append("sleep_stages_poor")

        # ── 4. SpO2 最低值（5分）────────────────────────────────────────
        spo2_part = 3  # 无数据时给中间值
        if spo2_lowest is not None:
            details.append(f"SpO2 最低 {spo2_lowest:.0f}%")
            if spo2_lowest >= 93:
                spo2_part = 5
            elif spo2_lowest >= 90:
                spo2_part = 3
            elif spo2_lowest >= 88:
                spo2_part = 1
                tags.append("spo2_low")
            else:
                spo2_part = 0
                tags.append("spo2_critical")

        score = garmin_part + duration_part + stage_part + spo2_part
        details.append(
            f"Garmin评分 {sleep_score}（→{garmin_part}）+ 时长{duration_part} + 分期{stage_part} + SpO2_{spo2_part}"
        )

        # ── 状态判断 ───────────────────────────────────────────────────
        if score >= 88:
            status = "优秀"
        elif score >= 75:
            status = "良好"
        elif score >= 60:
            status = "一般"
        elif score >= 40:
            status = "较差"
            tags.append("sleep_poor")
        else:
            status = "严重不足"
            tags.append("sleep_critical")

        # ── 5. 7天 vs 90天趋势修正 ────────────────────────────────────
        avg_90d = profile.trends.get("sleep_90d_avg")
        avg_7d = profile.trends.get("sleep_7d_avg")
        std_90d = profile.trends.get("sleep_90d_std")
        if avg_90d is not None and avg_7d is not None:
            diff = avg_7d - avg_90d
            sigma = std_90d or 5.0
            details.append(
                f"近7天均值 {avg_7d:.0f} vs 90天基线 {avg_90d:.0f}±{sigma:.0f}（{'+' if diff >= 0 else ''}{diff:.0f}）"
            )
            if diff <= -1.5 * sigma:
                score = max(score - 15, 20)
                tags.append("sleep_trending_down")
                details.append("近期睡眠质量显著低于个人基线")
                if status in ("优秀", "良好"):
                    status = "近期趋势下滑"
            elif diff <= -0.8 * sigma:
                score = max(score - 8, 20)
                tags.append("sleep_trending_down")
                details.append("近期睡眠质量低于个人基线")
                if status == "优秀":
                    status = "今日良好但近期偏低"
            elif diff >= 0.8 * sigma:
                details.append("近期睡眠优于个人基线")

        score = min(100, max(0, score))
        summary_baseline = (
            f"（今日 {sleep_score}，7天均值 {avg_7d:.0f}，90天基线 {avg_90d:.0f}）"
            if avg_90d and avg_7d
            else f"（{sleep_score}分）"
        )
        return AssessmentResult(
            model_name="SleepQualityModel",
            label="睡眠质量",
            score=score,
            status=status,
            summary=f"睡眠{status}{summary_baseline}",
            details=details,
            tags=tags,
        )


# ─── 压力管理模型 ─────────────────────────────────────────────────────


class StressModel(AssessmentModel):
    def assess(
        self, daily_data: dict, vitals_data: dict, profile: "HealthProfile"
    ) -> AssessmentResult:
        details = []
        tags = []

        stress_yesterday = profile.trends.get("stress_yesterday")
        stress_today = daily_data.get("avg_stress")  # 早报时只有凌晨~7点，仅作参考
        avg_7d = profile.trends.get("stress_7d_avg")
        avg_90d = profile.trends.get("stress_90d_avg")

        # 主评分依据：昨日完整压力；无昨日数据时降级用7天均值
        primary = stress_yesterday if stress_yesterday is not None else avg_7d
        std_90d = profile.trends.get("stress_90d_std")
        if primary is None:
            return AssessmentResult("StressModel", "压力管理", 60, "无数据", "无压力数据")

        # 基于个人90天基线的分级（越低越好）
        stress_z = _zscore(primary, avg_90d, std_90d)
        if stress_z is not None:
            if stress_z <= -1.0:
                score, status = 90, "低压力"
            elif stress_z <= -0.5:
                score, status = 78, "适中偏低"
            elif stress_z <= 0.5:
                score, status = 65, "适中"
            elif stress_z <= 1.0:
                score, status = 45, "偏高"
                tags.append("stress_elevated")
            elif stress_z <= 1.5:
                score, status = 30, "较高"
                tags.append("stress_high")
            else:
                score, status = 20, "过高"
                tags.append("stress_critical")
            details.append(
                f"昨日压力 {primary:.0f}（个人基线 {avg_90d:.0f}±{std_90d:.0f}，z={stress_z:+.1f}）"
            )
        else:
            # fallback：绝对阈值
            if primary < 25:
                score, status = 90, "低压力"
            elif primary < 35:
                score, status = 75, "适中"
            elif primary < 45:
                score, status = 55, "偏高"
                tags.append("stress_elevated")
            elif primary < 55:
                score, status = 35, "较高"
                tags.append("stress_high")
            else:
                score, status = 20, "过高"
                tags.append("stress_critical")
            if stress_yesterday is not None:
                details.append(f"昨日压力 {stress_yesterday:.0f}（全天）")
        if stress_today is not None:
            details.append(f"今日压力 {stress_today:.0f}（凌晨至7点，仅参考）")

        # 7天均值 vs 90天基线趋势
        if avg_90d is not None and avg_7d is not None:
            diff = avg_7d - avg_90d
            sigma = std_90d or 5.0
            details.append(
                f"近7天均值 {avg_7d:.0f} vs 90天基线 {avg_90d:.0f}±{sigma:.0f}（{'+' if diff >= 0 else ''}{diff:.0f}）"
            )
            if diff > 1.5 * sigma:
                score = max(score - 20, 20)
                tags.append("stress_trending_up")
                details.append("近期压力显著高于个人基线，建议主动干预")
                if status in ("低压力", "适中偏低", "适中"):
                    status = "趋势上升"
            elif diff >= 0.8 * sigma:
                score = max(score - 10, 20)
                tags.append("stress_trending_up")
                details.append("近期压力持续高于个人基线")
                if status in ("低压力", "适中偏低"):
                    status = "今日偏低但趋势上升"

        # HRV 辅助评估
        hrv_status = (daily_data.get("hrv_status") or "").upper()
        if hrv_status == "LOW" and primary > 35:
            tags.append("hrv_stress_combo")
            details.append("HRV 偏低 + 压力偏高，建议主动恢复")

        summary_parts = []
        if stress_yesterday is not None:
            summary_parts.append(f"昨日 {stress_yesterday:.0f}")
        if avg_7d is not None:
            summary_parts.append(f"7天均值 {avg_7d:.0f}")
        if avg_90d is not None:
            summary_parts.append(f"90天基线 {avg_90d:.0f}")

        return AssessmentResult(
            model_name="StressModel",
            label="压力管理",
            score=score,
            status=status,
            summary=f"压力水平{status}（{'，'.join(summary_parts)}）",
            details=details,
            tags=tags,
        )


# ─── 心血管模型 ───────────────────────────────────────────────────────


class CardiovascularModel(AssessmentModel):
    def assess(
        self, daily_data: dict, vitals_data: dict, profile: "HealthProfile"
    ) -> AssessmentResult:
        details = []
        tags = []
        score = 80  # 基础分

        systolic = vitals_data.get("systolic") if vitals_data else None
        diastolic = vitals_data.get("diastolic") if vitals_data else None
        bp_label = "今日"

        # 无今日数据时用近7天均值
        if systolic is None:
            systolic = profile.trends.get("bp_7d_avg_systolic")
            diastolic = profile.trends.get("bp_7d_avg_diastolic")
            bp_label = "近7天均值"

        if systolic is not None:
            # 收缩压评估（ACC/AHA 2017）
            if systolic >= 140:
                score -= 25
                tags.append("bp_high")
                details.append(f"收缩压 {systolic:.0f} mmHg（{bp_label}，II级高血压）")
            elif systolic >= 130:
                score -= 12
                tags.append("bp_elevated")
                details.append(f"收缩压 {systolic:.0f} mmHg（{bp_label}，I级高血压前期）")
            elif systolic >= 120:
                score -= 5
                details.append(f"收缩压 {systolic:.0f} mmHg（{bp_label}，血压偏高正常值）")
            else:
                details.append(f"收缩压 {systolic:.0f} mmHg（{bp_label}，正常）")

            # 舒张压评估
            if diastolic is not None:
                if diastolic >= 90:
                    score -= 15
                    tags.append("dbp_high")
                    details.append(f"舒张压 {diastolic:.0f} mmHg（{bp_label}，II级）")
                elif diastolic >= 80:
                    score -= 8
                    tags.append("dbp_elevated")
                    details.append(f"舒张压 {diastolic:.0f} mmHg（{bp_label}，I级前期）")
                else:
                    details.append(f"舒张压 {diastolic:.0f} mmHg（{bp_label}，正常）")
        else:
            details.append("暂无血压数据")

        if "bp_trending_up" in profile.risk_factors:
            score -= 10
            tags.append("bp_trending_up")
            details.append("近期血压呈上升趋势")

        # 静息心率（相对个人基线判断，避免绝对阈值误判低基线人群）
        rhr = daily_data.get("resting_hr")
        if rhr:
            rhr_baseline = profile.trends.get("rhr_90d_avg") or daily_data.get("avg7_resting_hr")
            if rhr_baseline:
                diff = rhr - rhr_baseline
                if diff > 8:
                    score -= 8
                    tags.append("rhr_elevated")
                    details.append(
                        f"静息心率 {rhr:.0f} bpm（高于个人基线 {rhr_baseline:.0f} +{diff:.0f}）"
                    )
                elif diff > 4:
                    score -= 4
                    tags.append("rhr_elevated")
                    details.append(
                        f"静息心率 {rhr:.0f} bpm（高于个人基线 {rhr_baseline:.0f} +{diff:.0f}）"
                    )
                else:
                    details.append(f"静息心率 {rhr:.0f} bpm（个人基线 {rhr_baseline:.0f}，正常）")
            else:
                details.append(f"静息心率 {rhr:.0f} bpm")

        score = max(0, min(100, score))
        if score >= 75:
            status = "良好"
        elif score >= 55:
            status = "一般"
        else:
            status = "需关注"

        bp_summary = (
            f"（血压 {systolic:.0f}/{diastolic:.0f}，{bp_label}）" if systolic and diastolic else ""
        )
        return AssessmentResult(
            model_name="CardiovascularModel",
            label="心血管健康",
            score=score,
            status=status,
            summary=f"心血管{status}{bp_summary}",
            details=details,
            tags=tags,
        )


# ─── 代谢管理模型（高尿酸）────────────────────────────────────────────


class MetabolicModel(AssessmentModel):
    def assess(
        self, daily_data: dict, vitals_data: dict, profile: "HealthProfile"
    ) -> AssessmentResult:
        details = []
        tags = ["hyperuricemia_active"]
        score = 70  # 基础分（已用药控制）

        ua_trend = profile.trends.get("uric_acid", "未知")
        if ua_trend == "正常":
            score = 80
            status = "控制良好"
            details.append("尿酸在正常范围（药物控制中）")
        elif ua_trend == "偏高":
            score = 55
            status = "需关注"
            tags.append("ua_high")
            details.append("尿酸偏高，需调整管理策略")
        else:
            status = "监控中"
            details.append("高尿酸血症（用药管理中）")

        steps_7d = profile.trends.get("steps_7d_avg")
        steps_90d = profile.trends.get("steps_90d_avg")
        ratio = _calc_steps_ratio(steps_7d, steps_90d, details)
        if ratio is not None:
            if ratio >= 1.1:
                score = min(100, score + 5)
                details.append("活动量高于个人基线，有助尿酸代谢")
            elif ratio < 0.7:
                score = max(0, score - 8)
                tags.append("low_activity")
                details.append("活动量显著低于个人基线，不利尿酸管理")
            elif ratio < 0.85:
                score = max(0, score - 4)
                tags.append("low_activity")
                details.append("活动量低于个人基线")

        return AssessmentResult(
            model_name="MetabolicModel",
            label="代谢管理",
            score=score,
            status=status,
            summary=f"高尿酸管理{status}",
            details=details,
            tags=tags,
        )


# ─── 青光眼管理模型 ───────────────────────────────────────────────────


class GlaucomaModel(AssessmentModel):
    def assess(
        self, daily_data: dict, vitals_data: dict, profile: "HealthProfile"
    ) -> AssessmentResult:
        details = []
        tags = ["glaucoma_active", "avoid_valsalva"]
        score = 70  # 基础分（青光眼须持续管理）
        status = "用药控制中"

        details.append("滴眼液维持治疗")

        # 眼压控制评分
        od_iop = profile.trends.get("eye_od_iop")
        os_iop = profile.trends.get("eye_os_iop")
        exam_date = profile.trends.get("eye_exam_date", "")
        if od_iop is not None or os_iop is not None:
            iop_vals = [v for v in [od_iop, os_iop] if v is not None]
            max_iop = max(iop_vals)
            iop_str = "/".join(f"{v}" for v in iop_vals)
            if max_iop < 15:
                score += 10
                details.append(f"眼压 {iop_str} mmHg（{exam_date}，控制优秀）")
            elif max_iop < 18:
                score += 5
                details.append(f"眼压 {iop_str} mmHg（{exam_date}，控制良好）")
            elif max_iop <= 21:
                details.append(f"眼压 {iop_str} mmHg（{exam_date}，临界正常）")
                tags.append("iop_borderline")
            else:
                score -= 15
                tags.append("iop_high")
                details.append(f"眼压 {iop_str} mmHg（{exam_date}，偏高需关注）")

        # 杯盘比评分
        od_cdr = profile.trends.get("eye_od_cdr")
        os_cdr = profile.trends.get("eye_os_cdr")
        if od_cdr is not None or os_cdr is not None:
            cdr_vals = [v for v in [od_cdr, os_cdr] if v is not None]
            max_cdr = max(cdr_vals)
            cdr_str = "/".join(f"{v}" for v in cdr_vals)
            if max_cdr >= 0.9:
                score -= 10
                tags.append("cdr_high")
                details.append(f"杯盘比 {cdr_str}（视神经损害显著）")
            elif max_cdr >= 0.8:
                score -= 5
                tags.append("cdr_elevated")
                details.append(f"杯盘比 {cdr_str}（视神经损害中度）")
            else:
                details.append(f"杯盘比 {cdr_str}（稳定）")

        # 疾病稳定性（fundus_note + note）
        fundus_note = profile.trends.get("eye_fundus_note", "")
        eye_note = profile.trends.get("eye_note", "")
        combined = fundus_note + eye_note
        progression_keywords = ["进展", "扩大", "恶化", "加重", "缺损增加"]
        stable_keywords = ["无变化", "稳定"]
        if any(k in combined for k in progression_keywords):
            score -= 15
            tags.append("glaucoma_progressing")
            details.append("复查发现病情进展，需及时就诊")
        else:
            stable_count = sum(1 for k in stable_keywords if k in combined)
            if stable_count >= 1:
                score += 10
                details.append(f"视杯/视野稳定（{exam_date}）")

        # 压力影响
        avg_stress = daily_data.get("avg_stress")
        stress_7d = profile.trends.get("stress_7d_avg")
        stress_90d = profile.trends.get("stress_90d_avg")
        if avg_stress and avg_stress > 45:
            score -= 5
            tags.append("stress_iop_risk")
            details.append(f"今日压力 {avg_stress:.0f}（过高，可能影响眼压）")
        elif stress_7d and stress_90d and (stress_7d - stress_90d) >= 4:
            score -= 3
            tags.append("stress_trending_iop_risk")
            details.append(
                f"近期压力趋升（7天均值 {stress_7d:.0f} vs 基线 {stress_90d:.0f}），注意眼压波动"
            )

        score = max(0, min(100, score))
        if score >= 80:
            status = "控制良好"
        elif score >= 65:
            status = "用药控制中"
        else:
            status = "需加强管理"

        return AssessmentResult(
            model_name="GlaucomaModel",
            label="青光眼管理",
            score=score,
            status=status,
            summary=f"青光眼{status}，避免憋气/倒立",
            details=details,
            tags=tags,
        )


# ─── 血脂管理模型 ─────────────────────────────────────────────────────


class LipidModel(AssessmentModel):
    def assess(
        self, daily_data: dict, vitals_data: dict, profile: "HealthProfile"
    ) -> AssessmentResult:
        details = []
        tags = []
        score = 85  # 基础分（无异常）

        ldl = profile.trends.get("ldl_latest")
        tg = profile.trends.get("tg_latest")
        hdl = profile.trends.get("hdl_latest")
        ldl_date = profile.trends.get("ldl_date", "")

        # LDL 评分（ACC/AHA 标准）
        if ldl is not None:
            if ldl >= 4.1:
                score -= 25
                tags.append("ldl_high")
                details.append(f"LDL-C {ldl:.2f} mmol/L（{ldl_date}，偏高）")
            elif ldl >= 3.4:
                score -= 15
                tags.append("ldl_borderline")
                details.append(f"LDL-C {ldl:.2f} mmol/L（{ldl_date}，临界偏高）")
            elif ldl >= 3.0:
                score -= 8
                details.append(f"LDL-C {ldl:.2f} mmol/L（{ldl_date}，接近临界）")
            else:
                details.append(f"LDL-C {ldl:.2f} mmol/L（{ldl_date}，正常）")

        # TG 评分
        if tg is not None:
            if tg >= 2.3:
                score -= 15
                tags.append("tg_high")
                details.append(f"TG {tg:.2f} mmol/L（偏高）")
            elif tg >= 1.7:
                score -= 8
                tags.append("tg_borderline")
                details.append(f"TG {tg:.2f} mmol/L（临界偏高）")
            elif tg >= 1.5:
                score -= 3
                details.append(f"TG {tg:.2f} mmol/L（接近临界）")
            else:
                details.append(f"TG {tg:.2f} mmol/L（正常）")

        # HDL 评分（低 HDL 是风险因素）
        if hdl is not None:
            if hdl < 1.0:
                score -= 8
                tags.append("hdl_low")
                details.append(f"HDL-C {hdl:.2f} mmol/L（偏低，心血管风险增加）")
            else:
                details.append(f"HDL-C {hdl:.2f} mmol/L（正常）")

        # 步数：与90天基线比较
        steps_7d = profile.trends.get("steps_7d_avg")
        steps_90d = profile.trends.get("steps_90d_avg")
        ratio = _calc_steps_ratio(steps_7d, steps_90d, details)
        if ratio is not None:
            if ratio >= 1.1:
                score = min(100, score + 5)
                tags.append("good_activity_for_lipid")
                details.append("活动量高于个人基线，有助血脂控制")
            elif ratio < 0.7:
                score = max(0, score - 8)
                tags.append("low_activity_lipid")
                details.append("活动量显著低于个人基线，不利血脂管理")
            elif ratio < 0.85:
                score = max(0, score - 4)
                tags.append("low_activity_lipid")
                details.append("活动量低于个人基线")

        score = max(0, min(100, score))
        if score >= 80:
            status = "控制良好"
        elif score >= 65:
            status = "需关注"
        else:
            status = "需积极干预"

        ldl_summary = f"LDL {ldl:.2f}" if ldl else ""
        tg_summary = f"TG {tg:.2f}" if tg else ""
        bp_parts = "，".join(x for x in [ldl_summary, tg_summary] if x)
        return AssessmentResult(
            model_name="LipidModel",
            label="血脂管理",
            score=score,
            status=status,
            summary=f"血脂{status}（{bp_parts}）" if bp_parts else f"血脂{status}",
            details=details,
            tags=tags,
        )


# ─── 体成分模型 ───────────────────────────────────────────────────────


class BodyCompositionModel(AssessmentModel):
    def assess(
        self, daily_data: dict, vitals_data: dict, profile: "HealthProfile"
    ) -> AssessmentResult:
        bc = profile.body_composition
        details = []
        tags = []

        if bc.bmi is None:
            return AssessmentResult(
                "BodyCompositionModel", "体成分管理", 60, "无数据", "缺少体成分数据"
            )

        if bc.bmi_status == "underweight":
            score = 60
            status = "偏瘦"
            tags.append("bmi_low")
            tags.append("priority_muscle_gain")
            details.append(f"BMI {bc.bmi}（偏瘦），建议增肌训练")
        elif bc.bmi_status == "normal":
            if bc.bmi < 19.5:
                score = 72
                status = "正常偏瘦"
                tags.append("bmi_low_normal")
                details.append(f"BMI {bc.bmi}（正常范围但偏低，建议适度增肌）")
            elif bc.bmi >= 23.5:
                score = 75
                status = "正常偏重"
                tags.append("bmi_high_normal")
                details.append(f"BMI {bc.bmi}（正常范围但偏高，注意饮食控制）")
            else:
                score = 80
                status = "正常"
                details.append(f"BMI {bc.bmi}（正常范围）")
        elif bc.bmi_status == "overweight":
            score = 65
            status = "偏重"
            tags.append("bmi_high")
            details.append(f"BMI {bc.bmi}（偏重），建议有氧+控制饮食")
        else:
            score = 50
            status = "肥胖"
            tags.append("bmi_obese")
            details.append(f"BMI {bc.bmi}（肥胖），优先减重")

        if bc.body_fat_pct is not None:
            fat_status_cn = {
                "low": "偏低",
                "normal": "正常",
                "high": "偏高",
                "very_high": "过高",
            }.get(bc.body_fat_status, bc.body_fat_status)
            details.append(f"体脂率 {bc.body_fat_pct:.1f}%（{fat_status_cn}）")
            if bc.body_fat_status in ("high", "very_high"):
                score = max(0, score - 10)
                tags.append("body_fat_high")
            elif bc.body_fat_status == "low":
                tags.append("body_fat_low")

        if bc.weight_kg:
            details.append(f"体重 {bc.weight_kg:.1f} kg")

        return AssessmentResult(
            model_name="BodyCompositionModel",
            label="体成分管理",
            score=min(100, max(0, score)),
            status=status,
            summary=f"体成分{status}（{bc.assessment}）",
            details=details,
            tags=tags,
        )


# ─── 基因风险管理模型 ─────────────────────────────────────────────────


class GeneticRiskModel(AssessmentModel):
    def assess(
        self, daily_data: dict, vitals_data: dict, profile: "HealthProfile"
    ) -> AssessmentResult:
        details = []
        tags = ["genetic_risk_aware"]

        genes = list(profile.genetic_markers.keys())
        gene_count = len(genes)
        details.append(f"肿瘤易感基因变异：{', '.join(genes)}")

        # 基础分：变异数量越多基线越低
        if gene_count >= 5:
            score = 68
        elif gene_count >= 3:
            score = 73
        else:
            score = 78

        # 运动：与90天基线比较（有氧激活解毒替代通路）
        steps_7d = profile.trends.get("steps_7d_avg")
        steps_90d = profile.trends.get("steps_90d_avg")
        ratio = _calc_steps_ratio(steps_7d, steps_90d, details)
        if ratio is not None:
            if ratio >= 1.1:
                score += 7
                details.append("活动量高于个人基线，有效激活抗氧化解毒通路")
            elif ratio >= 0.85:
                score += 3
                details.append("活动量接近个人基线，维持基础抗氧化能力")
            elif ratio < 0.7:
                score -= 7
                tags.append("low_activity_genetic_risk")
                details.append("活动量显著低于个人基线，抗氧化通路受抑")
            else:
                score -= 3
                tags.append("low_activity_genetic_risk")
                details.append("活动量低于个人基线")

        # 睡眠加分（睡眠修复 DNA 损伤）
        sleep_score = daily_data.get("sleep_score")
        sleep_7d = profile.trends.get("sleep_7d_avg")
        ref_sleep = sleep_7d or sleep_score
        if ref_sleep is not None:
            if ref_sleep >= 80:
                score += 4
                details.append(f"睡眠质量良好（均值 {ref_sleep:.0f}分，有助 DNA 修复）")
            elif ref_sleep < 65:
                score -= 5
                tags.append("poor_sleep_genetic_risk")
                details.append(f"睡眠质量较差（均值 {ref_sleep:.0f}分），削弱 DNA 修复能力")

        # 压力扣分（长期应激增加氧化损伤）
        stress_7d = profile.trends.get("stress_7d_avg")
        stress_90d = profile.trends.get("stress_90d_avg")
        avg_stress = daily_data.get("avg_stress")
        if stress_7d and stress_90d and (stress_7d - stress_90d) >= 4:
            score -= 5
            tags.append("stress_genetic_risk")
            details.append(
                f"近期压力高于基线（7天 {stress_7d:.0f} vs 90天 {stress_90d:.0f}），氧化应激升高"
            )
        elif avg_stress and avg_stress > 45:
            score -= 8
            tags.append("stress_genetic_risk")
            details.append(f"今日压力过高（{avg_stress:.0f}），需主动干预")

        score = max(0, min(100, score))
        if score >= 80:
            status = "干预有效"
        elif score >= 65:
            status = "可干预"
        else:
            status = "需加强干预"

        return AssessmentResult(
            model_name="GeneticRiskModel",
            label="基因风险管理",
            score=score,
            status=status,
            summary=f"{gene_count} 个易感基因变异，当前生活方式干预{status}",
            details=details,
            tags=tags,
        )


# ─── 模型工厂 ─────────────────────────────────────────────────────────

MODEL_REGISTRY: dict[str, type[AssessmentModel]] = {
    "RecoveryModel": RecoveryModel,
    "SleepQualityModel": SleepQualityModel,
    "StressModel": StressModel,
    "CardiovascularModel": CardiovascularModel,
    "MetabolicModel": MetabolicModel,
    "GlaucomaModel": GlaucomaModel,
    "LipidModel": LipidModel,
    "BodyCompositionModel": BodyCompositionModel,
    "GeneticRiskModel": GeneticRiskModel,
}


def run_assessments(
    model_names: list[str],
    daily_data: dict,
    vitals_data: dict,
    profile: "HealthProfile",
) -> list[AssessmentResult]:
    """批量运行选中的评估模型，返回结果列表。"""
    results = []
    for name in model_names:
        cls = MODEL_REGISTRY.get(name)
        if cls is None:
            log.warning("未知模型: %s", name)
            continue
        try:
            result = cls().assess(daily_data, vitals_data, profile)
            results.append(result)
        except Exception as e:
            log.error("模型 %s 评估失败: %s", name, e)
    return results
