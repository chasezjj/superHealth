"""测试相关性分析模块的纯函数逻辑。"""
import pytest

from superhealth.analysis.correlation import (
    CorrelationResult,
    pearson_correlation,
)


class TestPearsonCorrelation:
    def test_perfect_positive(self):
        x = [1, 2, 3, 4, 5]
        y = [1, 2, 3, 4, 5]
        r, n = pearson_correlation(x, y)
        assert round(r, 6) == 1.0
        assert n == 5

    def test_perfect_negative(self):
        x = [1, 2, 3, 4, 5]
        y = [5, 4, 3, 2, 1]
        r, n = pearson_correlation(x, y)
        assert round(r, 6) == -1.0

    def test_no_correlation(self):
        x = [1, 2, 3]
        y = [2, 2, 2]
        r, n = pearson_correlation(x, y)
        assert r == 0.0
        assert n == 3

    def test_mismatched_lengths(self):
        r, n = pearson_correlation([1, 2], [1, 2, 3])
        assert r == 0.0
        assert n == 0

    def test_too_few_samples(self):
        r, n = pearson_correlation([1], [1])
        assert r == 0.0
        assert n == 0

    def test_insufficient_variance_x(self):
        x = [2, 2, 2]
        y = [1, 2, 3]
        r, n = pearson_correlation(x, y)
        assert r == 0.0
        assert n == 3

    def test_insufficient_variance_y(self):
        x = [1, 2, 3]
        y = [2, 2, 2]
        r, n = pearson_correlation(x, y)
        assert r == 0.0
        assert n == 3


class TestCorrelationResult:
    def test_strength_strong_positive(self):
        cr = CorrelationResult("x", "y", 10, 0.75, 0.56, None, "强相关")
        assert cr.strength() == "强相关"

    def test_strength_moderate(self):
        cr = CorrelationResult("x", "y", 10, 0.5, 0.25, None, "中等相关")
        assert cr.strength() == "中等相关"

    def test_strength_weak(self):
        cr = CorrelationResult("x", "y", 10, 0.3, 0.09, None, "弱相关")
        assert cr.strength() == "弱相关"

    def test_strength_none(self):
        cr = CorrelationResult("x", "y", 10, 0.1, 0.01, None, "几乎无关")
        assert cr.strength() == "几乎无关"

    def test_direction_positive(self):
        cr = CorrelationResult("x", "y", 10, 0.5, 0.25, None, "")
        assert cr.direction() == "正相关"

    def test_direction_negative(self):
        cr = CorrelationResult("x", "y", 10, -0.5, 0.25, None, "")
        assert cr.direction() == "负相关"
