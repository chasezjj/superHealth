"""高血脂风险评分器。

基于《中国血脂管理指南（2023年）》：
1. LDL-C 按心血管风险分层定目标值
2. TG 分级
3. 非 HDL-C 评估
4. HDL-C 降低作为危险因素
5. 综合判断是否达标 → 风险分层

核心逻辑：先确定心血管风险等级 → 再看 LDL-C 是否达到对应目标。

参考：中国成人血脂异常防治指南（2016年修订版）
      中国血脂管理指南（2023年）
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from superhealth.dashboard.data_loader import (
    get_user_profile,
    load_lab_results,
    load_vitals,
)


# ─── LDL-C 目标值（按心血管风险分层）─────────────────────────────────

LDL_TARGETS = {
    "低危": 3.4,
    "中危": 2.6,
    "高危": 1.8,
    "极高危": 1.4,
}

# TG 分级
TG_NORMAL = 0       # <1.7
TG_BORDERLINE = 1   # 1.7-2.3
TG_HIGH = 2         # 2.3-5.6（需药物治疗）
TG_VERY_HIGH = 3    # ≥5.6（急性胰腺炎风险，紧急处理）

TG_LABELS = {
    TG_NORMAL: "正常",
    TG_BORDERLINE: "边缘升高",
    TG_HIGH: "升高",
    TG_VERY_HIGH: "严重升高",
}


def _grade_tg(tg: Optional[float]) -> int:
    if tg is None:
        return TG_NORMAL
    if tg < 1.7:
        return TG_NORMAL
    if tg < 2.3:
        return TG_BORDERLINE
    if tg < 5.6:
        return TG_HIGH
    return TG_VERY_HIGH


# ─── 心血管危险因素评估 ─────────────────────────────────────────────

def _check_age_risk(profile: dict) -> bool:
    """年龄：男 >45。"""
    bd = profile.get("birthdate")
    if not bd:
        return False
    try:
        birth = date.fromisoformat(bd)
    except (ValueError, TypeError):
        return False
    age = (date.today() - birth).days / 365.25
    gender = profile.get("gender", "")
    threshold = 45 if gender == "male" else 55
    return age > threshold


def _check_hypertension_risk(df_vitals: pd.DataFrame) -> bool:
    """高血压：SBP ≥140 或 DBP ≥90。"""
    if df_vitals.empty:
        return False
    if "systolic" in df_vitals.columns:
        sbp = df_vitals["systolic"].dropna()
        if not sbp.empty and float(sbp.iloc[-1]) >= 140:
            return True
    if "diastolic" in df_vitals.columns:
        dbp = df_vitals["diastolic"].dropna()
        if not dbp.empty and float(dbp.iloc[-1]) >= 90:
            return True
    return False


def _check_low_hdl(df_lab: pd.DataFrame) -> bool:
    """低 HDL-C：<1.0 mmol/L。"""
    if df_lab.empty:
        return False
    hdl = df_lab[df_lab["item_name"].str.contains("高密度|HDL", case=False, na=False)]
    if not hdl.empty:
        return float(hdl["value"].iloc[-1]) < 1.0
    return False


def _check_high_bmi(df_vitals: pd.DataFrame, profile: dict) -> bool:
    """BMI ≥24（中国超重标准）。"""
    if df_vitals.empty or "weight_kg" not in df_vitals.columns:
        return False
    latest_w = df_vitals["weight_kg"].dropna()
    if latest_w.empty:
        return False
    height_cm = float(profile.get("height_cm", 173))
    bmi = float(latest_w.iloc[-1]) / (height_cm / 100) ** 2
    return bmi >= 24


def _check_hyperuricemia_risk(df_lab: pd.DataFrame) -> bool:
    """高尿酸：UA >420。"""
    if df_lab.empty:
        return False
    ua = df_lab[df_lab["item_name"].str.contains("尿酸|Uric Acid|UA", case=False, na=False)]
    if not ua.empty:
        return float(ua["value"].iloc[-1]) > 420
    return False


def _check_smoking() -> bool:
    """吸烟：暂无数据源，返回 False。"""
    return False


# ─── 靶器官损害 / 临床合并症 ─────────────────────────────────────────

def _check_diabetes(df_lab: pd.DataFrame) -> bool:
    """糖尿病。"""
    if df_lab.empty:
        return False
    fg = df_lab[df_lab["item_name"].str.contains("空腹血糖|FBG|GLU|Fasting Glucose", case=False, na=False)]
    if not fg.empty and float(fg["value"].iloc[-1]) >= 7.0:
        return True
    hba1c = df_lab[df_lab["item_name"].str.contains("糖化血红蛋白|HbA1c", case=False, na=False)]
    if not hba1c.empty and float(hba1c["value"].iloc[-1]) >= 6.5:
        return True
    return False


def _check_ckd(df_lab: pd.DataFrame) -> bool:
    """慢性肾病：肌酐 >133。"""
    if df_lab.empty:
        return False
    cr = df_lab[df_lab["item_name"].str.contains("肌酐|Creatinine", case=False, na=False)]
    if not cr.empty:
        return float(cr["value"].iloc[-1]) > 133
    return False


# ─── 心血管风险分层 ──────────────────────────────────────────────────

ASCVD_LOW = "低危"
ASCVD_MEDIUM = "中危"
ASCVD_HIGH = "高危"
ASCVD_VERY_HIGH = "极高危"


def _ascvd_risk_tier(
    has_diabetes: bool,
    has_ckd: bool,
    n_risk_factors: int,
) -> str:
    """确定 ASCVD 风险等级（简化版）。

    极高危：糖尿病 + 靶器官损害，或 CKD
    高危：糖尿病，或 ≥3 个危险因素
    中危：1-2 个危险因素
    低危：无危险因素
    """
    if has_diabetes and (has_ckd or n_risk_factors >= 1):
        return ASCVD_VERY_HIGH
    if has_ckd:
        return ASCVD_HIGH
    if has_diabetes:
        return ASCVD_HIGH
    if n_risk_factors >= 3:
        return ASCVD_HIGH
    if n_risk_factors >= 1:
        return ASCVD_MEDIUM
    return ASCVD_LOW


# ─── 主计算函数 ──────────────────────────────────────────────────────

def compute(days: int = 365 * 3) -> dict:
    """基于中国血脂管理指南计算血脂风险分层。"""
    profile = get_user_profile()
    df_lab = load_lab_results()
    df_vitals = load_vitals(30)

    # ── 提取最新血脂值 ────────────────────────────────────────────
    def _latest(item_pattern: str) -> Optional[float]:
        if df_lab.empty:
            return None
        sub = df_lab[df_lab["item_name"].str.contains(item_pattern, case=False, na=False)]
        return float(sub["value"].iloc[-1]) if not sub.empty else None

    latest_ldl = _latest("低密度|LDL")
    latest_tg = _latest("甘油三酯|TG|Triglyceride")
    latest_hdl = _latest("高密度|HDL")
    latest_tc = _latest("总胆固醇|Total Cholesterol")

    # 非 HDL-C = TC - HDL-C
    non_hdl = None
    if latest_tc is not None and latest_hdl is not None:
        non_hdl = latest_tc - latest_hdl

    # ── 心血管危险因素 ────────────────────────────────────────────
    risk_factor_checks = [
        ("年龄", _check_age_risk(profile)),
        ("高血压", _check_hypertension_risk(df_vitals)),
        ("低HDL-C", _check_low_hdl(df_lab)),
        ("超重/肥胖", _check_high_bmi(df_vitals, profile)),
        ("高尿酸", _check_hyperuricemia_risk(df_lab)),
        ("吸烟", _check_smoking()),
    ]
    risk_factor_names = [name for name, hit in risk_factor_checks if hit]
    n_risk_factors = len(risk_factor_names)

    # ── 合并症 ────────────────────────────────────────────────────
    has_diabetes = _check_diabetes(df_lab)
    has_ckd = _check_ckd(df_lab)

    # ── ASCVD 风险分层 → LDL-C 目标 ──────────────────────────────
    risk_tier = _ascvd_risk_tier(has_diabetes, has_ckd, n_risk_factors)
    ldl_target = LDL_TARGETS[risk_tier]

    # ── LDL-C 是否达标 ────────────────────────────────────────────
    ldl_status = "未知"
    ldl_excess = 0.0
    if latest_ldl is not None:
        if latest_ldl <= ldl_target:
            ldl_status = "达标"
        else:
            ldl_status = "未达标"
            ldl_excess = latest_ldl - ldl_target

    # ── TG 分级 ───────────────────────────────────────────────────
    tg_grade = _grade_tg(latest_tg)
    tg_label = TG_LABELS[tg_grade]

    # ── 综合风险评分 ──────────────────────────────────────────────
    score = _compute_score(risk_tier, ldl_status, ldl_excess, tg_grade, latest_hdl)

    # ── 因子贡献（供图表）─────────────────────────────────────────
    factors = {
        "LDL-C达标情况": 0 if ldl_status == "达标" else min(100, 30 + ldl_excess / ldl_target * 70),
        "甘油三酯": min(100, tg_grade * 33),
        "心血管风险等级": {"低危": 10, "中危": 35, "高危": 65, "极高危": 90}.get(risk_tier, 0),
        "HDL-C": 40 if (latest_hdl is not None and latest_hdl < 1.0) else 0,
    }

    # ── 文字解读 ──────────────────────────────────────────────────
    parts = [f"血脂风险：{risk_tier}（{score:.1f}分）"]
    ldl_str = f"{latest_ldl:.2f}" if latest_ldl else "未知"
    tg_str = f"{latest_tg:.2f}" if latest_tg else "未知"
    hdl_str = f"{latest_hdl:.2f}" if latest_hdl else "未知"
    parts.append(f"LDL-C {ldl_str}（目标 <{ldl_target}，{ldl_status}）")
    parts.append(f"TG {tg_str}（{tg_label}），HDL-C {hdl_str}")
    if non_hdl is not None:
        nh_target = ldl_target + 0.8
        parts.append(f"非HDL-C {non_hdl:.2f}（目标 <{nh_target}）")
    if risk_factor_names:
        parts.append(f"危险因素（{n_risk_factors}项）：{'；'.join(risk_factor_names)}")

    advice = _get_advice(risk_tier, ldl_status, tg_grade)
    parts.append(advice)
    summary = "\n".join(parts)

    return {
        "score": round(score, 1),
        "factors": factors,
        "summary": summary,
        "status_label": risk_tier,
        "latest_ldl": latest_ldl,
        "latest_tg": latest_tg,
        "latest_hdl": latest_hdl,
        "latest_tc": latest_tc,
        "non_hdl_c": non_hdl,
        "ldl_target": ldl_target,
        "ldl_status": ldl_status,
        "tg_grade": tg_label,
        "ascvd_risk_tier": risk_tier,
        "risk_factors": risk_factor_names,
    }


def _compute_score(
    risk_tier: str,
    ldl_status: str,
    ldl_excess: float,
    tg_grade: int,
    hdl: Optional[float],
) -> float:
    """综合评分 0-100。"""
    base = {"低危": 10, "中危": 30, "高危": 55, "极高危": 75}.get(risk_tier, 0)

    # LDL-C 未达标加分
    if ldl_status == "未达标":
        base += min(20, ldl_excess * 10)

    # TG 加分
    base += tg_grade * 5

    # 低 HDL-C 加分
    if hdl is not None and hdl < 1.0:
        base += 5

    return max(0.0, min(100.0, base))


def _get_advice(risk_tier: str, ldl_status: str, tg_grade: int) -> str:
    if ldl_status == "达标" and tg_grade <= TG_NORMAL:
        return "血脂全面达标，继续保持健康饮食和规律运动。"
    if risk_tier in (ASCVD_HIGH, ASCVD_VERY_HIGH):
        return "心血管高风险，LDL-C 需严格控制，建议遵医嘱用药并定期复查血脂。"
    if ldl_status == "未达标":
        return f"LDL-C 未达标，建议减少饱和脂肪和反式脂肪摄入，增加膳食纤维，必要时就医评估。"
    if tg_grade >= TG_HIGH:
        return "甘油三酯升高，建议控制碳水摄入、戒酒、增加有氧运动，必要时药物治疗。"
    if tg_grade >= TG_BORDERLINE:
        return "甘油三酯边缘升高，建议控制精制碳水和酒精摄入。"
    return "血脂基本正常，注意保持健康生活方式。"
