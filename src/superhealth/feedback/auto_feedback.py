"""自动反馈收集器：每日 morning 运行，从 Garmin 数据自动填充 recommendation_feedback。

两阶段写入设计：
  Phase 1（advanced_daily_report.py 生成报告当日）：
    INSERT recommendation_feedback，填写 recommendation_content（LLM 推荐），
    compliance / actual_action 为 NULL。

  Phase 2（本脚本，次日 morning 运行）：
    UPDATE 已有记录，根据 exercises 表 + RecoveryModel 评估填写：
    - compliance   ← 5 路判断（见下表）
    - actual_action ← exercises 实际记录

compliance 判断规则（readiness 来自 RecoveryModel 重评估昨日数据）：
  ┌──────────────────────┬───────────────┬─────────────┐
  │ 建议（readiness）     │ 实际          │ compliance  │
  ├──────────────────────┼───────────────┼─────────────┤
  │ 适合高/中强度         │ 有运动        │ 1           │
  │ 适合高/中强度         │ 无运动        │ 0           │
  │ 建议轻度活动          │ 有正式运动    │ 0（过度）   │
  │ 建议轻度活动          │ 无运动 步≥6000│ 1（步行达标）│
  │ 建议轻度活动          │ 无运动 步<6000│ 0           │
  │ 建议休息              │ 无运动        │ 1           │
  │ 建议休息              │ 有运动        │ 0（覆盖建议）│
  └──────────────────────┴───────────────┴─────────────┘

幂等：若已存在 compliance IS NOT NULL 记录则跳过。
向后兼容：若无对应记录（旧日期未生成 Phase 1 记录），则 INSERT 新记录。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from superhealth import database as db
from superhealth.core.assessment_models import RecoveryModel
from superhealth.feedback.effect_tracker import EffectTracker

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent.parent / "health.db"


def compute_quality_score(
    compliance: int,
    goal_progress_norm: float | None = None,
    composite_score: float | None = None,
    user_rating: int | None = None,
) -> float:
    """将 4 个分散信号合成一个 [0, 1] 范围的标量 quality_score。

    compliance: 0-100 (LLM 评估的遵从度)
    goal_progress_norm: 0-1 (目标进度，None 则用 0.5 中性值)
    composite_score: -1 ~ +1 (effect_tracker 的运动后生理恢复评分，None 则用 0 中性)
    user_rating: 1-5 (用户主观评分，None 则用 0.5 中性值)
    """
    c = compliance / 100.0
    g = goal_progress_norm if goal_progress_norm is not None else 0.5
    g = max(0.0, min(1.0, g))
    e = (composite_score + 1) / 2.0 if composite_score is not None else 0.5
    r = (user_rating - 1) / 4.0 if user_rating else 0.5

    return 0.30 * c + 0.25 * g + 0.25 * e + 0.20 * r


def _get_readiness(conn, yesterday: str) -> str | None:
    """用 RecoveryModel 重新评估昨天的 readiness，返回 readiness 字符串或 None。

    readiness 存储在 AssessmentResult.tags 中，格式为 "readiness:建议休息"。
    """
    flat = db.query_daily_flat(conn, yesterday)
    if not flat:
        return None
    try:
        rm = RecoveryModel()
        result = rm.assess(flat, {}, None)  # vitals_data/profile 在 RecoveryModel 中未使用
        for tag in result.tags:
            if tag.startswith("readiness:"):
                return tag.split(":", 1)[1]
        return None
    except Exception as e:
        log.warning("RecoveryModel 评估失败: %s", e)
        return None


def _build_actual_action(exercise_rows) -> str | None:
    """将当日主运动记录整理为 actual_action 字符串。

    若当日有多条运动，只取主运动（时长 × 心率得分最高），
    避免辅助运动（呼吸、热身）被误认为主类型。
    """
    primary = EffectTracker._pick_primary_exercise(exercise_rows)
    if not primary:
        return None
    mins = int((primary["duration_seconds"] or 0) / 60)
    meta = primary["type_key"] or ""
    if primary.get("details"):
        meta += f", {primary['details']}"
    meta += f", {mins}min"
    return f"{primary['name']}({meta})"


def _judge_compliance_by_llm(
    recommendation_content: str,
    actual_action: str,
    user_feedback: str | None = None,
    exercise_stats: list[dict] | None = None,
    steps: int | None = None,
) -> int | None:
    """调用 Claude API 判断实际行动对建议的遵从度，返回 0-100 的百分比。

    例如：85 表示实际与建议的符合度为 85%。
    调用失败返回 None。
    """
    try:
        import anthropic

        from superhealth.config import load as load_cfg

        cfg = load_cfg()
        claude_cfg = cfg.claude
        if not claude_cfg.is_complete():
            log.warning("compliance LLM: Claude API key 未配置")
            return None

        kwargs = {"api_key": claude_cfg.api_key}
        if claude_cfg.base_url:
            kwargs["base_url"] = claude_cfg.base_url
        client = anthropic.Anthropic(**kwargs)

        feedback_section = ""
        if user_feedback:
            feedback_section = f"""
