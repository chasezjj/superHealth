"""就医提醒通知器（Phase 6）

每日 morning 运行时检查 appointments 表，对距今 14 天和 7 天的预约发送微信通知。
同时提供日报板块生成函数，供 advanced_daily_report.py 调用。

使用方式：
    python -m superhealth.reminders.reminder_notifier
    python -m superhealth.reminders.reminder_notifier --dry-run
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date

from superhealth import config as cfg
from superhealth.database import (
    get_all_appointments,
    get_conn,
    get_pending_appointments,
    mark_appointment_reminded,
)

REMIND_THRESHOLDS = [14, 7]  # 提前提醒天数


def _build_wechat_message(appt: dict, days_left: int) -> str:
    hospital = appt.get("hospital") or "—"
    department = appt.get("department") or "—"
    label_map = {
        "glaucoma": "青光眼复查",
        "hyperuricemia": "高尿酸复诊",
        "annual_checkup": "年度体检",
    }
    label = label_map.get(appt["condition"], appt["condition"])
    urgency = "即将到来" if days_left <= 7 else "快到了"

    return (
        f"⚕️ 就医提醒\n\n"
        f"{label}窗口{urgency}：还有 {days_left} 天\n"
        f"医院：{hospital} {department}\n"
        f"建议就诊日期：{appt['due_date']} 前后\n\n"
        f"请尽快预约挂号。"
    )


def _send_wechat(message: str, conf) -> int:
    cmd = [
        "openclaw",
        "agent",
        "--channel",
        conf.wechat.channel,
        "--to",
        conf.wechat.target,
        "--message",
        message,
        "--deliver",
        "--reply-channel",
        conf.wechat.channel,
        "--reply-account",
        conf.wechat.account_id,
        "--reply-to",
        conf.wechat.target,
        "--timeout",
        "60",
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    return proc.returncode


def check_and_notify(dry_run: bool = False) -> int:
    """检查所有待提醒的预约，对符合阈值的发送微信通知。

    返回发送成功的提醒数量。
    """
    today = date.today()
    sent_count = 0

    conf = cfg.load()
    wechat_ok = conf.wechat.is_complete()
    if not wechat_ok and not dry_run:
        print("[notifier] 微信配置不完整，跳过发送", file=sys.stderr)
        return 0

    with get_conn() as conn:
        pending = get_pending_appointments(conn)
        for appt in pending:
            try:
                due = date.fromisoformat(appt["due_date"])
            except (ValueError, TypeError):
                continue
            days_left = (due - today).days

            if days_left not in REMIND_THRESHOLDS:
                continue

            # 避免重复：已提醒过同天数的跳过
            current_status = appt.get("status", "pending")
            if current_status == f"reminded_{days_left}":
                continue

            msg = _build_wechat_message(appt, days_left)
            label_map = {
                "glaucoma": "青光眼复查",
                "hyperuricemia": "高尿酸复诊",
                "annual_checkup": "年度体检",
            }
            label = label_map.get(appt["condition"], appt["condition"])

            if dry_run:
                print(f"[dry-run] 将发送提醒：{label}，距今 {days_left} 天")
                print(f"  消息内容：\n{msg}\n")
                sent_count += 1
            else:
                rc = _send_wechat(msg, conf)
                if rc == 0:
                    mark_appointment_reminded(conn, appointment_id=appt["id"], days_left=days_left)
                    print(f"[notifier] 已发送：{label}，距今 {days_left} 天")
                    sent_count += 1
                else:
                    print(f"[notifier] 发送失败：{label}，returncode={rc}", file=sys.stderr)

    return sent_count


def build_report_section() -> str:
    """生成日报中"近期就诊提醒"板块的 Markdown 文本。

    供 advanced_daily_report.py 调用。
    """
    today = date.today()
    label_map = {
        "glaucoma": "青光眼复查",
        "hyperuricemia": "高尿酸复诊",
        "annual_checkup": "年度体检",
    }

    with get_conn() as conn:
        appointments = get_all_appointments(conn)

    if not appointments:
        return ""

    lines = [
        "## 近期就诊提醒\n",
        "| 病情 | 医院 | 建议就诊日期 | 距今 |",
        "|------|------|------------|------|",
    ]
    for appt in appointments:
        if appt.get("status") == "completed":
            continue
        try:
            due = date.fromisoformat(appt["due_date"])
        except (ValueError, TypeError):
            continue
        days_left = (due - today).days
        label = label_map.get(appt["condition"], appt["condition"])
        hospital = appt.get("hospital") or "—"
        department = appt.get("department") or ""
        hospital_str = f"{hospital} {department}".strip()

        # 只显示已逾期或 ≤14 天的预约
        if days_left < 0:
            days_str = f"**已逾期 {abs(days_left)} 天**"
        elif days_left <= 14:
            days_str = f"**{days_left} 天**"
        else:
            continue  # 超过14天的不显示

        lines.append(f"| {label} | {hospital_str} | {appt['due_date']} | {days_str} |")

    # 如果只有表头没有数据行，返回空
    if len(lines) <= 3:
        return ""

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="检查并发送就医提醒微信通知")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不实际发送")
    args = parser.parse_args()
    count = check_and_notify(dry_run=args.dry_run)
    print(f"[notifier] 本次触发提醒数：{count}")
