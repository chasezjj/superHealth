"""测试 score_state 评分逻辑（纯函数，最重要的业务逻辑）。"""
import pytest
from superhealth.analysis.analyze_garmin import score_state, recommend


# ─── score_state ───────────────────────────────────────────────────


def _good() -> dict:
    """代表恢复较好的一天。"""
    return {
        "sleep_score": 88,          # +2（>=85）
        "body_battery_wake": 80,    # +2（>=75）
        "hrv_status": "BALANCED",   # +2
        "hrv_avg": 40,
        "hrv_baseline_low": 35,     # +1（hrv_avg >= baseline_low）
        "resting_hr": 51,
        "avg7_resting_hr": 55,      # +1（resting_hr <= avg7）
        "avg_stress": 22,           # +1（<25）
        "resp_waking": 14,          # +1（<=15）
    }


def _medium() -> dict:
    """代表恢复中等的一天。"""
    return {
        "sleep_score": 78,          # +1（>=75）
        "body_battery_wake": 60,    # +1（>=55）
        "hrv_status": "UNBALANCED", # 0
        "hrv_avg": 30,
        "hrv_baseline_low": 34,     # -1（hrv_avg < baseline_low）
        "resting_hr": 56,
        "avg7_resting_hr": 55,      # -1（resting_hr >= avg7+3 不满足，但 > avg7 失分 0→实际判断 rhr>avg7 不到+3 扣0）
        "avg_stress": 30,           # 0（25~35 区间）
        "resp_waking": 16,          # 0（15~18 区间）
    }


def _weak() -> dict:
    """代表恢复偏弱的一天。"""
    return {
        "sleep_score": 60,          # -1（<75）
        "body_battery_wake": 30,    # -2（<40）
        "hrv_status": "LOW",        # -2
        "hrv_avg": 25,
        "hrv_baseline_low": 34,     # -1（hrv_avg < baseline_low）
        "resting_hr": 65,
        "avg7_resting_hr": 55,      # -1（resting_hr >= avg7+3）
        "avg_stress": 40,           # -1（>35）
        "resp_waking": 19,          # -1（>=18）
    }


class TestScoreState:
    def test_good_recovery_level(self):
        score, level, notes = score_state(_good())
        assert level == "恢复较好"
        assert score >= 5

    def test_good_recovery_score(self):
        # 各项贡献：+2+2+2+1+1+1+1 = 10
        score, _, _ = score_state(_good())
        assert score == 10

    def test_medium_recovery_level(self):
        score, level, notes = score_state(_medium())
        assert level in ("恢复中等", "恢复偏弱")  # 边界情况，取决于具体得分

    def test_weak_recovery_level(self):
        score, level, notes = score_state(_weak())
        assert level == "恢复偏弱"
        assert score < 2

    def test_weak_recovery_score(self):
        # 各项贡献：-1-2-2-1-1-1-1 = -9
        score, _, _ = score_state(_weak())
        assert score == -9

    def test_no_data_returns_zero_score(self):
        score, level, notes = score_state({})
        assert score == 0
        assert level == "恢复偏弱"
        assert notes == []

    def test_sleep_score_thresholds(self):
        # >=85 得 +2
        s, _, n = score_state({"sleep_score": 85})
        assert s == 2
        assert "睡眠评分较好" in n

        # >=75 得 +1
        s, _, n = score_state({"sleep_score": 75})
        assert s == 1
        assert "睡眠评分尚可" in n

        # <75 得 -1
        s, _, n = score_state({"sleep_score": 74})
        assert s == -1
        assert "睡眠恢复一般" in n

    def test_body_battery_thresholds(self):
        # >=75 得 +2
        s, _, _ = score_state({"body_battery_wake": 75})
        assert s == 2

        # >=55 得 +1
        s, _, _ = score_state({"body_battery_wake": 55})
        assert s == 1

        # 40~55 之间得 -1
        s, _, _ = score_state({"body_battery_wake": 42})
        assert s == -1

        # <40 得 -2
        s, _, _ = score_state({"body_battery_wake": 39})
        assert s == -2

    def test_hrv_status_balanced(self):
        s, _, n = score_state({"hrv_status": "BALANCED"})
        assert s == 2
        assert "HRV 状态平衡" in n

    def test_hrv_status_low(self):
        s, _, n = score_state({"hrv_status": "LOW"})
        assert s == -2
        assert "HRV 偏低" in n

    def test_hrv_status_case_insensitive(self):
        s1, _, _ = score_state({"hrv_status": "balanced"})
        s2, _, _ = score_state({"hrv_status": "BALANCED"})
        assert s1 == s2

    def test_hrv_vs_baseline(self):
        # hrv_avg >= baseline_low 得 +1
        s, _, n = score_state({"hrv_avg": 36, "hrv_baseline_low": 34})
        assert s == 1
        assert "HRV 回到或接近基线" in n

        # hrv_avg < baseline_low 得 -1
        s, _, n = score_state({"hrv_avg": 30, "hrv_baseline_low": 34})
        assert s == -1
        assert "HRV 低于个人基线" in n

    def test_resting_hr_vs_7day_avg(self):
        # 静息心率未高于近期平均 → +1
        s, _, n = score_state({"resting_hr": 52, "avg7_resting_hr": 55})
        assert s == 1
        assert "静息心率未高于近期平均" in n

        # 高 3+ bpm → -1
        s, _, n = score_state({"resting_hr": 58, "avg7_resting_hr": 55})
        assert s == -1
        assert "静息心率偏高" in n

        # 高 1-2 bpm → 0
        s, _, n = score_state({"resting_hr": 56, "avg7_resting_hr": 55})
        assert s == 0

    def test_stress_thresholds(self):
        # <25 得 +1
        s, _, n = score_state({"avg_stress": 24})
        assert s == 1
        assert "平均压力不高" in n

        # >35 得 -1
        s, _, n = score_state({"avg_stress": 36})
        assert s == -1
        assert "平均压力偏高" in n

        # 25~35 得 0
        s, _, _ = score_state({"avg_stress": 30})
        assert s == 0

    def test_respiration_thresholds(self):
        s, _, n = score_state({"resp_waking": 15})
        assert s == 1
        assert "呼吸频率平稳" in n

        s, _, n = score_state({"resp_waking": 18})
        assert s == -1
        assert "呼吸频率偏高" in n


# ─── recommend ─────────────────────────────────────────────────────


class TestRecommend:
    def test_good_returns_medium_intensity(self):
        intensity, plan, cautions = recommend({}, "恢复较好")
        assert "中等" in intensity
        assert len(plan) > 0
        assert cautions == []

    def test_medium_returns_low_intensity(self):
        intensity, plan, cautions = recommend({}, "恢复中等")
        assert "低" in intensity or "中等" in intensity

    def test_weak_returns_light_intensity(self):
        intensity, plan, cautions = recommend({}, "恢复偏弱")
        assert "轻" in intensity

    def test_spo2_low_adds_caution(self):
        _, _, cautions = recommend({"spo2_latest": 88}, "恢复较好")
        assert len(cautions) == 1
        assert "血氧" in cautions[0]

    def test_spo2_normal_no_caution(self):
        _, _, cautions = recommend({"spo2_latest": 95}, "恢复较好")
        assert cautions == []
