"""因果推断引擎：从关联到因果的分析升级。

核心功能：
1. Granger 因果检验：判断 X 的过去值是否有助于预测 Y 的未来值
2. 干预前后配对检验：评估 goal/用药启动前后的指标变化
3. 间断时间序列分析（ITSA）：评估干预点带来的水平/斜率变化

统计检验采用 numpy 做矩阵运算，p 值使用标准近似公式，无需 scipy。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np

from superhealth import database as db
from superhealth.analysis.correlation import CorrelationAnalyzer

log = logging.getLogger(__name__)
DB_PATH = Path(__file__).parent.parent.parent.parent / "health.db"

# 复用 correlation.py 的指标映射，保持一致性
FIELD_MAP = {
    "sleep_score": "sleep_score",
    "sleep_duration": "sleep_total_seconds",
    "resting_hr": "hr_resting",
    "hrv_avg": "hrv_last_night_avg",
    "avg_stress": "stress_average",
    "max_stress": "stress_max",
    "body_battery_wake": "bb_at_wake",
    "body_battery_charged": "bb_charged",
    "steps": "steps",
    "active_calories": "active_calories",
}


@dataclass
class GrangerResult:
    """Granger 因果检验结果。"""

    metric_x: str
    metric_y: str
    lag: int
    f_statistic: float
    p_value: float
    rss_restricted: float
    rss_unrestricted: float
    n: int
    interpretation: str

    def is_significant(self, alpha: float = 0.1) -> bool:
        return self.p_value < alpha

    def strength(self) -> str:
        if self.p_value < 0.01:
            return "强因果证据"
        elif self.p_value < 0.05:
            return "中等因果证据"
        elif self.p_value < 0.1:
            return "弱因果证据"
        return "无显著因果证据"

    def direction(self) -> str:
        return f"{self.metric_x} 的过去值有助于预测 {self.metric_y}"


@dataclass
class InterventionResult:
    """干预前后配对检验结果。"""

    intervention_type: str
    intervention_name: str
    metric: str
    baseline_mean: float
    post_mean: float
    difference: float
    cohens_d: float
    t_statistic: float
    p_value: float
    n_baseline: int
    n_post: int
    interpretation: str

    def is_significant(self, alpha: float = 0.1) -> bool:
        return self.p_value < alpha

    def effect_direction(self) -> str:
        if self.difference < 0:
            return "下降"
        if self.difference > 0:
            return "上升"
        return "无变化"


@dataclass
class ITSAResult:
    """间断时间序列分析结果。"""

    metric: str
    intervention_date: str
    level_change: float
    slope_change: float
    level_p_value: float
    slope_p_value: float
    n_pre: int
    n_post: int
    r_squared: float
    interpretation: str

    def significant_level_change(self, alpha: float = 0.1) -> bool:
        return self.level_p_value < alpha

    def significant_slope_change(self, alpha: float = 0.1) -> bool:
        return self.slope_p_value < alpha


# ─── 统计工具函数（纯 Python + numpy）────────────────────────────────────


def _ols_fit(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, float, float, np.ndarray]:
    """OLS 回归拟合。

    Returns:
        beta: 系数向量 (k,)
        rss: 残差平方和
        tss: 总平方和
        std_errors: 系数标准误 (k,)
    """
    n, k = X.shape
    XtX = X.T @ X
    Xty = X.T @ y

    try:
        beta = np.linalg.solve(XtX, Xty)
    except np.linalg.LinAlgError:
        beta = np.linalg.pinv(XtX) @ Xty

    y_pred = X @ beta
    residuals = y - y_pred
    rss = float(np.sum(residuals**2))
    tss = float(np.sum((y - np.mean(y)) ** 2))

    if n > k:
        mse = rss / (n - k)
        try:
            var_beta = mse * np.linalg.inv(XtX)
            std_errors = np.sqrt(np.diag(var_beta))
        except np.linalg.LinAlgError:
            std_errors = np.full(k, np.nan)
    else:
        std_errors = np.full(k, np.nan)

    return beta, rss, tss, std_errors


def _f_pvalue(f_stat: float, d1: int, d2: int) -> float:
    """F 分布上尾 p 值近似（Wilson-Hilferty 变换）。"""
    if f_stat <= 0 or d1 <= 0 or d2 <= 0:
        return 1.0

    # Wilson-Hilferty: F^(1/3) 近似正态
    term1 = (1 - 2 / (9 * d2)) * (f_stat ** (1 / 3))
    term2 = 1 - 2 / (9 * d1)
    denom = math.sqrt(2 / (9 * d1) + (2 / (9 * d2)) * (f_stat ** (2 / 3)))

    if denom == 0:
        return 0.0 if f_stat > 1.0 else 1.0

    z = (term1 - term2) / denom
    p = 0.5 * (1 - math.erf(z / math.sqrt(2)))
    return max(0.0, min(1.0, p))


def _t_pvalue(t_stat: float, df: int) -> float:
    """t 分布双侧 p 值近似。"""
    if df <= 0:
        return 1.0

    z = abs(t_stat)
    if df <= 30:
        # 小样本修正：t 分布尾部比正态厚
        z = z * (1 - 1 / (4 * df))
    # 标准正态双侧尾概率
    p = math.erfc(z / math.sqrt(2))
    return max(0.0, min(1.0, p))


# ─── 主分析器类 ──────────────────────────────────────────────────────────


class CausalInferenceAnalyzer:
    """因果推断分析器。"""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._corr = CorrelationAnalyzer(db_path)

    def _get_conn(self):
        return db.get_conn(self.db_path)

    def get_metric_series(
        self,
        metric: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        days: Optional[int] = None,
    ) -> list[dict]:
        """获取指标时间序列（复用 CorrelationAnalyzer）"""
        return self._corr.get_metric_series(metric, start_date, end_date, days)

    # ── 1. Granger 因果检验 ──────────────────────────────────────────────

    def granger_causality(
        self,
        metric_x: str,
        metric_y: str,
        max_lag: int = 3,
        days: int = 90,
    ) -> GrangerResult:
        """Granger 因果检验：X 的过去值是否有助于预测 Y。

        算法：
        1. 受限模型：Y_t ~ 1 + Y_{t-1}..Y_{t-p}
        2. 非受限模型：Y_t ~ 1 + Y_{t-1}..Y_{t-p} + X_{t-1}..X_{t-p}
        3. F = ((RSS_r - RSS_u) / p) / (RSS_u / df_u)
        """
        series_x = self.get_metric_series(metric_x, days=days)
        series_y = self.get_metric_series(metric_y, days=days)

        x_by_date = {s["date"]: s["value"] for s in series_x}
        y_by_date = {s["date"]: s["value"] for s in series_y}
        common_dates = sorted(set(x_by_date.keys()) & set(y_by_date.keys()))

        min_required = 2 * max_lag + 10
        if len(common_dates) < min_required:
            return GrangerResult(
                metric_x=metric_x,
                metric_y=metric_y,
                lag=max_lag,
                f_statistic=0.0,
                p_value=1.0,
                rss_restricted=0.0,
                rss_unrestricted=0.0,
                n=len(common_dates),
                interpretation=f"数据不足（需至少 {min_required} 天，现有 {len(common_dates)} 天）",
            )

        x = np.array([x_by_date[d] for d in common_dates], dtype=float)
        y = np.array([y_by_date[d] for d in common_dates], dtype=float)

        n_obs = len(common_dates) - max_lag
        if n_obs <= 2 * max_lag + 1:
            return GrangerResult(
                metric_x=metric_x,
                metric_y=metric_y,
                lag=max_lag,
                f_statistic=0.0,
                p_value=1.0,
                rss_restricted=0.0,
                rss_unrestricted=0.0,
                n=len(common_dates),
                interpretation="有效观测不足（滞后构造后样本过少）",
            )

        # 构建滞后特征
        Y_lags = np.zeros((n_obs, max_lag))
        X_lags = np.zeros((n_obs, max_lag))
        for i in range(max_lag):
            start = max_lag - 1 - i
            end = len(y) - 1 - i
            Y_lags[:, i] = y[start:end]
            X_lags[:, i] = x[start:end]

        y_target = y[max_lag:]

        # 受限模型
        X_r = np.column_stack([np.ones(n_obs), Y_lags])
        beta_r, rss_r, _, se_r = _ols_fit(X_r, y_target)

        # 非受限模型
        X_u = np.column_stack([np.ones(n_obs), Y_lags, X_lags])
        beta_u, rss_u, _, se_u = _ols_fit(X_u, y_target)
        df_u = n_obs - 2 * max_lag - 1

        if rss_u <= 0 or df_u <= 0 or rss_r < rss_u:
            return GrangerResult(
                metric_x=metric_x,
                metric_y=metric_y,
                lag=max_lag,
                f_statistic=0.0,
                p_value=1.0,
                rss_restricted=round(rss_r, 2),
                rss_unrestricted=round(rss_u, 2),
                n=len(common_dates),
                interpretation="模型拟合异常（RSS 非正或自由度不足）",
            )

        f_stat = ((rss_r - rss_u) / max_lag) / (rss_u / df_u)
        f_stat = max(0.0, float(f_stat))
        p_value = _f_pvalue(f_stat, max_lag, df_u)

        # 解释文案
        if p_value < 0.05:
            direction = f"{metric_x} 的过去值对 {metric_y} 有显著预测能力（Granger 因果）"
        elif p_value < 0.1:
            direction = f"{metric_x} 的过去值对 {metric_y} 可能有弱预测能力"
        else:
            direction = f"{metric_x} 的过去值无法显著预测 {metric_y}"

        interpretation = f"滞后={max_lag} 期，F={f_stat:.2f}，p={p_value:.3f}，n={len(common_dates)}：{direction}"

        return GrangerResult(
            metric_x=metric_x,
            metric_y=metric_y,
            lag=max_lag,
            f_statistic=round(f_stat, 3),
            p_value=round(p_value, 4),
            rss_restricted=round(rss_r, 2),
            rss_unrestricted=round(rss_u, 2),
            n=len(common_dates),
            interpretation=interpretation,
        )

    def analyze_key_causal_pairs(self, days: int = 90) -> list[GrangerResult]:
        """分析预设的关键因果对。"""
        pairs = [
            ("sleep_score", "hrv_avg", 1, "睡眠评分 → 次日 HRV"),
            ("sleep_score", "body_battery_wake", 1, "睡眠评分 → 次日 Body Battery"),
            ("steps", "hrv_avg", 1, "步数 → 次日 HRV"),
            ("active_calories", "resting_hr", 1, "活动卡路里 → 次日静息心率"),
            ("avg_stress", "sleep_score", 1, "压力 → 次日睡眠评分"),
            ("hrv_avg", "avg_stress", 1, "HRV → 次日压力（反向因果检测）"),
            ("sleep_duration", "resting_hr", 1, "睡眠时长 → 次日静息心率"),
            ("steps", "avg_stress", 1, "步数 → 次日压力"),
        ]

        results = []
        for metric_x, metric_y, lag, desc in pairs:
            try:
                result = self.granger_causality(metric_x, metric_y, max_lag=lag, days=days)
                # 在 interpretation 前追加描述
                result.interpretation = f"{desc}：{result.interpretation}"
                results.append(result)
            except Exception as e:
                log.warning("Granger 检验 %s → %s 失败: %s", metric_x, metric_y, e)
                continue

        return results

    # ── 2. 干预前后配对检验 ──────────────────────────────────────────────

    def paired_intervention_test(
        self,
        metric: str,
        intervention_date: str,
        period_days: int = 14,
    ) -> InterventionResult:
        """评估干预点前后的指标变化（时期均值对比 + Welch's t-test）。

        由于每日数据存在自相关，不宜做逐日配对，
        改为比较基线期和干预期各自的日均值差异（Welch's t-test，更稳健）。
        """
        end_baseline = (datetime.fromisoformat(intervention_date) - timedelta(days=1)).isoformat()[
            :10
        ]
        start_baseline = (
            datetime.fromisoformat(end_baseline) - timedelta(days=period_days - 1)
        ).isoformat()[:10]
        start_post = intervention_date
        end_post = (
            datetime.fromisoformat(start_post) + timedelta(days=period_days - 1)
        ).isoformat()[:10]

        baseline_series = self.get_metric_series(
            metric, start_date=start_baseline, end_date=end_baseline
        )
        post_series = self.get_metric_series(metric, start_date=start_post, end_date=end_post)

        baseline_vals = [s["value"] for s in baseline_series if s["value"] is not None]
        post_vals = [s["value"] for s in post_series if s["value"] is not None]

        n_b = len(baseline_vals)
        n_p = len(post_vals)

        if n_b < 7 or n_p < 7:
            return InterventionResult(
                intervention_type="manual",
                intervention_name=f"干预点 {intervention_date}",
                metric=metric,
                baseline_mean=0.0,
                post_mean=0.0,
                difference=0.0,
                cohens_d=0.0,
                t_statistic=0.0,
                p_value=1.0,
                n_baseline=n_b,
                n_post=n_p,
                interpretation=f"数据不足（基线期 {n_b} 天，干预期 {n_p} 天，均需 ≥7 天）",
            )

        mean_b = sum(baseline_vals) / n_b
        mean_p = sum(post_vals) / n_p
        diff = mean_p - mean_b

        # 合并标准差（Cohen's d 用）
        var_b = sum((v - mean_b) ** 2 for v in baseline_vals) / n_b
        var_p = sum((v - mean_p) ** 2 for v in post_vals) / n_p
        pooled_std = (
            math.sqrt(((n_b - 1) * var_b + (n_p - 1) * var_p) / (n_b + n_p - 2))
            if (n_b + n_p) > 2
            else 0
        )

        cohens_d = diff / pooled_std if pooled_std > 0 else 0.0

        # 两独立样本 t 检验（ Welch's t-test，更稳健）
        if var_b > 0 and var_p > 0:
            se = math.sqrt(var_b / n_b + var_p / n_p)
            # Welch-Satterthwaite df
            numerator = (var_b / n_b + var_p / n_p) ** 2
            denominator = (var_b / n_b) ** 2 / (n_b - 1) + (var_p / n_p) ** 2 / (n_p - 1)
            df = int(numerator / denominator) if denominator > 0 else n_b + n_p - 2
        else:
            se = 0
            df = n_b + n_p - 2

        if se > 0:
            t_stat = diff / se
            p_value = _t_pvalue(t_stat, df)
        else:
            t_stat = 0.0
            p_value = 1.0

        if p_value < 0.05:
            effect = f"干预后 {metric} 显著{self._direction_label(diff)}（p={p_value:.3f}）"
        elif p_value < 0.1:
            effect = f"干预后 {metric} 可能{self._direction_label(diff)}（p={p_value:.3f}）"
        else:
            effect = f"干预后 {metric} 无显著变化（p={p_value:.3f}）"

        interpretation = (
            f"基线期（{start_baseline}~{end_baseline}）均值 {mean_b:.2f}，"
            f"干预期（{start_post}~{end_post}）均值 {mean_p:.2f}，"
            f"差异 {diff:+.2f}，Cohen's d={cohens_d:.2f}，{effect}"
        )

        return InterventionResult(
            intervention_type="manual",
            intervention_name=f"干预点 {intervention_date}",
            metric=metric,
            baseline_mean=round(mean_b, 2),
            post_mean=round(mean_p, 2),
            difference=round(diff, 2),
            cohens_d=round(cohens_d, 2),
            t_statistic=round(t_stat, 3),
            p_value=round(p_value, 4),
            n_baseline=n_b,
            n_post=n_p,
            interpretation=interpretation,
        )

    def paired_intervention_test_for_goal(
        self,
        goal_id: int,
        metric: str,
        period_days: int = 14,
    ) -> InterventionResult:
        """对指定 goal 自动查询其 start_date 并做干预检验。"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT name, start_date FROM goals WHERE id = ?", (goal_id,)
            ).fetchone()

        if not row:
            return InterventionResult(
                intervention_type="goal",
                intervention_name=f"Goal #{goal_id}",
                metric=metric,
                baseline_mean=0.0,
                post_mean=0.0,
                difference=0.0,
                cohens_d=0.0,
                t_statistic=0.0,
                p_value=1.0,
                n_baseline=0,
                n_post=0,
                interpretation="未找到该 goal",
            )

        result = self.paired_intervention_test(metric, row["start_date"], period_days)
        result.intervention_type = "goal"
        result.intervention_name = row["name"]
        return result

    def _direction_label(self, diff: float) -> str:
        if diff > 0:
            return "上升"
        if diff < 0:
            return "下降"
        return "无变化"

    # ── 3. 间断时间序列分析（ITSA）────────────────────────────────────────

    def interrupted_time_series(
        self,
        metric: str,
        intervention_date: str,
        pre_days: int = 30,
        post_days: int = 30,
    ) -> ITSAResult:
        """ITSA：评估干预点是否带来显著的水平/斜率变化。

        模型：Y_t = β0 + β1·time + β2·intervention + β3·time_after + ε
        - β2：水平变化（干预点跳跃）
        - β3：斜率变化（干预后趋势改变）
        """
        start = (datetime.fromisoformat(intervention_date) - timedelta(days=pre_days)).isoformat()[
            :10
        ]
        end = (datetime.fromisoformat(intervention_date) + timedelta(days=post_days)).isoformat()[
            :10
        ]

        series = self.get_metric_series(metric, start_date=start, end_date=end)
        if len(series) < pre_days + post_days // 2:
            return ITSAResult(
                metric=metric,
                intervention_date=intervention_date,
                level_change=0.0,
                slope_change=0.0,
                level_p_value=1.0,
                slope_p_value=1.0,
                n_pre=0,
                n_post=0,
                r_squared=0.0,
                interpretation="数据不足",
            )

        # 按日期排序并同步过滤 None 值，保证 dates 和 values 对齐
        filtered = [(s["date"], s["value"]) for s in series if s["value"] is not None]
        dates = [d for d, _ in filtered]
        values = np.array([v for _, v in filtered], dtype=float)

        if len(values) < 14:
            return ITSAResult(
                metric=metric,
                intervention_date=intervention_date,
                level_change=0.0,
                slope_change=0.0,
                level_p_value=1.0,
                slope_p_value=1.0,
                n_pre=0,
                n_post=0,
                r_squared=0.0,
                interpretation=f"有效数据点仅 {len(values)} 个，需 ≥14 个",
            )

        # 找到干预点在序列中的索引
        try:
            intervention_idx = dates.index(intervention_date)
        except ValueError:
            # 干预日期可能不在 daily_health 中，找最近的后一个日期
            valid_dates = [d for d in dates if d >= intervention_date]
            if not valid_dates:
                return ITSAResult(
                    metric=metric,
                    intervention_date=intervention_date,
                    level_change=0.0,
                    slope_change=0.0,
                    level_p_value=1.0,
                    slope_p_value=1.0,
                    n_pre=0,
                    n_post=0,
                    r_squared=0.0,
                    interpretation="干预日期不在数据范围内",
                )
            intervention_idx = dates.index(valid_dates[0])

        n = len(values)
        time = np.arange(n, dtype=float)
        intervention = np.zeros(n)
        intervention[intervention_idx:] = 1.0
        time_after = np.zeros(n)
        time_after[intervention_idx:] = np.arange(n - intervention_idx, dtype=float)

        X = np.column_stack([np.ones(n), time, intervention, time_after])
        beta, rss, tss, std_errors = _ols_fit(X, values)

        if len(beta) < 4 or np.any(np.isnan(std_errors)):
            return ITSAResult(
                metric=metric,
                intervention_date=intervention_date,
                level_change=0.0,
                slope_change=0.0,
                level_p_value=1.0,
                slope_p_value=1.0,
                n_pre=intervention_idx,
                n_post=n - intervention_idx,
                r_squared=0.0,
                interpretation="回归拟合失败（设计矩阵奇异）",
            )

        level_change = float(beta[2])
        slope_change = float(beta[3])
        r_squared = 1 - rss / tss if tss > 0 else 0.0

        # t 统计量 = 系数 / 标准误
        level_t = beta[2] / std_errors[2] if std_errors[2] > 0 else 0.0
        slope_t = beta[3] / std_errors[3] if std_errors[3] > 0 else 0.0
        df = n - 4

        level_p = _t_pvalue(level_t, df)
        slope_p = _t_pvalue(slope_t, df)

        n_pre = intervention_idx
        n_post = n - intervention_idx

        parts = []
        if level_p < 0.1:
            parts.append(f"水平显著变化 {level_change:+.2f}（p={level_p:.3f}）")
        if slope_p < 0.1:
            parts.append(f"斜率显著变化 {slope_change:+.3f}/天（p={slope_p:.3f}）")
        if not parts:
            parts.append("水平与斜率均无显著变化")

        interpretation = (
            f"干预点 {intervention_date}（前{n_pre}天/后{n_post}天）："
            f"R²={r_squared:.2f}，{'；'.join(parts)}"
        )

        return ITSAResult(
            metric=metric,
            intervention_date=intervention_date,
            level_change=round(level_change, 2),
            slope_change=round(slope_change, 3),
            level_p_value=round(level_p, 4),
            slope_p_value=round(slope_p, 4),
            n_pre=n_pre,
            n_post=n_post,
            r_squared=round(r_squared, 3),
            interpretation=interpretation,
        )


