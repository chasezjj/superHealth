"""测试 daily_report.py 的纯逻辑函数。"""
import json
from unittest.mock import MagicMock, patch

from superhealth.reports.advanced_daily_report import (
    AdvancedDailyReportGenerator,
    build_recommendation_feedback_content,
)
from superhealth.reports.daily_report import (
    DailyReportGenerator,
    RecoveryAssessment,
    VitalStats,
)


def test_build_recommendation_feedback_content_uses_consistent_section_labels():
    content = build_recommendation_feedback_content(
        {
            "exercise": {
                "specific": "热身：5分钟动态拉伸。主项：深蹲3组。整理：静态拉伸。"
            },
            "recovery": {"actions": ["训练前吃一根香蕉", "训练后补充蛋白质"]},
            "lifestyle": ["上午完成训练", "睡前呼吸放松"],
        }
    )

    assert content == (
        "【运动建议】热身：5分钟动态拉伸。主项：深蹲3组。整理：静态拉伸。\n\n"
        "【恢复建议】训练前吃一根香蕉；训练后补充蛋白质\n\n"
        "【生活建议】上午完成训练；睡前呼吸放松"
    )


def test_normalize_schedule_wording_rewrites_unsupported_future_meeting_reference():
    advice = {
        "summary": "恢复尚可",
        "exercise": {},
        "recovery": {"needed": False, "actions": []},
        "lifestyle": ["明晚有会议至20:00，建议21:30前结束屏幕使用"],
        "risk_alerts": [],
    }

    fixed = AdvancedDailyReportGenerator._normalize_schedule_wording(
        advice,
        calendar_summary={"last_event_end": "20:00"},
    )

    assert fixed["lifestyle"] == ["今晚有会议至20:00，建议21:30前结束屏幕使用"]


def test_normalize_schedule_wording_preserves_user_future_context():
    advice = {
        "summary": "恢复尚可",
        "exercise": {},
        "recovery": {"needed": False, "actions": []},
        "lifestyle": ["明晚有会议至20:00，建议21:30前结束屏幕使用"],
        "risk_alerts": [],
    }

    fixed = AdvancedDailyReportGenerator._normalize_schedule_wording(
        advice,
        calendar_summary={"last_event_end": "20:00"},
        user_context="明天晚上有活动，需要提前结束工作",
    )

    assert fixed["lifestyle"] == advice["lifestyle"]


def test_advanced_daily_report_writes_debug_sidecar(tmp_path):
    output_dir = tmp_path / "daily-reports"
    with patch("superhealth.reports.advanced_daily_report.DATA_DIR", output_dir):
        gen = AdvancedDailyReportGenerator(db_path=tmp_path / "health.db")
        with patch.object(gen.base_generator, "load_garmin_data", return_value={"sleep_total_min": 480}):
            with patch.object(gen.base_generator, "load_vitals_stats", return_value=VitalStats()):
                with patch.object(gen, "_load_vitals_today", return_value={}):
                    with patch.object(gen, "_load_recent_exercises", return_value=[]):
                        with patch.object(gen, "_load_recent_feedback", return_value=[]):
                            with patch.object(gen.profile_builder, "build", return_value=MagicMock(active_goals=[])):
                                with patch.object(gen.model_selector, "select", return_value=[]):
                                    with patch.object(gen.model_selector, "get_model_names", return_value=[]):
                                        with patch.object(gen.model_selector, "get_guide_keys", return_value=[]):
                                            with patch(
                                                "superhealth.reports.advanced_daily_report.run_assessments",
                                                return_value=[],
                                            ):
                                                with patch(
                                                    "superhealth.reports.advanced_daily_report.fetch_weather",
                                                    return_value=None,
                                                ):
                                                    with patch(
                                                        "superhealth.reports.advanced_daily_report.fetch_calendar",
                                                        return_value=None,
                                                    ):
                                                        with patch.object(
                                                            gen.claude_advisor,
                                                            "advise",
                                                            return_value={
                                                                "summary": "测试摘要",
                                                                "exercise": {},
                                                                "recovery": {"needed": False, "actions": []},
                                                                "lifestyle": [],
                                                                "risk_alerts": [],
                                                            },
                                                        ):
                                                            gen.advisor_mode = "claude_only"
                                                            gen.generate_report("2026-05-06", save=True, test_mode=True)

    debug_path = output_dir / "2026-05-06-advanced-daily-report-test.debug.json"
    payload = json.loads(debug_path.read_text(encoding="utf-8"))
    assert payload["advisor_mode"] == "claude_only"
    assert payload["llm"]["claude"]["summary"] == "测试摘要"
    assert payload["llm"]["merged"]["summary"] == "测试摘要"


