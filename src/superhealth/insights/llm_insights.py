"""周期性健康洞察生成器（周报/月报）。

调用 analysis/ 的趋势和相关性分析，通过 LLM 生成深度洞察。

依赖：
- analysis/trends.py    → 趋势统计数据
- analysis/correlation.py → 相关性发现
- core/llm_advisor.py  → LLM 调用
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from superhealth.analysis.causal import CausalInferenceAnalyzer
from superhealth.analysis.correlation import CorrelationAnalyzer
from superhealth.analysis.trends import TrendAnalyzer
from superhealth.config import load as load_config

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent.parent / "health.db"
DATA_DIR = Path(__file__).parent.parent.parent.parent / "activity-data" / "reports"


class LLMInsightsGenerator:
    """使用 LLM 生成周期性健康洞察报告。"""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.trend_analyzer = TrendAnalyzer(db_path)
        self.correlation_analyzer = CorrelationAnalyzer(db_path)
        self._client = None

    def _get_claude_client(self):
        if self._client is None:
            cfg = load_config()
            if not cfg.claude.is_complete():
                return None
            try:
                import anthropic

                kwargs = {"api_key": cfg.claude.api_key}
                if cfg.claude.base_url:
                    kwargs["base_url"] = cfg.claude.base_url
                self._client = anthropic.Anthropic(**kwargs)
            except ImportError:
                log.warning("anthropic SDK 未安装")
                return None
        return self._client

    def _collect_trend_summary(self, end_date: str, days: int) -> str:
        """收集趋势摘要文本，用于 LLM prompt。"""
        lines = []
        metrics = ["sleep_score", "hrv_avg", "avg_stress", "body_battery_wake", "resting_hr"]
        metric_labels = {
            "sleep_score": "睡眠评分",
            "hrv_avg": "HRV",
            "avg_stress": "平均压力",
            "body_battery_wake": "Body Battery",
            "resting_hr": "静息心率",
        }
        for metric in metrics:
            try:
                result = self.trend_analyzer.analyze_trend(metric, days, end_date=end_date)
                if result.current_value is not None:
                    label = metric_labels.get(metric, metric)
                    direction = {"up": "上升", "down": "下降", "stable": "稳定"}.get(
                        result.trend_direction, result.trend_direction
                    )
                    anomaly = " ⚠️ 异常" if result.is_anomaly else ""
                    lines.append(
                        f"- {label}: 当前 {result.current_value:.1f}"
                        f"，{days}天趋势 {direction}"
                        + (f"，30天均值 {result.avg_30d:.1f}" if result.avg_30d else "")
                        + anomaly
                    )
            except Exception as e:
                log.debug("趋势分析 %s 失败: %s", metric, e)

        return "\n".join(lines) if lines else "暂无趋势数据"

    def _collect_correlation_summary(self, days: int = 30) -> str:
        """收集相关性发现摘要。"""
        try:
            pairs = [
                ("sleep_score", "hrv_avg", "睡眠评分 ↔ HRV"),
                ("avg_stress", "hrv_avg", "压力 ↔ HRV"),
                ("steps", "sleep_score", "步数 ↔ 睡眠评分"),
            ]
            lines = []
            for m1, m2, label in pairs:
                result = self.correlation_analyzer.correlate_same_day(m1, m2, days=days)
                if abs(result.r) > 0.3:
                    direction = "正相关" if result.r > 0 else "负相关"
                    lines.append(f"- {label}: {direction}（r={result.r:.2f}，n={result.n}）")
            return "\n".join(lines) if lines else "相关性数据不足"
        except Exception as e:
            log.debug("相关性分析失败: %s", e)
            return "相关性分析暂不可用"

    def _collect_causal_discovery_summary(self, end_date: str, days: int) -> str:
        """收集因果发现摘要。

        因果推断是结构性关系，需要较长窗口（默认 30 天），
        不因周报仅看 7 天就缩减。
        """
        try:
            analyzer = CausalInferenceAnalyzer(self.db_path)
            # Granger 因果需要至少 2*lag+10 天，用 30 天作为默认
            causal_days = max(days, 30)
            results = analyzer.analyze_key_causal_pairs(days=causal_days)
            significant = [r for r in results if r.p_value < 0.1]

            if not significant:
                return "暂无显著因果发现（数据量或效应强度不足）"

            lines = []
            for r in significant:
                lines.append(f"- {r.interpretation}")
            return "\n".join(lines)
        except Exception as e:
            log.debug("因果发现分析失败: %s", e)
            return "因果发现分析暂不可用"

    def generate_weekly_report(self, end_date: str | None = None, save: bool = True) -> str:
        """生成周报。"""
        if end_date is None:
            end_date = date.today().isoformat()

        start_date = (datetime.fromisoformat(end_date) - timedelta(days=7)).isoformat()[:10]

        trend_summary = self._collect_trend_summary(end_date, days=7)
        correlation_summary = self._collect_correlation_summary(days=30)
        causal_summary = self._collect_causal_discovery_summary(end_date, days=7)

        # 标注各模块数据窗口，避免读者误以为是同一口径
        if correlation_summary and correlation_summary != "相关性数据不足":
            correlation_summary = "（基于最近 30 天数据）\n\n" + correlation_summary
        if (
            causal_summary
            and not causal_summary.startswith("暂无")
            and not causal_summary.startswith("因果")
        ):
            causal_summary = "（基于最近 30 天数据）\n\n" + causal_summary

        client = self._get_claude_client()

        if client:
            cfg = load_config()
            prompt = (
                f"请基于以下过去7天（{start_date} ~ {end_date}）的健康数据摘要，"
                "生成一份简洁的周报洞察，包含：\n"
                "1. 本周最值得关注的1-2个健康发现\n"
                "2. 需要调整的生活/运动习惯\n"
                "3. 下周重点关注事项\n\n"
                f"趋势数据：\n{trend_summary}\n\n"
                f"相关性发现：\n{correlation_summary}\n\n"
                f"因果发现：\n{causal_summary}\n\n"
                "格式要求：使用 Markdown 列表（每段以 '- ' 开头），"
                "标题与正文之间、段落之间必须有空行，确保排版清晰。"
                "请用中文回复，言简意赅，总字数控制在300字以内。"
            )
            try:
                message = client.messages.create(
                    model=cfg.claude.model,
                    max_tokens=getattr(cfg.claude, "max_tokens", 2048) or 2048,
                    messages=[{"role": "user", "content": prompt}],
                )
                llm_text = next(b.text for b in message.content if b.type == "text").strip()
            except Exception as e:
                log.error("LLM 周报生成失败: %s", e)
                llm_text = "（LLM 不可用，请检查 API 配置）"
        else:
            llm_text = "（未配置 Claude API Key，跳过 LLM 洞察）"

        lines = [
            f"# {start_date} ~ {end_date} 周报",
            "",
            "## 数据趋势",
            "",
            trend_summary,
            "",
            "## 相关性发现",
            "",
            correlation_summary,
            "",
            "## 因果发现",
            "",
            causal_summary,
            "",
            "## LLM 深度洞察",
            "",
            llm_text,
            "",
        ]

        report_text = "\n".join(lines)

        if save:
            output_path = DATA_DIR / f"{end_date}-weekly-report.md"
            output_path.write_text(report_text, encoding="utf-8")
            log.info("已生成周报: %s", output_path)

        return report_text


def main():
    from superhealth.log_config import setup_logging

    setup_logging()
    ap = argparse.ArgumentParser(description="生成健康周报")
    ap.add_argument("--date", type=str, help="结束日期 (YYYY-MM-DD)，默认今天")
    ap.add_argument("--no-save", action="store_true", help="不保存文件")
    args = ap.parse_args()

    generator = LLMInsightsGenerator()
    report = generator.generate_weekly_report(args.date, save=not args.no_save)
    log.info("周报已生成:\n%s", report)


if __name__ == "__main__":
    main()
