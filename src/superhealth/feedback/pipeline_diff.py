"""管线差异追踪工具：快照效果追踪与策略学习的状态，对比运行前后差异。

用法：
  python -m superhealth.feedback.pipeline_diff snapshot before   # 快照当前状态
  python -m superhealth.feedback.pipeline_diff snapshot after    # 快照新状态
  python -m superhealth.feedback.pipeline_diff diff              # 对比最近两次快照
  python -m superhealth.feedback.pipeline_diff run --days 180    # 自动：快照→跑全量→快照→对比
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from superhealth import database as db
from superhealth.feedback.effect_tracker import EffectTracker

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent.parent / "health.db"
SNAPSHOT_DIR = Path(__file__).parent.parent.parent.parent / "data" / "pipeline_snapshots"

# 从 tracked_metrics JSON 中提取的关键字段
_TRACKED_FIELDS = [
    "assessment",
    "composite_score_avg",
    "composite_score_day1",
    "composite_score_day2",
    "baseline_type",
    "positive_signals",
    "negative_signals",
    "skipped_negative",
    "control_dates",
    "contaminated_days",
    "net_effect_available",
    "control_avg_changes",
    "personal_stds",
    "net_effects",
]


def _ts_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _snapshot_path(tag: str) -> Path:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return SNAPSHOT_DIR / f"{_ts_tag()}_{tag}.json"


def _latest_snapshot(tag: str) -> Optional[Path]:
    if not SNAPSHOT_DIR.exists():
        return None
    candidates = sorted(SNAPSHOT_DIR.glob(f"*_{tag}.json"))
    return candidates[-1] if candidates else None


def _extract_tracked_metrics(raw_json: str) -> Optional[dict]:
    try:
        full = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return None
    return {k: full.get(k) for k in _TRACKED_FIELDS if k in full}


def take_snapshot(tag: str, db_path: Path = DB_PATH) -> Path:
    """从 DB 读取当前状态，写入快照 JSON。"""
    tracker = EffectTracker(db_path)
    snapshot = {
        "tag": tag,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "learned_preferences": [],
        "tracked_metrics": {},
        "intermediate": {},
    }

    with db.get_conn(db_path) as conn:
        # 1. learned_preferences 全量
        prefs = db.query_learned_preferences(conn)
        snapshot["learned_preferences"] = [{k: v for k, v in dict(p).items()} for p in prefs]

        # 2. tracked_metrics
        rows = conn.execute(
            """SELECT date, tracked_metrics
               FROM recommendation_feedback
               WHERE tracked_metrics IS NOT NULL
                 AND recommendation_type = 'exercise'
               ORDER BY date"""
        ).fetchall()
        for row in rows:
            extracted = _extract_tracked_metrics(row["tracked_metrics"])
            if extracted:
                snapshot["tracked_metrics"][row["date"]] = extracted

        # 3. 中间计算参数
        personal_stds = tracker._compute_personal_stds(conn, lookback_days=180)
        global_stds = tracker._compute_global_stds(conn)
        schedule_stds = tracker._compute_schedule_stds(conn)

        since = (date.today() - timedelta(days=180)).isoformat()
        no_ex_count = conn.execute(
            """SELECT COUNT(DISTINCT d.date)
               FROM daily_health d
               LEFT JOIN exercises e ON d.date = e.date
               WHERE e.date IS NULL AND d.date >= ?""",
            (since,),
        ).fetchone()[0]

        snapshot["intermediate"] = {
            "personal_stds": {k: round(v, 3) for k, v in personal_stds.items()},
            "global_stds": {k: round(v, 3) for k, v in global_stds.items()},
            "schedule_stds": {k: round(v, 3) for k, v in schedule_stds.items()},
            "lookback_window": {
                "start": since,
                "end": date.today().isoformat(),
                "days": 180,
            },
            "total_no_exercise_days": no_ex_count,
        }

    path = _snapshot_path(tag)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str))
    log.info(
        "快照已写入: %s (偏好%d条, 追踪%d天)",
        path,
        len(snapshot["learned_preferences"]),
        len(snapshot["tracked_metrics"]),
    )
    return path


def _load_snapshot(path: Path) -> dict:
    return json.loads(path.read_text())


def compare_snapshots(before_path: Path, after_path: Path) -> dict:
    """逐层对比两个快照，返回结构化 diff。"""
    before = _load_snapshot(before_path)
    after = _load_snapshot(after_path)

    result = {
        "before_ts": before.get("timestamp"),
        "after_ts": after.get("timestamp"),
        "intermediate_diff": _diff_intermediate(
            before.get("intermediate", {}),
            after.get("intermediate", {}),
        ),
        "preferences_diff": _diff_preferences(
            before.get("learned_preferences", []),
            after.get("learned_preferences", []),
        ),
        "tracked_diff": _diff_tracked(
            before.get("tracked_metrics", {}),
            after.get("tracked_metrics", {}),
        ),
    }
    return result


def _diff_intermediate(before: dict, after: dict) -> dict:
    diff = {"personal_stds": {}, "global_stds": {}, "schedule_stds": {}, "window": {}, "other": {}}

    for key in ("personal_stds", "global_stds", "schedule_stds"):
        b_vals = before.get(key, {})
        a_vals = after.get(key, {})
        all_keys = sorted(set(b_vals) | set(a_vals))
        for k in all_keys:
            bv = b_vals.get(k)
            av = a_vals.get(k)
            if bv != av:
                delta = None
                if isinstance(av, (int, float)) and isinstance(bv, (int, float)):
                    delta = round(av - bv, 3)
                diff[key][k] = {"before": bv, "after": av, "delta": delta}

    bw = before.get("lookback_window", {})
    aw = after.get("lookback_window", {})
    if bw.get("start") != aw.get("start") or bw.get("end") != aw.get("end"):
        diff["window"] = {"before": bw, "after": aw}

    b_nox = before.get("total_no_exercise_days")
    a_nox = after.get("total_no_exercise_days")
    if b_nox != a_nox:
        diff["other"]["total_no_exercise_days"] = {"before": b_nox, "after": a_nox}

    return diff


def _pref_key(p: dict) -> str:
    return f"{p.get('preference_type', '')}/{p.get('preference_key', '')}"


def _diff_preferences(before: list, after: list) -> dict:
    b_map = {_pref_key(p): p for p in before}
    a_map = {_pref_key(p): p for p in after}

    diff = {"added": [], "removed": [], "updated": [], "unchanged_count": 0}

    all_keys = sorted(set(b_map) | set(a_map))
    for k in all_keys:
        bp = b_map.get(k)
        ap = a_map.get(k)
        if bp and not ap:
            diff["removed"].append({"key": k, "before": bp})
        elif ap and not bp:
            diff["added"].append({"key": k, "after": ap})
        else:
            changes = {}
            for field in ("preference_value", "confidence_score", "evidence_count", "status"):
                if bp.get(field) != ap.get(field):
                    changes[field] = {"before": bp.get(field), "after": ap.get(field)}
            if changes:
                entry = {"key": k, "changes": changes}
                if bp.get("status") == "active" and ap.get("status") == "reverted":
                    entry["type"] = "reverted"
                elif bp.get("status") == "active" and ap.get("status") == "committed":
                    entry["type"] = "committed"
                elif changes.get("confidence_score") and ap.get("confidence_score", 1) < bp.get(
                    "confidence_score", 0
                ):
                    entry["type"] = "stale_decay"
                else:
                    entry["type"] = "updated"
                diff["updated"].append(entry)
            else:
                diff["unchanged_count"] += 1

    return diff


def _diff_tracked(before: dict, after: dict) -> dict:
    diff = {"added": [], "removed": [], "changed": [], "unchanged_count": 0}

    all_dates = sorted(set(before) | set(after))
    for d in all_dates:
        bt = before.get(d)
        at = after.get(d)
        if bt and not at:
            diff["removed"].append({"date": d, "assessment": bt.get("assessment")})
        elif at and not bt:
            diff["added"].append({"date": d, "assessment": at.get("assessment")})
        else:
            changes = {}
            for field in (
                "assessment",
                "composite_score_avg",
                "composite_score_day1",
                "composite_score_day2",
                "baseline_type",
                "positive_signals",
                "negative_signals",
                "net_effect_available",
            ):
                if bt.get(field) != at.get(field):
                    changes[field] = {"before": bt.get(field), "after": at.get(field)}

            # personal_stds / control_avg_changes 细粒度对比
            for metric_field in ("personal_stds", "control_avg_changes", "net_effects"):
                bm = bt.get(metric_field, {})
                am = at.get(metric_field, {})
                if bm != am:
                    metric_changes = {}
                    for mk in sorted(set(bm) | set(am)):
                        bv = bm.get(mk)
                        av = am.get(mk)
                        if bv != av:
                            delta = None
                            if isinstance(av, (int, float)) and isinstance(bv, (int, float)):
                                delta = round(av - bv, 3)
                            metric_changes[mk] = {"before": bv, "after": av, "delta": delta}
                    if metric_changes:
                        changes[metric_field] = metric_changes

            # control_dates 对比
            b_cd = bt.get("control_dates", [])
            a_cd = at.get("control_dates", [])
            if b_cd != a_cd:
                b_dates = {c.get("date") for c in b_cd}
                a_dates = {c.get("date") for c in a_cd}
                changes["control_dates"] = {
                    "removed": sorted(b_dates - a_dates),
                    "added": sorted(a_dates - b_dates),
                    "count_before": len(b_cd),
                    "count_after": len(a_cd),
                }

            # contaminated_days 对比
            b_cont = bt.get("contaminated_days", {})
            a_cont = at.get("contaminated_days", {})
            if b_cont != a_cont:
                changes["contaminated_days"] = {
                    "before": b_cont,
                    "after": a_cont,
                }

            if changes:
                diff["changed"].append({"date": d, "changes": changes})
            else:
                diff["unchanged_count"] += 1

    return diff


def format_report(diff_result: dict) -> str:
    """将结构化 diff 格式化为可读报告。"""
    lines = []
    lines.append("=== Pipeline Diff Report ===")
    lines.append(f"Before: {diff_result['before_ts']}  →  After: {diff_result['after_ts']}")
    lines.append("")

    # Intermediate
    interp = diff_result.get("intermediate_diff", {})
    has_interp = any(
        interp.get(k) for k in ("personal_stds", "global_stds", "schedule_stds", "window", "other")
    )
    if has_interp:
        lines.append("--- Intermediate Parameters ---")
        for key in ("personal_stds", "global_stds", "schedule_stds"):
            vals = interp.get(key, {})
            if vals:
                label = key.replace("_", " ")
                lines.append(f"{label}:")
                for mk, mv in sorted(vals.items()):
                    delta_str = f" ({mv['delta']:+.3f})" if mv.get("delta") is not None else ""
                    lines.append(f"  {mk}: {mv['before']} → {mv['after']}{delta_str}")
        if interp.get("window"):
            w = interp["window"]
            lines.append(
                f"lookback window: {w['before']['start']}~{w['before']['end']} → {w['after']['start']}~{w['after']['end']}"
            )
        if interp.get("other"):
            for k, v in interp["other"].items():
                lines.append(f"{k}: {v['before']} → {v['after']}")
        lines.append("")

    # Preferences
    pdiff = diff_result.get("preferences_diff", {})
    has_pref = pdiff.get("added") or pdiff.get("removed") or pdiff.get("updated")
    if has_pref:
        lines.append("--- Learned Preferences Changes ---")
        for item in pdiff.get("added", []):
            a = item["after"]
            lines.append(
                f'[NEW] {item["key"]}: "{a.get("preference_value")}" '
                f"(confidence={a.get('confidence_score')}, evidence={a.get('evidence_count')})"
            )
        for item in pdiff.get("removed", []):
            b = item["before"]
            lines.append(
                f'[REMOVED] {item["key"]}: "{b.get("preference_value")}" '
                f"(was confidence={b.get('confidence_score')})"
            )
        for item in pdiff.get("updated", []):
            change_type = item.get("type", "updated").upper()
            changes = item["changes"]
            parts = []
            for field, fv in changes.items():
                if field == "preference_value":
                    parts.append(f'"{fv["before"]}" → "{fv["after"]}"')
                elif field == "confidence_score":
                    parts.append(f"confidence: {fv['before']} → {fv['after']}")
                elif field == "evidence_count":
                    parts.append(f"evidence: {fv['before']} → {fv['after']}")
                elif field == "status":
                    parts.append(f"status: {fv['before']} → {fv['after']}")
            lines.append(f"[{change_type}] {item['key']}: {' | '.join(parts)}")
        if pdiff.get("unchanged_count"):
            lines.append(f"[UNCHANGED] {pdiff['unchanged_count']} 条偏好无变化")
        lines.append("")

    # Tracked metrics
    tdiff = diff_result.get("tracked_diff", {})
    total_tracked = (
        len(tdiff.get("added", []))
        + len(tdiff.get("removed", []))
        + len(tdiff.get("changed", []))
        + tdiff.get("unchanged_count", 0)
    )
    has_tracked = tdiff.get("added") or tdiff.get("removed") or tdiff.get("changed")
    if has_tracked:
        lines.append(f"--- Tracked Metrics Changes ({total_tracked} dates) ---")
        for item in tdiff.get("added", []):
            lines.append(f"[NEW] {item['date']}: assessment={item['assessment']}")
        for item in tdiff.get("removed", []):
            lines.append(f"[REMOVED] {item['date']}: was assessment={item['assessment']}")
        for item in tdiff.get("changed", []):
            d = item["date"]
            changes = item["changes"]
            assessment = changes.get("assessment", {})
            a_str = ""
            if assessment:
                a_str = f" {assessment['before']} → {assessment['after']}"
            elif "assessment" not in changes:
                before_snapshot = diff_result.get("before_snapshot_tracked", {}).get(d, {})
                a_str = f" ({before_snapshot.get('assessment', '?')})"

            lines.append(f"[CHANGED] {d}:assessment{a_str}")
            for field, fv in changes.items():
                if field == "assessment":
                    continue
                if (
                    isinstance(fv, dict)
                    and "before" in fv
                    and "after" in fv
                    and not isinstance(fv.get("before"), dict)
                ):
                    lines.append(f"  {field}: {fv['before']} → {fv['after']}")
                elif isinstance(fv, dict) and "removed" in fv:
                    lines.append(
                        f"  control_dates: {fv['count_before']}个 → {fv['count_after']}个 "
                        f"(removed: {fv['removed']}, added: {fv['added']})"
                    )
                elif isinstance(fv, dict) and any(isinstance(vv, dict) for vv in fv.values()):
                    for mk, mv in fv.items():
                        if isinstance(mv, dict) and "before" in mv:
                            delta_str = (
                                f" ({mv['delta']:+.3f})" if mv.get("delta") is not None else ""
                            )
                            lines.append(
                                f"  {field}.{mk}: {mv['before']} → {mv['after']}{delta_str}"
                            )
        if tdiff.get("unchanged_count"):
            lines.append(f"[UNCHANGED] {tdiff['unchanged_count']} dates 无变化")
        lines.append("")

    # Root cause hints
    lines.append("--- Root Cause Hints ---")
    hints = _generate_hints(diff_result)
    if hints:
        for h in hints:
            lines.append(f"• {h}")
    else:
        lines.append("无显著差异")

    return "\n".join(lines)


def _generate_hints(diff_result: dict) -> list[str]:
    hints = []
    interp = diff_result.get("intermediate_diff", {})

    # personal_stds 变化 → 窗口滑动
    ps_diff = interp.get("personal_stds", {})
    if ps_diff:
        parts = [
            f"{k} {v['delta']:+.3f}"
            for k, v in sorted(ps_diff.items())
            if v.get("delta") is not None
        ]
        if parts:
            hints.append(f"personal_stds 变化: {', '.join(parts)} → 180天窗口滑动导致基线偏移")

    # 窗口变化
    if interp.get("window"):
        hints.append("lookback_window 起始日期变化 → 窗口滑动")

    # 偏好衰减
    pdiff = diff_result.get("preferences_diff", {})
    decayed = [u for u in pdiff.get("updated", []) if u.get("type") == "stale_decay"]
    if decayed:
        hints.append(f"{len(decayed)} 条偏好被 stale cleanup 衰减 → 超过180天的偏好置信度降低")

    # 偏好 revert
    reverted = [u for u in pdiff.get("updated", []) if u.get("type") == "reverted"]
    if reverted:
        keys = ", ".join(u["key"] for u in reverted)
        hints.append(f"{len(reverted)} 条偏好被 revert ({keys}) → 近30天 avg_quality 可能低于阈值")

    # 对照组变化
    tdiff = diff_result.get("tracked_diff", {})
    ctrl_changed = [c for c in tdiff.get("changed", []) if "control_dates" in c.get("changes", {})]
    if ctrl_changed:
        dates = ", ".join(c["date"] for c in ctrl_changed[:5])
        suffix = " 等" if len(ctrl_changed) > 5 else ""
        hints.append(
            f"{len(ctrl_changed)} 天的对照组变化 ({dates}{suffix}) → control_avg_changes 偏移可能导致 assessment 翻转"
        )

    # 新增追踪
    new_tracked = tdiff.get("added", [])
    if new_tracked:
        hints.append(f"{len(new_tracked)} 天新增追踪 → 新数据进入学习样本集")

    return hints


def run_full(days: int = 180, db_path: Path = DB_PATH) -> str:
    """完整流程：before快照 → 跑全量分析 → after快照 → diff。"""
    from superhealth.feedback.strategy_learner import StrategyLearner

    log.info("Step 1/4: 拍摄 before 快照...")
    before_path = take_snapshot("before", db_path)

    log.info("Step 2/4: 运行策略学习全量分析 (%d 天)...", days)
    learner = StrategyLearner(db_path)
    learner.run_full_analysis(days=days)

    log.info("Step 3/4: 拍摄 after 快照...")
    after_path = take_snapshot("after", db_path)

    log.info("Step 4/4: 对比差异...")
    diff_result = compare_snapshots(before_path, after_path)
    report = format_report(diff_result)
    return report


def main():
    parser = argparse.ArgumentParser(description="管线差异追踪工具")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="列出所有快照")

    sp_snap = sub.add_parser("snapshot", help="拍摄快照")
    sp_snap.add_argument("tag", choices=["before", "after"], help="快照标签")

    sub.add_parser("diff", help="对比最近一次 before/after 快照")

    sp_run = sub.add_parser("run", help="自动：快照→跑全量→快照→对比")
    sp_run.add_argument("--days", type=int, default=180, help="分析最近 N 天（默认180）")

    args = parser.parse_args()

    from superhealth.log_config import setup_logging

    setup_logging()

    if args.command == "list":
        if not SNAPSHOT_DIR.exists():
            log.info("无快照")
            return
        for f in sorted(SNAPSHOT_DIR.glob("*.json")):
            data = json.loads(f.read_text())
            n_prefs = len(data.get("learned_preferences", []))
            n_tracked = len(data.get("tracked_metrics", {}))
            log.info(
                "  %s  tag=%s  ts=%s  偏好=%d  追踪=%d天",
                f.name, data.get("tag"), data.get("timestamp"), n_prefs, n_tracked,
            )

    elif args.command == "snapshot":
        path = take_snapshot(args.tag)
        log.info("快照已保存: %s", path)

    elif args.command == "diff":
        before_path = _latest_snapshot("before")
        after_path = _latest_snapshot("after")
        if not before_path:
            log.error(
                "未找到 before 快照，请先运行: python -m superhealth.feedback.pipeline_diff snapshot before"
            )
            return
        if not after_path:
            log.error(
                "未找到 after 快照，请先运行: python -m superhealth.feedback.pipeline_diff snapshot after"
            )
            return
        log.info("对比: %s vs %s", before_path.name, after_path.name)
        diff_result = compare_snapshots(before_path, after_path)
        log.info(format_report(diff_result))

    elif args.command == "run":
        report = run_full(days=args.days)
        log.info(report)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