用户反馈：
{user_feedback}
"""

        stats_section = ""
        if exercise_stats:
            stats_lines = ["【Garmin 记录的实际运动数据】"]
            for i, s in enumerate(exercise_stats, 1):
                parts = [f"运动{i}: {s['name']}"]
                if s.get("duration_min"):
                    parts.append(f"时长{s['duration_min']}分钟")
                if s.get("avg_hr"):
                    parts.append(f"平均心率{s['avg_hr']:.0f}bpm")
                if s.get("max_hr"):
                    parts.append(f"最高心率{s['max_hr']:.0f}bpm")
                if s.get("distance_km"):
                    parts.append(f"距离{s['distance_km']:.1f}km")
                if s.get("calories"):
                    parts.append(f"消耗{s['calories']:.0f}kcal")
                stats_lines.append("  ".join(parts))
            if steps is not None:
                stats_lines.append(f"全天步数: {steps}步")
            stats_section = "\n".join(stats_lines) + "\n"

        prompt = f"""你是一个健康行为评估助手。请评估用户当天的实际行动与健康建议的符合程度。

用户佩戴 Garmin 手表，运动类型只能记录为 running/cycling/strength/yoga 等基础类型，无法自动区分"间歇跑"、"节奏跑"或"3:1模式"。评估时请不要仅因活动名称不完全匹配而扣分，应重点结合心率区间、运动时长、距离和强度判断实际执行与建议的符合度。

健康建议：
{recommendation_content}

实际行动（Garmin 自动记录）：
{actual_action}
{feedback_section}
{stats_section}
请评估实际行动与健康建议的符合程度，给出一个 0-100 的百分比分数。
评分标准：
- 100：完全符合建议（方向、强度和时长都一致）
- 80-99：基本符合（方向一致，强度/时长略有偏差但在合理范围）
- 50-79：部分符合（方向大致正确但强度差异较大，或因设备限制无法精确记录模式）
- 0-49：未遵从或过度运动