class TestAssessRecovery:
    def test_perfect_recovery(self):
        gen = DailyReportGenerator()
        garmin = {
            "sleep_score": 90,
            "hrv_status": "BALANCED",
            "body_battery_wake": 85,
        }
        result = gen.assess_recovery(garmin)
        assert isinstance(result, RecoveryAssessment)
        assert result.level == "优秀"
        assert result.sleep_quality == "优秀"
        assert result.hrv_status == "平衡"
        assert result.body_battery_status == "充足"

    def test_poor_sleep(self):
        gen = DailyReportGenerator()
        garmin = {"sleep_score": 45}
        result = gen.assess_recovery(garmin)
        assert result.sleep_quality == "严重不足"

    def test_low_hrv(self):
        gen = DailyReportGenerator()
        garmin = {"hrv_status": "LOW"}
        result = gen.assess_recovery(garmin)
        assert result.hrv_status == "偏低"

    def test_empty_data(self):
        gen = DailyReportGenerator()
        result = gen.assess_recovery({})
        assert result.sleep_quality == "无数据"
        assert result.hrv_status == "未知"
        assert result.body_battery_status == "无数据"


class TestGenerateExerciseRecommendation:
    def test_high_intensity(self):
        gen = DailyReportGenerator()
        assessment = RecoveryAssessment(
            overall_score=90, level="优秀", sleep_quality="优秀",
            hrv_status="平衡", body_battery_status="充足", readiness="适合高强度"
        )
        rec = gen.generate_exercise_recommendation(assessment, {}, VitalStats())
        assert rec.intensity == "高强度"
        assert "间歇跑" in rec.type_suggestion

    def test_rest_day(self):
        gen = DailyReportGenerator()
        assessment = RecoveryAssessment(
            overall_score=40, level="需恢复", sleep_quality="较差",
            hrv_status="偏低", body_battery_status="严重不足", readiness="建议休息"
        )
        rec = gen.generate_exercise_recommendation(assessment, {}, VitalStats())
        assert rec.intensity == "休息"

    def test_bp_caution_high(self):
        gen = DailyReportGenerator()
        assessment = RecoveryAssessment(
            overall_score=70, level="良好", sleep_quality="良好",
            hrv_status="平衡", body_battery_status="良好", readiness="适合中等强度"
        )
        vitals = VitalStats(latest_systolic=145, latest_diastolic=92)
        rec = gen.generate_exercise_recommendation(assessment, {}, vitals)
        assert any("血压偏高" in c for c in rec.cautions)
        assert rec.intensity == "中等强度"  # 高强度被降级

    def test_weight_gain_caution(self):
        gen = DailyReportGenerator()
        assessment = RecoveryAssessment(
            overall_score=70, level="良好", sleep_quality="良好",
            hrv_status="平衡", body_battery_status="良好", readiness="适合中等强度"
        )
        vitals = VitalStats(weight_change_7d=0.8)
        rec = gen.generate_exercise_recommendation(assessment, {}, vitals)
        assert any("体重上升" in c for c in rec.cautions)

    def test_weight_loss_caution(self):
        gen = DailyReportGenerator()
        assessment = RecoveryAssessment(
            overall_score=70, level="良好", sleep_quality="良好",
            hrv_status="平衡", body_battery_status="良好", readiness="适合中等强度"
        )
        vitals = VitalStats(weight_change_7d=-0.7)
        rec = gen.generate_exercise_recommendation(assessment, {}, vitals)
        assert any("体重下降" in c for c in rec.cautions)

    def test_hrv_low_caution(self):
        gen = DailyReportGenerator()
        assessment = RecoveryAssessment(
            overall_score=70, level="良好", sleep_quality="良好",
            hrv_status="平衡", body_battery_status="良好", readiness="适合中等强度"
        )
        rec = gen.generate_exercise_recommendation(assessment, {"hrv_status": "LOW"}, VitalStats())
        assert any("HRV偏低" in c for c in rec.cautions)

    def test_sleep_caution(self):
        gen = DailyReportGenerator()
        assessment = RecoveryAssessment(
            overall_score=70, level="良好", sleep_quality="一般",
            hrv_status="平衡", body_battery_status="良好", readiness="适合中等强度"
        )
        rec = gen.generate_exercise_recommendation(assessment, {"sleep_score": 65}, VitalStats())
        assert any("睡眠不足" in c for c in rec.cautions)


