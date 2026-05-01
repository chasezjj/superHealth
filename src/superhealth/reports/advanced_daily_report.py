"""高级日报生成器（Layer 4）。

整合健康画像（Layer 2）+ LLM 建议（Layer 3）生成包含以下结构的高级日报：
1. 执行摘要（LLM 生成）
2. 系统发现的健康画像
3. 今日动态评估雷达
4. LLM 个性化建议
5. 数据详情
6. 趋势洞察

与 daily_report.py 的关系：
- 复用其数据加载逻辑（load_garmin_data / load_vitals_stats）
- 回退：若 LLM 不可用，建议区使用规则降级
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from superhealth import database as db
from superhealth.collectors.outlook_collector import fetch_calendar
from superhealth.collectors.weather_collector import fetch_weather
from superhealth.config import load as load_config
from superhealth.core.assessment_models import run_assessments
from superhealth.core.baichuan_advisor import BaichuanMedicalAdvisor
from superhealth.core.claude_advisor import ClaudeHealthAdvisor
from superhealth.core.health_profile_builder import HealthProfileBuilder
from superhealth.core.model_selector import ModelSelector
from superhealth.reminders.reminder_notifier import build_report_section as _build_reminder_section
from superhealth.reports.daily_report import DailyReportGenerator

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent.parent / "health.db"
DATA_DIR = Path(__file__).parent.parent.parent.parent / "activity-data"


class AdvancedDailyReportGenerator:
    """高级日报生成器，整合 Layer 2-4 能力。"""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.base_generator = DailyReportGenerator(db_path)
        self.profile_builder = HealthProfileBuilder(db_path)
        self.model_selector = ModelSelector()

        cfg = load_config()
        self.advisor_mode = cfg.advisor.mode  # claude_only | baichuan_only | both
        self.claude_advisor = ClaudeHealthAdvisor()
        self.baichuan_advisor = BaichuanMedicalAdvisor()

    def _get_conn(self):
        return db.get_conn(self.db_path)

    def _load_vitals_today(self, day_str: str) -> dict:
        """获取当日最新体征（血压/体重）。"""
        with self._get_conn() as conn:
            row = db.query_vitals_by_date(conn, day_str)
            return row or {}

    def _load_recent_exercises(self, day_str: str, days: int = 7) -> list[dict]:
        """查询 day_str 之前 days 天的运动记录（含当天）。"""
        from datetime import timedelta

        end = day_str
        start = (
            datetime.strptime(day_str, "%Y-%m-%d").date() - timedelta(days=days - 1)
        ).isoformat()
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT date, name, type_key, duration_seconds,
                       distance_meters, avg_hr, max_hr, calories
                FROM exercises
                WHERE date BETWEEN ? AND ?
                ORDER BY date DESC, start_time
                """,
                (start, end),
            ).fetchall()
        result = []
        for r in rows:
            result.append(
                {
                    "date": r["date"],
                    "name": r["name"],
                    "type_key": r["type_key"],
                    "duration_min": round(r["duration_seconds"] / 60)
                    if r["duration_seconds"]
                    else None,
                    "distance_km": round(r["distance_meters"] / 1000, 1)
                    if r["distance_meters"]
                    else None,
                    "avg_hr": r["avg_hr"],
                    "max_hr": r["max_hr"],
                    "calories": r["calories"],
                }
            )
        return result

    def _load_recent_feedback(self, day_str: str, days: int = 7) -> list[dict]:
        """查询 day_str 之前 days 天（不含当天）的用户反馈和执行记录。

        返回最近 N 天的 actual_action 和 user_feedback，用于 LLM 了解用户实际执行情况。
        """
        end = (datetime.strptime(day_str, "%Y-%m-%d").date() - timedelta(days=1)).isoformat()
        start = (datetime.strptime(day_str, "%Y-%m-%d").date() - timedelta(days=days)).isoformat()
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT date, recommendation_type, recommendation_content,
                       actual_action, user_feedback, compliance
                FROM recommendation_feedback
                WHERE date BETWEEN ? AND ?
                  AND recommendation_type = 'exercise'
                ORDER BY date DESC
                """,
                (start, end),
            ).fetchall()
        result = []
        for r in rows:
            result.append(
                {
                    "date": r["date"],
                    "recommendation_type": r["recommendation_type"],
                    "recommendation_content": r["recommendation_content"],
                    "actual_action": r["actual_action"],
                    "user_feedback": r["user_feedback"],
                    "compliance": r["compliance"],
                }
            )
        return result

    @staticmethod
    def _merge_advice(claude: dict, baichuan: dict) -> dict:
        """合并 Claude（运动/恢复）与百川（医学摘要/风险）的建议。"""
        import re

        def _keywords(text: str) -> set:
            """提取单个汉字作为关键词（对数字/英文分隔的文本更鲁棒）。"""
            return set(re.findall(r"[\u4e00-\u9fff]", text))

        # 话题关键词：命中同一组任意词 → 同话题，直接视为重复
        _TOPIC_GROUPS = [
            {"补水", "饮水", "水分", "水量", "喝水"},
            {"十字花科"},
            {"尿酸", "高尿酸"},
            {"眼压", "青光眼"},
            {"血压"},
            {"睡眠", "入睡", "作息"},
        ]

        def _same_topic(a: str, b: str) -> bool:
            for group in _TOPIC_GROUPS:
                if any(w in a for w in group) and any(w in b for w in group):
                    return True
            return False

        def _dedup(items: list) -> list:
            """去重：话题关键词匹配 OR 汉字 Jaccard > 0.25，保留更详细的条目。"""
            result: list[str] = []
            for item in items:
                kw = _keywords(item)
                is_dup = False
                for i, existing in enumerate(result):
                    existing_kw = _keywords(existing)
                    # 话题关键词命中
                    topic_match = _same_topic(item, existing)
                    # 字符相似度
                    sim = 0.0
                    if existing_kw and kw:
                        union = len(kw | existing_kw)
                        sim = len(kw & existing_kw) / union if union else 0
                    if topic_match or sim > 0.25:
                        if len(item) > len(existing):
                            result[i] = item  # 保留更详细的
                        is_dup = True
                        break
                if not is_dup:
                    result.append(item)
            return result

        # 风险提醒：百川的 risk_alerts + medical_advice 合并，去重，最多保留3条
        combined_risk = baichuan.get("risk_alerts", []) + baichuan.get("medical_advice", [])
        combined_risk = _dedup(combined_risk)[:3]

        return {
            "summary": claude.get("summary") or baichuan.get("summary", ""),
            "exercise": claude.get("exercise", {}),
            "recovery": claude.get("recovery", {}),
            "lifestyle": claude.get("lifestyle", []),
            "risk_alerts": combined_risk,
        }

    def generate_report(self, day_str: str, save: bool = True, test_mode: bool = False) -> str:
        """生成高级日报。"""
        lines = []

        # ── 数据加载 ──
        garmin = self.base_generator.load_garmin_data(day_str)
        if not garmin:
            return f"# {day_str} 高级健康日报\n\n未找到 Garmin 数据。"

        vitals_stats = self.base_generator.load_vitals_stats(day_str)
        vitals_today = self._load_vitals_today(day_str)

        # ── Layer 1: 天气采集 ──
        weather_obj = fetch_weather(target_date=day_str, db_path=self.db_path)
        weather_data = weather_obj.to_dict() if weather_obj else None

        # ── Layer 1: 日历采集 ──
        _cal_obj = fetch_calendar(day_str, db_path=self.db_path)
        calendar_summary: dict | None = _cal_obj.to_dict() if _cal_obj else None

        # ── Layer 2: 健康画像 + 模型选择 ──
        profile = self.profile_builder.build(day_str)
        selected_models = self.model_selector.select(
            profile, vitals_today, goals=profile.active_goals
        )
        model_names = self.model_selector.get_model_names(selected_models)
        guide_keys = self.model_selector.get_guide_keys(selected_models)

        # ── Layer 2: 运行评估模型 ──
        assessment_results = run_assessments(model_names, garmin, vitals_today, profile)
        recovery_result = next(
            (r for r in assessment_results if r.model_name == "RecoveryModel"), None
        )

        # ── Layer 3: LLM 建议 ──
        recent_exercises = self._load_recent_exercises(day_str)
        recent_feedback = self._load_recent_feedback(day_str, days=7)
        advise_kwargs: dict[str, Any] = dict(
            daily_data=garmin,
            profile=profile,
            guide_keys=guide_keys,
            assessment_results=assessment_results,
            reference_date=day_str,
            weather_data=weather_data,
            recent_exercises=recent_exercises,
            recent_feedback=recent_feedback,
            calendar_summary=calendar_summary,
        )

        if self.advisor_mode == "baichuan_only":
            llm_advice = self.baichuan_advisor.advise(**advise_kwargs)
        elif self.advisor_mode == "both":
            # 并行调用两个 LLM，避免串行等待（节省 50% 耗时）
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                f_claude = pool.submit(self.claude_advisor.advise, **advise_kwargs)
                f_baichuan = pool.submit(self.baichuan_advisor.advise, **advise_kwargs)
                claude_result = f_claude.result()
                baichuan_result = f_baichuan.result()
            llm_advice = self._merge_advice(claude_result, baichuan_result)
        else:  # claude_only（默认）
            llm_advice = self.claude_advisor.advise(**advise_kwargs)

        # ── 预写 recommendation_feedback（Phase 1：存储 LLM 推荐内容）──
        if not test_mode:
            report_id = f"{day_str}-advanced-daily-report"
            exercise_advice = llm_advice.get("exercise", {})
            recommendation_content = (
                exercise_advice.get("specific") or exercise_advice.get("type") or ""
            )
            # 获取 LLM 判断的建议类型（exercise/recovery/rest），默认 exercise
            rec_type = llm_advice.get("recommendation_type", "exercise")
            if rec_type not in ("exercise", "recovery", "rest"):
                rec_type = "exercise"
            try:
                with self._get_conn() as conn:
                    existing = conn.execute(
                        "SELECT id, user_feedback FROM recommendation_feedback "
                        "WHERE date = ? AND recommendation_type = ? "
                        "ORDER BY id DESC LIMIT 1",
                        (day_str, rec_type),
                    ).fetchone()
                    if not existing:
                        db.insert_recommendation_feedback(
                            conn,
                            date=day_str,
                            report_id=report_id,
                            recommendation_type=rec_type,
                            recommendation_content=recommendation_content,
                            compliance=None,
                            actual_action=None,
                            tracked_metrics=None,
                        )
                    elif existing["user_feedback"] is None:
                        # 用户尚未提交反馈，用最新 LLM 结果覆盖 recommendation_content
                        conn.execute(
                            "UPDATE recommendation_feedback "
                            "SET recommendation_content = ?, report_id = ? "
                            "WHERE id = ?",
                            (recommendation_content, report_id, existing["id"]),
                        )
            except Exception as e:
                log.warning("预写 recommendation_feedback 失败: %s", e)

        # ── Layer 4: 报告生成 ──

        # === 标题 ===
        lines.append(f"# {day_str} 高级健康日报")
        lines.append("")

        # === 执行摘要 ===
        lines.append("## 执行摘要")
        lines.append("")
        summary = llm_advice.get("summary", "")
        if summary:
            lines.append(f"> {summary}")
        else:
            if recovery_result:
                lines.append(
                    f"> 恢复{recovery_result.status}（{recovery_result.score}/100），{recovery_result.summary}"
                )
        lines.append("")
        lines.append("")

        # === 天气信息 ===
        if weather_data:
            cond = weather_data.get("condition", "")
            temp_max = weather_data.get("temp_max")
            temp_min = weather_data.get("temp_min")
            temp_now = weather_data.get("temperature")
            wind = weather_data.get("wind_scale")
            aqi = weather_data.get("aqi")
            outdoor_ok = weather_data.get("outdoor_ok", True)
            weather_parts = [cond]
            # 优先显示全天温度区间，fallback 实时温度
            if temp_max is not None and temp_min is not None:
                weather_parts.append(f"{temp_min:.0f}~{temp_max:.0f}°C")
            elif temp_now is not None:
                weather_parts.append(f"{temp_now:.0f}°C（实时）")
            if wind is not None:
                weather_parts.append(f"风力{wind}级")
            if aqi is not None:
                weather_parts.append(f"AQI={aqi:.0f}（全天）")
            outdoor_tag = "✅ 适合户外" if outdoor_ok else "⚠️ 不适合户外"
            lines.append(f"天气：{' | '.join(weather_parts)}  {outdoor_tag}")
            lines.append("")

        # === 今日日程 ===
        if calendar_summary:
            cs = calendar_summary
            event_count = cs.get("event_count", 0)
            total_min = cs.get("total_meeting_min", 0)
            busy_level = cs.get("busy_level", "low")
            level_labels = {"low": "低", "medium": "中", "high": "高"}
            busy_emoji = {"low": "🟢", "medium": "🟡", "high": "🔴"}
            cal_parts = [
                f"{busy_emoji.get(busy_level, '')} 忙碌等级：{level_labels.get(busy_level, busy_level)}"
            ]
            if event_count > 0:
                cal_parts.append(f"{event_count} 个日程")
                cal_parts.append(f"总计 {total_min // 60}h{total_min % 60}m")
            busiest = cs.get("busiest_period")
            if busiest:
                cal_parts.append(f"最忙 {busiest}")
            back_to_back = cs.get("back_to_back_count", 0)
            if back_to_back > 0:
                cal_parts.append(f"连续会议 {back_to_back} 组")
            lines.append(f"日程：{' | '.join(cal_parts)}")
            lines.append("")

        # === 今日动态评估雷达 ===
        lines.append("## 今日评估雷达（动态）")
        lines.append("")
        for result in assessment_results:
            model_label = next(
                (m.reason for m in selected_models if m.name == result.model_name), ""
            )
            selected_tag = (
                f" [选中：{model_label}]" if model_label and model_label not in ("所有人",) else ""
            )
            lines.append(
                f"- **{result.label}**: {result.score}/100（{result.status}）{selected_tag}"
            )
        lines.append("")

        # === LLM 个性化建议 ===
        lines.append("## 今日个性化建议")

        # 运动处方
        exercise = llm_advice.get("exercise", {})
        if exercise:
            lines.append("### 运动处方")
            lines.append("")
            ex_type = exercise.get("type", "N/A")
            ex_intensity = exercise.get("intensity", "N/A")
            ex_duration = exercise.get("duration", "N/A")
            ex_specific = exercise.get("specific", "")
            ex_reason = exercise.get("reasoning", "")

            lines.append(f"- **类型**：{ex_type}")
            lines.append(f"- **强度**：{ex_intensity}/10")
            lines.append(f"- **时长**：{ex_duration}")
            if ex_specific:
                # 多行内容需缩进，避免在 Markdown 列表中断裂为顶级段落
                indented = "\n".join(
                    line if i == 0 else f"  {line}"
                    for i, line in enumerate(ex_specific.splitlines())
                )
                lines.append(f"- **具体**：{indented}")
            if ex_reason:
                lines.append(f"- **依据**：{ex_reason}")
            lines.append("")

        # 恢复建议
        recovery_advice = llm_advice.get("recovery", {})
        if recovery_advice.get("needed"):
            lines.append("### 恢复建议")
            lines.append("")
            for action in recovery_advice.get("actions", []):
                lines.append(f"- {action}")
            lines.append("")

        # 生活方式建议
        lifestyle = llm_advice.get("lifestyle", [])
        if lifestyle:
            lines.append("### 生活方式")
            lines.append("")
            for item in lifestyle:
                lines.append(f"- {item}")
            lines.append("")

        # 风险提醒
        risk_alerts = llm_advice.get("risk_alerts", [])
        if risk_alerts:
            lines.append("### 风险提醒")
            lines.append("")
            for alert in risk_alerts:
                lines.append(f"- ⚠️ {alert}")
            lines.append("")

        # === 数据摘要（一行）===
        data_parts = []
        sleep_min = garmin.get("sleep_total_min")
        sleep_score_v = garmin.get("sleep_score")
        if sleep_min:
            data_parts.append(
                f"睡眠 {sleep_min // 60}h{sleep_min % 60}m/{sleep_score_v:.0f}分"
                if sleep_score_v
                else f"睡眠 {sleep_min // 60}h{sleep_min % 60}m"
            )
        hrv = garmin.get("hrv_avg")
        hrv_status_v = garmin.get("hrv_status")
        if hrv:
            data_parts.append(f"HRV {hrv:.0f}ms({hrv_status_v or 'N/A'})")
        rhr = garmin.get("resting_hr")
        if rhr:
            data_parts.append(f"静息心率 {rhr:.0f}bpm")
        bb = garmin.get("body_battery_highest") or garmin.get("body_battery_wake")
        if bb:
            data_parts.append(f"BB {bb:.0f}")
        if vitals_stats.latest_systolic and vitals_stats.latest_diastolic:
            data_parts.append(
                f"血压 {vitals_stats.latest_systolic}/{vitals_stats.latest_diastolic}mmHg"
            )
        if vitals_stats.latest_weight:
            data_parts.append(f"体重 {vitals_stats.latest_weight:.1f}kg")
        if data_parts:
            lines.append(f"数据：{' | '.join(data_parts)}")
        lines.append("")

        # === 就诊提醒 ===
        try:
            reminder_section = _build_reminder_section()
            if reminder_section:
                lines.append("")
                lines.append(reminder_section)
        except Exception as e:
            log.warning("就诊提醒板块加载失败: %s", e)

        report_text = "\n".join(lines)

        if save:
            suffix = "-test" if test_mode else ""
            output_path = DATA_DIR / "reports" / f"{day_str}-advanced-daily-report{suffix}.md"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(report_text, encoding="utf-8")
            log.info("已生成高级日报: %s", output_path)

        return report_text


def main():
    from superhealth.log_config import setup_logging

    setup_logging()

    ap = argparse.ArgumentParser(description="生成高级健康日报（Phase 4）")
    ap.add_argument("--date", type=str, help="日期 (YYYY-MM-DD)，默认今天")
    ap.add_argument("--no-save", action="store_true", help="不保存文件，仅打印")
    ap.add_argument(
        "--test-mode",
        action="store_true",
        help="测试模式：不写入 recommendation_feedback，保存到 -test.md",
    )
    args = ap.parse_args()

    day_str = args.date or date.today().isoformat()
    generator = AdvancedDailyReportGenerator()

    report = generator.generate_report(day_str, save=not args.no_save, test_mode=args.test_mode)
    print(report)


if __name__ == "__main__":
    main()
