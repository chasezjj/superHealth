"""测试 dashboard/prediction 风险评分器的纯函数逻辑。"""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from superhealth.dashboard.prediction.hyperglycemia_risk import (
    GLU_DIABETES,
    GLU_NORMAL,
    GLU_PREDIABETES_HBA1C,
    GLU_PREDIABETES_IFG,
    _grade_fasting_glucose,
    _grade_hba1c,
    _glucose_grade,
    _risk_stratify as _glu_risk_stratify,
    _risk_to_score as _glu_risk_to_score,
)
from superhealth.dashboard.prediction.hyperlipidemia_risk import (
    TG_BORDERLINE,
    TG_HIGH,
    TG_NORMAL,
    TG_VERY_HIGH,
    _ascvd_risk_tier,
    _check_age_risk,
    _compute_score,
    _grade_tg,
)
from superhealth.dashboard.prediction.hypertension_risk import (
    BP_GRADE_1,
    BP_GRADE_2,
    BP_GRADE_3,
    BP_GRADE_LABELS,
    BP_HIGH_NORMAL,
    BP_NORMAL,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
    RISK_VERY_HIGH,
    _bp_grade,
    _grade_dbp,
    _grade_sbp,
    _risk_stratification,
    _risk_to_score as _ht_risk_to_score,
)
from superhealth.dashboard.prediction.uric_acid_risk import (
    UA_ELEVATED,
    UA_HIGH,
    UA_NORMAL,
    UA_VERY_HIGH,
    _grade_ua,
    _risk_stratify as _ua_risk_stratify,
    _risk_to_score as _ua_risk_to_score,
)


# ─── Hyperglycemia ─────────────────────────────────────────────────

class TestGlucoseGrading:
    def test_fasting_glucose_normal(self):
        assert _grade_fasting_glucose(5.5) == GLU_NORMAL

    def test_fasting_glucose_ifg(self):
        assert _grade_fasting_glucose(6.5) == GLU_PREDIABETES_IFG

    def test_fasting_glucose_diabetes(self):
        assert _grade_fasting_glucose(7.5) == GLU_DIABETES

    def test_fasting_glucose_none(self):
        assert _grade_fasting_glucose(None) == GLU_NORMAL

    def test_hba1c_normal(self):
        assert _grade_hba1c(5.0) == GLU_NORMAL

    def test_hba1c_prediabetes(self):
        assert _grade_hba1c(6.0) == GLU_PREDIABETES_HBA1C

    def test_hba1c_diabetes(self):
        assert _grade_hba1c(7.0) == GLU_DIABETES

    def test_glucose_grade_takes_max(self):
        assert _glucose_grade(5.5, 7.0) == GLU_DIABETES


class TestGluRiskStratify:
    def test_diabetes_very_high(self):
        assert _glu_risk_stratify(GLU_DIABETES, 0, False, None) == "极高风险"

    def test_prediabetes_with_metabolic(self):
        assert _glu_risk_stratify(GLU_PREDIABETES_IFG, 0, True, None) == "高风险"

    def test_prediabetes_medium(self):
        assert _glu_risk_stratify(GLU_PREDIABETES_IFG, 1, False, None) == "中等风险"

    def test_normal_low(self):
        assert _glu_risk_stratify(GLU_NORMAL, 0, False, None) == "低风险"


class TestGluRiskToScore:
    def test_ranges(self):
        assert 0 <= _glu_risk_to_score("低风险", 4.0, 4.5) <= 25
        assert 25 <= _glu_risk_to_score("中等风险", 5.5, 5.8) <= 50
        assert 50 <= _glu_risk_to_score("高风险", 6.5, 6.5) <= 75
        assert 75 <= _glu_risk_to_score("极高风险", 8.0, 8.0) <= 100


# ─── Hypertension ──────────────────────────────────────────────────

class TestBpGrading:
    def test_sbp_normal(self):
        assert _grade_sbp(110) == BP_NORMAL

    def test_sbp_high_normal(self):
        assert _grade_sbp(130) == BP_HIGH_NORMAL

    def test_sbp_grade1(self):
        assert _grade_sbp(150) == BP_GRADE_1

    def test_sbp_grade2(self):
        assert _grade_sbp(170) == BP_GRADE_2

    def test_sbp_grade3(self):
        assert _grade_sbp(190) == BP_GRADE_3

    def test_dbp_normal(self):
        assert _grade_dbp(75) == BP_NORMAL

    def test_dbp_grade1(self):
        assert _grade_dbp(95) == BP_GRADE_1

    def test_bp_grade_takes_max(self):
        assert _bp_grade(110, 95) == BP_GRADE_1

    def test_bp_grade_none(self):
        assert _bp_grade(None, None) == BP_NORMAL