class TestGetMetricTrendAnalysis:
    def test_no_data(self):
        gen = DailyReportGenerator()
        with patch.object(gen.trend_analyzer, "calculate_rolling_averages", return_value=[]):
            val, trend = gen.get_metric_trend_analysis("sleep_score", "2025-04-01")
        assert val is None
        assert trend is None

    def test_today_not_found(self):
        gen = DailyReportGenerator()
        with patch.object(
            gen.trend_analyzer, "calculate_rolling_averages", return_value=[{"date": "2025-03-31", "value": 80}]
        ):
            val, trend = gen.get_metric_trend_analysis("sleep_score", "2025-04-01")
        assert val is None

    def test_near_average(self):
        gen = DailyReportGenerator()
        data = [
            {"date": "2025-03-31", "value": 80, "avg_30d": 82},
            {"date": "2025-04-01", "value": 81, "avg_30d": 82},
        ]
        with patch.object(gen.trend_analyzer, "calculate_rolling_averages", return_value=data):
            val, trend = gen.get_metric_trend_analysis("sleep_score", "2025-04-01")
        assert val == 81
        assert "接近30天平均" in trend

    def test_above_average_good_metric(self):
        gen = DailyReportGenerator()
        data = [
            {"date": "2025-04-01", "value": 90, "avg_30d": 80},
        ]
        with patch.object(gen.trend_analyzer, "calculate_rolling_averages", return_value=data):
            val, trend = gen.get_metric_trend_analysis("sleep_score", "2025-04-01")
        assert val == 90
        assert "📈 高于" in trend

    def test_above_average_bad_metric(self):
        gen = DailyReportGenerator()
        data = [
            {"date": "2025-04-01", "value": 70, "avg_30d": 60},
        ]
        with patch.object(gen.trend_analyzer, "calculate_rolling_averages", return_value=data):
            val, trend = gen.get_metric_trend_analysis("resting_hr", "2025-04-01")
        assert val == 70
        assert "⚠️ 高于" in trend


class TestGetTrendInsights:
    def test_sleep_down_trend(self):
        gen = DailyReportGenerator()
        mock_trend = MagicMock()
        mock_trend.trend_direction = "down"
        with patch.object(gen.trend_analyzer, "analyze_trend", return_value=mock_trend):
            insights = gen.get_trend_insights("2025-04-01")
        assert any("睡眠评分" in i and "下降" in i for i in insights)

    def test_hrv_anomaly(self):
        gen = DailyReportGenerator()
        mock_sleep = MagicMock()
        mock_sleep.trend_direction = "stable"
        mock_hrv = MagicMock()
        mock_hrv.is_anomaly = True
        mock_hrv.z_score = -2.0
        with patch.object(gen.trend_analyzer, "analyze_trend", side_effect=[mock_sleep, mock_hrv]):
            insights = gen.get_trend_insights("2025-04-01")
        assert any("HRV显著低于" in i for i in insights)

    def test_rhr_up_trend(self):
        gen = DailyReportGenerator()
        mock_sleep = MagicMock()
        mock_sleep.trend_direction = "stable"
        mock_hrv = MagicMock()
        mock_hrv.is_anomaly = False
        mock_rhr = MagicMock()
        mock_rhr.trend_direction = "up"
        with patch.object(gen.trend_analyzer, "analyze_trend", side_effect=[mock_sleep, mock_hrv, mock_rhr]):
            insights = gen.get_trend_insights("2025-04-01")
        assert any("静息心率" in i and "上升" in i for i in insights)

    def test_exception_handling(self):
        gen = DailyReportGenerator()
        with patch.object(gen.trend_analyzer, "analyze_trend", side_effect=RuntimeError("boom")):
            insights = gen.get_trend_insights("2025-04-01")
        assert insights == []


class TestGenerateReport:
    def test_no_garmin_data(self):
        gen = DailyReportGenerator()
        with patch.object(gen, "load_garmin_data", return_value=None):
            report = gen.generate_report("2025-04-01")
        assert "未找到 Garmin 数据" in report

    def test_full_report_structure(self):
        gen = DailyReportGenerator()
        garmin = {
            "sleep_total_min": 480,
            "sleep_score": 85,
            "hrv_avg": 50,
            "hrv_status": "BALANCED",
            "resting_hr": 55,
            "body_battery_wake": 80,
            "avg_stress": 25,
            "steps": 10000,
        }
        with patch.object(gen, "load_garmin_data", return_value=garmin):
            with patch.object(gen, "load_vitals_stats", return_value=VitalStats()):
                with patch.object(gen.trend_analyzer, "calculate_rolling_averages", return_value=[]):
                    with patch.object(gen.trend_analyzer, "analyze_trend", side_effect=Exception("no trend")):
                        report = gen.generate_report("2025-04-01")
        assert "2025-04-01 健康日报" in report
        assert "恢复状态" in report
        assert "Garmin 数据" in report
        assert "今日建议" in report
        assert "睡眠: 8小时0分" in report
        assert "步数: 10,000" in report
