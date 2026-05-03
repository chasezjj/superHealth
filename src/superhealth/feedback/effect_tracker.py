"""效果追踪器：追踪建议执行后的生理指标变化。

追踪维度：
- 运动后 1-2 天的 HRV / 睡眠 / 压力 / Body Battery 变化

核心改进（抗干扰 + 同类日配对比较）：
1. 早上7点运行时，当天日间指标（压力等）尚未完整，按 run_date 过滤不完整的日间指标。
2. 检测 contaminated 日（工作压力/演讲紧张/喝酒熬夜等）：
   - 通过压力异常阈值自动识别
   - 通过 user_feedback 关键词识别
   - contaminated 日的 negative 信号不计分，避免误判运动效果。
3. 同类日配对比较（matched control）：
   - 用运动前一天的 5 维指标，在历史无运动日中找最相似的 3 个对照日
   - 计算对照日的平均恢复曲线，作为环境噪声基线
   - 运动净效应 = 运动日 raw 变化 − 对照日平均变化
   - 数据量要求：>=2 个有效对照日才启用净效应（当前 348 个无运动日，完全满足）

性能优化：
- track_recent_exercises() 开头批量预加载所有涉及日期的 daily_health 到内存缓存
- write_effects_to_db() 单一事务批量 UPDATE
"""

from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean, stdev
from typing import Optional

from superhealth import database as db
from superhealth.collectors.outlook_collector import _build_summary

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent.parent / "health.db"

# 用户反馈中的干扰关键词（运动效果评估时应排除负向信号）
CONTAMINATION_KEYWORDS = [
    "演讲",
    "紧张",
    "加班",
    "出差",
    "deadline",
    "应酬",
    "失眠",
    "喝酒",
    "熬夜",
    "会议",
    "压力大",
    "焦虑",
]

# 日间指标：早上7点运行时，run_date（今天）的数据尚未完整，需要过滤
DAYTIME_METRICS = {"avg_stress"}

# 配对比较权重（生理 + 日程）
_MATCH_WEIGHTS = {
    "hrv_avg": 0.26,
    "sleep_score": 0.22,
    "resting_hr": 0.17,
    "avg_stress": 0.13,
    "body_battery_wake": 0.08,
    "total_meeting_min": 0.09,
    "back_to_back_count": 0.05,
}


