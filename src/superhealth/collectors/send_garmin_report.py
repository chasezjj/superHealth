#!/usr/bin/env python3
"""发送综合健康日报到消息渠道。

整合 Garmin 数据、体征数据和高级分析，生成并发送个性化健康建议。
"""

import argparse
import logging
import re
from pathlib import Path

from superhealth import config as cfg
from superhealth.notifications import send_push_message

log = logging.getLogger(__name__)

_PKG_DIR = Path(__file__).parent.parent  # src/superhealth/
BASE_DIR = _PKG_DIR.parent.parent  # superhealth/ (project root)
DATA_DIR = BASE_DIR / "data" / "activity-data" / "garmin"
REPORTS_DIR = BASE_DIR / "data" / "daily-reports"


def extract(pattern: str, text: str, default: str = "") -> str:
    m = re.search(pattern, text, re.MULTILINE)
    return m.group(1).strip() if m else default


def send_report(day_str: str) -> int:
    """读取并发送指定日期的健康日报。"""
    conf = cfg.load()
    if not conf.wechat.is_complete():
        print(
            "错误：消息推送配置不完整。请创建 ~/.superhealth/config.toml 或设置环境变量\n"
            "  HEALTHY_WECHAT_ACCOUNT_ID / HEALTHY_WECHAT_TARGET\n"
            "  或企业微信字段：SUPERHEALTH_WECOM_BOT_ID / SUPERHEALTH_WECOM_SECRET / SUPERHEALTH_WECOM_TOUSER",
            file=sys.stderr,
        )
        return 1

    # 优先读取 Phase 4 高级日报（含 LLM 建议和多模型评估）
    advanced_path = REPORTS_DIR / f"{day_str}-advanced-daily-report.md"
    if advanced_path.exists():
        return send_advanced_report(advanced_path, day_str, conf.wechat)

    # 降级到基础综合日报
    report_path = REPORTS_DIR / f"{day_str}-daily-report.md"
    if report_path.exists():
        return send_comprehensive_report(report_path, day_str, conf.wechat)

    print(f"报告文件不存在: {advanced_path} 或 {report_path}", file=sys.stderr)
    return 1


def send_advanced_report(path: Path, day_str: str, push_config) -> int:
    """发送 Phase 4 高级健康日报（含 LLM 建议和多模型评估）。"""
    text = path.read_text(encoding="utf-8")

    return send_push_message(
        channel=push_config.push_channel,
        target=push_config.push_target,
        account_id=push_config.account_id,
        wecom_bot_id=push_config.bot_id,
        wecom_secret=push_config.secret,
        message=text,
    )


def send_comprehensive_report(path: Path, day_str: str, push_config) -> int:
    """发送新的综合健康日报。"""
    text = path.read_text(encoding="utf-8")

    # 提取关键信息
    recovery_score = extract(r"综合评分: (\d+)/100", text, "N/A")
    recovery_level = extract(r"\*\*综合评分: \d+/100\*\* \(([^)]+)\)", text, "未知")
    readiness = extract(r"准备状态: \*\*([^*]+)\*\*", text, "未知")

    # 提取 Garmin 关键指标（使用统一的列表格式）
    sleep = extract(r"- 睡眠: ([^\n]+)", text, "N/A")
    hrv = extract(r"- HRV: ([^\n]+)", text, "N/A")
    rhr = extract(r"- 静息心率: ([^\n]+)", text, "N/A")
    bb = extract(r"- Body Battery: ([^\n]+)", text, "N/A")
    stress = extract(r"- 平均压力: ([^\n]+)", text, "N/A")
    steps = extract(r"- 步数: ([^\n]+)", text, "N/A")

    # 提取体征数据
    bp = extract(r"- 血压: ([^\n]+)", text, "")
    weight = extract(r"- 体重: ([^\n]+)", text, "")

    # 提取运动建议
    intensity = extract(r"\*\*运动\*\*: ([^,]+)", text, "未知")
    exercise_type = extract(r"\*\*类型\*\*: ([^\n]+)", text, "")

    # 提取注意事项
    caution_list = re.findall(r"⚠️ \*\*注意事项\*\*:\n((?:- [^\n]+\n?)+)", text)
    cautions = []
    if caution_list:
        cautions = [c.strip()[2:] for c in caution_list[0].split("\n") if c.strip().startswith("-")]

    # 构建微信消息
    lines = [
        f"📊 健康日报 ({day_str})",
        "",
        f"🎯 恢复状态: {recovery_level} ({recovery_score}/100)",
        f"🏃 准备状态: {readiness}",
        "",
        "📈 关键指标:",
        f"  睡眠: {sleep.strip()}",
        f"  HRV: {hrv.strip()}",
        f"  静息心率: {rhr.strip()}",
        f"  Body Battery: {bb.strip()}",
        f"  平均压力: {stress.strip()}",
        f"  步数: {steps.strip()}",
    ]

    if bp:
        lines.append(f"  血压: {bp.strip()}")
    if weight:
        lines.append(f"  体重: {weight.strip()}")

    lines.extend(
        [
            "",
            f"💪 今日建议: {intensity.strip()}",
        ]
    )

    if exercise_type:
        lines.append(f"  类型: {exercise_type.strip()}")

    if cautions:
        lines.extend(["", "⚠️ 注意:"])
        for c in cautions[:3]:  # 最多3条
            lines.append(f"  • {c}")

    message = "\n".join(lines)

    return send_push_message(
        channel=push_config.push_channel,
        target=push_config.push_target,
        account_id=push_config.account_id,
        wecom_bot_id=push_config.bot_id,
        wecom_secret=push_config.secret,
        message=message,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    args = ap.parse_args()

    return send_report(args.date)


if __name__ == "__main__":
    raise SystemExit(main())
