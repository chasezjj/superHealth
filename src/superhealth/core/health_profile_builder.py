"""健康画像构建器：自动从多源数据发现用户健康画像。

数据源：
- SQLite: medications, lab_results, eye_exams, annual_checkups, vitals, learned_preferences
- Markdown: data/genetic-data/, data/medical-records/

输出 HealthProfile，供 ModelSelector 和 LLMAdvisor 使用。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Optional

from superhealth import database as db
from superhealth.analysis.trends import TrendAnalyzer

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent.parent  # healthy/
DB_PATH = REPO_ROOT / "health.db"
GENETIC_DIR = REPO_ROOT / "data" / "genetic-data"
MEDICAL_DIR = REPO_ROOT / "data" / "medical-records"


@dataclass
class BodyComposition:
    """体成分评估。"""

    bmi: Optional[float] = None
    bmi_status: str = "unknown"  # underweight/normal/overweight/obese
    body_fat_pct: Optional[float] = None
    body_fat_status: str = "unknown"  # low/normal/high
    weight_kg: Optional[float] = None
    assessment: str = ""  # 综合判断描述
    target: str = "maintain"  # muscle_gain/fat_loss/maintain/recomposition
    priority_training: str = "balanced"  # resistance/cardio/balanced


@dataclass
class HealthProfile:
    """结构化健康画像。"""

    # 已确诊疾病
    conditions: list[str] = field(default_factory=list)
    # 历史疾病（已缓解但需关注）
    history_conditions: list[str] = field(default_factory=list)
    # 当前用药
    active_medications: list[dict] = field(default_factory=list)
    # 基因标记（从基因报告解析）
    genetic_markers: dict[str, str] = field(default_factory=dict)
    # 风险因素（从趋势/指标发现）
    risk_factors: list[str] = field(default_factory=list)
    # 近期趋势摘要
    trends: dict[str, Any] = field(default_factory=dict)
    # 运动禁忌（自动推导）
    exercise_contraindications: list[str] = field(default_factory=list)
    # 运动优先目标（自动推导）
    exercise_priorities: list[str] = field(default_factory=list)
    # 体成分
    body_composition: BodyComposition = field(default_factory=BodyComposition)
    # 学习到的个人偏好（从 learned_preferences 表）
    learned_preferences: dict[str, str] = field(default_factory=dict)
    # 画像数据来源（用于报告展示）
    profile_sources: list[dict] = field(default_factory=list)
    # 患者基本信息（从体检推算）
    height_cm: Optional[float] = None
    # 工作日/休息日模式分析
    workday_patterns: dict = field(default_factory=dict)
    # 活跃阶段性目标
    active_goals: list[dict] = field(default_factory=list)

    def add_source(self, category: str, finding: str, source: str):
        self.profile_sources.append(
            {
                "category": category,
                "finding": finding,
                "source": source,
            }
        )


class HealthProfileBuilder:
    """从多源数据自动构建健康画像。"""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.trend_analyzer = TrendAnalyzer(db_path)

    def _get_conn(self):
        return db.get_conn(self.db_path)

    def build(self, reference_date: str | None = None) -> HealthProfile:
        """构建健康画像。reference_date: YYYY-MM-DD，默认今天。"""
        if reference_date is None:
            reference_date = date.today().isoformat()

        profile = HealthProfile()

        self._load_medical_records(profile)
        self._load_medications(profile)
        self._load_lab_trends(profile, reference_date)
        self._load_vitals_trends(profile, reference_date)
        # 原 _load_stress_trends / _load_sleep_trends / _load_activity_trends / _load_rhr_trends
        # 合并为单次 daily_health 批量查询（4 次独立连接 → 1 次）
        self._load_daily_health_aggregates(profile, reference_date)
        self._load_eye_exam_trends(profile, reference_date)
        self._load_body_composition(profile, reference_date)
        self._load_genetic_markers(profile)
        self._load_learned_preferences(profile)
        self._load_workday_patterns(profile, reference_date)
        self._derive_exercise_guidance(profile)
        self._load_active_goals(profile, reference_date)

        return profile

    def _load_medical_records(self, profile: HealthProfile):
        """从 markdown 病历文件解析已确诊疾病。"""
        med_files = {
            "hyperuricemia-history.md": ("hyperuricemia", "高尿酸血症"),
            "glaucoma-history.md": ("glaucoma", "青光眼"),
        }
        for filename, (code, label) in med_files.items():
            path = MEDICAL_DIR / filename
            if path.exists():
                profile.conditions.append(code)
                profile.add_source(
                    "长期管理", f"{label}（病历记录）", f"data/medical-records/{filename}"
                )

        # 从体检报告检测历史血脂异常（通过 SQLite annual_checkups）
        try:
            with self._get_conn() as conn:
                rows = conn.execute(
                    """SELECT checkup_date, total_cholesterol, ldl_c, triglyceride
                       FROM annual_checkups
                       WHERE total_cholesterol > 5.2 OR ldl_c > 3.4 OR triglyceride > 1.7
                       ORDER BY checkup_date"""
                ).fetchall()
                if rows:
                    profile.history_conditions.append("dyslipidemia_history")
                    dates = [r["checkup_date"] for r in rows]
                    profile.add_source(
                        "历史状况", f"血脂异常（{dates[0]}–{dates[-1]}，曾干预）", "annual_checkups"
                    )
        except Exception as e:
            log.debug("血脂历史查询失败: %s", e)

        # 从肾超声检测肾结石历史
        try:
            with self._get_conn() as conn:
                rows = conn.execute(
                    """SELECT date, conclusion FROM kidney_ultrasounds
                       WHERE conclusion LIKE '%结石%' OR right_finding LIKE '%结石%'
                            OR left_finding LIKE '%结石%'
                       ORDER BY date"""
                ).fetchall()
                if rows:
                    if "kidney_stone_history" not in profile.history_conditions:
                        profile.history_conditions.append("kidney_stone_history")
                    profile.add_source(
                        "历史状况", f"肾结石（{rows[0]['date']}起，需随访）", "kidney_ultrasounds"
                    )
        except Exception as e:
            log.debug("肾结石历史查询失败: %s", e)

    def _load_medications(self, profile: HealthProfile):
        """从 SQLite medications 表加载当前用药。"""
        try:
            with self._get_conn() as conn:
                meds = db.query_active_medications(conn)
                for m in meds:
                    profile.active_medications.append(
                        {
                            "name": m["name"],
                            "condition": m["condition"],
                            "dosage": m.get("dosage", ""),
                            "notes": m.get("note", ""),
                        }
                    )
                if meds:
                    names = "、".join(m["name"] for m in meds)
                    profile.add_source("当前用药", names, "medications")
        except Exception as e:
            log.debug("用药查询失败: %s", e)

    def _load_lab_trends(self, profile: HealthProfile, reference_date: str):
        """从 lab_results 检测近期化验指标趋势。"""
        try:
            with self._get_conn() as conn:
                # 最近两次尿酸
                rows = conn.execute(
                    """SELECT date, value FROM lab_results
                       WHERE item_name LIKE '%尿酸%'
                       ORDER BY date DESC LIMIT 2"""
                ).fetchall()
                if rows:
                    latest = rows[0]
                    ua_val = latest["value"]
                    ua_status = "正常" if ua_val and ua_val <= 420 else "偏高"
                    profile.trends["uric_acid"] = ua_status
                    profile.add_source(
                        "当前关注",
                        f"尿酸 {ua_val} μmol/L（{latest['date']}，{ua_status}）",
                        "lab_results",
                    )

                # 最近血脂全项
                for item_code, trend_key in [("LDL-C", "ldl"), ("TG", "tg"), ("HDL-C", "hdl")]:
                    row = conn.execute(
                        """SELECT date, value FROM lab_results
                           WHERE item_code = ? ORDER BY date DESC LIMIT 1""",
                        (item_code,),
                    ).fetchone()
                    if row and row["value"] is not None:
                        profile.trends[f"{trend_key}_latest"] = row["value"]
                        profile.trends[f"{trend_key}_date"] = row["date"]

                ldl_latest = profile.trends.get("ldl_latest")
                ldl_date = profile.trends.get("ldl_date", "")
                if ldl_latest and float(ldl_latest) > 3.4:
                    profile.risk_factors.append("ldl_borderline")
                    profile.add_source(
                        "当前关注",
                        f"LDL {ldl_latest:.2f} mmol/L（偏高，{ldl_date}）",
                        "lab_results",
                    )
                elif ldl_latest:
                    profile.add_source(
                        "当前关注",
                        f"LDL {ldl_latest:.2f} mmol/L（{ldl_date}，正常范围）",
                        "lab_results",
                    )
        except Exception as e:
            log.debug("化验趋势查询失败: %s", e)

    def _load_vitals_trends(self, profile: HealthProfile, reference_date: str):
        """从 vitals 检测血压趋势。单次查询 30d 数据，7d 子集在内存过滤。"""
        try:
            end = reference_date
            start_30d = (datetime.fromisoformat(reference_date) - timedelta(days=30)).isoformat()[
                :10
            ]
            start_7d = (datetime.fromisoformat(reference_date) - timedelta(days=7)).isoformat()[:10]

            with self._get_conn() as conn:
                rows_30d = conn.execute(
                    """SELECT measured_at, systolic, diastolic FROM vitals
                       WHERE measured_at >= ? AND measured_at < ? || 'T23:59:59'
                         AND systolic IS NOT NULL
                       ORDER BY measured_at""",
                    (start_30d + "T00:00:00", end),
                ).fetchall()

            # 7d 子集在内存过滤，避免重复查询
            rows_7d = [r for r in rows_30d if r["measured_at"] >= start_7d + "T00:00:00"]

            if rows_7d:
                profile.risk_factors.append("has_recent_bp_data")
                avg_sys_7d = mean([r["systolic"] for r in rows_7d])
                profile.trends["bp_7d_avg_systolic"] = round(avg_sys_7d, 1)
                dia_vals = [r["diastolic"] for r in rows_7d if r["diastolic"] is not None]
                if dia_vals:
                    profile.trends["bp_7d_avg_diastolic"] = round(mean(dia_vals), 1)

            if rows_30d and rows_7d:
                avg_30d = mean([r["systolic"] for r in rows_30d])
                avg_7d = mean([r["systolic"] for r in rows_7d])

                if avg_7d > avg_30d + 5:
                    profile.trends["bp"] = "rising"
                    profile.risk_factors.append("bp_trending_up")
                    profile.add_source(
                        "当前关注",
                        f"血压上升趋势（7天均值 {avg_7d:.0f} vs 30天均值 {avg_30d:.0f}）",
                        "vitals 趋势分析",
                    )
                elif avg_7d < avg_30d - 5:
                    profile.trends["bp"] = "falling"
                else:
                    profile.trends["bp"] = "stable"

                # 检测高血压风险
                high_count = sum(1 for r in rows_7d if r["systolic"] >= 130)
                if high_count >= 3:
                    if "bp_trending_up" not in profile.risk_factors:
                        profile.risk_factors.append("bp_elevated")
                    profile.add_source(
                        "当前关注", f"近7天有 {high_count} 次收缩压≥130mmHg", "vitals"
                    )
        except Exception as e:
            log.debug("血压趋势查询失败: %s", e)

    def _load_daily_health_aggregates(self, profile: HealthProfile, reference_date: str):
        """单次查询填充压力/睡眠/步数/静息心率趋势（原 4 个独立连接合并为 1 次）。"""
        try:
            yesterday = (datetime.fromisoformat(reference_date) - timedelta(days=1)).isoformat()[
                :10
            ]
            start_90d = (datetime.fromisoformat(reference_date) - timedelta(days=90)).isoformat()[
                :10
            ]
            start_7d = (datetime.fromisoformat(reference_date) - timedelta(days=6)).isoformat()[:10]

            with self._get_conn() as conn:
                rows = conn.execute(
                    """SELECT date, stress_average, sleep_score, steps, hr_resting, hrv_last_night_avg, bb_at_wake
                       FROM daily_health
                       WHERE date >= ? AND date <= ?""",
                    (start_90d, reference_date),
                ).fetchall()

            if not rows:
                return

            # 分组：90天（不含今日）和 7 天（含今日）
            rows_90d = [r for r in rows if r["date"] <= yesterday]
            rows_7d = [r for r in rows if r["date"] >= start_7d]

            def _agg(vals):
                return round(mean(vals), 1), round(pstdev(vals), 2) if len(vals) >= 2 else (
                    round(mean(vals), 1),
                    0.0,
                )

            # ── 压力趋势 ──
            stress_yesterday = next(
                (
                    r["stress_average"]
                    for r in rows
                    if r["date"] == yesterday and r["stress_average"] is not None
                ),
                None,
            )
            if stress_yesterday is not None:
                profile.trends["stress_yesterday"] = round(stress_yesterday, 1)

            stress_90d_vals = [
                r["stress_average"] for r in rows_90d if r["stress_average"] is not None
            ]
            stress_7d_vals = [
                r["stress_average"] for r in rows_7d if r["stress_average"] is not None
            ]
            if stress_90d_vals:
                mu, std = _agg(stress_90d_vals)
                profile.trends["stress_90d_avg"] = mu
                profile.trends["stress_90d_std"] = std
            if stress_7d_vals:
                profile.trends["stress_7d_avg"] = round(mean(stress_7d_vals), 1)
            if stress_90d_vals and stress_7d_vals:
                diff = mean(stress_7d_vals) - mean(stress_90d_vals)
                _, std = _agg(stress_90d_vals)
                threshold = max(5, 1.5 * std) if std else 5
                if diff > threshold:
                    profile.risk_factors.append("stress_trending_up")

            # ── 睡眠趋势 ──
            sleep_90d_vals = [r["sleep_score"] for r in rows_90d if r["sleep_score"] is not None]
            sleep_7d_vals = [r["sleep_score"] for r in rows_7d if r["sleep_score"] is not None]
            if sleep_90d_vals:
                mu, std = _agg(sleep_90d_vals)
                profile.trends["sleep_90d_avg"] = mu
                profile.trends["sleep_90d_std"] = std
            if sleep_7d_vals:
                profile.trends["sleep_7d_avg"] = round(mean(sleep_7d_vals), 1)

            # ── 步数趋势 ──
            # 排除今日（当日数据不完整），仅用昨日及之前的7天数据
            rows_7d_steps = [r for r in rows_7d if r["date"] < reference_date]
            steps_7d_vals = [r["steps"] for r in rows_7d_steps if r["steps"] is not None]
            steps_90d_vals = [r["steps"] for r in rows_90d if r["steps"] is not None]
            if steps_7d_vals:
                profile.trends["steps_7d_avg"] = round(mean(steps_7d_vals))
            if steps_90d_vals:
                mu, std = _agg(steps_90d_vals)
                profile.trends["steps_90d_avg"] = mu
                profile.trends["steps_90d_std"] = std

            # ── 静息心率趋势 ──
            rhr_90d_vals = [r["hr_resting"] for r in rows_90d if r["hr_resting"] is not None]
            if rhr_90d_vals:
                mu, std = _agg(rhr_90d_vals)
                profile.trends["rhr_90d_avg"] = mu
                profile.trends["rhr_90d_std"] = std

            # ── HRV 趋势 ──
            hrv_90d_vals = [
                r["hrv_last_night_avg"] for r in rows_90d if r["hrv_last_night_avg"] is not None
            ]
            hrv_7d_vals = [
                r["hrv_last_night_avg"] for r in rows_7d if r["hrv_last_night_avg"] is not None
            ]
            if hrv_90d_vals:
                mu, std = _agg(hrv_90d_vals)
                profile.trends["hrv_90d_avg"] = mu
                profile.trends["hrv_90d_std"] = std
            if hrv_7d_vals:
                profile.trends["hrv_7d_avg"] = round(mean(hrv_7d_vals), 1)

            # ── Body Battery 趋势 ──
            bb_90d_vals = [r["bb_at_wake"] for r in rows_90d if r["bb_at_wake"] is not None]
            bb_7d_vals = [r["bb_at_wake"] for r in rows_7d if r["bb_at_wake"] is not None]
            if bb_90d_vals:
                mu, std = _agg(bb_90d_vals)
                profile.trends["bb_90d_avg"] = mu
                profile.trends["bb_90d_std"] = std
            if bb_7d_vals:
                profile.trends["bb_7d_avg"] = round(mean(bb_7d_vals), 1)

        except Exception as e:
            log.debug("daily_health 聚合查询失败: %s", e)

    def _load_eye_exam_trends(self, profile: HealthProfile, reference_date: str):
        """加载最近一次眼科复查结果，供青光眼模型使用。"""
        try:
            with self._get_conn() as conn:
                row = conn.execute(
                    """SELECT date, od_iop, os_iop, od_cd_ratio, os_cd_ratio,
                              fundus_note, note
                       FROM eye_exams
                       WHERE date <= ? AND (od_iop IS NOT NULL OR os_iop IS NOT NULL)
                       ORDER BY date DESC LIMIT 1""",
                    (reference_date,),
                ).fetchone()
            if row:
                if row["od_iop"] is not None:
                    profile.trends["eye_od_iop"] = row["od_iop"]
                if row["os_iop"] is not None:
                    profile.trends["eye_os_iop"] = row["os_iop"]
                if row["od_cd_ratio"] is not None:
                    profile.trends["eye_od_cdr"] = row["od_cd_ratio"]
                if row["os_cd_ratio"] is not None:
                    profile.trends["eye_os_cdr"] = row["os_cd_ratio"]
                profile.trends["eye_exam_date"] = row["date"]
                profile.trends["eye_fundus_note"] = row["fundus_note"] or ""
                profile.trends["eye_note"] = row["note"] or ""
        except Exception as e:
            log.debug("眼科复查数据加载失败: %s", e)

    def _load_body_composition(self, profile: HealthProfile, reference_date: str):
        """计算体成分指标（BMI、体脂率）。"""
        bc = profile.body_composition
        try:
            with self._get_conn() as conn:
                # 最新体重/体脂
                start_7d = (datetime.fromisoformat(reference_date) - timedelta(days=7)).isoformat()[
                    :10
                ]
                rows = conn.execute(
                    """SELECT weight_kg, body_fat_pct FROM vitals
                       WHERE measured_at >= ? AND weight_kg IS NOT NULL
                       ORDER BY measured_at DESC LIMIT 1""",
                    (start_7d + "T00:00:00",),
                ).fetchall()

                if rows:
                    bc.weight_kg = rows[0]["weight_kg"]
                    bc.body_fat_pct = rows[0]["body_fat_pct"]

                # 身高（从最近体检获取）
                height_row = conn.execute(
                    """SELECT height_cm FROM annual_checkups
                       WHERE height_cm IS NOT NULL
                       ORDER BY checkup_date DESC LIMIT 1"""
                ).fetchone()
                if height_row:
                    profile.height_cm = height_row["height_cm"]

        except Exception as e:
            log.debug("体成分查询失败: %s", e)

        # 计算 BMI
        if bc.weight_kg and profile.height_cm:
            h_m = profile.height_cm / 100
            bc.bmi = round(bc.weight_kg / (h_m**2), 1)

        # BMI 分类（亚洲标准）
        if bc.bmi is not None:
            if bc.bmi < 18.5:
                bc.bmi_status = "underweight"
            elif bc.bmi < 24.0:
                bc.bmi_status = "normal"
            elif bc.bmi < 28.0:
                bc.bmi_status = "overweight"
            else:
                bc.bmi_status = "obese"

        # 体脂率分类（男性标准）
        if bc.body_fat_pct is not None:
            if bc.body_fat_pct < 10:
                bc.body_fat_status = "low"
            elif bc.body_fat_pct < 20:
                bc.body_fat_status = "normal"
            elif bc.body_fat_pct < 25:
                bc.body_fat_status = "high"
            else:
                bc.body_fat_status = "very_high"

        # 体成分综合评估
        if bc.bmi_status == "underweight":
            bc.assessment = "偏瘦型（BMI不足）"
            bc.target = "muscle_gain"
            bc.priority_training = "resistance"
            profile.add_source(
                "体成分", f"BMI {bc.bmi}（偏瘦），优先增肌训练", "vitals + annual_checkups"
            )
        elif bc.bmi_status == "normal" and bc.body_fat_status in ("high", "very_high"):
            bc.assessment = "隐性肥胖型（BMI正常但体脂偏高）"
            bc.target = "recomposition"
            bc.priority_training = "balanced"
            profile.add_source(
                "体成分", f"体脂率 {bc.body_fat_pct}%（偏高），建议减脂增肌", "vitals"
            )
        elif bc.bmi_status in ("overweight", "obese"):
            bc.assessment = "超重/肥胖型"
            bc.target = "fat_loss"
            bc.priority_training = "cardio"
            profile.add_source(
                "体成分", f"BMI {bc.bmi}（超重），优先有氧减脂", "vitals + annual_checkups"
            )
        elif bc.bmi is not None:
            bc.assessment = "体重正常"
            bc.target = "maintain"
            bc.priority_training = "balanced"

    def _load_genetic_markers(self, profile: HealthProfile):
        """从基因报告 markdown 解析基因标记。"""
        gene_file = GENETIC_DIR / "gene-testing-2024.md"
        if not gene_file.exists():
            return

        text = gene_file.read_text(encoding="utf-8")

        # 解析已知高风险基因模式
        gene_patterns = {
            "GSTM1": r"GSTM1[^\n]*Null",
            "GSTT1": r"GSTT1[^\n]*Null",
            "CYP1A1": r"CYP1A1[^\n]*(?:CC型|rs1048943)",
            "MTHFR": r"MTHFR[^\n]*Null",
            "P53": r"P53[^\n]*CG型",
            "XPD": r"XPD[^\n]*AA型",
        }
        found_genes = []
        for gene, pattern in gene_patterns.items():
            if re.search(pattern, text):
                profile.genetic_markers[gene] = "risk_variant"
                found_genes.append(gene)

        if found_genes:
            profile.add_source(
                "基因特征",
                f"肿瘤易感基因变异：{', '.join(found_genes)}（详见基因报告）",
                "data/genetic-data/gene-testing-2024.md",
            )

    def _load_learned_preferences(self, profile: HealthProfile):
        """从 learned_preferences 表加载历史学习偏好（排除 reverted，按类型使用不同置信度阈值）。"""
        try:
            with self._get_conn() as conn:
                prefs = db.query_learned_preferences(conn, exclude_status="reverted")
                for p in prefs:
                    ctype = p["preference_type"]
                    conf = p["confidence_score"]
                    if ctype == "context_exercise":
                        threshold = 0.15
                    elif ctype == "experiment_suggestion":
                        threshold = 0.20
                    elif ctype in ("intensity", "timing"):
                        threshold = 0.35
                    else:
                        threshold = 0.60
                    if conf >= threshold:
                        profile.learned_preferences[p["preference_key"]] = p["preference_value"]
        except Exception as e:
            log.debug("学习偏好查询失败: %s", e)

    def _load_workday_patterns(self, profile: HealthProfile, reference_date: str):
        """分析工作日 vs 休息日的血压/压力差异，识别工作日综合征。"""
        try:
            start_60d = (datetime.fromisoformat(reference_date) - timedelta(days=60)).isoformat()[
                :10
            ]

            with self._get_conn() as conn:
                # 按工作日/周末分组查询血压
                bp_rows = conn.execute(
                    """SELECT systolic, strftime('%w', measured_at) as weekday
                       FROM vitals
                       WHERE measured_at >= ? AND systolic IS NOT NULL""",
                    (start_60d + "T00:00:00",),
                ).fetchall()

                # 按工作日/周末分组查询压力
                stress_rows = conn.execute(
                    """SELECT stress_average, strftime('%w', date) as weekday
                       FROM daily_health
                       WHERE date >= ? AND stress_average IS NOT NULL""",
                    (start_60d,),
                ).fetchall()

            patterns = {}

            # 分析血压差异
            if bp_rows:
                weekday_bp = [r["systolic"] for r in bp_rows if r["weekday"] not in ("0", "6")]
                weekend_bp = [r["systolic"] for r in bp_rows if r["weekday"] in ("0", "6")]

                if weekday_bp and weekend_bp:
                    weekday_avg = mean(weekday_bp)
                    weekend_avg = mean(weekend_bp)
                    patterns["bp_weekday_avg"] = round(weekday_avg, 1)
                    patterns["bp_weekend_avg"] = round(weekend_avg, 1)
                    patterns["bp_diff"] = round(weekday_avg - weekend_avg, 1)
                    patterns["bp_weekday_count"] = len(weekday_bp)
                    patterns["bp_weekend_count"] = len(weekend_bp)

            # 分析压力差异
            if stress_rows:
                weekday_stress = [
                    r["stress_average"] for r in stress_rows if r["weekday"] not in ("0", "6")
                ]
                weekend_stress = [
                    r["stress_average"] for r in stress_rows if r["weekday"] in ("0", "6")
                ]

                if weekday_stress and weekend_stress:
                    weekday_avg = mean(weekday_stress)
                    weekend_avg = mean(weekend_stress)
                    patterns["stress_weekday_avg"] = round(weekday_avg, 1)
                    patterns["stress_weekend_avg"] = round(weekend_avg, 1)
                    patterns["stress_diff"] = round(weekday_avg - weekend_avg, 1)
                    patterns["stress_weekday_count"] = len(weekday_stress)
                    patterns["stress_weekend_count"] = len(weekend_stress)

            # 判断是否存在工作日综合征
            # 定义：工作日血压比周末高 5mmHg 以上，或压力高 3 以上
            is_weekday_elevated = False
            if patterns.get("bp_diff", 0) >= 5:
                is_weekday_elevated = True
                profile.add_source(
                    "工作日模式",
                    f"工作日血压显著高于周末（{patterns['bp_weekday_avg']:.0f} vs {patterns['bp_weekend_avg']:.0f} mmHg）",
                    "vitals 分析",
                )
            if patterns.get("stress_diff", 0) >= 3:
                is_weekday_elevated = True
                profile.add_source(
                    "工作日模式",
                    f"工作日压力显著高于周末（{patterns['stress_weekday_avg']:.0f} vs {patterns['stress_weekend_avg']:.0f}）",
                    "daily_health 分析",
                )

            if is_weekday_elevated:
                patterns["is_weekday_elevated"] = True
                profile.risk_factors.append("weekday_elevated")

            profile.workday_patterns = patterns

        except Exception as e:
            log.debug("工作日模式分析失败: %s", e)

    def _derive_exercise_guidance(self, profile: HealthProfile):
        """基于健康画像自动推导运动禁忌和优先目标。"""
        contraindications = []
        priorities = []

        # 青光眼 → 避免憋气动作（Valsalva）
        if "glaucoma" in profile.conditions:
            contraindications.append("valsalva_maneuver")
            contraindications.append("inverted_positions")
            profile.add_source("运动注意", "避免憋气动作、倒立（青光眼）", "glaucoma 推导")

        # 高尿酸 → 避免剧烈无氧、保证水分
        if "hyperuricemia" in profile.conditions:
            priorities.append("adequate_hydration")
            profile.add_source("运动注意", "充足饮水（高尿酸管理）", "hyperuricemia 推导")

        # 血压上升趋势 → 增加有氧比例
        if "bp_trending_up" in profile.risk_factors or "bp_elevated" in profile.risk_factors:
            priorities.append("cardiovascular")
            contraindications.append("maximal_isometric")

        # 工作日综合征 → 工作日优先减压运动，周末可适当提升强度
        if "weekday_elevated" in profile.risk_factors:
            priorities.append("weekday_stress_management")
            profile.add_source(
                "运动注意", "工作日优先减压/降压运动，周末可提升强度", "workday_pattern 推导"
            )

        # 体成分目标
        if profile.body_composition.priority_training == "resistance":
            priorities.append("resistance_training")
        elif profile.body_composition.priority_training == "cardio":
            priorities.append("aerobic_training")
        else:
            priorities.append("balanced_training")

        # 肿瘤易感基因 → 建议抗氧化生活方式
        if profile.genetic_markers:
            priorities.append("antioxidant_lifestyle")

        profile.exercise_contraindications = contraindications
        profile.exercise_priorities = priorities

    def _load_active_goals(self, profile: HealthProfile, reference_date: str):
        """加载活跃阶段性目标。"""
        from superhealth.goals.metrics import METRIC_REGISTRY

        try:
            with self._get_conn() as conn:
                rows = conn.execute(
                    """SELECT g.*, gp.current_value, gp.progress_pct
                       FROM goals g
                       LEFT JOIN goal_progress gp ON g.id = gp.goal_id AND gp.date = (
                           SELECT MAX(date) FROM goal_progress WHERE goal_id = g.id
                       )
                       WHERE g.status = 'active'
                       ORDER BY g.priority"""
                ).fetchall()
                for row in rows:
                    goal_dict = dict(row)
                    spec = METRIC_REGISTRY.get(goal_dict["metric_key"])
                    if spec:
                        goal_dict["metric_label"] = spec.label
                    profile.active_goals.append(goal_dict)
        except Exception as e:
            log.warning("加载活跃目标失败（可能 schema 未迁移）: %s", e)

    def to_dict(self, profile: HealthProfile) -> dict:
        """将 HealthProfile 转换为可序列化的字典。"""
        return {
            "conditions": profile.conditions,
            "history_conditions": profile.history_conditions,
            "active_medications": profile.active_medications,
            "genetic_markers": profile.genetic_markers,
            "risk_factors": profile.risk_factors,
            "trends": profile.trends,
            "exercise_contraindications": profile.exercise_contraindications,
            "exercise_priorities": profile.exercise_priorities,
            "body_composition": {
                "bmi": profile.body_composition.bmi,
                "bmi_status": profile.body_composition.bmi_status,
                "body_fat_pct": profile.body_composition.body_fat_pct,
                "body_fat_status": profile.body_composition.body_fat_status,
                "weight_kg": profile.body_composition.weight_kg,
                "assessment": profile.body_composition.assessment,
                "target": profile.body_composition.target,
                "priority_training": profile.body_composition.priority_training,
            },
            "learned_preferences": profile.learned_preferences,
            "profile_sources": profile.profile_sources,
            "height_cm": profile.height_cm,
            "workday_patterns": profile.workday_patterns,
            "active_goals": profile.active_goals,
        }