class TestHtRiskStratification:
    def test_clinical_disease_very_high(self):
        assert _risk_stratification(BP_GRADE_1, 0, False, True) == RISK_VERY_HIGH

    def test_organ_damage_high(self):
        assert _risk_stratification(BP_GRADE_1, 3, True, False) == RISK_HIGH

    def test_no_risk_low(self):
        assert _risk_stratification(BP_NORMAL, 0, False, False) == RISK_LOW

    def test_grade3_no_risk_medium(self):
        assert _risk_stratification(BP_GRADE_2, 0, False, False) == RISK_MEDIUM


class TestHtRiskToScore:
    def test_score_ranges(self):
        assert 0 <= _ht_risk_to_score(RISK_LOW, 110, 70) < 25
        assert 25 <= _ht_risk_to_score(RISK_MEDIUM, 140, 90) < 50
        assert 50 <= _ht_risk_to_score(RISK_HIGH, 160, 100) < 75
        assert 75 <= _ht_risk_to_score(RISK_VERY_HIGH, 180, 110) <= 100


# ─── Hyperlipidemia ────────────────────────────────────────────────

class TestTgGrading:
    def test_tg_normal(self):
        assert _grade_tg(1.5) == TG_NORMAL

    def test_tg_borderline(self):
        assert _grade_tg(2.0) == TG_BORDERLINE

    def test_tg_high(self):
        assert _grade_tg(3.0) == TG_HIGH

    def test_tg_very_high(self):
        assert _grade_tg(6.0) == TG_VERY_HIGH

    def test_tg_none(self):
        assert _grade_tg(None) == TG_NORMAL


class TestAscvdRiskTier:
    def test_diabetes_with_ckd_very_high(self):
        assert _ascvd_risk_tier(True, True, 0) == "极高危"

    def test_ckd_high(self):
        assert _ascvd_risk_tier(False, True, 0) == "高危"

    def test_diabetes_high(self):
        assert _ascvd_risk_tier(True, False, 0) == "高危"

    def test_three_factors_high(self):
        assert _ascvd_risk_tier(False, False, 3) == "高危"

    def test_one_factor_medium(self):
        assert _ascvd_risk_tier(False, False, 1) == "中危"

    def test_no_factors_low(self):
        assert _ascvd_risk_tier(False, False, 0) == "低危"


class TestHyperlipidemiaScore:
    def test_compute_score_ranges(self):
        assert _compute_score("低危", "达标", 0, TG_NORMAL, 1.2) < 20
        assert _compute_score("极高危", "未达标", 0.5, TG_HIGH, 0.8) > 55


# ─── Uric Acid ─────────────────────────────────────────────────────

class TestUaGrading:
    def test_ua_normal(self):
        assert _grade_ua(400) == UA_NORMAL

    def test_ua_elevated(self):
        assert _grade_ua(450) == UA_ELEVATED

    def test_ua_high(self):
        assert _grade_ua(500) == UA_HIGH

    def test_ua_very_high(self):
        assert _grade_ua(560) == UA_VERY_HIGH

    def test_ua_none(self):
        assert _grade_ua(None) == UA_NORMAL


class TestUaRiskStratify:
    def test_very_high(self):
        assert _ua_risk_stratify(UA_VERY_HIGH, 0, 0, False) == "极高风险"

    def test_high_with_comorbidity(self):
        assert _ua_risk_stratify(UA_HIGH, 1, 0, False) == "极高风险"

    def test_high_no_comorbidity(self):
        assert _ua_risk_stratify(UA_HIGH, 0, 0, False) == "高风险"

    def test_normal_low(self):
        assert _ua_risk_stratify(UA_NORMAL, 0, 0, False) == "低风险"


class TestUaRiskToScore:
    def test_ranges(self):
        assert 0 <= _ua_risk_to_score("低风险", 300) <= 25
        assert 75 <= _ua_risk_to_score("极高风险", 580) <= 100
