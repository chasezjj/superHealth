"""测试 daily_pipeline 的核心流程和辅助函数。"""
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from superhealth.daily_pipeline import _log_step, _run_step, fetch_and_log, run_pipeline


class TestLogStep:
    def test_logs_success(self, tmp_path):
        db_path = tmp_path / "test.db"
        from superhealth import database as db
        db.init_db(db_path)
        with patch("superhealth.daily_pipeline.DB_PATH", db_path):
            _log_step("2025-04-01", "test_step", True)
            with db.get_conn(db_path) as conn:
                rows = conn.execute("SELECT * FROM sync_logs WHERE step = ?", ("test_step",)).fetchall()
            assert len(rows) == 1
            assert rows[0]["status"] == "success"

    def test_logs_failure(self, tmp_path):
        db_path = tmp_path / "test.db"
        from superhealth import database as db
        db.init_db(db_path)
        with patch("superhealth.daily_pipeline.DB_PATH", db_path):
            _log_step("2025-04-01", "test_step", False, "something broke")
            with db.get_conn(db_path) as conn:
                rows = conn.execute("SELECT * FROM sync_logs WHERE step = ?", ("test_step",)).fetchall()
            assert len(rows) == 1
            assert rows[0]["status"] == "failure"
            assert "something broke" in rows[0]["error_message"]


class TestRunStep:
    def test_successful_step(self, tmp_path):
        db_path = tmp_path / "test.db"
        from superhealth import database as db
        db.init_db(db_path)
        with patch("superhealth.daily_pipeline.DB_PATH", db_path):
            result = _run_step("2025-04-01", "my_step", lambda: "ok")
            assert result is True

    def test_step_returning_nonzero_int(self, tmp_path):
        db_path = tmp_path / "test.db"
        from superhealth import database as db
        db.init_db(db_path)
        with patch("superhealth.daily_pipeline.DB_PATH", db_path):
            result = _run_step("2025-04-01", "my_step", lambda: 1)
            assert result is False

    def test_step_exception(self, tmp_path):
        db_path = tmp_path / "test.db"
        from superhealth import database as db
        db.init_db(db_path)
        with patch("superhealth.daily_pipeline.DB_PATH", db_path):
            result = _run_step("2025-04-01", "my_step", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
            assert result is False


class TestFetchAndLog:
    @patch("superhealth.daily_pipeline._ensure_session")
    @patch("superhealth.daily_pipeline.fg.save_day")
    def test_fetch_success(self, mock_save_day, mock_ensure, tmp_path):
        db_path = tmp_path / "test.db"
        from superhealth import database as db
        db.init_db(db_path)
        mock_ensure.return_value = (MagicMock(), "user123")
        with patch("superhealth.daily_pipeline.DB_PATH", db_path):
            result = fetch_and_log("2025-04-01")
            assert result is True
            mock_save_day.assert_called_once()

    @patch("superhealth.daily_pipeline._ensure_session")
    def test_fetch_failure_after_retries(self, mock_ensure, tmp_path):
        db_path = tmp_path / "test.db"
        from superhealth import database as db
        db.init_db(db_path)
        mock_ensure.side_effect = RuntimeError("network error")
        with patch("superhealth.daily_pipeline.DB_PATH", db_path):
            result = fetch_and_log("2025-04-01")
            assert result is False


class TestRunPipeline:
    @patch("superhealth.daily_pipeline.fetch_and_log")
    @patch("superhealth.daily_pipeline._run_step")
    @patch("superhealth.daily_pipeline.AdvancedDailyReportGenerator")
    def test_test_mode(self, mock_report_gen, mock_run_step, mock_fetch, tmp_path):
        db_path = tmp_path / "test.db"
        from superhealth import database as db
        db.init_db(db_path)
        mock_gen = MagicMock()
        mock_gen.generate_report.return_value = "test report"
        mock_report_gen.return_value = mock_gen

        with patch("superhealth.daily_pipeline.DB_PATH", db_path):
            run_pipeline(test_mode=True, target_date="2025-04-01")
            mock_gen.generate_report.assert_called_once_with("2025-04-01", save=True, test_mode=True)
            mock_fetch.assert_not_called()

    @patch("superhealth.daily_pipeline.fetch_and_log")
    @patch("superhealth.daily_pipeline._run_step")
    def test_normal_pipeline(self, mock_run_step, mock_fetch, tmp_path):
        db_path = tmp_path / "test.db"
        from superhealth import database as db
        db.init_db(db_path)
        with patch("superhealth.daily_pipeline.DB_PATH", db_path):
            run_pipeline(test_mode=False, target_date="2025-04-01")
            # fetch_and_log should be called for yesterday and today
            assert mock_fetch.call_count >= 2
