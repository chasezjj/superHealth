"""尿酸发作风险评分器。

基于《中国高尿酸血症与痛风诊疗指南（2023版）》：
1. 尿酸分级（>420 诊断高尿酸血症，>540 建议药物治疗）
2. 合并症评估（高血压、CKD、血脂异常、糖尿病）
3. 痛风发作诱因评估（肾结石、天气、运动、体重变化）
4. 综合风险分层 → 0-100 分

参考：中华医学会内分泌学分会《中国高尿酸血症与痛风诊疗指南（2019）》
      中华医学会风湿病学分会《痛风诊疗规范（2023）》
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from superhealth.dashboard.data_loader import (
    load_exercises,
    load_lab_results,
    load_vitals,
    load_weather,
)

# ─── 尿酸分级 ────────────────────────────────────────────────────────

UA_NORMAL = 0  # ≤420
UA_ELEVATED = 1  # 420-480（高尿酸血症，可先生活方式干预）
UA_HIGH = 2  # 480-540（合并症时需药物治疗）
UA_VERY_HIGH = 3  # >540（无论有无合并症均建议药物治疗）

UA_GRADE_LABELS = {
    UA_NORMAL: "正常",
    UA_ELEVATED: "偏高（高尿酸血症）",
    UA_HIGH: "显著升高",
    UA_VERY_HIGH: "严重升高",
}


def _grade_ua(ua: Optional[float]) -> int:
    if ua is None:
        return UA_NORMAL
    if ua <= 420:
        return UA_NORMAL
    if ua <= 480:
        return UA_ELEVATED
    if ua <= 540:
        return UA_HIGH
    return UA_VERY_HIGH


# ─── 合并症评估 ──────────────────────────────────────────────────────


def _check_hypertension(df_vitals: pd.DataFrame) -> tuple[bool, str]:
    """高血压合并症：最新收缩压 ≥140 或舒张压 ≥90。"""
    if df_vitals.empty:
        return False, ""
    reasons = []
    if "systolic" in df_vitals.columns:
        sbp = df_vitals["systolic"].dropna()
        if not sbp.empty and float(sbp.iloc[-1]) >= 140:
            reasons.append(f"收缩压 {float(sbp.iloc[-1]):.0f}≥140")
    if "diastolic" in df_vitals.columns:
        dbp = df_vitals["diastolic"].dropna()
        if not dbp.empty and float(dbp.iloc[-1]) >= 90:
            reasons.append(f"舒张压 {float(dbp.iloc[-1]):.0f}≥90")
    if reasons:
        return True, f"高血压（{'，'.join(reasons)} mmHg）"
    return False, ""


def _check_ckd(df_lab: pd.DataFrame) -> tuple[bool, str]:
    """慢性肾病：肌酐 >133 或尿素 >8.3。"""
    if df_lab.empty:
        return False, ""
    reasons = []
    cr = df_lab[df_lab["item_name"].str.contains("肌酐|Creatinine", case=False, na=False)]
    if not cr.empty:
        val = float(cr["value"].iloc[-1])
        if val > 133:
            reasons.append(f"肌酐 {val:.0f}>133")
    urea = df_lab[df_lab["item_name"].str.contains("尿素|Urea", case=False, na=False)]
    if not urea.empty:
        val = float(urea["value"].iloc[-1])
        if val > 8.3:
            reasons.append(f"尿素 {val:.1f}>8.3")
    if reasons:
        return True, f"肾功能异常（{'，'.join(reasons)}）"
    return False, ""


def _check_dyslipidemia_for_ua(df_lab: pd.DataFrame) -> tuple[bool, str]:
    """血脂异常合并症。"""
    if df_lab.empty:
        return False, ""
    reasons = []
    tg = df_lab[df_lab["item_name"].str.contains("甘油三酯|TG|Triglyceride", case=False, na=False)]
    if not tg.empty:
        val = float(tg["value"].iloc[-1])
        if val > 1.7:
            reasons.append(f"TG {val:.2f}>1.7")
    ldl = df_lab[df_lab["item_name"].str.contains("低密度|LDL", case=False, na=False)]
    if not ldl.empty:
        val = float(ldl["value"].iloc[-1])
        if val > 3.4:
            reasons.append(f"LDL {val:.2f}>3.4")
    if reasons:
        return True, f"血脂异常（{'，'.join(reasons)}）"
    return False, ""


def _check_diabetes_for_ua(df_lab: pd.DataFrame) -> tuple[bool, str]:
    """糖尿病合并症。"""
    if df_lab.empty:
        return False, ""
    fg = df_lab[
        df_lab["item_name"].str.contains("空腹血糖|FBG|GLU|Fasting Glucose", case=False, na=False)
    ]
    if not fg.empty:
        val = float(fg["value"].iloc[-1])
        if val >= 7.0:
            return True, f"空腹血糖 {val:.1f}≥7.0"
    return False, ""


# ─── 痛风发作诱因 ─────────────────────────────────────────────────────


def _check_kidney_stones(df_lab: pd.DataFrame) -> tuple[bool, str]:
    """肾结石/钙化（超声发现）→ 尿酸排泄障碍 + 痛风石风险。"""
    from superhealth.database import DEFAULT_DB_PATH, get_conn

    with get_conn(DEFAULT_DB_PATH) as conn:
        row = conn.execute(
            """SELECT value_text AS conclusion FROM medical_observations
               WHERE category='ultrasound' AND body_site='kidney' AND value_text IS NOT NULL
               ORDER BY obs_date DESC LIMIT 1"""
        ).fetchone()
    if row and row["conclusion"]:
        conclusion = row["conclusion"]
        # 阳性发现：结石或钙化（排除"未提及"、"未见"等否定表述）
        negative = any(kw in conclusion for kw in ["未提及", "未见", "无明显", "未发现"])
        if not negative and any(kw in conclusion for kw in ["结石", "钙化", "强回声"]):
            return True, f"肾超声提示（{conclusion}）"
    return False, ""


def _check_cold_weather(df_weather: pd.DataFrame) -> tuple[bool, str]:
    """低温诱发痛风：气温 <10°C。"""
    if df_weather.empty:
        return False, ""
    latest = df_weather.sort_values("date").iloc[-1]
    temp = latest.get("temperature")
    if temp is not None and float(temp) < 10:
        return True, f"低温（{float(temp):.0f}°C，低于10°C阈值）"
    return False, ""


def _check_intense_exercise(df_ex: pd.DataFrame) -> tuple[bool, str]:
    """高强度运动：近7天 ≥3 次高强度 → 乳酸竞争排泄 + 肌肉分解产尿酸。"""
    if df_ex.empty:
        return False, ""
    week_ago = date.today() - timedelta(days=7)
    recent = df_ex[df_ex["date"].dt.date >= week_ago]
    if "is_high_intensity" not in recent.columns:
        return False, ""
    hi = int(recent["is_high_intensity"].sum())
    if hi >= 3:
        return True, f"高强度运动 {hi}次/周（≥3）"
    return False, ""


def _check_rapid_weight_loss(df_vitals: pd.DataFrame) -> tuple[bool, str]:
    """快速减重：近7天减重 >1kg → 组织分解产尿酸。"""
    if df_vitals.empty or "weight_kg" not in df_vitals.columns:
        return False, ""
    w = df_vitals[df_vitals["weight_kg"].notna()].sort_values("measured_at")
    if len(w) < 2:
        return False, ""
    week_ago = pd.Timestamp.now() - pd.Timedelta(days=7)
    recent = w[w["measured_at"] >= week_ago]
    if len(recent) < 2:
        return False, ""
    loss = float(recent["weight_kg"].iloc[0]) - float(recent["weight_kg"].iloc[-1])
    if loss > 1.0:
        return True, f"近7天减重 {loss:.1f}kg（>1kg）"
    return False, ""


# ─── 尿酸趋势 ────────────────────────────────────────────────────────


def _check_ua_trend(df_lab: pd.DataFrame) -> tuple[bool, str]:
    """尿酸上升趋势：近3次化验斜率 >30 μmol/L/次。"""
    if df_lab.empty:
        return False, ""
    ua = df_lab[df_lab["item_name"].str.contains("尿酸|Uric Acid|UA", case=False, na=False)]
    if len(ua) < 2:
        return False, ""
    recent = ua.tail(3)
    vals = recent["value"].dropna().values
    if len(vals) < 2:
        return False, ""
    x = np.arange(len(vals))
    slope = float(np.polyfit(x, vals, 1)[0])
    if slope > 30:
        return True, f"尿酸上升趋势（+{slope:.0f} μmol/L/次）"
    return False, ""


# ─── 综合风险分层 ─────────────────────────────────────────────────────

RISK_LOW = "低风险"
RISK_MEDIUM = "中等风险"
RISK_HIGH = "高风险"
RISK_VERY_HIGH = "极高风险"


def _risk_stratify(
    ua_grade: int,
    n_comorbidities: int,
    n_triggers: int,
    has_stone: bool,
) -> str:
    """基于指南的综合风险分层。

    - UA >540：无论合并症均需药物 → 高风险起步
    - UA 480-540 + 合并症：需药物 → 高风险
    - UA 420-480 + 合并症：中等风险
    - 肾结石 + 高尿酸：额外提升
    - 多项诱因叠加：急性发作风险上升
    """
    if ua_grade == UA_VERY_HIGH:
        return RISK_VERY_HIGH

    if ua_grade == UA_HIGH:
        if n_comorbidities > 0 or has_stone:
            return RISK_VERY_HIGH
        if n_triggers >= 2:
            return RISK_HIGH
        return RISK_HIGH

    if ua_grade == UA_ELEVATED:
        if n_comorbidities >= 2:
            return RISK_HIGH
        if n_comorbidities >= 1 or has_stone:
            return RISK_MEDIUM
        if n_triggers >= 2:
            return RISK_MEDIUM
        return RISK_MEDIUM

    # UA 正常
    if n_triggers >= 3:
        return RISK_MEDIUM
    return RISK_LOW


def _risk_to_score(risk_level: str, ua: Optional[float]) -> float:
    """风险等级 → 0-100 分，等级内按尿酸绝对值细化。"""
    ua_factor = 0.0
    if ua is not None:
        ua_factor = min(1.0, max(0.0, ua / 600))
    ranges = {
        RISK_LOW: (0, 25),
        RISK_MEDIUM: (25, 50),
        RISK_HIGH: (50, 75),
        RISK_VERY_HIGH: (75, 100),
    }
    lo, hi = ranges[risk_level]
    return round(lo + (hi - lo) * ua_factor * 0.7, 1)


# ─── 主计算函数 ──────────────────────────────────────────────────────


def compute(days: int = 90) -> dict:
    """基于中国高尿酸血症与痛风诊疗指南计算风险分层。"""
    df_lab = load_lab_results()
    df_vitals = load_vitals(30)
    df_ex = load_exercises(days)
    df_weather = load_weather(3)

    # ── 最新尿酸 ──────────────────────────────────────────────────
    ua_df = df_lab[df_lab["item_name"].str.contains("尿酸|Uric Acid|UA", case=False, na=False)]
    latest_ua = float(ua_df["value"].iloc[-1]) if not ua_df.empty else None
    ua_grade = _grade_ua(latest_ua)
    grade_label = UA_GRADE_LABELS[ua_grade]

    # ── 合并症 ────────────────────────────────────────────────────
    comorbidity_checks = [
        _check_hypertension(df_vitals),
        _check_ckd(df_lab),
        _check_dyslipidemia_for_ua(df_lab),
        _check_diabetes_for_ua(df_lab),
    ]
    comorbidities = [desc for hit, desc in comorbidity_checks if hit]
    n_comorbidities = len(comorbidities)

    # ── 发作诱因 ──────────────────────────────────────────────────
    trigger_checks = [
        _check_kidney_stones(df_lab),
        _check_cold_weather(df_weather),
        _check_intense_exercise(df_ex),
        _check_rapid_weight_loss(df_vitals),
        _check_ua_trend(df_lab),
    ]
    triggers = [desc for hit, desc in trigger_checks if hit]
    has_stone = any(hit for hit, _ in [trigger_checks[0]])

    # ── 风险分层 ──────────────────────────────────────────────────
    risk_level = _risk_stratify(ua_grade, n_comorbidities, len(triggers), has_stone)
    score = _risk_to_score(risk_level, latest_ua)

    # ── 因子贡献（供图表）─────────────────────────────────────────
    factors = {
        "尿酸水平": min(100, ua_grade * 33),
        "合并症": min(100, n_comorbidities * 35),
        "发作诱因": min(100, len(triggers) * 25),
        "尿酸趋势": 60 if any(hit for hit, _ in [_check_ua_trend(df_lab)]) else 0,
    }

    # ── 文字解读 ──────────────────────────────────────────────────
    ua_str = f"{latest_ua:.0f} μmol/L" if latest_ua else "未知"
    parts = [f"高尿酸风险：{risk_level}（{score:.1f}分）"]
    parts.append(f"尿酸 {ua_str}（{grade_label}）")
    if comorbidities:
        parts.append(f"合并症（{n_comorbidities}项）：{'；'.join(comorbidities)}")
    if triggers:
        parts.append(f"发作诱因（{len(triggers)}项）：{'；'.join(triggers)}")

    advice_map = {
        RISK_LOW: "尿酸正常，继续保持低嘌呤饮食、多饮水。",
        RISK_MEDIUM: "尿酸偏高或存在合并症/诱因，建议限制高嘌呤食物（内脏、海鲜、浓汤），日饮水 ≥2L。",
        RISK_HIGH: "尿酸显著升高或合并多项危险因素，建议遵医嘱用药控制，定期复查尿酸和肾功能。",
        RISK_VERY_HIGH: "尿酸严重升高或合并严重合并症，需药物干预（指南建议 >540 或 >480+合并症时启动降尿酸治疗）。",
    }
    parts.append(advice_map[risk_level])
    summary = "\n".join(parts)

    return {
        "score": score,
        "factors": factors,
        "summary": summary,
        "status_label": risk_level,
        "latest_ua": latest_ua,
        "ua_grade": grade_label,
        "comorbidities": comorbidities,
        "triggers": triggers,
    }