只输出一个 0-100 的整数，不要输出任何其他内容。"""

        resp = client.messages.create(
            model=claude_cfg.model,
            max_tokens=getattr(claude_cfg, "max_tokens", 2048) or 2048,
            messages=[{"role": "user", "content": prompt}],
        )
        text_blocks = [b for b in resp.content if b.type == "text"]
        if not text_blocks:
            log.warning(
                "compliance LLM 无 TextBlock 返回，content types: %s",
                [b.type for b in resp.content],
            )
            return None
        result = text_blocks[0].text.strip()
        # 提取数字
        match = re.search(r"\d+", result)
        if match:
            score = int(match.group())
            return max(0, min(100, score))  # 限制在 0-100
        log.warning("compliance LLM 返回内容无法解析为数字: %r", result)
        return None

    except Exception as e:
        log.warning("compliance LLM 判断失败: %s", e, exc_info=True)
        return None


def run(target_date: str = None, db_path: Path = DB_PATH) -> bool:
    """为 target_date（默认昨天）自动更新反馈记录。

    流程：
    1. 获取昨天的运动记录和步数
    2. 从 recommendation_feedback 查询 recommendation_content 和 user_feedback
    3. 用 LLM 评估 compliance（0-100 的百分比）
    4. 更新数据库

    Returns: True 表示成功更新/插入，False 表示跳过（数据不足或已完成）。
    """
    today = date.today().isoformat()
    yesterday = target_date or (datetime.fromisoformat(today) - timedelta(days=1)).isoformat()[:10]
    report_id = f"{yesterday}-advanced-daily-report"

    with db.get_conn(db_path) as conn:
        # ── 幂等检查：compliance 已填写则跳过 ──
        existing = conn.execute(
            """SELECT id, compliance, recommendation_type, recommendation_content, user_feedback, user_rating, tracked_metrics FROM recommendation_feedback
               WHERE date = ? AND recommendation_type IN ('exercise', 'recovery', 'rest')""",
            (yesterday,),
        ).fetchone()
        if existing and existing["compliance"] is not None:
            log.info(
                "auto_feedback: %s 已完成 compliance=%d，计算 quality_score",
                yesterday,
                existing["compliance"],
            )
            rec_type = existing["recommendation_type"]
            _write_quality_score(conn, yesterday, rec_type, existing["compliance"], existing)
            return False

        # ── 1. 昨天的运动记录 ──
        exercise_rows = conn.execute(
            "SELECT name, duration_seconds, type_key, details, avg_hr, max_hr, distance_meters, calories FROM exercises WHERE date = ? ORDER BY id",
            (yesterday,),
        ).fetchall()

        # ── 2. 昨天的步数 ──
        steps_row = conn.execute(
            "SELECT steps FROM daily_health WHERE date = ?", (yesterday,)
        ).fetchone()
        steps = int((steps_row["steps"] or 0) if steps_row else 0)

        log.info("auto_feedback: %s steps=%d exercises=%d", yesterday, steps, len(exercise_rows))

        # ── 3. 整理 actual_action ──
        actual_action = _build_actual_action(exercise_rows) or "未运动"

        # ── 4. 用 LLM 判断 compliance ──
        recommendation_content = existing["recommendation_content"] if existing else None
        user_feedback = existing["user_feedback"] if existing else None

        if not recommendation_content:
            log.info("auto_feedback: %s 无 recommendation_content，跳过 compliance 写入", yesterday)
            return False

        # 构建运动统计供 LLM 参考
        exercise_stats = []
        for r in exercise_rows:
            s = {"name": r["name"], "type_key": r["type_key"]}
            if r["duration_seconds"]:
                s["duration_min"] = int(r["duration_seconds"] / 60)
            if r["avg_hr"]:
                s["avg_hr"] = r["avg_hr"]
            if r["max_hr"]:
                s["max_hr"] = r["max_hr"]
            if r["distance_meters"]:
                s["distance_km"] = round(r["distance_meters"] / 1000, 1)
            if r["calories"]:
                s["calories"] = r["calories"]
            exercise_stats.append(s)

        compliance = _judge_compliance_by_llm(
            recommendation_content,
            actual_action,
            user_feedback,
            exercise_stats=exercise_stats,
            steps=steps,
        )
        if compliance is None:
            log.warning("auto_feedback: %s LLM 判断失败，跳过写入", yesterday)
            return False

        # ── 5. 写入：优先 UPDATE 已有记录，无记录则 INSERT ──
        # 从 existing 记录获取实际的 recommendation_type
        rec_type = existing["recommendation_type"] if existing else "exercise"
        updated = db.update_recommendation_feedback(
            conn,
            date=yesterday,
            recommendation_type=rec_type,
            compliance=compliance,
            actual_action=actual_action,
        )
        if not updated:
            # 向后兼容：旧日期无 Phase 1 记录，直接 INSERT
            db.insert_recommendation_feedback(
                conn,
                date=yesterday,
                report_id=report_id,
                recommendation_type="exercise",
                recommendation_content=None,  # 旧日期无推荐内容
                compliance=compliance,
                actual_action=actual_action,
                tracked_metrics=None,
            )

        log.info(
            "auto_feedback: %s 写入完成 compliance=%d action=%s",
            yesterday,
            compliance,
            actual_action,
        )

        # ── 6. 计算 quality_score ──
        _write_quality_score(conn, yesterday, rec_type, compliance, existing)

    return True


def _write_quality_score(conn, yesterday, rec_type, compliance, existing):
    """计算并写入 quality_score，失败不影响主流程。"""
    try:
        # composite_score 来自 tracked_metrics JSON
        composite_score = None
        tracked_raw = existing["tracked_metrics"] if existing else None
        if tracked_raw:
            try:
                tracked = json.loads(tracked_raw)
                composite_score = tracked.get("composite_score_avg")
            except (json.JSONDecodeError, TypeError):
                pass

        # goal_progress_norm 来自 goal_progress 表
        goal_progress_norm = EffectTracker.compute_goal_progress_norm(conn, yesterday)

        # user_rating 来自 existing 记录
        user_rating = existing["user_rating"] if existing else None

        score = compute_quality_score(
            compliance=compliance,
            goal_progress_norm=goal_progress_norm,
            composite_score=composite_score,
            user_rating=user_rating,
        )
        db.update_recommendation_quality_score(
            conn, date=yesterday, recommendation_type=rec_type, quality_score=score
        )
        log.info(
            "auto_feedback: %s quality_score=%.3f (c=%d, g=%s, e=%s, r=%s)",
            yesterday,
            score,
            compliance,
            f"{goal_progress_norm:.2f}" if goal_progress_norm is not None else "N/A",
            f"{composite_score:.3f}" if composite_score is not None else "N/A",
            user_rating or "N/A",
        )
    except Exception as e:
        log.warning("auto_feedback: %s quality_score 计算失败: %s", yesterday, e)


def main():
    import argparse

    from superhealth.log_config import setup_logging

    setup_logging()
    ap = argparse.ArgumentParser(description="自动填充昨日反馈数据")
    ap.add_argument("--date", type=str, help="目标日期 YYYY-MM-DD，默认昨天")
    args = ap.parse_args()
    ok = run(target_date=args.date)
    log.info("已写入" if ok else "跳过（已存在或数据不足）")


if __name__ == "__main__":
    main()