class EffectTracker:
    """追踪建议执行后的生理指标变化。"""

    TRACKED_METRICS = ["hrv_avg", "sleep_score", "avg_stress", "body_battery_wake", "resting_hr"]

    _METRIC_LABELS = {
        "hrv_avg": "HRV",
        "sleep_score": "睡眠",
        "avg_stress": "压力",
        "body_battery_wake": "Body Battery",
        "resting_hr": "静息心率",
    }

    # metric_key → TRACKED_METRICS 内部键的映射
    # 注：血压指标无直接对应运动恢复指标，需从 vitals 单独分析，不参与 goal_aligned_score
    _GOAL_METRIC_MAP = {
        "hrv_mean_7d": "hrv_avg",
        "sleep_score_mean_7d": "sleep_score",
        "stress_mean_7d": "avg_stress",
        "body_battery_wake_mean_7d": "body_battery_wake",
        "resting_hr_mean_7d": "resting_hr",
    }

    GOAL_WEIGHT_BOOST = 1.5

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path

    def _get_conn(self):
        return db.get_conn(self.db_path)

    def _get_daily_metrics(self, day_str: str, cache: dict | None = None) -> Optional[dict]:
        """获取某天的核心指标，优先从内存缓存读取。"""
        if cache is not None and day_str in cache:
            return cache[day_str]  # type: ignore[no-any-return]
        with self._get_conn() as conn:
            row = conn.execute(
                """SELECT date,
                          hrv_last_night_avg  AS hrv_avg,
                          sleep_score,
                          stress_average      AS avg_stress,
                          bb_at_wake          AS body_battery_wake,
                          hr_resting          AS resting_hr
                   FROM daily_health WHERE date = ?""",
                (day_str,),
            ).fetchone()
            return dict(row) if row else None

    def _build_metrics_cache(self, dates: list[str], conn) -> dict[str, dict]:
        """批量从 DB 加载多天的核心指标，返回 {date_str: metrics_dict}。"""
        if not dates:
            return {}
        placeholders = ",".join("?" * len(dates))
        rows = conn.execute(
            f"""SELECT date,
                       hrv_last_night_avg  AS hrv_avg,
                       sleep_score,
                       stress_average      AS avg_stress,
                       bb_at_wake          AS body_battery_wake,
                       hr_resting          AS resting_hr
                FROM daily_health
                WHERE date IN ({placeholders})""",
            dates,
        ).fetchall()
        return {r["date"]: dict(r) for r in rows}

    def _get_pre_exercise_baseline(
        self, exercise_date: str, pre_days: int = 5, cache: dict | None = None
    ) -> Optional[dict]:
        """取运动前 N 天的指标均值作为基线（排除运动当天）。

        有数据的天数不足 2 天时返回 None，由调用方回退到当天数据。
        """
        base_dt = datetime.fromisoformat(exercise_date)
        samples: dict[str, list[float]] = {m: [] for m in self.TRACKED_METRICS}

        for i in range(1, pre_days + 1):
            d = (base_dt - timedelta(days=i)).isoformat()[:10]
            row = self._get_daily_metrics(d, cache)
            if not row:
                continue
            for m in self.TRACKED_METRICS:
                v = row.get(m)
                if v is not None:
                    samples[m].append(v)

        valid_days = max((len(v) for v in samples.values()), default=0)
        if valid_days < 2:
            return None

        return {m: mean(vals) if vals else None for m, vals in samples.items()}

    def _compute_global_stds(self, conn) -> dict[str, float]:
        """计算全量 daily_health 各指标的标准差，用于标准化配对距离。"""
        rows = conn.execute(
            """SELECT hrv_last_night_avg  AS hrv_avg,
                       sleep_score,
                       stress_average      AS avg_stress,
                       hr_resting          AS resting_hr,
                       bb_at_wake          AS body_battery_wake
                FROM daily_health"""
        ).fetchall()
        result = {}
        for key in self.TRACKED_METRICS:
            vals = [r[key] for r in rows if r[key] is not None]
            result[key] = stdev(vals) if len(vals) > 1 else 1.0
        return result

    def _compute_personal_stds(self, conn, lookback_days: int = 180) -> dict[str, float]:
        """计算个人近期历史指标标准差，作为'对你而言多大变化算大'的基准。"""
        since = (date.today() - timedelta(days=lookback_days)).isoformat()
        rows = conn.execute(
            """SELECT hrv_last_night_avg  AS hrv_avg,
                       sleep_score,
                       stress_average      AS avg_stress,
                       hr_resting          AS resting_hr,
                       bb_at_wake          AS body_battery_wake
                FROM daily_health
                WHERE date >= ?""",
            (since,),
        ).fetchall()
        result = {}
        for key in self.TRACKED_METRICS:
            vals = [r[key] for r in rows if r[key] is not None]
            result[key] = stdev(vals) if len(vals) > 1 else 1.0
        return result

    def _compute_metric_percentiles(
        self, conn, lookback_days: int = 180
    ) -> dict[str, dict[str, float | None]]:
        """计算个人近期历史指标分位值，用于动态判定 post 绝对水平。

        返回 {metric: {"excellent": v, "poor": v}}。
        对越低越好的指标（resting_hr, avg_stress）取相反方向分位。
        """
        since = (date.today() - timedelta(days=lookback_days)).isoformat()
        rows = conn.execute(
            """SELECT hrv_last_night_avg  AS hrv_avg,
                       sleep_score,
                       stress_average      AS avg_stress,
                       hr_resting          AS resting_hr,
                       bb_at_wake          AS body_battery_wake
                FROM daily_health
                WHERE date >= ?""",
            (since,)
        ).fetchall()
        result: dict[str, dict[str, float | None]] = {}
        for key in self.TRACKED_METRICS:
            vals = sorted([r[key] for r in rows if r[key] is not None])
            if not vals:
                result[key] = {"excellent": None, "poor": None}
                continue
            n = len(vals)
            p80 = vals[min(int(n * 0.8), n - 1)]
            p20 = vals[max(int(n * 0.2), 0)]
            if key in ("resting_hr", "avg_stress"):
                result[key] = {"excellent": p20, "poor": p80}
            else:
                result[key] = {"excellent": p80, "poor": p20}
        return result

    @staticmethod
    def _compute_schedule_stds(conn) -> dict[str, float]:
        """计算历史日程指标标准差，用于标准化配对距离。

        从 calendar_events 聚合为 CalendarSummary 后计算。
        """
        rows = conn.execute(
            """SELECT date, duration_min, is_all_day
               FROM calendar_events"""
        ).fetchall()
        if not rows:
            return {"total_meeting_min": 1.0, "back_to_back_count": 1.0}

        # 按日期分组构建 summary，复用 _build_summary
        events_by_date: dict[str, list[dict]] = {}
        for row in rows:
            d = row["date"]
            events_by_date.setdefault(d, []).append(dict(row))

        totals = []
        b2bs = []
        for d, evs in events_by_date.items():
            summary = _build_summary(d, evs)
            totals.append(summary.total_meeting_min)
            b2bs.append(summary.back_to_back_count)

        return {
            "total_meeting_min": stdev(totals) if len(totals) > 1 else 1.0,
            "back_to_back_count": stdev(b2bs) if len(b2bs) > 1 else 1.0,
        }

    @staticmethod
    def _pick_primary_exercise(exercise_rows: list) -> dict | None:
        """从当日多条运动中选出主运动（时长 × 心率得分最高）。

        低强度辅助运动（如 breathwork、warmup）通常时长短、心率低，
        得分自然低于主运动（跑步、力量等）。
        兼容 dict 和 sqlite3.Row 输入。
        """
        if not exercise_rows:
            return None

        def _get(row, key):
            if hasattr(row, "get"):
                return row.get(key)
            try:
                return row[key]
            except (KeyError, IndexError):
                return None

        def score(row):
            duration = _get(row, "duration_seconds") or 0
            hr = _get(row, "avg_hr") or 0
            # 对无 avg_hr 的运动给最低心率基线 60，避免 duration 极短的运动被选中
            return duration * max(hr, 60)

        winner = max(exercise_rows, key=score)
        # 统一返回 dict，避免调用方收到 sqlite3.Row
        if hasattr(winner, "keys"):  # sqlite3.Row 有 .keys() 方法
            return dict(winner)
        return winner  # type: ignore[no-any-return]

    @staticmethod
    def _metric_effect_score(metric: str, net_change: float, personal_std: float) -> float:
        """将单个指标的净变化映射到 [-1, +1] 的连续效应分。

        使用 tanh(z/1.5)，其中 z = net_change / personal_std。
        对越低越好的指标（静息心率、压力）自动反转符号。
        """
        if personal_std == 0 or personal_std is None:
            personal_std = 1.0
        z = net_change / personal_std
        # 反转符号的指标：越高反而越差
        if metric in ("resting_hr", "avg_stress"):
            z = -z
        return math.tanh(z / 1.5)

    def _composite_recovery_score(
        self,
        post_metrics: dict,
        baseline: dict,
        control_avg_changes: dict,
        personal_stds: dict,
    ) -> float:
        """计算单天的复合恢复评分 (CRS)，范围 [-1, +1]。"""
        weights = {
            "hrv_avg": 0.30,
            "sleep_score": 0.25,
            "resting_hr": 0.20,
            "avg_stress": 0.15,
            "body_battery_wake": 0.10,
        }
        total = 0.0
        weight_sum = 0.0
        for m, w in weights.items():
            b = baseline.get(m)
            p = post_metrics.get(m)
            if b is None or p is None:
                continue
            net_change = (p - b) - control_avg_changes.get(m, 0.0)
            std = personal_stds.get(m, 1.0)
            score = self._metric_effect_score(m, net_change, std)
            total += w * score
            weight_sum += w
        if weight_sum == 0:
            return 0.0
        return total / weight_sum

    def _day_similarity(
        self,
        target: dict,
        candidate: dict,
        stds: dict,
        target_schedule: dict | None = None,
        candidate_schedule: dict | None = None,
    ) -> float:
        """计算两天的加权标准化欧氏距离；星期几相同则额外优惠。

        若 target_schedule / candidate_schedule 提供，则纳入 total_meeting_min
        与 back_to_back_count 两个日程维度；任一缺失时回退到纯生理指标匹配。
        """
        total = 0.0
        weight_sum = 0.0
        for key, w in _MATCH_WEIGHTS.items():
            t = target.get(key)
            c = candidate.get(key)
            # 日程维度：target_schedule / candidate_schedule 中取值
            if key in ("total_meeting_min", "back_to_back_count"):
                if target_schedule is None or candidate_schedule is None:
                    continue
                t = target_schedule.get(key)
                c = candidate_schedule.get(key)
            if t is None or c is None:
                continue
            sigma = stds.get(key, 1.0)
            if sigma == 0:
                sigma = 1.0
            total += w * ((t - c) / sigma) ** 2
            weight_sum += w
        if weight_sum == 0:
            return float("inf")
        score: float = float((total / weight_sum) ** 0.5)

        # 星期几相同优先（约15%优惠）
        t_date = target.get("date")
        c_date = candidate.get("date")
        if t_date and c_date:
            t_wd = datetime.fromisoformat(t_date).weekday()
            c_wd = datetime.fromisoformat(c_date).weekday()
            if t_wd == c_wd:
                score *= 0.85
        return score

    def _find_control_days(
        self,
        exercise_date: str,
        pre_metrics: dict,
        stds: dict,
        cache: dict | None = None,
        top_n: int = 3,
    ) -> list[dict]:
        """在历史无运动日中找与 pre_metrics 最相似的 top_n 个对照日。

        返回列表项包含：control_date, similarity, baseline, post
        """
        candidates: list[tuple[float, str, dict]] = []
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT d.date,
                       d.hrv_last_night_avg  AS hrv_avg,
                       d.sleep_score,
                       d.stress_average      AS avg_stress,
                       d.hr_resting          AS resting_hr,
                       d.bb_at_wake          AS body_battery_wake
                FROM daily_health d
                LEFT JOIN exercises e ON d.date = e.date
                WHERE e.date IS NULL
                  AND d.date != ?
                  AND julianday(?) - julianday(d.date) BETWEEN 30 AND 90
                  AND d.hrv_last_night_avg IS NOT NULL
                  AND (d.stress_average IS NULL OR d.stress_average < 40)
                """,
                (exercise_date, exercise_date),
            ).fetchall()

            # 获取运动日的日程摘要（用于匹配对照日的日程负荷）
            target_schedule: dict | None = None
            target_cal_rows = db.query_calendar_events(conn, exercise_date)
            if target_cal_rows:
                target_summary = _build_summary(exercise_date, target_cal_rows)
                target_schedule = {
                    "total_meeting_min": target_summary.total_meeting_min,
                    "back_to_back_count": target_summary.back_to_back_count,
                }

            # 批量获取所有候选日的日历事件
            candidate_dates = [row["date"] for row in rows]
            cal_multi = db.query_calendar_events_multi(conn, candidate_dates)
            schedule_cache: dict[str, dict] = {}
            for d, evs in cal_multi.items():
                summary = _build_summary(d, evs)
                schedule_cache[d] = {
                    "total_meeting_min": summary.total_meeting_min,
                    "back_to_back_count": summary.back_to_back_count,
                }

            for row in rows:
                cand = dict(row)
                cand["date"] = cand.pop("date")
                cand_schedule = schedule_cache.get(cand["date"])
                sim = self._day_similarity(
                    pre_metrics,
                    cand,
                    stds,
                    target_schedule=target_schedule,
                    candidate_schedule=cand_schedule,
                )
                candidates.append((sim, cand["date"], cand))

        candidates.sort(key=lambda x: x[0])

        controls = []
        for sim, ctrl_date, _ in candidates[:top_n]:
            ctrl_baseline = self._get_pre_exercise_baseline(ctrl_date, pre_days=5, cache=cache)
            if not ctrl_baseline:
                ctrl_baseline = self._get_daily_metrics(ctrl_date, cache)
            if not ctrl_baseline:
                continue

            ctrl_post = {}
            ctrl_dt = datetime.fromisoformat(ctrl_date)
            for i in range(1, 3):
                d = (ctrl_dt + timedelta(days=i)).isoformat()[:10]
                m = self._get_daily_metrics(d, cache)
                if m:
                    ctrl_post[f"day+{i}"] = m

            if len(ctrl_post) >= 1:
                controls.append(
                    {
                        "control_date": ctrl_date,
                        "similarity": round(sim, 3),
                        "baseline": ctrl_baseline,
                        "post": ctrl_post,
                    }
                )

        return controls

    @staticmethod
    def _has_negative_signal(metrics: dict, baseline: dict) -> bool:
        """判断追踪日是否有至少一个负面恢复信号（用于条件性干扰检测）。"""
        if baseline.get("hrv_avg") and metrics.get("hrv_avg"):
            if metrics["hrv_avg"] - baseline["hrv_avg"] < -5:
                return True
        if baseline.get("sleep_score") and metrics.get("sleep_score"):
            if metrics["sleep_score"] - baseline["sleep_score"] < -5:
                return True
        if baseline.get("resting_hr") and metrics.get("resting_hr"):
            if metrics["resting_hr"] - baseline["resting_hr"] > 3:
                return True
        if baseline.get("body_battery_wake") and metrics.get("body_battery_wake"):
            if metrics["body_battery_wake"] - baseline["body_battery_wake"] < -5:
                return True
        return False

    def _detect_contaminated_days(
        self,
        exercise_date: str,
        baseline: dict,
        post_data: dict[str, dict],
        run_date: str,
        conn,
    ) -> dict[str, str]:
        """检测哪些追踪日被非运动因素干扰。

        返回: {day_key: contamination_reason}
        """
        contaminated: dict[str, str] = {}
        base_stress = baseline.get("avg_stress")

        # 1. 压力异常自动检测（仅限日间指标完整的日期）
        for day_key, metrics in post_data.items():
            post_date = metrics.get("date")
            if not post_date or post_date >= run_date:
                continue
            post_stress = metrics.get("avg_stress")
            if post_stress is None:
                continue
            if base_stress is not None and post_stress > base_stress + 8:
                contaminated[day_key] = f"压力升高({base_stress:.0f}→{post_stress:.0f})"
            elif post_stress > 35:
                contaminated[day_key] = f"压力绝对值高({post_stress:.0f})"

        # 2. Calendar 繁忙度检测（条件触发：只有出现负面恢复信号时才归因）
        # day+1 看运动当天，day+2 看 day+1
        base_dt = datetime.fromisoformat(exercise_date)
        for day_key, metrics in post_data.items():
            post_date = metrics.get("date")
            if not post_date:
                continue
            # 恢复数据不错 → 无需归因，跳过 calendar 检测
            if not self._has_negative_signal(metrics, baseline):
                continue
            delta = int(day_key.replace("day+", ""))
            prev_date = (base_dt + timedelta(days=delta - 1)).isoformat()[:10]
            cal_rows = db.query_calendar_events(conn, prev_date)
            if not cal_rows:
                continue
            cal_summary = _build_summary(prev_date, cal_rows)
            if cal_summary.busy_level == "high":
                reasons = []
                if cal_summary.total_meeting_min > 240:
                    reasons.append(f"会议{cal_summary.total_meeting_min}分钟")
                if cal_summary.back_to_back_count >= 3:
                    reasons.append(f"连续会议{cal_summary.back_to_back_count}组")
                if cal_summary.has_all_day:
                    reasons.append("全天事件")
                if reasons:
                    reason = "日程繁忙(" + ",".join(reasons) + ")"
                else:
                    reason = "日程繁忙"
                if day_key in contaminated:
                    contaminated[day_key] += f"; {reason}"
                else:
                    contaminated[day_key] = reason
            elif cal_summary.busy_level == "medium":
                # medium 忙碌 + 已有压力异常 → 追加标注，但不单独触发污染
                if day_key in contaminated and "压力" in contaminated[day_key]:
                    contaminated[day_key] += "; 日程较忙"

        # 3. user_feedback 关键词检测（day+1/day+2，以及运动日本身 → 映射到 day+1）
        base_dt = datetime.fromisoformat(exercise_date)
        window_dates = [(base_dt + timedelta(days=i)).isoformat()[:10] for i in range(-2, 3)]
        placeholders = ",".join("?" * len(window_dates))
        rows = conn.execute(
            f"""SELECT date, user_feedback
                FROM recommendation_feedback
                WHERE date IN ({placeholders})
                  AND user_feedback IS NOT NULL""",
            window_dates,
        ).fetchall()

        for row in rows:
            fb = row["user_feedback"] or ""
            hit_keywords = [kw for kw in CONTAMINATION_KEYWORDS if kw in fb]
            if not hit_keywords:
                continue
            fb_date = row["date"]
            delta = (datetime.fromisoformat(fb_date) - base_dt).days
            if delta == 0:
                mapped_key = "day+1"
            elif 1 <= delta <= 2:
                mapped_key = f"day+{delta}"
            else:
                continue
            reason = f"user_feedback命中关键词({','.join(hit_keywords)})"
            if mapped_key in contaminated:
                contaminated[mapped_key] += f"; {reason}"
            else:
                contaminated[mapped_key] = reason

        return contaminated

    def track_exercise_effect(
        self,
        exercise_date: str,
        exercise_type: str,
        days_after: int = 2,
        pre_days: int = 5,
        cache: dict | None = None,
        run_date: str | None = None,
    ) -> dict:
        """追踪运动后 N 天的指标变化（含同类日配对净效应）。

        基线优先使用运动前 pre_days 天的均值，数据不足时回退到运动当天。
        cache: 可选的内存日期缓存（批量调用时传入以避免重复 DB 查询）。
        run_date: 流水线运行日期（默认今天）。用于过滤尚未完整的日间指标，
                  并标记 contaminated 日。
        """
        if run_date is None:
            run_date = date.today().isoformat()

        baseline = self._get_pre_exercise_baseline(exercise_date, pre_days, cache)
        original_baseline_type = "pre_avg"
        if not baseline:
            baseline = self._get_daily_metrics(exercise_date, cache)
            original_baseline_type = "exercise_day"
        if not baseline:
            return {"assessment": "no_data", "details": ["运动当天及前序均无数据"]}

        post_data = {}
        base_dt = datetime.fromisoformat(exercise_date)
        for i in range(1, days_after + 1):
            d = (base_dt + timedelta(days=i)).isoformat()[:10]
            metrics = self._get_daily_metrics(d, cache)
            if metrics:
                metrics["date"] = d
                post_data[f"day+{i}"] = metrics

        if not post_data:
            return {"assessment": "no_data", "details": ["追踪期间无数据"]}

        # 检测 contaminated 日
        contaminated: dict[str, str] = {}
        with self._get_conn() as conn:
            contaminated = self._detect_contaminated_days(
                exercise_date, baseline, post_data, run_date, conn
            )

        # 寻找同类日对照组
        controls = []
        stds = {}
        net_effect_available = False
        control_avg_changes = {}
        pre_day = (base_dt - timedelta(days=1)).isoformat()[:10]
        pre_metrics = self._get_daily_metrics(pre_day, cache) or baseline.copy()
        pre_metrics["date"] = pre_day

        with self._get_conn() as conn:
            stds = self._compute_global_stds(conn)
            schedule_stds = self._compute_schedule_stds(conn)
            all_stds = {**stds, **schedule_stds}
            personal_stds = self._compute_personal_stds(conn, lookback_days=180)
            percentiles = self._compute_metric_percentiles(conn, lookback_days=180)
            controls = self._find_control_days(
                exercise_date, pre_metrics, all_stds, cache=cache, top_n=3
            )

        if len(controls) >= 2:
            net_effect_available = True
            # 计算对照组的平均变化
            tmp_changes: dict[str, list[float]] = {m: [] for m in self.TRACKED_METRICS}
            for ctrl in controls:
                for day_key, metrics in ctrl["post"].items():
                    for m in self.TRACKED_METRICS:
                        cb = ctrl["baseline"].get(m)
                        cm = metrics.get(m)
                        if cb is not None and cm is not None:
                            tmp_changes[m].append(cm - cb)
            control_avg_changes = {
                m: round(mean(vals), 2) if vals else 0.0 for m, vals in tmp_changes.items()
            }

        baseline_type = "matched_control" if net_effect_available else original_baseline_type

        # 净效应二次干扰检测：当 raw 变化不明显但净效应显著时，
        # 用净效应阈值重新检查日程干扰（覆盖 _has_negative_signal 用 raw 阈值遗漏的场景）
        if net_effect_available:
            with self._get_conn() as conn:
                for day_key, metrics in post_data.items():
                    if day_key in contaminated:
                        continue
                    post_date = metrics.get("date")
                    if not post_date:
                        continue
                    is_run_date = post_date >= run_date
                    has_net_negative = False
                    for m, neg_thresh, is_upper in [
                        ("hrv_avg", -5, False),
                        ("sleep_score", -5, False),
                        ("resting_hr", 3, True),
                        ("body_battery_wake", -5, False),
                        ("avg_stress", 5, True),
                    ]:
                        # run_date 当天日间指标尚未完整，跳过压力检测
                        if is_run_date and m == "avg_stress":
                            continue
                        b = baseline.get(m)
                        p = metrics.get(m)
                        if b is not None and p is not None:
                            net = (p - b) - control_avg_changes.get(m, 0.0)
                            if (is_upper and net > neg_thresh) or (
                                not is_upper and net < neg_thresh
                            ):
                                has_net_negative = True
                                break
                    if not has_net_negative:
                        continue
                    delta = int(day_key.replace("day+", ""))
                    prev_date = (base_dt + timedelta(days=delta - 1)).isoformat()[:10]
                    cal_rows = db.query_calendar_events(conn, prev_date)
                    if not cal_rows:
                        continue
                    cal_summary = _build_summary(prev_date, cal_rows)
                    if cal_summary.busy_level == "high":
                        reasons = []
                        if cal_summary.total_meeting_min > 240:
                            reasons.append(f"会议{cal_summary.total_meeting_min}分钟")
                        if cal_summary.back_to_back_count >= 3:
                            reasons.append(f"连续会议{cal_summary.back_to_back_count}组")
                        if cal_summary.has_all_day:
                            reasons.append("全天事件")
                        reason = "日程繁忙(" + ",".join(reasons) + ")" if reasons else "日程繁忙"
                        contaminated[day_key] = reason

        # 评估效果（按天分桶，避免 day+1 压力数据在 day+2 循环里产生导致交错展示）
        positive_signals = 0
        negative_signals = 0
        details_by_day: dict[str, list[str]] = {dk: [] for dk in post_data.keys()}
        skipped_negative = 0

        def _eval_signal(
            day_key: str,
            metric_key: str,
            raw_change: float,
            positive_thresh: float,
            negative_thresh: float,
            positive_label: str,
            negative_label: str,
            is_contaminated: bool,
            contam_reason: str,
        ):
            nonlocal positive_signals, negative_signals, skipped_negative
            net_change = raw_change - control_avg_changes.get(metric_key, 0.0)
            change = net_change if net_effect_available else raw_change
            label = "净" if net_effect_available else ""

            suffix = ""
            if net_effect_available:
                suffix = f"(raw={raw_change:+.0f}, ctrl={control_avg_changes[metric_key]:+.1f}, net={net_change:+.0f})"

            if positive_thresh > 0 and change > positive_thresh:
                positive_signals += 1
                details_by_day[day_key].append(
                    f"{day_key}: {positive_label}{label} {change:+.0f}{suffix}（恢复良好）"
                )
            elif negative_thresh < 0 and change < negative_thresh:
                if is_contaminated:
                    skipped_negative += 1
                    details_by_day[day_key].append(
                        f"{day_key}: {negative_label}{label} {change:+.0f}{suffix}（{contam_reason}，标记为干扰，negative信号不计入）"
                    )
                else:
                    negative_signals += 1
                    details_by_day[day_key].append(
                        f"{day_key}: {negative_label}{label} {change:+.0f}{suffix}（恢复不佳）"
                    )

        for day_key, metrics in post_data.items():
            is_contaminated = day_key in contaminated
            contam_reason = contaminated.get(day_key, "")

            # HRV
            if baseline.get("hrv_avg") and metrics.get("hrv_avg"):
                _eval_signal(
                    day_key,
                    "hrv_avg",
                    metrics["hrv_avg"] - baseline["hrv_avg"],
                    3,
                    -5,
                    "HRV",
                    "HRV",
                    is_contaminated,
                    contam_reason,
                )

            # 睡眠
            if baseline.get("sleep_score") and metrics.get("sleep_score"):
                _eval_signal(
                    day_key,
                    "sleep_score",
                    metrics["sleep_score"] - baseline["sleep_score"],
                    5,
                    -5,
                    "睡眠",
                    "睡眠",
                    is_contaminated,
                    contam_reason,
                )

            # 静息心率（降低=正面）
            if baseline.get("resting_hr") and metrics.get("resting_hr"):
                raw = metrics["resting_hr"] - baseline["resting_hr"]
                net = raw - control_avg_changes.get("resting_hr", 0.0)
                change = net if net_effect_available else raw
                label = "净" if net_effect_available else ""
                suffix = (
                    f"(raw={raw:+.0f}, ctrl={control_avg_changes['resting_hr']:+.1f}, net={net:+.0f})"
                    if net_effect_available
                    else ""
                )
                if change < -2:
                    positive_signals += 1
                    details_by_day[day_key].append(
                        f"{day_key}: 静息心率下降{label} {abs(change):.0f}bpm{suffix}（恢复良好）"
                    )
                elif change > 3:
                    if is_contaminated:
                        skipped_negative += 1
                        details_by_day[day_key].append(
                            f"{day_key}: 静息心率升高{label} +{change:.0f}bpm{suffix}（{contam_reason}，标记为干扰，negative信号不计入）"
                        )
                    else:
                        negative_signals += 1
                        details_by_day[day_key].append(
                            f"{day_key}: 静息心率升高{label} +{change:.0f}bpm{suffix}（恢复不佳）"
                        )

            # Body Battery
            if baseline.get("body_battery_wake") and metrics.get("body_battery_wake"):
                raw = metrics["body_battery_wake"] - baseline["body_battery_wake"]
                net = raw - control_avg_changes.get("body_battery_wake", 0.0)
                change = net if net_effect_available else raw
                label = "净" if net_effect_available else ""
                suffix = (
                    f"(raw={raw:+.0f}, ctrl={control_avg_changes['body_battery_wake']:+.1f}, net={net:+.0f})"
                    if net_effect_available
                    else ""
                )
                if change > 5:
                    positive_signals += 1
                    details_by_day[day_key].append(
                        f"{day_key}: 起床 Body Battery +{label}{change:.0f}{suffix}（恢复良好）"
                    )
                elif change < -5:
                    if is_contaminated:
                        skipped_negative += 1
                        details_by_day[day_key].append(
                            f"{day_key}: 起床 Body Battery {label}{change:.0f}{suffix}（{contam_reason}，标记为干扰，negative信号不计入）"
                        )
                    else:
                        negative_signals += 1
                        details_by_day[day_key].append(
                            f"{day_key}: 起床 Body Battery {label}{change:.0f}{suffix}（恢复不佳）"
                        )

            # 压力变化（仅 day+2 对比 day+1 vs baseline）
            # 逻辑上在 day+2 循环里执行（确保 day+1 数据完整），但展示归属 day+1
            if day_key == "day+2":
                day1_metrics = post_data.get("day+1")
                day1_date = day1_metrics.get("date") if day1_metrics else None
                if day1_date and day1_date >= run_date:
                    details_by_day["day+1"].append(
                        "day+1: 压力数据尚未完整（run_date限制），跳过压力对比"
                    )
                    continue
                if baseline.get("avg_stress") and day1_metrics and day1_metrics.get("avg_stress"):
                    raw = day1_metrics["avg_stress"] - baseline["avg_stress"]
                    net = raw - control_avg_changes.get("avg_stress", 0.0)
                    change = net if net_effect_available else raw
                    label = "净" if net_effect_available else ""
                    suffix = (
                        f"(raw={raw:+.0f}, ctrl={control_avg_changes['avg_stress']:+.1f}, net={net:+.0f})"
                        if net_effect_available
                        else ""
                    )
                    day1_contaminated = "day+1" in contaminated
                    day1_reason = contaminated.get("day+1", "")
                    if change < -5:
                        positive_signals += 1
                        details_by_day["day+1"].append(
                            f"day+1: 压力下降{label} {abs(change):.0f}{suffix}（恢复良好）"
                        )
                    elif change > 5:
                        if day1_contaminated:
                            skipped_negative += 1
                            details_by_day["day+1"].append(
                                f"day+1: 压力升高{label} +{change:.0f}{suffix}（{day1_reason}，标记为干扰，negative信号不计入）"
                            )
                        else:
                            negative_signals += 1
                            details_by_day["day+1"].append(
                                f"day+1: 压力升高{label} +{change:.0f}{suffix}（恢复不佳）"
                            )

        # 为有数据但无显著信号的 day 添加占位记录，方便排查（格式与有信号时统一：raw/ctrl/net）
        for day_key, metrics in post_data.items():
            if not details_by_day[day_key]:
                parts = []
                for m in self.TRACKED_METRICS:
                    b = baseline.get(m)
                    p = metrics.get(m)
                    if b is not None and p is not None:
                        raw = p - b
                        ctrl_val = control_avg_changes.get(m, 0.0) if net_effect_available else 0.0
                        net = raw - ctrl_val
                        parts.append(
                            f"{self._METRIC_LABELS[m]} raw={raw:+.1f}/ctrl={ctrl_val:+.1f}/net={net:+.1f}"
                        )
                if parts:
                    details_by_day[day_key].append(
                        f"{day_key}: 各项指标变化均在正常范围内（{', '.join(parts)}），无显著信号"
                    )

        # ── 高基线/绝对水平 override ──
        # 当 post 指标已处于个人历史优秀区间时，net 变化的 negative 判定可能受天花板效应影响
        # （对照组从低基线反弹空间大，高基线组提升空间有限），此时应参考绝对水平。
        excellent_post_count = 0
        poor_post_count = 0
        for day_key, metrics in post_data.items():
            if day_key in contaminated:
                continue
            for m in self.TRACKED_METRICS:
                v = metrics.get(m)
                thresh = percentiles.get(m, {})
                exc = thresh.get("excellent")
                poor = thresh.get("poor")
                if v is None or exc is None or poor is None:
                    continue
                if m in ("resting_hr", "avg_stress"):
                    if v <= exc:
                        excellent_post_count += 1
                    if v >= poor:
                        poor_post_count += 1
                else:
                    if v >= exc:
                        excellent_post_count += 1
                    if v <= poor:
                        poor_post_count += 1

            hrv_status = str(metrics.get("hrv_status", "")).upper()
            if hrv_status == "BALANCED":
                excellent_post_count += 1
            if hrv_status == "LOW":
                poor_post_count += 1

        override_msg = None
        if negative_signals > 0 and excellent_post_count >= 4 and poor_post_count == 0:
            skipped_negative += negative_signals
            override_msg = f"override: {negative_signals} 个 negative 信号因 post 指标全面优秀被排除（高基线天花板效应）"
            negative_signals = 0

        # 按天顺序合并，保证 day+1 在前、day+2 在后；override 摘要置于末尾
        details = [d for dk in sorted(details_by_day.keys()) for d in details_by_day[dk]]
        if override_msg:
            details.append(override_msg)

        # 计算复合恢复评分 (CRS)，contaminated 日不参与
        composite_scores: dict[str, float | None] = {}
        for day_key, metrics in post_data.items():
            if day_key in contaminated:
                composite_scores[day_key] = None
                continue
            # 去掉我们之前加上的 date 字段，避免传入 _composite_recovery_score
            metrics_for_score = {k: v for k, v in metrics.items() if k != "date"}
            cs = self._composite_recovery_score(
                metrics_for_score, baseline, control_avg_changes, personal_stds
            )
            composite_scores[day_key] = round(cs, 3)

        composite_score_day1 = composite_scores.get("day+1")
        composite_score_day2 = composite_scores.get("day+2")
        valid_cs = [v for v in [composite_score_day1, composite_score_day2] if v is not None]
        composite_score_avg = round(mean(valid_cs), 3) if valid_cs else None

        # 计算各指标净效应（用于目标感知评分）
        net_effects = {}
        for m in self.TRACKED_METRICS:
            changes = []
            for day_key, metrics in post_data.items():
                if day_key in contaminated:
                    continue
                b = baseline.get(m)
                p = metrics.get(m)
                if b is not None and p is not None:
                    raw_change = p - b
                    net_change = raw_change - control_avg_changes.get(m, 0.0)
                    changes.append(net_change)
            if changes:
                avg_change = mean(changes)
                std = personal_stds.get(m, 1.0)
                net_effects[m] = self._metric_effect_score(m, avg_change, std)

        if positive_signals > negative_signals:
            assessment = "positive"
        elif negative_signals > positive_signals:
            assessment = "negative"
        else:
            assessment = "neutral"

        return {
            "exercise_date": exercise_date,
            "exercise_type": exercise_type,
            "baseline": baseline,
            "baseline_type": baseline_type,
            "control_dates": [
                {"date": c["control_date"], "similarity": c["similarity"]} for c in controls
            ],
            "post": post_data,
            "assessment": assessment,
            "positive_signals": positive_signals,
            "negative_signals": negative_signals,
            "skipped_negative": skipped_negative,
            "contaminated_days": contaminated,
            "net_effect_available": net_effect_available,
            "control_avg_changes": control_avg_changes,
            "composite_scores": composite_scores,
            "composite_score_day1": composite_score_day1,
            "composite_score_day2": composite_score_day2,
            "composite_score_avg": composite_score_avg,
            "net_effects": net_effects,
            "personal_stds": personal_stds,
            "details": details,
        }

    def track_recent_exercises(self, days: int = 14, run_date: str | None = None) -> list[dict]:
        """追踪近 N 天内有反馈的运动效果。"""
        if run_date is None:
            run_date = date.today().isoformat()
        end = run_date
        start = (datetime.fromisoformat(end) - timedelta(days=days)).isoformat()[:10]

        try:
            with self._get_conn() as conn:
                rows = conn.execute(
                    """SELECT date, actual_action, compliance
                       FROM recommendation_feedback
                       WHERE date BETWEEN ? AND ?
                         AND recommendation_type = 'exercise'
                       ORDER BY date""",
                    (start, end),
                ).fetchall()

                if not rows:
                    return []

                # 收集所有需要的日期（基线 5 天 + 当天 + 追踪 2 天 + 对照日可能需要前后7天外的数据）
                all_dates: set[str] = set()
                for row in rows:
                    d = datetime.fromisoformat(row["date"])
                    for i in range(-5, 4):
                        all_dates.add((d + timedelta(days=i)).isoformat()[:10])

                cache = self._build_metrics_cache(list(all_dates), conn)
        except Exception:
            return []

        results = []
        for row in rows:
            effect = self.track_exercise_effect(
                row["date"],
                row["actual_action"] or "未知运动",
                days_after=2,
                cache=cache,
                run_date=run_date,
            )
            results.append(effect)

        return results

    def update_tracked_metrics(self, feedback_date: str, run_date: str | None = None):
        """将追踪到的生理变化写回 recommendation_feedback.tracked_metrics。"""
        results = self.track_recent_exercises(days=7, run_date=run_date)
        self.write_effects_to_db(results, only_date=feedback_date, run_date=run_date)

    def write_effects_to_db(
        self,
        results: list[dict],
        only_date: str | None = None,
        run_date: str | None = None,
        lock_days: int = 3,
    ):
        """批量将追踪结果写回 recommendation_feedback.tracked_metrics。

        锁定策略：运动日期距离 run_date 超过 lock_days 天且已有 tracked_metrics 的记录
        不再覆盖，避免历史评估 retroactively 变化。首次计算（无 tracked_metrics）不受限制。
        """
        if run_date is None:
            run_date = date.today().isoformat()

        # 先筛选有效结果
        candidates = [
            r
            for r in results
            if r.get("exercise_date")
            and r.get("assessment") != "no_data"
            and (only_date is None or r["exercise_date"] == only_date)
        ]
        if not candidates:
            return

        try:
            with self._get_conn() as conn:
                # 查询哪些日期已有 tracked_metrics（用于锁定判断）
                dates = [r["exercise_date"] for r in candidates]
                placeholders = ",".join("?" * len(dates))
                existing_rows = conn.execute(
                    f"""SELECT date, tracked_metrics IS NOT NULL as has_data
                        FROM recommendation_feedback
                        WHERE date IN ({placeholders})
                          AND recommendation_type = 'exercise'""",
                    dates,
                ).fetchall()
                has_tracked = {r["date"]: bool(r["has_data"]) for r in existing_rows}

                run_dt = datetime.fromisoformat(run_date)
                to_write = []
                for r in candidates:
                    ex_date = r["exercise_date"]
                    days_diff = (run_dt - datetime.fromisoformat(ex_date)).days
                    # 锁定条件：超过 lock_days 且已有 tracked_metrics → 跳过
                    if days_diff > lock_days and has_tracked.get(ex_date):
                        log.debug("锁定 %s 的效果评估（已计算且超过 %d 天）", ex_date, lock_days)
                        continue
                    to_write.append(r)

                written = 0
                unmatched = 0
                for result in to_write:
                    tracked_json = json.dumps(result, ensure_ascii=False, default=str)
                    cursor = conn.execute(
                        """UPDATE recommendation_feedback
                           SET tracked_metrics = ?
                           WHERE date = ?""",
                        (tracked_json, result["exercise_date"]),
                    )
                    if cursor.rowcount and cursor.rowcount > 0:
                        written += 1
                    else:
                        unmatched += 1
                        log.warning(
                            "追踪指标写入失败：未找到 date=%s 的 feedback 记录",
                            result["exercise_date"],
                        )
                if to_write:
                    log.info(
                        "写回追踪指标 %d 条（成功 %d 条，未匹配 %d 条，锁定 %d 条）",
                        len(to_write),
                        written,
                        unmatched,
                        len(candidates) - len(to_write),
                    )
        except Exception as e:
            log.error("批量写回追踪指标失败: %s", e)

    def compute_goal_aligned_score(
        self, tracked_metrics: dict, goals: list[dict]
    ) -> Optional[float]:
        """计算目标感知的综合效果评分。

        将目标指标对应的 TRACKED_METRICS 权重提高 GOAL_WEIGHT_BOOST 倍，
        其余权重等比缩减使总和仍为 1。
        """
        if not goals or not tracked_metrics:
            return None

        # 收集目标关联的内部指标
        goal_internal_keys = set()
        for goal in goals:
            internal = self._GOAL_METRIC_MAP.get(goal.get("metric_key", ""))
            if internal:
                goal_internal_keys.add(internal)

        if not goal_internal_keys:
            return None

        # 构建加权权重
        weights = dict(_MATCH_WEIGHTS)
        boost_targets = goal_internal_keys & set(weights.keys())
        if not boost_targets:
            return None

        # 提权目标指标，等比缩减其余
        for k in boost_targets:
            weights[k] *= self.GOAL_WEIGHT_BOOST
        total = sum(weights.values())
        weights = {k: v / total for k, v in weights.items()}

        # 从 tracked_metrics 中提取已标准化的指标分
        net_effects = tracked_metrics.get("net_effects", {})
        if not net_effects:
            return None

        score = 0.0
        for metric, weight in weights.items():
            effect = net_effects.get(metric)
            if effect is not None:
                score += weight * effect
        return round(score, 3)

    @staticmethod
    def compute_goal_progress_norm(conn, ref_date: str) -> float | None:
        """计算指定日期的目标进度归一化值 (0-1)。

        使用最近创建的活跃目标 progress_pct；若当天无 goal_progress 数据，返回 None。
        若当天无 goal_progress 数据，返回 None。
        """
        rows = conn.execute(
            """SELECT gp.progress_pct
               FROM goal_progress gp
               JOIN goals g ON g.id = gp.goal_id
               WHERE gp.date = ?
                 AND g.status = 'active'
               ORDER BY g.start_date DESC""",
            (ref_date,),
        ).fetchall()

        if not rows:
            return None

        pcts = [r["progress_pct"] for r in rows if r["progress_pct"] is not None]  # type: ignore[index]
        if not pcts:
            return None

        # 取最近创建的活跃目标 progress_pct，限制在 [0, 100] 再归一化
        raw: float = max(0.0, min(100.0, float(pcts[0])))
        return raw / 100.0