# ─── 报告生成 ────────────────────────────────────────────────────────────


def generate_causal_report(days: int = 90) -> str:
    """生成因果推断分析报告（Markdown）。"""
    analyzer = CausalInferenceAnalyzer()

    lines = [f"# 因果推断报告（最近{days}天）", ""]
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    # Granger 因果
    lines.append("## Granger 因果发现")
    lines.append("")
    lines.append("Granger 因果检验判断：X 的过去值是否有助于预测 Y 的未来值。")
    lines.append("**注意**：这是统计预测关系，不等于确定性因果。")
    lines.append("")

    results = analyzer.analyze_key_causal_pairs(days)
    results.sort(key=lambda r: r.p_value)

    significant = [r for r in results if r.p_value < 0.1]
    if significant:
        for r in significant:
            emoji = "🔴" if r.p_value < 0.05 else "🟡"
            lines.append(f"{emoji} **{r.strength()}**: {r.interpretation}")
            lines.append("")
    else:
        lines.append("未发现显著的 Granger 因果关系（可能数据量不足或效应较弱）。")
        lines.append("")

    # 非显著结果简要列出
    non_sig = [r for r in results if r.p_value >= 0.1][:3]
    if non_sig:
        lines.append("其他检验对（不显著）：")
        for r in non_sig:
            lines.append(f"- ⚪ {r.interpretation}")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    from superhealth.log_config import setup_logging

    setup_logging()
    report = generate_causal_report(90)
    print(report)
