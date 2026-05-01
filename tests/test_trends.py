"""测试趋势分析模块的纯函数逻辑。"""
from unittest.mock import MagicMock, patch

import pytest

from superhealth.analysis.trends import TrendAnalyzer, TrendResult


class TestTrendResult:
    def test_anomaly_detection(self):
        tr = TrendResult(
            metric="sleep_score",
            current_value=60,
            avg_7d=80,
            avg_30d=82,
            avg_90d=80,
            baseline_mean=80,
            baseline_std=5,
            z_score=-4.0,
            is_anomaly=True,
            trend_direction="down",
        )
        assert tr.is_anomaly is True
        assert tr.trend_direction == "down"

    def test_no_anomaly(self):
        tr = TrendResult(
            metric="sleep_score",
            current_value=82,
            avg_7d=80,
            avg_30d=81,
            avg_90d=80,
            baseline_mean=80,
            baseline_std=5,
            z_score=0.4,
            is_anomaly=False,
            trend_direction="stable",
        )
        assert tr.is_anomaly is False


class TestTrendAnalyzerPureFunctions:
    def test_calculate_personal_baseline_with_data(self):
        with patch.object(TrendAnalyzer, "_get_conn") as mock_conn:
            mock_cursor = MagicMock()
            mock_cursor.execute.return_value.fetchall.return_value = [
                {"value": 80}, {"value": 82}, {"value": 78}, {"value": 80}
            ]
            mock_conn.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)

            analyzer = TrendAnalyzer("/tmp/fake.db")
            baseline = analyzer.calculate_personal_baseline("sleep_score", days=30)

            assert baseline["mean"] == 80.0
            assert baseline["n"] == 4
            assert baseline["min"] == 78
            assert baseline["max"] == 82
            assert baseline["std"] > 0

    def test_calculate_personal_baseline_no_data(self):
        with patch.object(TrendAnalyzer, "_get_conn") as mock_conn:
            mock_cursor = MagicMock()
            mock_cursor.execute.return_value.fetchall.return_value = []
            mock_conn.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)

            analyzer = TrendAnalyzer("/tmp/fake.db")
            baseline = analyzer.calculate_personal_baseline("sleep_score", days=30)
            assert baseline["mean"] is None
            assert baseline["n"] == 0

    def test_detect_anomalies(self):
        with patch.object(TrendAnalyzer, "_get_conn") as mock_conn:
            mock_cursor = MagicMock()
            # mean=80, std=5, so z=2 at 90, z=-2 at 70
            mock_cursor.execute.return_value.fetchall.return_value = [
                {"date": "2025-04-01", "value": 80},
                {"date": "2025-04-02", "value": 92},  # anomaly (>2σ)
                {"date": "2025-04-03", "value": 68},  # anomaly (<-2σ)
                {"date": "2025-04-04", "value": 81},
            ]
            mock_conn.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)

            with patch.object(TrendAnalyzer, "calculate_personal_baseline", return_value={"mean": 80, "std": 5}):
                analyzer = TrendAnalyzer("/tmp/fake.db")
                anomalies = analyzer.detect_anomalies("sleep_score", z_threshold=2.0)

            assert len(anomalies) == 2
            dates = {a["date"] for a in anomalies}
            assert "2025-04-02" in dates
            assert "2025-04-03" in dates

    def test_compare_periods(self):
        with patch.object(TrendAnalyzer, "_get_conn") as mock_conn:
            mock_cursor = MagicMock()
            # First call = period1, second = period2
            mock_cursor.execute.return_value.fetchone.side_effect = [
                {"mean": 80, "n": 30},
                {"mean": 85, "n": 30},
            ]
            mock_conn.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)

            analyzer = TrendAnalyzer("/tmp/fake.db")
            result = analyzer.compare_periods("sleep_score", "2025-01-01", "2025-01-31", "2025-02-01", "2025-02-28")

            assert result["period1"]["mean"] == 80
            assert result["period2"]["mean"] == 85
            assert result["change"] == 5
            assert result["change_pct"] == 6.2

    def test_unknown_metric_raises(self):
        analyzer = TrendAnalyzer("/tmp/fake.db")
        with pytest.raises(ValueError, match="未知指标"):
            analyzer.calculate_rolling_averages("unknown_metric")
