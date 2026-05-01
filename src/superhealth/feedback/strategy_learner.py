"""策略学习引擎：从反馈数据中学习个人偏好，更新 learned_preferences 表。

学习维度：
- 运动类型有效性（哪类运动带来更好的恢复）
- 个人恢复速度模式
- 剂量-反应关系（最佳心率区间和运动时长）
- 时间偏好（早晨/下午/晚间哪个时段效果最好）
- 身体状态分层学习（高/中/低 HRV 时的最优运动）
- 主动探索建议（数据稀疏时的 A/B 建议）

学习结果写入 SQLite learned_preferences 表，供 HealthProfileBuilder 加载。
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean, variance
from typing import Optional

from superhealth import database as db
from superhealth.feedback.effect_tracker import EffectTracker

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent.parent / "health.db"


class StrategyLearner:
    """从反馈数据中学习个人运动偏好，更新 learned_preferences 表。"""

    # 需要多少条反馈才开始更新偏好（防止过早下结论）
    MIN_EVIDENCE = 8

    # 指标权重（与 effect_tracker 一致）
    METRIC_WEIGHTS = {
        "hrv_avg": 0.30,
        "sleep_score": 0.25,
        "resting_hr": 0.20,
        "avg_stress": 0.15,
        "body_battery_wake": 0.10,
    }

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.effect_tracker = EffectTracker(db_path)

    def _get_conn(self):
        return db.get_conn(self.db_path)

    # --- 数据提取 ---------------------------------------------------------

    def _extract_training_samples(
        self, feedbacks: list[dict], parsed_tracked: dict[str, dict], conn
    ) -> list[dict]:
        """从 feedback 中提取带 context + treatment + outcome 的训练样本。"""
        samples = []

        dates = [fb["date"] for fb in feedbacks if fb.get("date")]
        if not dates:
            return samples

        # 实验期间跳过偏好学习，防止受控干预污染正常学习
        try:
            active_exp = conn.execute(
                "SELECT start_date, end_date FROM experiments WHERE status = 'active' LIMIT 1"
            ).fetchone()
            if active_exp and active_exp["start_date"]:
                exp_start = active_exp["start_date"]
                exp_end = active_exp["end_date"] or date.today().isoformat()
                feedbacks = [
                    fb for fb in feedbacks
                    if fb.get("date") and not (exp_start <= fb["date"] <= exp_end)
                ]
                dates = [fb["date"] for fb in feedbacks if fb.get("date")]
                if not dates:
                    return samples
        except Exception:
            pass

        placeholders = ",".join("?" * len(dates))
        ex_rows = conn.execute(
            f"""SELECT date, name, start_time, avg_hr, duration_seconds
                FROM exercises
                WHERE date IN ({placeholders})
                  AND avg_hr IS NOT NULL""",
            dates,
        ).fetchall()

        ex_by_date: dict[str, list[dict]] = defaultdict(list)
        for row in ex_rows:
            ex_by_date[row["date"]].append(dict(row))

        dh_rows = conn.execute(
            f"""SELECT date,
                       hrv_last_night_avg AS hrv_avg,
                       sleep_score
                FROM daily_health
                WHERE date IN ({placeholders})""",
            dates,
        ).fetchall()
        dh_by_date = {r["date"]: dict(r) for r in dh_rows}

        today = date.today()
        for fb in feedbacks:
            d = fb.get("date")
            tracked = parsed_tracked.get(d)
            if not tracked or not d:
                continue

            cs_avg = tracked.get("composite_score_avg")
            if cs_avg is None:
                pos = tracked.get("positive_signals", 0)
                neg = tracked.get("negative_signals", 0)
                cs_avg = (pos - neg) * 0.3
                cs_day1 = cs_avg
                cs_day2 = None
            else:
                cs_day1 = tracked.get("composite_score_day1")
                cs_day2 = tracked.get("composite_score_day2")

            days_ago = (today - datetime.fromisoformat(d).date()).days
            decay = math.exp(-days_ago / 90.0)

            dh = dh_by_date.get(d, {})
            hrv = dh.get("hrv_avg")
            sleep = dh.get("sleep_score")
            hrv_level = self._classify_hrv(hrv)
            sleep_level = self._classify_sleep(sleep)

            exs = ex_by_date.get(d)
            if exs:
                primary = self.effect_tracker._pick_primary_exercise(exs)
                ex_type = self._normalize_exercise_type(primary["name"] if primary else "未知运动")
                # 只保留主运动用于时长/心率/时段分析，避免辅助运动稀释主类型信号
                exs = [primary]
            else:
                # 无详细运动记录，仍保留样本（仅用于类型学习）
                exs = [{"avg_hr": None, "duration_seconds": None, "start_time": None}]
                ex_type = self._normalize_exercise_type(fb.get("actual_action") or "未知运动")

            for ex in exs:
                hr_zone = self._classify_hr_zone(ex.get("avg_hr"))
                dur_bin = self._classify_duration((ex.get("duration_seconds") or 0) / 60)
                time_slot = self._classify_time_slot(ex.get("start_time"))

                samples.append({
                    "date": d,
                    "exercise_type": ex_type,
                    "hr_zone": hr_zone,
                    "duration_bin": dur_bin,
                    "time_slot": time_slot,
                    "hrv_level": hrv_level,
                    "sleep_level": sleep_level,
                    "composite_score_avg": cs_avg,
                    "composite_score_day1": cs_day1,
                    "composite_score_day2": cs_day2,
                    "days_ago": days_ago,
                    "decay": decay,
                })

        return samples

    @staticmethod
    def _normalize_exercise_type(name: str) -> str:
        m = re.match(r"^([^\(（]+)", name)
        base = m.group(1).strip() if m else name.split(" ")[0]
        if base in StrategyLearner._GARMIN_RUN_AUTO_LABELS:
            return "跑步"
        return base

    @staticmethod
    def _classify_hrv(hrv: float | None) -> str:
        if hrv is None:
            return "unknown"
        if hrv > 45:
            return "high"
        if hrv >= 35:
            return "mid"
        return "low"

    @staticmethod
    def _classify_sleep(sleep: float | None) -> str:
        if sleep is None:
            return "unknown"
        if sleep >= 80:
            return "high"
        if sleep >= 60:
            return "mid"
        return "low"

    @staticmethod
    def _classify_hr_zone(avg_hr: float | None) -> str:
        if avg_hr is None:
            return "unknown"
        if avg_hr < 120:
            return "low"
        if avg_hr <= 150:
            return "moderate"
        return "high"

    @staticmethod
    def _classify_duration(duration_min: float) -> str:
        if duration_min < 25:
            return "short"
        if duration_min <= 50:
            return "medium"
        return "long"

    @staticmethod
    def _classify_time_slot(start_time: str | None) -> str:
        if not start_time:
            return "unknown"
        try:
            hour = int(start_time.split(":")[0])
        except (ValueError, AttributeError):
            return "unknown"
        if 5 <= hour < 9:
            return "early_morning"
        if 9 <= hour < 12:
            return "morning"
        if 12 <= hour < 18:
            return "afternoon"
        if 18 <= hour < 23:
            return "evening"
        return "unknown"

    # --- 核心学习器：经验贝叶斯收缩 ----------------------------------------

    def _learn_with_shrinkage(
        self,
        samples: list[dict],
        group_fn,
        min_evidence: int = 3,
    ) -> dict[str, dict]:
        """通用经验贝叶斯收缩估计器。

        group_fn: sample -> group_name | None
        返回: {group_name: {"shrunk_mean", "raw_mean", "n", "lambda", "confidence"}}
        """
        groups: dict[str, list[float]] = defaultdict(list)
        all_outcomes = []

        for s in samples:
            g = group_fn(s)
            if g is None:
                continue
            outcome = s["composite_score_avg"] * s["decay"]
            groups[g].append(outcome)
            all_outcomes.append(outcome)

        if len(all_outcomes) < min_evidence:
            return {}

        mu_global = mean(all_outcomes)
        var_global = variance(all_outcomes) if len(all_outcomes) > 1 else 1.0

        group_means = [mean(v) for v in groups.values() if len(v) >= 1]
        if len(group_means) > 1:
            tau_sq = variance(group_means)
        else:
            tau_sq = 0.0

        k = max(3.0, var_global / max(tau_sq, 0.001))

        results = {}
        for g, outcomes in groups.items():
            n = len(outcomes)
            mu_group = mean(outcomes)
            lam = n / (n + k)
            mu_shrunk = lam * mu_group + (1 - lam) * mu_global
            confidence = lam * min(0.95, 0.3 + n * 0.1)

            results[g] = {
                "shrunk_mean": mu_shrunk,
                "raw_mean": mu_group,
                "n": n,
                "lambda": lam,
                "confidence": confidence,
            }
            log.debug(
                "shrinkage [%s] raw=%.2f shrunk=%.2f n=%d lambda=%.2f",
                g, mu_group, mu_shrunk, n, lam
            )

        return results

    # --- 各维度学习方法 ----------------------------------------------------

    # 不应作为"偏好运动类型"学习的特殊值
    _NON_EXERCISE_TYPES = {"未运动", "未知运动"}

    # Garmin Connect 服务端基于默认参数（未校准的最大心率/LTHR）
    # 自动给跑步活动打子类标签；分类结果与实际强度脱节，
    # 为避免污染偏好学习，统一归一为「跑步」。
    _GARMIN_RUN_AUTO_LABELS = {"基础训练", "节奏", "乳酸阈值", "恢复跑", "长距离", "冲刺"}

    def _learn_exercise_type(self, samples: list[dict]) -> dict[str, str]:
        # 过滤掉"未运动/未知运动"，全局偏好只从实际运动类型中学习
        exercise_samples = [
            s for s in samples
            if s["exercise_type"] not in self._NON_EXERCISE_TYPES
        ]
        results = self._learn_with_shrinkage(
            exercise_samples, lambda s: s["exercise_type"], min_evidence=3
        )
        if not results:
            return {}

        updates = {}
        best = max(results.items(), key=lambda x: x[1]["shrunk_mean"])
        if best[1]["shrunk_mean"] > 0.05 and best[1]["n"] >= 3:
            self._update_preference(
                "exercise_type", "preferred_type",
                best[0],
                confidence=best[1]["confidence"],
                evidence_count=best[1]["n"],
            )
            updates["preferred_type"] = best[0]
            log.info(
                "学习到偏好运动类型: %s（shrunk=%.2f, n=%d, λ=%.2f）",
                best[0], best[1]["shrunk_mean"], best[1]["n"], best[1]["lambda"]
            )

        for g, info in results.items():
            if info["shrunk_mean"] < -0.05 and info["n"] >= 3:
                key = f"avoid_{g.replace(' ', '_')}"
                self._update_preference(
                    "exercise_type", key, "true",
                    confidence=info["confidence"],
                    evidence_count=info["n"],
                )
                updates[key] = "true"
                log.info("学习到应避免的运动: %s", g)

        return updates

    def _learn_contextual_exercise(self, samples: list[dict]) -> dict[str, str]:
        """按 HRV 分层学习最优运动类型。"""
        exercise_samples = [
            s for s in samples
            if s["exercise_type"] not in self._NON_EXERCISE_TYPES
        ]
        results = self._learn_with_shrinkage(
            exercise_samples,
            lambda s: (s["hrv_level"], s["exercise_type"])
            if s["hrv_level"] != "unknown" else None,
            min_evidence=2,
        )
        if not results:
            return {}

        by_context: dict[str, dict[str, dict]] = defaultdict(dict)
        for (ctx, ex_type), info in results.items():
            by_context[ctx][ex_type] = info

        updates = {}
        for ctx, ex_dict in by_context.items():
            valid = {
                k: v for k, v in ex_dict.items()
                if v["n"] >= 2 and v["lambda"] > 0.2
            }
            if not valid:
                continue
            best = max(valid.items(), key=lambda x: x[1]["shrunk_mean"])
            if best[1]["shrunk_mean"] > 0.0:
                key = f"{ctx}_best_type"
                self._update_preference(
                    "context_exercise", key,
                    best[0],
                    confidence=best[1]["confidence"],
                    evidence_count=best[1]["n"],
                )
                updates[key] = best[0]
                log.info(
                    "学习到 [%s] 最优运动: %s（shrunk=%.2f, n=%d）",
                    ctx, best[0], best[1]["shrunk_mean"], best[1]["n"]
                )

        return updates

    def _learn_dose_response(self, samples: list[dict]) -> dict[str, str]:
        """分析运动强度和时长与恢复效果的关系（连续评分版）。"""
        exercise_samples = [
            s for s in samples
            if s["exercise_type"] not in self._NON_EXERCISE_TYPES
        ]
        updates = {}

        hr_results = self._learn_with_shrinkage(
            exercise_samples,
            lambda s: s["hr_zone"] if s["hr_zone"] != "unknown" else None,
            min_evidence=3,
        )
        if hr_results:
            best_hr = max(hr_results.items(), key=lambda x: x[1]["shrunk_mean"])
            if best_hr[1]["n"] >= 3:
                zone_map = {"low": "<120bpm", "moderate": "120-150bpm", "high": ">150bpm"}
                val = zone_map.get(best_hr[0], best_hr[0])
                self._update_preference(
                    "intensity", "optimal_hr_zone",
                    val,
                    confidence=best_hr[1]["confidence"],
                    evidence_count=best_hr[1]["n"],
                )
                updates["optimal_hr_zone"] = val
                log.info("学习到最优心率区间: %s（shrunk=%.2f）", val, best_hr[1]["shrunk_mean"])

        dur_results = self._learn_with_shrinkage(
            exercise_samples,
            lambda s: s["duration_bin"] if s["duration_bin"] != "unknown" else None,
            min_evidence=3,
        )
        if dur_results:
            best_dur = max(dur_results.items(), key=lambda x: x[1]["shrunk_mean"])
            if best_dur[1]["n"] >= 3:
                dur_map = {"short": "<25min", "medium": "25-50min", "long": ">50min"}
                val = dur_map.get(best_dur[0], best_dur[0])
                self._update_preference(
                    "intensity", "optimal_duration",
                    val,
                    confidence=best_dur[1]["confidence"],
                    evidence_count=best_dur[1]["n"],
                )
                updates["optimal_duration"] = val
                log.info("学习到最优运动时长: %s（shrunk=%.2f）", val, best_dur[1]["shrunk_mean"])

        combo_results = self._learn_with_shrinkage(
            exercise_samples,
            lambda s: f"{s['hr_zone']}_{s['duration_bin']}"
            if s["hr_zone"] != "unknown" and s["duration_bin"] != "unknown" else None,
            min_evidence=2,
        )
        if combo_results:
            valid_combos = {
                k: v for k, v in combo_results.items()
                if v["n"] >= 3 and v["lambda"] > 0.25
            }
            if valid_combos:
                best_combo = max(valid_combos.items(), key=lambda x: x[1]["shrunk_mean"])
                self._update_preference(
                    "intensity", "optimal_combo",
                    best_combo[0],
                    confidence=best_combo[1]["confidence"],
                    evidence_count=best_combo[1]["n"],
                )
                updates["optimal_combo"] = best_combo[0]
                log.info(
                    "学习到最优组合: %s（shrunk=%.2f）",
                    best_combo[0], best_combo[1]["shrunk_mean"]
                )

        return updates

    def _learn_time_preference(self, samples: list[dict]) -> dict[str, str]:
        """分析运动时段与效果的关系（连续评分版）。"""
        results = self._learn_with_shrinkage(
            samples,
            lambda s: s["time_slot"] if s["time_slot"] != "unknown" else None,
            min_evidence=15,
        )
        if not results:
            return {}

        best = max(results.items(), key=lambda x: x[1]["shrunk_mean"])
        if best[1]["n"] >= 15:
            self._update_preference(
                "timing", "optimal_time_slot",
                best[0],
                confidence=best[1]["confidence"],
                evidence_count=best[1]["n"],
            )
            log.info("学习到最优运动时段: %s（shrunk=%.2f）", best[0], best[1]["shrunk_mean"])
            return {"optimal_time_slot": best[0]}

        return {}

    def _learn_recovery_speed(self, samples: list[dict]) -> dict[str, str]:
        """基于 composite_score 分析恢复速度。"""
        recovery_days = []
        for s in samples:
            cs1 = s.get("composite_score_day1")
            cs2 = s.get("composite_score_day2")

            first_pos = None
            if cs1 is not None and cs1 > 0.1:
                first_pos = 1
            elif cs2 is not None and cs2 > 0.1:
                first_pos = 2
            else:
                first_pos = 4

            recovery_days.append(first_pos)

        if len(recovery_days) < 3:
            return {}

        avg_day = mean(recovery_days)
        if avg_day <= 1.3:
            speed = "fast"
        elif avg_day <= 2.2:
            speed = "normal"
        else:
            speed = "slow"

        self._update_preference(
            "recovery_pattern", "recovery_speed",
            speed,
            confidence=min(0.9, 0.4 + len(recovery_days) * 0.05),
            evidence_count=len(recovery_days),
        )
        log.info("学习到恢复速度: %s（平均 %.1f 天，%d 条记录）", speed, avg_day, len(recovery_days))
        return {"recovery_speed": speed}

    def _apply_safety_constraints(self, samples: list[dict], updates: dict[str, str]):
        """医学安全硬约束：覆盖不符合安全常识的学习结果。"""
        low_hrv_samples = [s for s in samples if s["hrv_level"] == "low"]
        if low_hrv_samples:
            key = "low_best_type"
            learned = updates.get(key)
            # 低 HRV 时只允许恢复性活动；其余一律覆盖为休息
            allowed_low_hrv = {"散步/休息", "步行", "放松和专注", "瑜伽", "健行", "拉伸", "冥想"}
            if learned and learned not in allowed_low_hrv:
                log.warning("安全约束覆盖: %s 从 %s 改为 散步/休息", key, learned)
                self._update_preference(
                    "context_exercise", key, "散步/休息",
                    confidence=0.7, evidence_count=len(low_hrv_samples)
                )
                updates[key] = "散步/休息"

    def _cleanup_stale_preferences(self, max_age_days: int = 180):
        """降级过旧的学习偏好置信度。"""
        try:
            from datetime import date, timedelta
            cutoff = (date.today() - timedelta(days=max_age_days)).isoformat()
            with self._get_conn() as conn:
                conn.execute(
                    """UPDATE learned_preferences
                       SET confidence_score = ROUND(confidence_score * 0.8, 3)
                       WHERE last_updated < ?
                         AND confidence_score > 0.3""",
                    (cutoff,),
                )
        except Exception as e:
            log.debug("清理旧偏好失败: %s", e)

    def _evaluate_existing_preferences(self, recent_days: int = 30):
        """评估所有 active 偏好的近期效果，执行 commit/revert 决策。

        改进版：不再用全局 avg_q 一刀切抹杀所有偏好，而是按偏好自身的证据量和
        置信度给予分层保护，避免一条 bad feedback 摧毁整个学习体系。

        Commit 规则（保持不变，偏保守）：
        - 全局 avg_q >= 0.70 → 所有符合条件的偏好 committed

        Revert 规则（分层免疫）：
        - 基础阈值 0.30，但证据越多、置信度越高，阈值越低（越不容易被 revert）
        - 7 天内新学习的偏好受保护（给观察期）
        - experiment_suggestion 不参与 revert
        """
        try:
            with self._get_conn() as conn:
                avg_q = db.query_avg_quality_for_preference(conn, recent_days=recent_days)
        except Exception as e:
            log.warning("查询 quality_score 失败: %s", e)
            return

        if avg_q is None:
            log.info("evaluate_preferences: quality_score 数据不足（<3天），跳过评估")
            return

        try:
            with self._get_conn() as conn:
                active_prefs = db.query_learned_preferences(conn, exclude_status="reverted")
        except Exception as e:
            log.warning("查询活跃偏好失败: %s", e)
            return

        committed_count = 0
        reverted_count = 0
        for pref in active_prefs:
            # 跳过临时/实验类偏好
            if pref["preference_type"] in ("experiment_suggestion",):
                continue
            ev = pref.get("evidence_count", 0)
            if ev < 3:
                continue

            conf = pref.get("confidence_score", 0.0)
            last_updated = pref.get("last_updated", "")

            # Commit: 全局表现优秀时统一 commit（安全操作）
            if avg_q >= 0.70:
                try:
                    with self._get_conn() as conn:
                        db.update_preference_status(
                            conn,
                            preference_type=pref["preference_type"],
                            preference_key=pref["preference_key"],
                            status="committed",
                            confidence_multiplier=1.2,
                        )
                    committed_count += 1
                except Exception as e:
                    log.warning("commit 偏好失败 [%s]: %s", pref["preference_key"], e)
                continue

            # Revert: 分层免疫，高证据/高置信度偏好更难被误杀
            if avg_q <= 0.30:
                # 7 天保护期：新学习的偏好给观察时间
                try:
                    from datetime import datetime, timedelta
                    updated_dt = datetime.fromisoformat(str(last_updated).replace(" ", "T"))
                    if (datetime.now() - updated_dt).days < 7:
                        continue
                except Exception:
                    pass

                # 动态阈值：证据越多越免疫
                revert_threshold = 0.30
                if ev >= 10:
                    revert_threshold = 0.15
                elif ev >= 5:
                    revert_threshold = 0.20

                # 高置信度额外保护
                if conf >= 0.6:
                    revert_threshold -= 0.05

                if avg_q <= revert_threshold:
                    try:
                        with self._get_conn() as conn:
                            db.update_preference_status(
                                conn,
                                preference_type=pref["preference_type"],
                                preference_key=pref["preference_key"],
                                status="reverted",
                                confidence_multiplier=0.8,
                            )
                        reverted_count += 1
                    except Exception as e:
                        log.warning("revert 偏好失败 [%s]: %s", pref["preference_key"], e)

        if committed_count or reverted_count:
            log.info(
                "evaluate_preferences: avg_q=%.3f, committed=%d, reverted=%d",
                avg_q, committed_count, reverted_count
            )
        else:
            log.debug("evaluate_preferences: avg_q=%.3f, 无变更", avg_q)

    def _suggest_experiments(self, samples: list[dict]) -> list[dict]:
        """在数据稀疏区域生成探索性建议。

        使用 upsert 写入当前探索建议，然后清理本次未生成（已过时）的旧建议，
        避免全量 DELETE + INSERT 的竞态条件。
        """
        all_types = sorted({s["exercise_type"] for s in samples})
        contexts = ["high", "mid", "low"]

        counts: dict[tuple[str, str], int] = defaultdict(int)
        for s in samples:
            if s["hrv_level"] != "unknown":
                counts[(s["hrv_level"], s["exercise_type"])] += 1

        suggestions = []
        generated_keys = set()
        excluded_types = {"未运动", "休息", "无运动", "无", "", "未知运动"}
        safe_types_by_context = {
            "high": [t for t in all_types if t not in excluded_types],
            "mid": [t for t in all_types if t not in excluded_types],
            "low": [t for t in all_types if t not in excluded_types and t not in ("跑步", "力量", "HIIT")],
        }

        for ctx in contexts:
            for ex_type in safe_types_by_context.get(ctx, []):
                n = counts.get((ctx, ex_type), 0)
                if 0 < n < 3:
                    key = f"experiment_{ctx}_{ex_type.replace(' ', '_')}"
                    generated_keys.add(key)
                    desc = (
                        f"建议低 HRV 时尝试 {ex_type} 以收集更多数据"
                        if ctx == "low" else
                        f"建议 {ctx} HRV 时尝试 {ex_type} 以收集更多数据"
                    )
                    self._update_preference(
                        "experiment_suggestion",
                        key,
                        desc,
                        confidence=0.3,
                        evidence_count=n,
                    )
                    suggestions.append({
                        "key": key,
                        "context": ctx,
                        "exercise_type": ex_type,
                        "n": n,
                    })

        # 清理本次未重新生成的过时探索建议
        if generated_keys:
            self._cleanup_stale_experiments(generated_keys)
        else:
            # 无新建议时清理所有旧 explore 建议
            try:
                with self._get_conn() as conn:
                    conn.execute(
                        "DELETE FROM learned_preferences WHERE preference_type = ?",
                        ("experiment_suggestion",),
                    )
            except Exception as e:
                log.debug("清理旧探索建议失败: %s", e)

        if suggestions:
            log.info("生成 %d 条探索建议", len(suggestions))
        return suggestions

    def _cleanup_stale_experiments(self, active_keys: set[str]):
        """删除不在 active_keys 中的过时 experiment_suggestion。"""
        if not active_keys:
            return
        try:
            with self._get_conn() as conn:
                placeholders = ",".join("?" * len(active_keys))
                conn.execute(
                    f"""DELETE FROM learned_preferences
                        WHERE preference_type = 'experiment_suggestion'
                          AND preference_key NOT IN ({placeholders})""",
                    tuple(active_keys),
                )
        except Exception as e:
            log.debug("清理过时探索建议失败: %s", e)

    # --- 主入口 -----------------------------------------------------------

    def learn_from_recent_feedback(self, days: int = 180,
                                     active_goals: list[dict] = None) -> dict[str, str]:
        """分析近 N 天反馈，更新学习偏好。

        Args:
            active_goals: 活跃目标列表，传入后偏好写入会带上 goal_id。
        """
        end = date.today().isoformat()
        start = (datetime.fromisoformat(end) - timedelta(days=days)).isoformat()[:10]

        # 获取主目标的 goal_id（P1 目标优先）
        primary_goal_id = None
        if active_goals:
            primary = next((g for g in active_goals if g.get("priority") == 1), None)
            if primary is None:
                primary = active_goals[0]
            primary_goal_id = primary.get("id")

        try:
            with self._get_conn() as conn:
                feedbacks = db.query_feedback_by_date_range(
                    conn, start, end, recommendation_type="exercise"
                )
        except Exception as e:
            log.error("查询反馈失败: %s", e)
            return {}

        if len(feedbacks) < self.MIN_EVIDENCE:
            log.info("反馈数量不足（%d < %d），跳过学习", len(feedbacks), self.MIN_EVIDENCE)
            return {}

        parsed_tracked: dict[str, dict] = {}
        for fb in feedbacks:
            if fb.get("tracked_metrics"):
                try:
                    parsed_tracked[fb["date"]] = json.loads(fb["tracked_metrics"])
                except (json.JSONDecodeError, TypeError):
                    pass

        with self._get_conn() as conn:
            samples = self._extract_training_samples(feedbacks, parsed_tracked, conn)

        if len(samples) < 3:
            log.info("有效训练样本不足（%d < 3），跳过学习", len(samples))
            return {}

        log.info("开始策略学习，样本数: %d", len(samples))

        # 将 primary_goal_id 传递给各学习方法
        self._current_goal_id = primary_goal_id

        updates = {}
        updates.update(self._learn_exercise_type(samples))
        updates.update(self._learn_contextual_exercise(samples))
        updates.update(self._learn_dose_response(samples))
        updates.update(self._learn_time_preference(samples))
        updates.update(self._learn_recovery_speed(samples))
        self._apply_safety_constraints(samples, updates)
        self._suggest_experiments(samples)
        self._cleanup_stale_preferences()

        self._current_goal_id = None
        return updates

    def _update_preference(
        self,
        preference_type: str,
        preference_key: str,
        preference_value: str,
        confidence: float,
        evidence_count: int,
        goal_id: int = None,
        status: str = "active",
    ):
        """写入或更新偏好到数据库。"""
        gid = goal_id or getattr(self, '_current_goal_id', None)
        try:
            with self._get_conn() as conn:
                db.upsert_learned_preference(
                    conn,
                    preference_type=preference_type,
                    preference_key=preference_key,
                    preference_value=preference_value,
                    confidence_score=round(confidence, 3),
                    evidence_count=evidence_count,
                    goal_id=gid,
                    status=status,
                )
        except Exception as e:
            log.error("更新偏好失败 [%s/%s]: %s", preference_type, preference_key, e)

    def get_summary(self) -> list[dict]:
        """获取所有已学习的偏好摘要。"""
        try:
            with self._get_conn() as conn:
                return db.query_learned_preferences(conn)
        except Exception:
            return []

    def top_exercises_for_goal(self, goal_id: int, top_n: int = 3) -> list[dict]:
        """返回对指定目标最有效的 top-N 运动/习惯。"""
        try:
            with self._get_conn() as conn:
                rows = conn.execute(
                    """SELECT preference_key, preference_value,
                              confidence_score, evidence_count
                       FROM learned_preferences
                       WHERE goal_id = ?
                         AND preference_type = 'exercise_type'
                       ORDER BY confidence_score DESC
                       LIMIT ?""",
                    (goal_id, top_n)
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception:
            return []

    def run_full_analysis(self, days: int = 30,
                          active_goals: list[dict] = None) -> dict:
        """运行完整的学习流程并返回分析报告。"""
        try:
            recent_effects = self.effect_tracker.track_recent_exercises(days=days)
            self.effect_tracker.write_effects_to_db(recent_effects)
            log.info("已追踪并写回 %d 条运动效果", len(recent_effects))
        except Exception as e:
            log.warning("效果追踪失败: %s", e)
            recent_effects = []

        updates = self.learn_from_recent_feedback(days=days, active_goals=active_goals)
        self._evaluate_existing_preferences(recent_days=30)
        all_prefs = self.get_summary()

        return {
            "analyzed_days": days,
            "tracked_exercises": len(recent_effects),
            "preference_updates": updates,
            "total_preferences": len(all_prefs),
            "preferences": all_prefs,
        }


def main():
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    ap = argparse.ArgumentParser(description="运行策略学习引擎")
    ap.add_argument("--days", type=int, default=180, help="分析最近 N 天的反馈（默认180）")
    args = ap.parse_args()

    learner = StrategyLearner()
    result = learner.run_full_analysis(days=args.days)

    print(f"\n=== 策略学习报告 ===")
    print(f"分析周期：{result['analyzed_days']} 天")
    print(f"追踪运动次数：{result['tracked_exercises']}")
    print(f"更新偏好数：{len(result['preference_updates'])}")

    if result["preference_updates"]:
        print("\n新学习到的偏好：")
        for k, v in result["preference_updates"].items():
            print(f"  - {k}: {v}")

    if result["preferences"]:
        print("\n全部已学习偏好：")
        for p in result["preferences"]:
            print(
                f"  [{p['preference_type']}] {p['preference_key']} = {p['preference_value']}"
                f" (置信度: {p['confidence_score']:.2f}, 证据: {p['evidence_count']}条)"
            )


if __name__ == "__main__":
    main()
