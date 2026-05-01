"""测试 causal.py 的纯统计函数和结果对象。"""
import math

import numpy as np
import pytest

from superhealth.analysis.causal import (
    GrangerResult,
    InterventionResult,
    ITSAResult,
    _f_pvalue,
    _ols_fit,
    _t_pvalue,
)


class TestOlsFit:
    def test_simple_linear(self):
        """y = 2x + 1, 无噪声。"""
        x = np.arange(10, dtype=float)
        y = 2 * x + 1
        X = np.column_stack([np.ones(10), x])
        beta, rss, tss, std_errors = _ols_fit(X, y)
        assert np.allclose(beta, [1.0, 2.0], atol=1e-10)
        assert rss < 1e-10
        assert tss > 0
        assert len(std_errors) == 2

    def test_multivariate(self):
        """y = 1 + 2*x1 + 3*x2。"""
        n = 50
        x1 = np.random.randn(n)
        x2 = np.random.randn(n)
        y = 1 + 2 * x1 + 3 * x2 + np.random.randn(n) * 0.01
        X = np.column_stack([np.ones(n), x1, x2])
        beta, rss, tss, std_errors = _ols_fit(X, y)
        assert np.allclose(beta, [1.0, 2.0, 3.0], atol=0.1)
        assert rss < tss
        assert len(std_errors) == 3
        assert np.all(std_errors >= 0)

    def test_singular_fallback(self):
        """奇异矩阵应回退到伪逆。"""
        X = np.ones((5, 2))  # 两列相同，奇异
        y = np.ones(5)
        beta, rss, tss, std_errors = _ols_fit(X, y)
        assert not np.isnan(beta).all()
        assert len(std_errors) == 2


class TestFPvalue:
    def test_zero_fstat(self):
        assert _f_pvalue(0, 2, 10) == 1.0

    def test_negative_fstat(self):
        assert _f_pvalue(-1, 2, 10) == 1.0

    def test_large_fstat(self):
        """F 很大时 p 应接近 0。"""
        p = _f_pvalue(100, 2, 30)
        assert 0 <= p < 0.01

    def test_fstat_near_one(self):
        """F ≈ 1 时 p 应接近 0.5。"""
        p = _f_pvalue(1.0, 5, 20)
        assert 0.3 < p < 0.7

    def test_invalid_df(self):
        assert _f_pvalue(2, 0, 10) == 1.0
        assert _f_pvalue(2, 2, 0) == 1.0


class TestTPvalue:
    def test_zero_tstat(self):
        assert _t_pvalue(0, 10) == 1.0

    def test_large_tstat(self):
        """|t| 很大时 p 应接近 0。"""
        p = _t_pvalue(5, 10)
        assert 0 <= p < 0.01

    def test_small_df_adjustment(self):
        """小样本修正不应改变太大趋势。"""
        p_5df = _t_pvalue(2, 5)
        p_100df = _t_pvalue(2, 100)
        assert p_5df > p_100df  # 小样本尾部更厚

    def test_invalid_df(self):
        assert _t_pvalue(2, 0) == 1.0
        assert _t_pvalue(2, -1) == 1.0


class TestGrangerResult:
    def test_is_significant(self):
        r = GrangerResult("x", "y", 1, 5.0, 0.03, 10.0, 5.0, 100, "")
        assert r.is_significant(alpha=0.05) is True
        assert r.is_significant(alpha=0.01) is False

    def test_strength(self):
        assert GrangerResult("x", "y", 1, 5.0, 0.005, 10, 5, 100, "").strength() == "强因果证据"
        assert GrangerResult("x", "y", 1, 5.0, 0.03, 10, 5, 100, "").strength() == "中等因果证据"
        assert GrangerResult("x", "y", 1, 5.0, 0.08, 10, 5, 100, "").strength() == "弱因果证据"
        assert GrangerResult("x", "y", 1, 5.0, 0.2, 10, 5, 100, "").strength() == "无显著因果证据"

    def test_direction(self):
        r = GrangerResult("x", "y", 1, 5.0, 0.03, 10, 5, 100, "")
        assert "x" in r.direction()
        assert "y" in r.direction()


class TestInterventionResult:
    def test_is_significant(self):
        r = InterventionResult(
            "t", "n", "m", 10.0, 12.0, 2.0, 0.5, 2.0, 0.04, 10, 10, ""
        )
        assert r.is_significant(alpha=0.05) is True
        assert r.is_significant(alpha=0.01) is False

    def test_effect_direction(self):
        assert InterventionResult("t", "n", "m", 0, 0, 5, 0, 0, 0, 0, 0, "").effect_direction() == "上升"
        assert InterventionResult("t", "n", "m", 0, 0, -3, 0, 0, 0, 0, 0, "").effect_direction() == "下降"
        assert InterventionResult("t", "n", "m", 0, 0, 0, 0, 0, 0, 0, 0, "").effect_direction() == "无变化"


class TestITSAResult:
    def test_significant_level_change(self):
        r = ITSAResult("m", "2025-01-01", 5.0, 0.0, 0.03, 0.5, 30, 30, 0.5, "")
        assert r.significant_level_change(alpha=0.05) is True
        assert r.significant_level_change(alpha=0.01) is False

    def test_significant_slope_change(self):
        r = ITSAResult("m", "2025-01-01", 0.0, 0.1, 0.5, 0.03, 30, 30, 0.5, "")
        assert r.significant_slope_change(alpha=0.05) is True
        assert r.significant_slope_change(alpha=0.01) is False
