"""高血糖风险评估器。

基于《中国2型糖尿病防治指南（2024年版）》：
1. 血糖分级（正常 / 糖尿病前期 / 糖尿病）
2. 糖尿病风险因素评分（年龄、BMI、高血压、血脂异常、家族史等）
3. 代谢综合征评估
4. 综合风险分层 → 0-100 分

参考：中国2型糖尿病防治指南（2024年版）
      中国糖尿病前期临床干预专家共识（2022）
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from superhealth.dashboard.data_loader import (
    get_user_profile,
    load_annual_checkups,
    load_lab_results,
    load_vitals,
)


# ─── 血糖分级 ────────────────────────────────────────────────────────

GLU_NORMAL = 0          # FBG <6.1 且 HbA1c <5.7%
GLU_PREDIABETES_IFG = 1 # 空腹血糖受损：FBG 6.1-7.0
GLU_PREDIABETES_HBA1C = 2  # HbA1c 偏高：5.7-6.5%
GLU_DIABETES = 3        # FBG ≥7.0 或 HbA1c ≥6.5%

GLU_LABELS = {
    GLU_NORMAL: "正常",
    GLU_PREDIABETES_IFG: "空腹血糖受损（IFG）",
    GLU_PREDIABETES_HBA1C: "糖化血红蛋白偏高",
    GLU_DIABETES: "糖尿病范围",
}


def _grade_fasting_glucose(fbg: Optional[float]) -> int:
    if fbg is None:
        return GLU_NORMAL
    if fbg < 6.1:
        return GLU_NORMAL
    if fbg < 7.0:
        return GLU_PREDIABETES_IFG
    return GLU_DIABETES


def _grade_hba1c(hba1c: Optional[float]) -> int:
    if hba1c is None:
        return GLU_NORMAL
    if hba1c < 5.7:
        return GLU_NORMAL
    if hba1c < 6.5:
        return GLU_PREDIABETES_HBA1C
    return GLU_DIABETES


def _glucose_grade(fbg: Optional[float], hba1c: Optional[float]) -> int:
    """取 FBG 和 HbA1c 中更高的级别。"""
    return max(_grade_fasting_glucose(fbg), _grade_hba1c(hba1c))


# ─── 从体检表获取最新血糖 ────────────────────────────────────────────

def _get_latest_glucose() -> tuple[Optional[float], Optional[float]]:
    """从 annual_checkups 获取最新空腹血糖和 HbA1c。"""
    df = load_annual_checkups()
    if df.empty:
        return None, None
    latest = df.iloc[-1]
    fbg = latest.get("fasting_glucose")
    hba1c = latest.get("hba1c")
    fbg = float(fbg) if pd.notna(fbg) else None
    hba1c = float(hba1c) if pd.notna(hba1c) else None
    return fbg, hba1c


def _get_glucose_trend() -> tuple[Optional[float], Optional[str]]:
    """空腹血糖近 N 次趋势（基于年度体检）。"""
    df = load_annual_checkups()
    if df.empty:
        return None, None
    vals = df[["checkup_date", "fasting_glucose"]].dropna(subset=["fasting_glucose"])
    if len(vals) < 2:
        return None, None
    recent = vals.tail(5)
    x = np.arange(len(recent))
    slope = float(np.polyfit(x, recent["fasting_glucose"].values, 1)[0])
    return slope, "上升" if slope > 0.05 else ("下降" if slope < -0.05 else "平稳")


# ─── 糖尿病危险因素 ──────────────────────────────────────────────────

def _check_age_risk(profile: dict) -> tuple[bool, str]:
    """年龄 ≥40 岁。"""
    bd = profile.get("birthdate")
    if not bd:
        return False, ""
    try:
        birth = date.fromisoformat(bd)
    except (ValueError, TypeError):
        return False, ""
    age = (date.today() - birth).days / 365.25
    if age >= 40:
        return True, f"年龄 {age:.0f}岁（≥40）"
    return False, ""


def _check_overweight(df_vitals: pd.DataFrame, profile: dict) -> tuple[bool, str]:
    """超重/肥胖：BMI ≥24（中国标准）。"""
    if df_vitals.empty or "weight_kg" not in df_vitals.columns:
        return False, ""
    latest_w = df_vitals["weight_kg"].dropna()
    if latest_w.empty:
        return False, ""
    height_cm = float(profile.get("height_cm", 173))
    bmi = float(latest_w.iloc[-1]) / (height_cm / 100) ** 2
    if bmi >= 28:
        return True, f"肥胖（BMI {bmi:.1f}≥28）"
    if bmi >= 24:
        return True, f"超重（BMI {bmi:.1f}≥24）"
    return False, ""


def _check_hypertension(df_vitals: pd.DataFrame) -> tuple[bool, str]:
    """高血压。"""
    if df_vitals.empty:
        return False, ""
    if "systolic" in df_vitals.columns:
        sbp = df_vitals["systolic"].dropna()
        if not sbp.empty and float(sbp.iloc[-1]) >= 140:
            return True, f"高血压（收缩压 {float(sbp.iloc[-1]):.0f}≥140）"
    if "diastolic" in df_vitals.columns:
        dbp = df_vitals["diastolic"].dropna()
        if not dbp.empty and float(dbp.iloc[-1]) >= 90:
            return True, f"高血压（舒张压 {float(dbp.iloc[-1]):.0f}≥90）"
    return False, ""


def _check_dyslipidemia(df_lab: pd.DataFrame) -> tuple[bool, str]:
    """血脂异常：TG >1.7 或 HDL-C <1.0。"""
    if df_lab.empty:
        return False, ""
    reasons = []
    tg = df_lab[df_lab["item_name"].str.contains("甘油三酯|TG|Triglyceride", case=False, na=False)]
    if not tg.empty:
        val = float(tg["value"].iloc[-1])
        if val > 1.7:
            reasons.append(f"TG {val:.2f}>1.7")
    hdl = df_lab[df_lab["item_name"].str.contains("高密度|HDL", case=False, na=False)]
    if not hdl.empty:
        val = float(hdl["value"].iloc[-1])
        if val < 1.0:
            reasons.append(f"HDL-C {val:.2f}<1.0")
    if reasons:
        return True, f"血脂异常（{'，'.join(reasons)}）"
    return False, ""


def _check_hyperuricemia(df_lab: pd.DataFrame) -> tuple[bool, str]:
    """高尿酸血症。"""
    if df_lab.empty:
        return False, ""
    ua = df_lab[df_lab["item_name"].str.contains("尿酸|Uric Acid|UA", case=False, na=False)]
    if not ua.empty:
        val = float(ua["value"].iloc[-1])
        if val > 420:
            return True, f"高尿酸（{val:.0f}>420 μmol/L）"
    return False, ""


def _check_high_fbg_history() -> tuple[bool, str]:
    """空腹血糖偏高史：近 5 次体检中曾 ≥5.6。"""
    df = load_annual_checkups()
    if df.empty:
        return False, ""
    recent = df[["checkup_date", "fasting_glucose"]].dropna(subset=["fasting_glucose"]).tail(5)
    high = recent[recent["fasting_glucose"] >= 5.6]
    if not high.empty:
        max_val = float(high["fasting_glucose"].max())
        return True, f"空腹血糖偏高史（最高 {max_val:.2f}≥5.6）"
    return False, ""


# ─── 代谢综合征评估 ──────────────────────────────────────────────────

def _check_metabolic_syndrome(
    fbg: Optional[float],
    df_vitals: pd.DataFrame,
    profile: dict,
    df_lab: pd.DataFrame,
) -> tuple[bool, int, list[str]]:
    """代谢综合征（≥3 项即为代谢综合征）：
    1. 腹型肥胖（BMI ≥24 替代）
    2. TG >1.7
    3. HDL-C <1.0
    4. 血压 ≥130/85
    5. FBG ≥5.6（或已诊断糖尿病）
    """
    components = []

    bmi_val = None
    if not df_vitals.empty and "weight_kg" in df_vitals.columns:
        w = df_vitals["weight_kg"].dropna()
        if not w.empty:
            height_cm = float(profile.get("height_cm", 173))
            bmi_val = float(w.iloc[-1]) / (height_cm / 100) ** 2
            if bmi_val >= 24:
                components.append(f"BMI {bmi_val:.1f}≥24")

    if not df_lab.empty:
        tg = df_lab[df_lab["item_name"].str.contains("甘油三酯|TG|Triglyceride", case=False, na=False)]
        if not tg.empty and float(tg["value"].iloc[-1]) > 1.7:
            components.append(f"TG {float(tg['value'].iloc[-1]):.2f}>1.7")
        hdl = df_lab[df_lab["item_name"].str.contains("高密度|HDL", case=False, na=False)]
        if not hdl.empty and float(hdl["value"].iloc[-1]) < 1.0:
            components.append(f"HDL-C {float(hdl['value'].iloc[-1]):.2f}<1.0")

    if not df_vitals.empty:
        if "systolic" in df_vitals.columns:
            sbp = df_vitals["systolic"].dropna()
            if not sbp.empty and float(sbp.iloc[-1]) >= 130:
                components.append(f"收缩压 {float(sbp.iloc[-1]):.0f}≥130")
        if "diastolic" in df_vitals.columns:
            dbp = df_vitals["diastolic"].dropna()
            if not dbp.empty and float(dbp.iloc[-1]) >= 85:
                components.append(f"舒张压 {float(dbp.iloc[-1]):.0f}≥85")

    if fbg is not None and fbg >= 5.6:
        components.append(f"空腹血糖 {fbg:.1f}≥5.6")

    has = len(components) >= 3
    return has, len(components), components


# ─── 风险分层 ────────────────────────────────────────────────────────

RISK_LOW = "低风险"
RISK_MEDIUM = "中等风险"
RISK_HIGH = "高风险"
RISK_VERY_HIGH = "极高风险"


def _risk_stratify(
    glu_grade: int,
    n_risk_factors: int,
    has_metabolic_syndrome: bool,
    glu_trend: Optional[float],
) -> str:
    """综合风险分层。"""
    # 糖尿病范围
    if glu_grade == GLU_DIABETES:
        return RISK_VERY_HIGH

    # 糖尿病前期
    if glu_grade in (GLU_PREDIABETES_IFG, GLU_PREDIABETES_HBA1C):
        if has_metabolic_syndrome or n_risk_factors >= 3:
            return RISK_HIGH
        return RISK_MEDIUM

    # 血糖正常
    if has_metabolic_syndrome:
        return RISK_MEDIUM
    if n_risk_factors >= 3:
        return RISK_MEDIUM
    if n_risk_factors >= 1 and glu_trend is not None and glu_trend > 0.05:
        return RISK_MEDIUM
    if n_risk_factors >= 2:
        return RISK_MEDIUM
    return RISK_LOW


def _risk_to_score(risk_level: str, fbg: Optional[float], hba1c: Optional[float]) -> float:
    """风险等级 → 0-100 分。"""
    fbg_factor = 0.0
    if fbg is not None:
        fbg_factor = max(0, min(1, (fbg - 4.0) / 4.0))
    hba1c_factor = 0.0
    if hba1c is not None:
        hba1c_factor = max(0, min(1, (hba1c - 4.0) / 3.0))
    detail = (fbg_factor + hba1c_factor) / 2

    ranges = {
        RISK_LOW: (0, 25),
        RISK_MEDIUM: (25, 50),
        RISK_HIGH: (50, 75),
        RISK_VERY_HIGH: (75, 100),
    }
    lo, hi = ranges[risk_level]
    return round(lo + (hi - lo) * detail * 0.7, 1)


# ─── 主计算函数 ──────────────────────────────────────────────────────

def compute(days: int = 90) -> dict:
    """基于中国2型糖尿病防治指南计算高血糖风险分层。"""
    profile = get_user_profile()
    df_lab = load_lab_results()
    df_vitals = load_vitals(30)

    # ── 血糖数据 ──────────────────────────────────────────────────
    fbg, hba1c = _get_latest_glucose()
    glu_grade = _glucose_grade(fbg, hba1c)
    grade_label = GLU_LABELS[glu_grade]
    glu_trend, trend_label = _get_glucose_trend()

    # ── 危险因素 ──────────────────────────────────────────────────
    risk_checks = [
        _check_age_risk(profile),
        _check_overweight(df_vitals, profile),
        _check_hypertension(df_vitals),
        _check_dyslipidemia(df_lab),
        _check_hyperuricemia(df_lab),
        _check_high_fbg_history(),
    ]
    risk_factors = [desc for hit, desc in risk_checks if hit]
    n_risk_factors = len(risk_factors)

    # ── 代谢综合征 ────────────────────────────────────────────────
    has_met_syn, met_syn_count, met_syn_items = _check_metabolic_syndrome(
        fbg, df_vitals, profile, df_lab,
    )

    # ── 风险分层 ──────────────────────────────────────────────────
    risk_level = _risk_stratify(glu_grade, n_risk_factors, has_met_syn, glu_trend)
    score = _risk_to_score(risk_level, fbg, hba1c)

    # ── 因子贡献 ──────────────────────────────────────────────────
    factors = {
        "血糖分级": min(100, glu_grade * 30),
        "危险因素": min(100, n_risk_factors * 20),
        "代谢综合征": min(100, met_syn_count * 25),
        "血糖趋势": 40 if (glu_trend is not None and glu_trend > 0.05) else 0,
    }

    # ── 文字解读 ──────────────────────────────────────────────────
    parts = [f"高血糖风险：{risk_level}（{score:.1f}分）"]

    glu_parts = []
    if fbg is not None:
        glu_parts.append(f"空腹血糖 {fbg:.2f} mmol/L")
    if hba1c is not None:
        glu_parts.append(f"HbA1c {hba1c:.1f}%")
    if glu_parts:
        parts.append(f"{'，'.join(glu_parts)}（{grade_label}）")

    if trend_label:
        trend_str = f"血糖趋势：{trend_label}"
        if glu_trend is not None:
            trend_str += f"（{glu_trend:+.3f} mmol/L/次）"
        parts.append(trend_str)

    if risk_factors:
        parts.append(f"危险因素（{n_risk_factors}项）：{'；'.join(risk_factors)}")

    if has_met_syn:
        parts.append(f"代谢综合征（{met_syn_count}/5项）：{'；'.join(met_syn_items)}")

    advice_map = {
        RISK_LOW: "血糖正常，继续保持健康饮食和规律运动。",
        RISK_MEDIUM: "存在糖尿病危险因素或血糖偏高趋势，建议控制碳水摄入、增加有氧运动、保持健康体重。",
        RISK_HIGH: "糖尿病前期合并多项危险因素，建议定期监测血糖，必要时进行 OGTT 检查。",
        RISK_VERY_HIGH: "血糖已达糖尿病范围，建议尽快就医评估，进行糖耐量试验和相关检查。",
    }
    parts.append(advice_map[risk_level])
    summary = "\n".join(parts)

    return {
        "score": score,
        "factors": factors,
        "summary": summary,
        "status_label": risk_level,
        "latest_fbg": fbg,
        "latest_hba1c": hba1c,
        "glu_grade": grade_label,
        "glu_trend": trend_label,
        "risk_factors": risk_factors,
        "metabolic_syndrome": met_syn_items if has_met_syn else [],
        "met_syn_count": met_syn_count,
    }
