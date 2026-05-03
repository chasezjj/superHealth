"""模型选择器：基于 HealthProfile 动态选择评估模型。

每个模型对应一个健康评估维度和权威指南来源。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from superhealth.core.health_profile_builder import HealthProfile


@dataclass
class ModelSpec:
    """评估模型规格。"""

    name: str
    label: str  # 中文名
    guide_key: str  # 对应 LLMAdvisor.GUIDE_LIBRARY 的 key
    reason: str  # 选中原因（用于报告展示）


class ModelSelector:
    """基于 HealthProfile 动态选择评估模型。"""

    # metric_key → guide_key 映射（目标驱动模型强激活）
    _GOAL_GUIDE_MAP = {
        "bp_systolic_mean_7d": "cardiovascular",
        "bp_diastolic_mean_7d": "cardiovascular",
        "body_battery_wake_mean_7d": "recovery",
        "sleep_score_mean_7d": "sleep",
        "hrv_mean_7d": "recovery",
        "resting_hr_mean_7d": "cardiovascular",
        "weight_kg_mean_7d": "body_composition",
        "body_fat_pct_mean_7d": "body_composition",
        "steps_mean_7d": "recovery",
        "stress_mean_7d": "stress",
        "uric_acid_latest": "metabolic",
        "iop_mean_recent": "glaucoma",
    }

    # guide_key → (model_name, model_label)
    _GUIDE_MODEL_MAP = {
        "cardiovascular": ("CardiovascularModel", "心血管健康"),
        "recovery": ("RecoveryModel", "恢复评估"),
        "sleep": ("SleepQualityModel", "睡眠质量"),
        "stress": ("StressModel", "压力管理"),
        "metabolic": ("MetabolicModel", "代谢管理"),
        "glaucoma": ("GlaucomaModel", "青光眼管理"),
        "body_composition": ("BodyCompositionModel", "体成分管理"),
    }

    def select(
        self, profile: "HealthProfile", daily_data: dict | None = None, goals: list[dict] | None = None
    ) -> list[ModelSpec]:
        """返回应激活的模型列表（总是包含基础模型）。

        Args:
            goals: 活跃目标列表，按目标 metric_key 强激活对应模型。
        """
        selected: list[ModelSpec] = []

        # === 基础模型（所有人必选）===

        selected.append(
            ModelSpec(
                name="RecoveryModel",
                label="恢复评估",
                guide_key="recovery",
                reason="所有人",
            )
        )
        selected.append(
            ModelSpec(
                name="SleepQualityModel",
                label="睡眠质量",
                guide_key="sleep",
                reason="所有人",
            )
        )
        selected.append(
            ModelSpec(
                name="StressModel",
                label="压力管理",
                guide_key="stress",
                reason="所有人",
            )
        )

        # === 条件模型（基于 HealthProfile）===

        # 心血管模型：近7天有血压数据或血压趋势异常
        if (
            "bp_trending_up" in profile.risk_factors
            or "bp_elevated" in profile.risk_factors
            or "has_recent_bp_data" in profile.risk_factors
        ):
            if "bp_trending_up" in profile.risk_factors:
                cv_reason = "血压上升趋势"
            elif "bp_elevated" in profile.risk_factors:
                cv_reason = "近期血压偏高"
            else:
                cv_reason = "近7天有血压数据"
            selected.append(
                ModelSpec(
                    name="CardiovascularModel",
                    label="心血管健康",
                    guide_key="cardiovascular",
                    reason=cv_reason,
                )
            )

        # 代谢模型：高尿酸
        if "hyperuricemia" in profile.conditions:
            selected.append(
                ModelSpec(
                    name="MetabolicModel",
                    label="代谢管理",
                    guide_key="metabolic",
                    reason="高尿酸血症",
                )
            )

        # 青光眼模型
        if "glaucoma" in profile.conditions or any(
            m["condition"] == "glaucoma" for m in profile.active_medications
        ):
            selected.append(
                ModelSpec(
                    name="GlaucomaModel",
                    label="青光眼管理",
                    guide_key="glaucoma",
                    reason="青光眼（用药控制中）",
                )
            )

        # 血脂模型：有血脂异常史或 LDL 偏高
        if (
            "dyslipidemia_history" in profile.history_conditions
            or "ldl_borderline" in profile.risk_factors
        ):
            selected.append(
                ModelSpec(
                    name="LipidModel",
                    label="血脂管理",
                    guide_key="lipid",
                    reason="血脂异常史（运动控制中）",
                )
            )

        # 体成分模型：近7天有体重数据即激活
        bc = profile.body_composition
        if bc.weight_kg is not None:
            if bc.bmi_status in ("underweight", "overweight", "obese") or bc.body_fat_status in (
                "high",
                "very_high",
                "low",
            ):
                bc_reason = f"BMI {bc.bmi}（{bc.bmi_status}），{bc.assessment}"
            else:
                parts = [f"体重 {bc.weight_kg:.1f} kg"]
                if bc.bmi is not None:
                    parts.append(f"BMI {bc.bmi}")
                if bc.body_fat_pct is not None:
                    parts.append(f"体脂率 {bc.body_fat_pct:.1f}%")
                bc_reason = "近7天有体重数据（" + "，".join(parts) + "）"
            selected.append(
                ModelSpec(
                    name="BodyCompositionModel",
                    label="体成分管理",
                    guide_key="body_composition",
                    reason=bc_reason,
                )
            )

        # 基因肿瘤易感模型：有高风险基因变异
        if profile.genetic_markers:
            selected.append(
                ModelSpec(
                    name="GeneticRiskModel",
                    label="基因风险管理",
                    guide_key="genetic_risk",
                    reason=f"肿瘤易感基因变异（{', '.join(profile.genetic_markers.keys())}）",
                )
            )

        # === 目标驱动模型强激活 ===
        if goals:
            existing_keys = {m.guide_key for m in selected}
            for goal in goals:
                metric_key = goal.get("metric_key", "")
                guide_key = self._GOAL_GUIDE_MAP.get(metric_key)
                if guide_key and guide_key not in existing_keys:
                    model_info = self._GUIDE_MODEL_MAP.get(guide_key)
                    if model_info:
                        name, label = model_info
                        selected.append(
                            ModelSpec(
                                name=name,
                                label=label,
                                guide_key=guide_key,
                                reason=f"目标驱动激活（{goal.get('name', '')}）",
                            )
                        )
                        existing_keys.add(guide_key)

        return selected

    def get_model_names(self, models: list[ModelSpec]) -> list[str]:
        return [m.name for m in models]

    def get_guide_keys(self, models: list[ModelSpec]) -> list[str]:
        """去重后的指南 key 列表。"""
        seen = set()
        keys = []
        for m in models:
            if m.guide_key not in seen:
                seen.add(m.guide_key)
                keys.append(m.guide_key)
        return keys
