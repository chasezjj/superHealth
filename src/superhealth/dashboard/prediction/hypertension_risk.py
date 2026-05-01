"""高血压风险评分器。

基于《中国高血压防治指南》风险分层：
1. 血压分级（SBP/DBP 取高级别）
2. 统计心血管危险因素
3. 评估靶器官损害
4. 检查临床合并症
5. 查表得出风险等级 → 转换为 0-100 分

参考：中国高血压防治指南（2024年修订版）
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from superhealth.dashboard.data_loader import (
    get_user_profile,
    load_eye_exams,
    load_lab_results,
    load_vitals,
)

# ─── 血压分级 ────────────────────────────────────────────────────────

BP_NORMAL = 0
BP_HIGH_NORMAL = 1  # 正常高值
BP_GRADE_1 = 2
BP_GRADE_2 = 3
BP_GRADE_3 = 4

BP_GRADE_LABELS = {
    BP_NORMAL: "正常",
    BP_HIGH_NORMAL: "正常高值",
    BP_GRADE_1: "1级高血压",
    BP_GRADE_2: "2级高血压",
    BP_GRADE_3: "3级高血压",
}


def _grade_sbp(sbp: Optional[float]) -> int:
    if sbp is None:
        return BP_NORMAL
    if sbp < 120:
        return BP_NORMAL
    if sbp < 140:
        return BP_HIGH_NORMAL
    if sbp < 160:
        return BP_GRADE_1
    if sbp < 180:
        return BP_GRADE_2
    return BP_GRADE_3


def _grade_dbp(dbp: Optional[float]) -> int:
    if dbp is None:
        return BP_NORMAL
    if dbp < 80:
        return BP_NORMAL
    if dbp < 90:
        return BP_HIGH_NORMAL
    if dbp < 100:
        return BP_GRADE_1
    if dbp < 110:
        return BP_GRADE_2
    return BP_GRADE_3


def _bp_grade(sbp: Optional[float], dbp: Optional[float]) -> int:
    """血压分级：取 SBP/DBP 中更高的级别。"""
    return max(_grade_sbp(sbp), _grade_dbp(dbp))


# ─── 危险因素评估 ─────────────────────────────────────────────────────


def _check_age_risk(profile: dict) -> tuple[bool, str]:
    """年龄：男性 >55 岁。"""
    bd = profile.get("birthdate")
    if not bd:
        return False, ""
    try:
        birth = date.fromisoformat(bd)
    except (ValueError, TypeError):
        return False, ""
    age = (date.today() - birth).days / 365.25
    gender = profile.get("gender", "")
    threshold = 55 if gender == "male" else 65
    if age > threshold:
        return True, f"年龄偏大（{age:.0f}岁，>{threshold}岁）"
    return False, ""


def _check_obesity(df_vitals: pd.DataFrame, height_cm: float) -> tuple[bool, str]:
    """肥胖：BMI ≥28。"""
    if df_vitals.empty or "weight_kg" not in df_vitals.columns:
        return False, ""
    latest_w = df_vitals["weight_kg"].dropna()
    if latest_w.empty:
        return False, ""
    bmi = float(latest_w.iloc[-1]) / (height_cm / 100) ** 2
    if bmi >= 28:
        return True, f"肥胖（BMI {bmi:.1f}≥28）"
    if bmi >= 24:
        return True, f"超重（BMI {bmi:.1f}≥24）"
    return False, ""


def _check_dyslipidemia(df_lab: pd.DataFrame) -> tuple[bool, str]:
    """血脂异常：LDL-C >3.4 mmol/L 或 TG >1.7 mmol/L。"""
    if df_lab.empty:
        return False, ""
    reasons = []
    ldl = df_lab[df_lab["item_name"].str.contains("低密度|LDL", case=False, na=False)]
    if not ldl.empty:
        val = float(ldl["value"].iloc[-1])
        if val > 3.4:
            reasons.append(f"LDL-C {val:.2f}>3.4")
    tg = df_lab[df_lab["item_name"].str.contains("甘油三酯|TG|Triglyceride", case=False, na=False)]
    if not tg.empty:
        val = float(tg["value"].iloc[-1])
        if val > 1.7:
            reasons.append(f"TG {val:.2f}>1.7")
    if reasons:
        return True, f"血脂异常（{'，'.join(reasons)}）"
    return False, ""


def _check_hyperuricemia(df_lab: pd.DataFrame) -> tuple[bool, str]:
    """高尿酸血症：UA >420 μmol/L。"""
    if df_lab.empty:
        return False, ""
    ua = df_lab[df_lab["item_name"].str.contains("尿酸|Uric Acid|UA", case=False, na=False)]
    if not ua.empty:
        val = float(ua["value"].iloc[-1])
        if val > 420:
            return True, f"高尿酸（{val:.0f}>420 μmol/L）"
    return False, ""


def _check_bp_trend_risk(df_vitals: pd.DataFrame) -> tuple[bool, str]:
    """近30天血压上升趋势明显（收缩压斜率 >0.5 mmHg/天）。"""
    if df_vitals.empty or "systolic" not in df_vitals.columns:
        return False, ""
    since = pd.Timestamp.now() - pd.Timedelta(days=30)
    recent = df_vitals[df_vitals["measured_at"] >= since][["measured_at", "systolic"]].dropna()
    if len(recent) < 4:
        return False, ""
    daily = recent.groupby(recent["measured_at"].dt.date)["systolic"].mean().sort_index()
    if len(daily) < 4:
        return False, ""
    x = np.arange(len(daily))
    slope = float(np.polyfit(x, daily.values, 1)[0])
    if slope > 0.5:
        return True, f"血压上升趋势（+{slope:.2f} mmHg/天）"
    return False, ""


# ─── 靶器官损害 ──────────────────────────────────────────────────────


def _check_kidney_damage(df_lab: pd.DataFrame) -> tuple[bool, str]:
    """肾功能异常：肌酐 >133 μmol/L（男）或尿素偏高。"""
    if df_lab.empty:
        return False, ""
    reasons = []
    cr = df_lab[df_lab["item_name"].str.contains("肌酐|Creatinine", case=False, na=False)]
    if not cr.empty:
        val = float(cr["value"].iloc[-1])
        if val > 133:
            reasons.append(f"肌酐 {val:.0f}>133 μmol/L")
    urea = df_lab[df_lab["item_name"].str.contains("尿素|Urea", case=False, na=False)]
    if not urea.empty:
        val = float(urea["value"].iloc[-1])
        if val > 8.3:
            reasons.append(f"尿素 {val:.1f}>8.3 mmol/L")
    if reasons:
        return True, f"肾功能异常（{'，'.join(reasons)}）"
    return False, ""


def _check_eye_damage(df_eye: pd.DataFrame) -> tuple[bool, str]:
    """眼底/眼科损害：青光眼（CD ratio ≥0.6）或眼压 >21。"""
    if df_eye.empty:
        return False, ""
    latest = df_eye.iloc[-1]
    reasons = []
    for side, prefix in [("右眼", "od_"), ("左眼", "os_")]:
        iop = latest.get(f"{prefix}iop")
        cd = latest.get(f"{prefix}cd_ratio")
        if iop is not None and float(iop) > 21:
            reasons.append(f"{side}眼压 {float(iop):.0f}>21")
        if cd is not None:
            try:
                if float(cd) >= 0.6:
                    reasons.append(f"{side}C/D {cd}≥0.6")
            except (ValueError, TypeError):
                pass
    if reasons:
        return True, f"眼科损害（{'，'.join(reasons)}）"
    return False, ""


# ─── 临床合并症 ──────────────────────────────────────────────────────


def _check_diabetes(df_lab: pd.DataFrame) -> tuple[bool, str]:
    """糖尿病：空腹血糖 ≥7.0 或 HbA1c ≥6.5%。"""
    if df_lab.empty:
        return False, ""
    fg = df_lab[
        df_lab["item_name"].str.contains("空腹血糖|Fasting Glucose|FBG|GLU", case=False, na=False)
    ]
    if not fg.empty:
        val = float(fg["value"].iloc[-1])
        if val >= 7.0:
            return True, f"空腹血糖 {val:.1f}≥7.0 mmol/L"
    hba1c = df_lab[df_lab["item_name"].str.contains("糖化血红蛋白|HbA1c", case=False, na=False)]
    if not hba1c.empty:
        val = float(hba1c["value"].iloc[-1])
        if val >= 6.5:
            return True, f"HbA1c {val:.1f}%≥6.5%"
    return False, ""


def _check_chronic_kidney_disease(df_lab: pd.DataFrame) -> tuple[bool, str]:
    """慢性肾病：eGFR <60（简化，用肌酐估算）。"""
    if df_lab.empty:
        return False, ""
    cr = df_lab[df_lab["item_name"].str.contains("肌酐|Creatinine", case=False, na=False)]
    if cr.empty:
        return False, ""
    val = float(cr["value"].iloc[-1])
    # 简化 MDRD 估算（男性）：eGFR ≈ 186 × (Scr/88.4)^(-1.154) × 年龄^(-0.203)
    # 仅做粗略判断，不替代临床评估
    # 若肌酐 >133 已在靶器官损害中处理，此处用更高阈值
    if val > 177:
        return True, f"肌酐显著升高 {val:.0f}>177 μmol/L（疑似 CKD）"
    return False, ""


# ─── 风险分层查表 ─────────────────────────────────────────────────────

RISK_LOW = "低危"
RISK_MEDIUM = "中危"
RISK_HIGH = "高危"
RISK_VERY_HIGH = "极高危"


def _risk_stratification(
    bp_grade: int,
    n_risk_factors: int,
    has_organ_damage: bool,
    has_clinical_disease: bool,
) -> str:
    """查表：基于血压分级 + 危险因素/靶器官损害/合并症 → 风险等级。"""
    if has_clinical_disease:
        if bp_grade >= BP_GRADE_1:
            return RISK_VERY_HIGH
        return RISK_HIGH

    if has_organ_damage or n_risk_factors >= 3:
        if bp_grade >= BP_GRADE_2:
            return RISK_VERY_HIGH if has_clinical_disease else RISK_HIGH
        if bp_grade == BP_GRADE_1:
            return RISK_HIGH
        return RISK_MEDIUM  # 正常高值

    if n_risk_factors >= 1:
        if bp_grade >= BP_GRADE_3:
            return RISK_HIGH
        if bp_grade >= BP_GRADE_1:
            return RISK_MEDIUM
        return RISK_LOW  # 正常高值

    # 无危险因素
    if bp_grade >= BP_GRADE_3:
        return RISK_HIGH
    if bp_grade >= BP_GRADE_2:
        return RISK_MEDIUM
    return RISK_LOW


def _risk_to_score(risk_level: str, sbp: Optional[float], dbp: Optional[float]) -> float:
    """将风险等级映射为 0-100 分，同一等级内按血压绝对值细化。"""
    # 用 SBP 和 DBP 的偏离程度作为细化因子（0-1）
    sbp_excess = 0.0
    if sbp is not None:
        sbp_excess = max(0, min(1, (sbp - 100) / 100))
    dbp_excess = 0.0
    if dbp is not None:
        dbp_excess = max(0, min(1, (dbp - 60) / 60))
    detail = (sbp_excess + dbp_excess) / 2  # 0-1

    ranges = {
        RISK_LOW: (0, 25),
        RISK_MEDIUM: (25, 50),
        RISK_HIGH: (50, 75),
        RISK_VERY_HIGH: (75, 100),
    }
    lo, hi = ranges[risk_level]
    return round(lo + (hi - lo) * detail * 0.8, 1)  # 留 20% 余量给更差情况


# ─── 主计算函数 ──────────────────────────────────────────────────────


def compute(days: int = 90) -> dict:
    """基于中国高血压防治指南计算高血压风险分层。

    Returns:
        score, factors, summary, status_label,
        latest_systolic, latest_diastolic,
        bp_grade, risk_factors
    """
    profile = get_user_profile()
    df_vitals = load_vitals(days)
    df_lab = load_lab_results()
    df_eye = load_eye_exams()

    # ── 最新血压 ──────────────────────────────────────────────────
    latest_sbp = None
    latest_dbp = None
    if not df_vitals.empty and "systolic" in df_vitals.columns:
        vals_s = df_vitals["systolic"].dropna()
        if not vals_s.empty:
            latest_sbp = float(vals_s.iloc[-1])
    if not df_vitals.empty and "diastolic" in df_vitals.columns:
        vals_d = df_vitals["diastolic"].dropna()
        if not vals_d.empty:
            latest_dbp = float(vals_d.iloc[-1])

    # ── 血压分级 ──────────────────────────────────────────────────
    grade = _bp_grade(latest_sbp, latest_dbp)
    grade_label = BP_GRADE_LABELS[grade]

    # ── 身高 ──────────────────────────────────────────────────────
    height_cm = float(profile.get("height_cm", 173))

    # ── 危险因素（逐一检查）──────────────────────────────────────
    risk_factor_checks = [
        _check_age_risk(profile),
        _check_obesity(df_vitals, height_cm),
        _check_dyslipidemia(df_lab),
        _check_hyperuricemia(df_lab),
        _check_bp_trend_risk(df_vitals),
    ]
    risk_factors = [desc for hit, desc in risk_factor_checks if hit]
    n_risk_factors = len(risk_factors)

    # ── 靶器官损害 ────────────────────────────────────────────────
    organ_damage_checks = [
        _check_kidney_damage(df_lab),
        _check_eye_damage(df_eye),
    ]
    organ_damages = [desc for hit, desc in organ_damage_checks if hit]
    has_organ_damage = len(organ_damages) > 0

    # ── 临床合并症 ────────────────────────────────────────────────
    comorbidity_checks = [
        _check_diabetes(df_lab),
        _check_chronic_kidney_disease(df_lab),
    ]
    comorbidities = [desc for hit, desc in comorbidity_checks if hit]
    has_comorbidity = len(comorbidities) > 0

    # ── 风险分层查表 ──────────────────────────────────────────────
    risk_level = _risk_stratification(grade, n_risk_factors, has_organ_damage, has_comorbidity)
    score = _risk_to_score(risk_level, latest_sbp, latest_dbp)

    # ── 因子贡献（供图表展示）─────────────────────────────────────
    factors = {
        "血压分级": min(100, grade * 25),
        "危险因素": min(100, n_risk_factors * 25),
        "靶器官损害": 80 if has_organ_damage else 0,
        "临床合并症": 90 if has_comorbidity else 0,
    }

    # ── 文字解读 ──────────────────────────────────────────────────
    sbp_str = f"{latest_sbp:.0f}" if latest_sbp else "未知"
    dbp_str = f"{latest_dbp:.0f}" if latest_dbp else "未知"

    advice_map = {
        RISK_LOW: "血压控制良好，继续保持健康生活方式。",
        RISK_MEDIUM: "血压偏高或存在危险因素，建议限盐、增加有氧运动、保持充足睡眠。",
        RISK_HIGH: "血压偏高且合并多项危险因素或靶器官损害，建议定期监测，必要时就医调整方案。",
        RISK_VERY_HIGH: "高血压合并临床疾病或靶器官损害，建议尽快就医评估，可能需要调整治疗方案。",
    }

    parts = [f"高血压风险：{risk_level}（{score:.1f}分）"]
    parts.append(f"血压 {sbp_str}/{dbp_str} mmHg（{grade_label}）")
    if risk_factors:
        parts.append(f"危险因素（{n_risk_factors}项）：{'；'.join(risk_factors)}")
    if organ_damages:
        parts.append(f"靶器官损害：{'；'.join(organ_damages)}")
    if comorbidities:
        parts.append(f"合并症：{'；'.join(comorbidities)}")
    parts.append(advice_map[risk_level])
    summary = "\n".join(parts)

    return {
        "score": score,
        "factors": factors,
        "summary": summary,
        "status_label": risk_level,
        "latest_systolic": latest_sbp,
        "latest_diastolic": latest_dbp,
        "bp_grade": grade_label,
        "risk_factors": risk_factors,
        "organ_damages": organ_damages,
        "comorbidities": comorbidities,
    }
