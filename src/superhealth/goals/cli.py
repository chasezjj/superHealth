"""阶段性目标 CLI 入口。

用法：
    python -m superhealth.goals list [--status active]
    python -m superhealth.goals add --name "降低血压" --priority 1 --metric bp_systolic_mean_7d --direction decrease --target 120 [--target-date 2026-07-31] [--baseline 130]
    python -m superhealth.goals progress <goal_id> [--days 30]
    python -m superhealth.goals achieve <goal_id> [--notes "..."]
    python -m superhealth.goals pause <goal_id>
    python -m superhealth.goals abandon <goal_id>
    python -m superhealth.goals metrics  # 列出可用指标
    python -m superhealth.goals experiment list [--status active]
    python -m superhealth.goals experiment suggest --goal-id <id>
    python -m superhealth.goals experiment create --goal-id <id> --name "..." --hypothesis "..." --intervention "..." [--duration 14]
    python -m superhealth.goals experiment activate <id>
    python -m superhealth.goals experiment cancel <id>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from superhealth.goals.manager import GoalManager
from superhealth.goals.metrics import METRIC_REGISTRY, VALID_METRIC_KEYS

DB_PATH = Path(__file__).parent.parent.parent.parent / "health.db"


def cmd_list(args):
    mgr = GoalManager(DB_PATH)
    goals = mgr.list_goals(status=args.status)
    if not goals:
        print("暂无目标。")
        return

    _PRIORITY_LABEL = {1: "P1 主要", 2: "P2 次要", 3: "P3 辅助"}
    for g in goals:
        status_icon = {"active": "●", "achieved": "✓", "paused": "⏸", "abandoned": "✗", "off_track": "⚠"}.get(g["status"], "?")
        print(f"\n{status_icon} [{g['id']}] {g['name']} ({_PRIORITY_LABEL.get(g['priority'], f'P{g["priority"]}')})")
        spec = METRIC_REGISTRY.get(g["metric_key"])
        print(f"  指标: {spec.label if spec else g['metric_key']}")
        print(f"  方向: {g['direction']}  基线: {g['baseline_value']}  目标: {g['target_value']}")
        print(f"  状态: {g['status']}  开始: {g['start_date']}  期限: {g['target_date'] or '无'}")
        if g["achieved_date"]:
            print(f"  达成日: {g['achieved_date']}")
        if g.get("notes"):
            print(f"  备注: {g['notes']}")


def cmd_add(args):
    mgr = GoalManager(DB_PATH)
    try:
        goal_id = mgr.add_goal(
            name=args.name,
            priority=args.priority,
            metric_key=args.metric,
            direction=args.direction,
            target=args.target,
            target_date=args.target_date,
            description=args.description,
            baseline_value=args.baseline,
        )
        print(f"目标已创建 (id={goal_id})")
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_progress(args):
    mgr = GoalManager(DB_PATH)
    goal = mgr.get_goal(args.goal_id)
    if not goal:
        print(f"目标 id={args.goal_id} 不存在", file=sys.stderr)
        sys.exit(1)

    progress = mgr.get_goal_progress(args.goal_id, days=args.days)

    spec = METRIC_REGISTRY.get(goal["metric_key"])
    print(f"\n目标: {goal['name']}")
    print(f"指标: {spec.label if spec else goal['metric_key']}")
    print(f"基线 → 目标: {goal['baseline_value']} → {goal['target_value']} ({goal['direction']})")
    print(f"开始日: {goal['start_date']}  期限: {goal['target_date'] or '无'}")

    if progress:
        print(f"\n最近 {len(progress)} 天进度:")
        print(f"{'日期':12s} {'当前值':>8s} {'变化':>8s} {'进度%':>8s}")
        print("-" * 40)
        for p in progress:
            delta = f"{p['delta_from_baseline']:+.1f}" if p.get("delta_from_baseline") is not None else "N/A"
            pct = f"{p['progress_pct']:.1f}" if p.get("progress_pct") is not None else "N/A"
            val = f"{p['current_value']:.1f}" if p.get("current_value") is not None else "N/A"
            print(f"{p['date']:12s} {val:>8s} {delta:>8s} {pct:>8s}")
    else:
        print("\n暂无进度记录。")


def cmd_achieve(args):
    mgr = GoalManager(DB_PATH)
    mgr.update_status(args.goal_id, "achieved", notes=args.notes)
    print(f"目标 id={args.goal_id} 已标记为达成。")


def cmd_pause(args):
    mgr = GoalManager(DB_PATH)
    mgr.update_status(args.goal_id, "paused", notes=args.notes)
    print(f"目标 id={args.goal_id} 已暂停。")


def cmd_abandon(args):
    mgr = GoalManager(DB_PATH)
    mgr.update_status(args.goal_id, "abandoned", notes=args.notes)
    print(f"目标 id={args.goal_id} 已废弃。")


def cmd_metrics(_args):
    print("可用指标白名单：\n")
    print(f"{'key':30s} {'说明':20s} {'频率':8s} {'聚合方式'}")
    print("-" * 75)
    for key, spec in sorted(METRIC_REGISTRY.items()):
        print(f"{key:30s} {spec.label:20s} {spec.frequency:8s} {spec.aggregation}")


# ── 实验子命令 ──────────────────────────────────────────────────────────

def cmd_experiment_list(args):
    from superhealth.feedback.experiment_manager import ExperimentManager
    mgr = ExperimentManager(DB_PATH)
    exps = mgr.list_experiments(status=args.status)
    if not exps:
        print("暂无实验。")
        return

    _STATUS_ICON = {"draft": "○", "active": "●", "completed": "✓", "reverted": "✗", "evaluating": "⏳"}
    for e in exps:
        icon = _STATUS_ICON.get(e["status"], "?")
        print(f"\n{icon} [{e['id']}] {e['name']}")
        print(f"  假设: {e['hypothesis']}")
        print(f"  干预: {e['intervention']}")
        print(f"  指标: {e['metric_key']}  方向: {e['direction']}  时长: {e['min_duration']}天")
        print(f"  状态: {e['status']}  开始: {e['start_date'] or '-'}  结束: {e['end_date'] or '-'}")
        if e.get("conclusion"):
            print(f"  结论: {e['conclusion'][:200]}")


def cmd_experiment_suggest(args):
    from superhealth.feedback.experiment_manager import ExperimentManager
    mgr = ExperimentManager(DB_PATH)
    candidates = mgr.suggest_for_goal(args.goal_id)
    if not candidates:
        print(f"Goal #{args.goal_id} 无预设干预候选（可能不是有效 goal id 或该指标尚无候选映射）。")
        return

    print(f"Goal #{args.goal_id} 的推荐干预候选：\n")
    for c in candidates:
        print(f"  [{c['index']}] {c['name']}")
        print(f"      干预: {c['intervention']}")
        print(f"      建议时长: {c['duration']}天")
        print()


def cmd_experiment_create(args):
    from superhealth.feedback.experiment_manager import ExperimentManager
    mgr = ExperimentManager(DB_PATH)

    if args.from_candidate is not None:
        candidates = mgr.suggest_for_goal(args.goal_id)
        match = [c for c in candidates if c["index"] == args.from_candidate]
        if not match:
            print(f"候选索引 {args.from_candidate} 不存在，请用 suggest 命令查看可用候选。", file=sys.stderr)
            sys.exit(1)
        c = match[0]
        name = args.name or c["name"]
        hypothesis = args.hypothesis or f"{c['intervention']} 对 {c['metric_key']}（方向: {c['direction']}）的效果"
        intervention = c["intervention"]
        duration = args.duration or c["duration"]
    else:
        if not args.name or not args.hypothesis or not args.intervention:
            print("手动创建需指定 --name, --hypothesis, --intervention", file=sys.stderr)
            sys.exit(1)
        name = args.name
        hypothesis = args.hypothesis
        intervention = args.intervention
        duration = args.duration or 14

    try:
        exp_id = mgr.create_draft(
            name=name,
            hypothesis=hypothesis,
            goal_id=args.goal_id,
            metric_key=args.metric,
            direction=args.direction or "decrease",
            intervention=intervention,
            min_duration=duration,
        )
        print(f"实验已创建 (id={exp_id})，使用 activate {exp_id} 激活")
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_experiment_activate(args):
    from superhealth.feedback.experiment_manager import ExperimentManager
    mgr = ExperimentManager(DB_PATH)
    try:
        mgr.activate(args.experiment_id)
        print(f"实验 #{args.experiment_id} 已激活。")
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_experiment_cancel(args):
    from superhealth.feedback.experiment_manager import ExperimentManager
    mgr = ExperimentManager(DB_PATH)
    try:
        mgr.cancel(args.experiment_id)
        print(f"实验 #{args.experiment_id} 已取消。")
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(
        prog="superhealth.goals",
        description="阶段性目标管理 CLI",
    )
    sub = ap.add_subparsers(dest="command")

    # list
    p_list = sub.add_parser("list", help="列出当前目标")
    p_list.add_argument("--status", help="按状态过滤 (active/achieved/paused/abandoned)")

    # add
    p_add = sub.add_parser("add", help="添加新目标")
    p_add.add_argument("--name", required=True, help="目标名称")
    p_add.add_argument("--priority", type=int, required=True, choices=[1, 2, 3], help="优先级 1-3")
    p_add.add_argument("--metric", required=True, help="指标 key（用 metrics 命令查看可选值）")
    p_add.add_argument("--direction", required=True, choices=["decrease", "increase", "stabilize"])
    p_add.add_argument("--target", type=float, help="目标值")
    p_add.add_argument("--target-date", help="期望达成日期 YYYY-MM-DD")
    p_add.add_argument("--description", help="目标说明")
    p_add.add_argument("--baseline", type=float, help="手动指定基线（默认自动从近7天数据计算）")

    # progress
    p_prog = sub.add_parser("progress", help="查看目标进度")
    p_prog.add_argument("goal_id", type=int)
    p_prog.add_argument("--days", type=int, default=30, help="显示最近 N 天")

    # achieve / pause / abandon
    p_ach = sub.add_parser("achieve", help="标记目标达成")
    p_ach.add_argument("goal_id", type=int)
    p_ach.add_argument("--notes")

    p_pause = sub.add_parser("pause", help="暂停目标")
    p_pause.add_argument("goal_id", type=int)
    p_pause.add_argument("--notes")

    p_aban = sub.add_parser("abandon", help="废弃目标")
    p_aban.add_argument("goal_id", type=int)
    p_aban.add_argument("--notes")

    # metrics
    sub.add_parser("metrics", help="列出可用指标白名单")

    # ── experiment 子命令 ──
    p_exp = sub.add_parser("experiment", help="干预实验管理")
    exp_sub = p_exp.add_subparsers(dest="exp_command")

    p_exp_list = exp_sub.add_parser("list", help="列出实验")
    p_exp_list.add_argument("--status", help="按状态过滤 (draft/active/completed/reverted)")

    p_exp_suggest = exp_sub.add_parser("suggest", help="查看目标推荐干预")
    p_exp_suggest.add_argument("--goal-id", type=int, required=True, help="目标 ID")

    p_exp_create = exp_sub.add_parser("create", help="创建实验草稿")
    p_exp_create.add_argument("--goal-id", type=int, required=True, help="关联目标 ID")
    p_exp_create.add_argument("--from-candidate", type=int, help="从候选索引创建（用 suggest 查看）")
    p_exp_create.add_argument("--name", help="实验名称（手动创建必填）")
    p_exp_create.add_argument("--hypothesis", help="假设（手动创建必填）")
    p_exp_create.add_argument("--intervention", help="干预描述（手动创建必填）")
    p_exp_create.add_argument("--metric", default="bp_systolic_mean_7d", help="指标 key")
    p_exp_create.add_argument("--direction", choices=["decrease", "increase", "stabilize"])
    p_exp_create.add_argument("--duration", type=int, help="实验天数（默认取候选建议值或14天）")

    p_exp_activate = exp_sub.add_parser("activate", help="激活实验")
    p_exp_activate.add_argument("experiment_id", type=int)

    p_exp_cancel = exp_sub.add_parser("cancel", help="取消实验")
    p_exp_cancel.add_argument("experiment_id", type=int)

    args = ap.parse_args()
    if not args.command:
        ap.print_help()
        return

    commands = {
        "list": cmd_list,
        "add": cmd_add,
        "progress": cmd_progress,
        "achieve": cmd_achieve,
        "pause": cmd_pause,
        "abandon": cmd_abandon,
        "metrics": cmd_metrics,
        "experiment": {
            "list": cmd_experiment_list,
            "suggest": cmd_experiment_suggest,
            "create": cmd_experiment_create,
            "activate": cmd_experiment_activate,
            "cancel": cmd_experiment_cancel,
        },
    }

    if args.command == "experiment":
        exp_cmds = commands["experiment"]
        if args.exp_command in exp_cmds:
            exp_cmds[args.exp_command](args)
        else:
            p_exp.print_help()
    else:
        commands[args.command](args)


if __name__ == "__main__":
    main()
