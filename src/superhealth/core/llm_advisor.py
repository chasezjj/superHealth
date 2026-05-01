"""LLM 健康建议引擎基类：共享 Prompt 构建逻辑与指南库。

子类：
- ClaudeHealthAdvisor  (claude_advisor.py)  ← 原有逻辑
- BaichuanMedicalAdvisor (baichuan_advisor.py) ← 百川医疗大模型

设计：
- GUIDE_LIBRARY：权威指南摘要库（按指南 key 组织）
- build_system_prompt()：基于选中模型 + 健康画像构建 prompt
- advise()：抽象方法，各子类实现 API 调用
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from superhealth.core.assessment_models import AssessmentResult
    from superhealth.core.health_profile_builder import HealthProfile

log = logging.getLogger(__name__)


class BaseHealthAdvisor(ABC):
    """个性化健康建议引擎基类。"""

    # 权威指南摘要库（key → 指南要点，嵌入 System Prompt）
    GUIDE_LIBRARY: dict[str, str] = {
        "recovery": (
            "【恢复科学 (WHOOP/HRV)】HRV 低于基线时避免高强度训练；Body Battery <40 建议主动恢复；"
            "睡眠 <6h 隔天肌力下降约 20%；充足睡眠(7-9h)是恢复最关键因素。"
        ),
        "sleep": (
            "【睡眠卫生指南】理想睡眠 7-9h；睡前 2h 避免剧烈运动；屏幕蓝光影响褪黑素分泌；"
            "保持规律作息；深睡眠比例>20% 为优质。"
        ),
        "stress": (
            "【压力管理指南】Garmin 压力>35 为中等压力；长期压力增加皮质醇影响免疫和代谢；"
            "呼吸训练（4-7-8 法）10 分钟可显著降低 HRV 压力；冥想对慢性压力效果持久。"
        ),
        "cardiovascular": (
            "【AHA 心血管指南 2024】收缩压 120-129 为 Elevated（预防阶段）；≥130 为 Stage 1 高血压；"
            "每周 150 分钟中等强度有氧可降低血压 5-8 mmHg；"
            "HIIT 对降压效果优于持续有氧但需心率监控；钠摄入<2300 mg/天。"
        ),
        "metabolic": (
            "【高尿酸血症管理指南】运动时大量出汗可短暂升高尿酸，需充分补水(≥2.5L/天)；"
            "中等强度有氧优于高强度无氧（减少乳酸抑制尿酸排泄）；"
            "避免果糖饮料、内脏、啤酒；减重对长期尿酸控制有益。"
        ),
        "glaucoma": (
            "【青光眼患者运动注意事项】避免 Valsalva 动作（憋气/用力），会升高眼压；"
            "避免完全倒立（头低脚高超过心脏水平）；"
            "有氧运动(慢跑/游泳/骑车)可降低眼压；器械力量训练需保持呼吸节律；"
            "高重量/低次数训练比低重量/高次数风险更高。"
        ),
        "lipid": (
            "【血脂异常运动干预指南】每周 3-5 次中等强度有氧（30-60 分钟）可降 LDL 5-10%；"
            "力量训练辅助改善 HDL；omega-3 脂肪酸协同效果；"
            "达到运动量的关键是坚持，而非单次强度。"
        ),
        "body_composition": (
            "【体成分管理指南】增肌：每周 3 次力量训练，蛋白质摄入 1.6-2.2 g/kg/天；"
            "减脂：热量赤字 200-500 kcal/天 + 有氧运动；"
            "BMI<18.5 偏瘦：优先力量训练增肌，避免过度有氧；"
            "隐性肥胖(BMI正常但体脂高)：力量+有氧结合，控制精制碳水。"
        ),
        "genetic_risk": (
            "【肿瘤易感基因风险干预】GSTM1/GSTT1 缺失型会降低解毒酶活性，增加化学致癌物暴露风险；"
            "干预措施：戒烟/远离二手烟、减少烧烤食品、增加十字花科蔬菜(提升其他解毒途径)；"
            "有氧运动可提升细胞抗氧化能力；定期体检早发现早治疗比基因型更重要。"
        ),
    }

    def build_system_prompt(
        self,
        guide_keys: list[str],
        profile: "HealthProfile",
        assessment_results: list["AssessmentResult"] = None,
    ) -> str:
        """动态构建 System Prompt。"""
        parts = []

        # 1. 基础角色
        parts.append(
            "你是一位专业的个人健康顾问，具备运动科学、预防医学和慢病管理专业知识。"
            "你的建议基于循证医学，同时考虑用户的个体健康画像和当日生理状态。"
            "回复使用中文，建议具体可执行，避免过于保守或泛泛而谈。"
        )

        # 2. 选中的权威指南
        if guide_keys:
            parts.append("\n## 适用指南（请遵循以下权威指南原则）\n")
            for key in guide_keys:
                guide = self.GUIDE_LIBRARY.get(key)
                if guide:
                    parts.append(guide)

        # 3. 用户健康画像
        parts.append("\n## 用户健康画像（系统自动识别）\n")

        if profile.conditions:
            cond_labels = {
                "hyperuricemia": "高尿酸血症",
                "glaucoma": "青光眼",
            }
            conditions_str = "、".join(cond_labels.get(c, c) for c in profile.conditions)
            parts.append(f"**当前诊断**：{conditions_str}（用药控制中）")

        if profile.history_conditions:
            hist_labels = {
                "dyslipidemia_history": "血脂异常（曾干预，现运动控制）",
                "kidney_stone_history": "肾结石（随访中）",
            }
            hist_str = "、".join(hist_labels.get(c, c) for c in profile.history_conditions)
            parts.append(f"**历史状况**：{hist_str}")

        if profile.active_medications:
            meds_lines = []
            for m in profile.active_medications:
                med_str = m["name"]
                if m.get("dosage"):
                    med_str += f"（{m['dosage']}）"
                if m.get("notes"):
                    med_str += f"：{m['notes']}"
                meds_lines.append(med_str)
            parts.append("**当前用药**：" + "；".join(meds_lines))

        if profile.genetic_markers:
            genes_str = ", ".join(profile.genetic_markers.keys())
            parts.append(f"**基因特征**：肿瘤易感基因变异（{genes_str}），生活方式干预可降低风险")

        if profile.risk_factors:
            risk_labels = {
                "bp_trending_up": "血压上升趋势",
                "bp_elevated": "血压偏高",
                "ldl_borderline": "LDL 轻度偏高",
            }
            risks_str = "、".join(risk_labels.get(r, r) for r in profile.risk_factors)
            parts.append(f"**当前风险**：{risks_str}")

        if profile.exercise_contraindications:
            contra_labels = {
                "valsalva_maneuver": "憋气/用力动作",
                "inverted_positions": "倒立体位",
                "maximal_isometric": "最大等长收缩",
            }
            contra_str = "、".join(
                contra_labels.get(c, c) for c in profile.exercise_contraindications
            )
            parts.append(f"**运动禁忌**：{contra_str}")

        bc = profile.body_composition
        if bc.bmi is not None:
            parts.append(
                f"**体成分**：BMI {bc.bmi}（{bc.bmi_status}）"
                + (f"，体脂率 {bc.body_fat_pct:.1f}%" if bc.body_fat_pct else "")
                + f"，目标：{bc.assessment}"
            )
            # BMI 接近偏瘦下限时，强制力量训练不可被 P1 目标覆盖
            if bc.bmi < 19:
                parts.append(
                    "**体成分强制规则**：用户BMI<19接近偏瘦下限，运动处方每周必须安排至少2次力量训练"
                    "（轻量或中等强度，每组12-15次，注意呼吸节律避免憋气），不可连续多日仅推荐有氧运动。"
                    "规律力量训练可提升基础代谢、辅助血压控制，与P1降压目标不冲突。"
                )

        # 工作日/休息日模式
        if profile.workday_patterns:
            wp = profile.workday_patterns
            parts.append("\n**工作日/休息日模式**：")
            if wp.get("bp_weekday_avg") and wp.get("bp_weekend_avg"):
                diff = wp["bp_weekday_avg"] - wp["bp_weekend_avg"]
                parts.append(
                    f"  - 血压：工作日 {wp['bp_weekday_avg']:.0f} vs 周末 {wp['bp_weekend_avg']:.0f} mmHg（差值 {diff:+.0f}）"
                )
            if wp.get("stress_weekday_avg") and wp.get("stress_weekend_avg"):
                diff = wp["stress_weekday_avg"] - wp["stress_weekend_avg"]
                parts.append(
                    f"  - 压力：工作日 {wp['stress_weekday_avg']:.0f} vs 周末 {wp['stress_weekend_avg']:.0f}（差值 {diff:+.0f}）"
                )
            if wp.get("is_weekday_elevated"):
                parts.append("  - ⚠️ 存在工作日综合征（工作日血压/压力显著高于周末）")

        # 学习到的偏好
        if profile.learned_preferences:
            prefs_lines = []
            for k, v in profile.learned_preferences.items():
                prefs_lines.append(f"  - {k}: {v}")
            parts.append("\n**历史偏好**（基于反馈学习）：\n" + "\n".join(prefs_lines))

        # 活跃阶段性目标
        if profile.active_goals:
            parts.append("\n### 当前阶段性目标（所有建议须优先服务于以下目标）\n")
            priority_labels = {1: "P1 主要", 2: "P2 次要", 3: "P3 辅助"}
            direction_labels = {"decrease": "降低", "increase": "提升", "stabilize": "稳定"}
            for g in profile.active_goals:
                p_label = priority_labels.get(g.get("priority"), f"P{g.get('priority')}")
                d_label = direction_labels.get(g.get("direction"), g.get("direction"))
                metric_label = g.get("metric_label", g.get("metric_key"))
                line = f"- **{p_label}**：{g['name']}（{metric_label}，方向：{d_label}"
                if g.get("baseline_value") is not None:
                    line += f"，基线：{g['baseline_value']:.1f}"
                if g.get("target_value") is not None:
                    line += f"，目标：{g['target_value']:.1f}"
                if g.get("current_value") is not None:
                    line += f"，当前：{g['current_value']:.1f}"
                if g.get("progress_pct") is not None:
                    line += f"，进度：{g['progress_pct']:.0f}%"
                line += "）"
                parts.append(line)
            parts.append(
                "\n**指令**：所有建议须优先服务于 P1 目标，其次 P2，并标注每条建议对应哪个目标。"
            )

        # 4. 今日评估摘要
        if assessment_results:
            parts.append("\n## 今日评估结果摘要\n")
            for r in assessment_results:
                line = f"- {r.label}：{r.score}/100（{r.status}）—— {r.summary}"
                if r.details:
                    line += "；" + "；".join(r.details)
                parts.append(line)

        # 5. 活跃实验约束
        active_exp = None
        for k, v in (profile.learned_preferences or {}).items():
            if k.startswith("active_exp_"):
                active_exp = v
                break
        if active_exp:
            parts.append(
                f"\n## 当前实验约束（最高优先级，必须遵守）\n"
                f"正在进行 N-of-1 干预实验：{active_exp}\n\n"
                "**实验期间规则**：\n"
                "1. 今日运动处方必须严格遵循上述实验干预方案，不可替换或省略\n"
                "2. 实验关联的 Goal 为本周期唯一优先目标，其他 Goal 暂缓服务\n"
                "3. 不可在实验干预之外额外追加高强度运动（避免干扰实验变量）\n"
                "4. 如需补充恢复类活动（拉伸/冥想/散步），强度须控制在 1-3 级且时长不超过 15 分钟\n"
            )

        return "\n".join(parts)

    def build_user_prompt(
        self,
        daily_data: dict,
        reference_date: str,
        weather_data: dict = None,
        recent_exercises: list = None,
        recent_feedback: list = None,
        is_weekday: bool = None,
        calendar_summary: dict = None,
    ) -> str:
        """构建用户 prompt（当日数据摘要 + 近7天运动历史 + 近期反馈 + 天气 + 建议请求）。"""
        from datetime import datetime

        lines = [f"今日日期：{reference_date}"]

        # 添加工作日/休息日信息
        dt = datetime.strptime(reference_date, "%Y-%m-%d")
        weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        weekday_name = weekday_names[dt.weekday()]
        day_type = "工作日" if is_weekday else "休息日"
        lines.append(f"今日是{weekday_name}（{day_type}）")
        if is_weekday:
            lines.append("【工作日模式】工作日血压/压力通常较高，建议优先选择减压降压类运动")
        else:
            lines.append("【休息日模式】休息日恢复状态更好，可适当提升运动强度")

        sleep_score = daily_data.get("sleep_score")
        if sleep_score:
            lines.append(f"睡眠评分：{sleep_score}")

        hrv = daily_data.get("hrv_avg")
        hrv_status = daily_data.get("hrv_status")
        if hrv:
            lines.append(f"HRV：{hrv:.0f} ms（{hrv_status or 'N/A'}）")

        bb = daily_data.get("body_battery_wake")
        if bb:
            lines.append(f"Body Battery（起床时）：{bb:.0f}")

        avg_stress = daily_data.get("avg_stress")
        if avg_stress:
            lines.append(f"平均压力：{avg_stress:.0f}")

        rhr = daily_data.get("resting_hr")
        if rhr:
            lines.append(f"静息心率：{rhr:.0f} bpm")

        # 近7天运动历史
        if recent_exercises:
            lines.append("")
            # 多样性统计
            from collections import Counter

            type_counts = Counter(
                ex.get("type_key") or ex.get("name") or "未知" for ex in recent_exercises
            )
            top_types = type_counts.most_common(3)
            type_summary = "、".join(f"{t} {c}次" for t, c in top_types)
            lines.append(f"【近7天运动统计】{type_summary}")
            # 连续同类型检测（按日期去重，取每天首个运动）
            seen_dates = set()
            daily_types = []
            for ex in recent_exercises:
                if ex["date"] not in seen_dates:
                    seen_dates.add(ex["date"])
                    daily_types.append(ex.get("type_key") or ex.get("name") or "未知")
            streak = 1
            for i in range(1, len(daily_types)):
                if daily_types[i] == daily_types[0]:
                    streak += 1
                else:
                    break
            if streak >= 2:
                lines.append(
                    f"【多样性提示】已连续 {streak} 天进行 {daily_types[0]}，今日建议换类型或安排恢复"
                )
            lines.append("【近7天运动记录】")
            for ex in recent_exercises:
                parts = [ex["date"], ex.get("name") or ex.get("type_key") or "未知"]
                if ex.get("duration_min"):
                    parts.append(f"{ex['duration_min']}分钟")
                if ex.get("distance_km"):
                    parts.append(f"{ex['distance_km']}km")
                if ex.get("avg_hr"):
                    parts.append(f"均心率{ex['avg_hr']:.0f}bpm")
                if ex.get("calories"):
                    parts.append(f"{ex['calories']:.0f}kcal")
                lines.append("  " + "  ".join(parts))
        else:
            lines.append("")
            lines.append("【近7天运动记录】无运动数据")

        # 近期用户反馈（DOMS、疲劳、不适等）
        if recent_feedback:
            lines.append("")
            lines.append("【近7天用户反馈】（请据此判断恢复状态，如 DOMS 通常需要 48-72h）")
            _TYPE_LABEL = {"exercise": "运动处方", "recovery": "主动恢复", "rest": "完全休息"}
            for fb in recent_feedback:
                label = _TYPE_LABEL.get(
                    fb.get("recommendation_type", ""), fb.get("recommendation_type", "")
                )
                lines.append(f"  {fb['date']} [{label}] 反馈：{fb['user_feedback']}")

        # 天气信息
        if weather_data:
            lines.append("")
            lines.append("【当日天气】")
            cond = weather_data.get("condition", "未知")
            temp_max = weather_data.get("temp_max")
            temp_min = weather_data.get("temp_min")
            temp_now = weather_data.get("temperature")
            wind = weather_data.get("wind_scale")
            aqi = weather_data.get("aqi")
            outdoor_ok = weather_data.get("outdoor_ok", True)
            weather_line = f"天气（白天预报）：{cond}"
            if temp_max is not None and temp_min is not None:
                weather_line += f"  全天气温{temp_min:.0f}~{temp_max:.0f}°C"
            elif temp_now is not None:
                weather_line += f"  当前{temp_now:.0f}°C"
            if wind is not None:
                weather_line += f"  风力{wind}级（白天预报）"
            if aqi is not None:
                weather_line += f"  AQI={aqi:.0f}（全天均值）"
            lines.append(weather_line)
            if not outdoor_ok:
                lines.append("⚠️ 今日不适合户外运动（降水/大风/空气污染），请推荐室内替代方案")

        # 日程信息
        if calendar_summary:
            lines.append("")
            lines.append("【今日日程】")
            event_count = calendar_summary.get("event_count", 0)
            total_min = calendar_summary.get("total_meeting_min", 0)
            busy_level = calendar_summary.get("busy_level", "low")
            level_labels = {"low": "低", "medium": "中", "high": "高"}
            lines.append(f"- 共 {event_count} 个日程，总计 {total_min // 60}h{total_min % 60}m")
            lines.append(f"- 忙碌等级：{level_labels.get(busy_level, busy_level)}")
            busiest = calendar_summary.get("busiest_period")
            if busiest:
                lines.append(f"- 最忙时段：{busiest}")
            back_to_back = calendar_summary.get("back_to_back_count", 0)
            if back_to_back > 0:
                lines.append(f"- 连续会议：{back_to_back} 组")
            first_start = calendar_summary.get("first_event_start")
            last_end = calendar_summary.get("last_event_end")
            if first_start and last_end:
                lines.append(f"- 日程范围：{first_start} ~ {last_end}")
            if calendar_summary.get("has_all_day"):
                lines.append("- 包含全天事件")
            if busy_level == "high":
                lines.append(
                    "⚠️ 今日日程非常繁忙，建议优先推荐短时高效运动（如午休快走/20分钟HIIT），或将运动安排至晚间"
                )
            elif busy_level == "medium":
                lines.append("📌 今日日程较满，建议利用会议间隙进行短时活动，或安排中等强度运动")

        lines.append(self._json_request_prompt())

        return "\n".join(lines)

    def _json_request_prompt(self) -> str:
        """子类可覆盖以定制 JSON schema 请求。"""
        return (
            "\n请根据以上今日状态和我的健康画像，生成今日个性化健康建议。"
            "请严格按照以下 JSON 格式回复（不要输出 JSON 以外的内容）：\n"
            "{\n"
            '  "summary": "今日状态一句话总结（30字内）",\n'
            '  "recommendation_type": "exercise 或 recovery 或 rest",\n'
            "  // exercise=正常运动训练(强度≥4), recovery=主动恢复(强度≤3,用于DOMS/疲劳/HRV偏低), rest=完全休息(极度疲劳/生病)\n"
            '  "exercise": {\n'
            '    "type": "运动类型",\n'
            '    "intensity": "强度等级（1-10）",\n'
            '    "duration": "建议时长",\n'
            '    "specific": "具体动作建议",\n'
            '    "reasoning": "建议逻辑（基于哪些模型/画像）"\n'
            "  },\n"
            '  "recovery": {"needed": true/false, "actions": ["建议1", "建议2"]},\n'
            '  "lifestyle": ["生活方式建议1", "建议2", "建议3"],\n'
            '  "risk_alerts": ["风险提醒1"]\n'
            "}"
        )

    @abstractmethod
    def _is_config_complete(self) -> bool:
        """子类检查 API 配置是否完整。"""

    @abstractmethod
    def _call_api(self, system_prompt: str, user_prompt: str) -> str:
        """子类实现具体的 LLM API 调用，返回原始文本。"""

    def advise(
        self,
        daily_data: dict,
        profile: "HealthProfile",
        guide_keys: list[str],
        assessment_results: list["AssessmentResult"] = None,
        reference_date: str = None,
        weather_data: dict = None,
        recent_exercises: list = None,
        recent_feedback: list = None,
        calendar_summary: dict = None,
    ) -> dict:
        """调用 LLM API 生成个性化建议，返回解析后的 JSON dict。"""
        if reference_date is None:
            reference_date = date.today().isoformat()

        dt = date.fromisoformat(reference_date)
        is_weekday = dt.weekday() < 5

        if not self._is_config_complete():
            log.warning("%s API key 未配置，使用降级建议", self.__class__.__name__)
            return self._fallback_advice(daily_data, profile, weather_data, is_weekday)

        system_prompt = self.build_system_prompt(guide_keys, profile, assessment_results)
        user_prompt = self.build_user_prompt(
            daily_data,
            reference_date,
            weather_data,
            recent_exercises,
            recent_feedback,
            is_weekday,
            calendar_summary,
        )

        max_retries = 3
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                raw = self._call_api(system_prompt, user_prompt)
                return self._extract_json(raw)
            except json.JSONDecodeError as e:
                last_error = e
                log.warning(
                    "%s 返回无效 JSON（第%d/%d次）: %s",
                    self.__class__.__name__,
                    attempt,
                    max_retries,
                    e,
                )
            except Exception as e:
                last_error = e
                log.warning(
                    "%s API 调用失败（第%d/%d次）: %s",
                    self.__class__.__name__,
                    attempt,
                    max_retries,
                    e,
                )

            if attempt < max_retries:
                time.sleep(3)

        log.error(
            "%s 所有 %d 次尝试均失败，降级: %s", self.__class__.__name__, max_retries, last_error
        )
        return self._fallback_advice(daily_data, profile, weather_data, is_weekday)

    def _fallback_advice(
        self,
        daily_data: dict,
        profile: "HealthProfile",
        weather_data: dict = None,
        is_weekday: bool = None,
    ) -> dict:
        """API 不可用时的规则降级建议。"""
        from datetime import date

        if is_weekday is None:
            is_weekday = date.today().weekday() < 5

        bb = daily_data.get("body_battery_wake") or 60
        sleep_score = daily_data.get("sleep_score") or 70
        hrv_status = (daily_data.get("hrv_status") or "").upper()
        outdoor_ok = weather_data.get("outdoor_ok", True) if weather_data else True

        # 工作日模式：降低一档强度，优先减压
        weekday_adjustment = is_weekday and "weekday_elevated" in profile.risk_factors

        if bb >= 70 and sleep_score >= 75 and hrv_status != "LOW":
            if weekday_adjustment:
                intensity = "5-6"
                ex_type = "中等强度有氧（快走/骑车）" if outdoor_ok else "室内有氧/瑜伽"
                duration = "30-40分钟"
            else:
                intensity = "6-7"
                ex_type = (
                    "中等强度有氧 + 力量训练"
                    if outdoor_ok
                    else "室内有氧（跑步机/动感单车）+ 力量训练"
                )
                duration = "40-50分钟"
        elif bb >= 50 and sleep_score >= 60:
            if weekday_adjustment:
                intensity = "3-4"
                ex_type = "轻度有氧（散步/慢走）+ 拉伸" if outdoor_ok else "室内拉伸/冥想"
                duration = "25-35分钟"
            else:
                intensity = "4-5"
                ex_type = "中等强度有氧（快走/骑车）" if outdoor_ok else "室内有氧（快走/动感单车）"
                duration = "30-40分钟"
        else:
            intensity = "2-3"
            ex_type = "轻度活动（散步/拉伸）"
            duration = "20-30分钟"

        lifestyle = ["每日饮水 2.5L（高尿酸管理）", "每2小时用眼休息（青光眼管理）"]
        if "bp_trending_up" in profile.risk_factors or "bp_elevated" in profile.risk_factors:
            lifestyle.append("监测并记录血压")
        if "weekday_elevated" in profile.risk_factors and is_weekday:
            lifestyle.append("工作日注意减压，建议午休10分钟深呼吸")
            lifestyle.append("工作间隙每小时起身活动2分钟")

        if "valsalva_maneuver" in profile.exercise_contraindications:
            lifestyle.append("运动时保持正常呼吸，避免憋气")

        # 降级模式下保留学习偏好与目标的上下文
        learned = profile.learned_preferences
        reasoning = "基于 Body Battery / 睡眠 / HRV 规则评估（LLM 降级模式）"
        if learned.get("preferred_type"):
            reasoning += f"；历史偏好：{learned['preferred_type']}"
        avoid_types = [k.replace("avoid_", "") for k in learned if k.startswith("avoid_")]
        if avoid_types:
            lifestyle.append(f"历史学习显示应避免：{'、'.join(avoid_types)}")
        if learned.get("optimal_time_slot"):
            lifestyle.append(f"历史偏好时段：{learned['optimal_time_slot']}")
        if learned.get("optimal_hr_zone"):
            lifestyle.append(f"历史偏好心率区间：{learned['optimal_hr_zone']}")

        for goal in profile.active_goals:
            gname = goal.get("name", "")
            if gname:
                lifestyle.append(f"当前目标：{gname}")
                break

        return {
            "summary": f"Body Battery {bb:.0f}，建议{ex_type}",
            "exercise": {
                "type": ex_type,
                "intensity": intensity,
                "duration": duration,
                "specific": "注意保持呼吸节律，避免憋气动作",
                "reasoning": reasoning,
            },
            "recovery": {
                "needed": bb < 40 or hrv_status == "LOW",
                "actions": ["优先保证睡眠质量", "适当拉伸放松"],
            },
            "lifestyle": lifestyle,
            "risk_alerts": [],
        }

    @staticmethod
    def _extract_json(raw: str) -> dict:
        """从 LLM 返回文本中提取 JSON，兼容 ```json``` 包裹和 JSON 后有多余文本。"""
        import re

        # 优先尝试 ```json``` 代码块
        if "```" in raw:
            m = re.search(r"```(?:json)?\s*([\s\S]+?)```", raw)
            if m:
                return json.loads(m.group(1).strip())

        # 找第一个 { 到其配对的 } 之间的完整 JSON 对象
        start = raw.find("{")
        if start != -1:
            depth = 0
            for i, ch in enumerate(raw[start:], start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return json.loads(raw[start : i + 1])

        return json.loads(raw)
