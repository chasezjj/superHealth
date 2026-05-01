"""每日健康数据流水线编排器。

负责：
- 历史失败日期重试拉取
- 拉取昨天和今天的 Garmin 数据
- 生成高级日报、发送微信、自动反馈
- 效果追踪、策略学习
- 预约提醒
- 每一步记录 sync_logs
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta

from superhealth import database as db
from superhealth.collectors import fetch_garmin as fg
from superhealth.collectors import send_garmin_report
from superhealth.collectors.fetch_garmin import GarminAuthError
from superhealth.collectors.outlook_collector import fetch_calendar
from superhealth.config import get_db_path
from superhealth.feedback import auto_feedback
from superhealth.feedback.effect_tracker import EffectTracker
from superhealth.feedback.strategy_learner import StrategyLearner
from superhealth.goals.manager import GoalManager
from superhealth.insights.llm_insights import LLMInsightsGenerator
from superhealth.reminders import appointment_scheduler, reminder_notifier
from superhealth.reports.advanced_daily_report import AdvancedDailyReportGenerator

log = logging.getLogger(__name__)

DB_PATH = get_db_path()
MAX_FETCH_RETRIES = 3


def _ensure_session():
    """确保 Garmin session 可用，必要时自动登录。"""
    if not fg.SESSION_FILE.exists():
        email, password = fg._load_config()
        if not email or not password:
            raise GarminAuthError(
                "未找到 Garmin 配置，无法自动登录。请先运行: python -m superhealth.collectors.fetch_garmin --login"
            )
        log.info("未找到 session，正在自动登录...")
        fg.login_with_credentials(email, password)
    return fg._load_session()


def fetch_and_log(target_date: str) -> bool:
    """拉取指定日期的 Garmin 数据并记录 sync_logs，失败时模块级重试。"""
    for attempt in range(1, MAX_FETCH_RETRIES + 1):
        try:
            session, user_id = _ensure_session()
            fg.save_day(session, user_id, target_date)
            with db.get_conn(DB_PATH) as conn:
                db.insert_sync_log(conn, date=target_date, step="fetch", status="success")
            log.info("FETCH_SUCCESS %s", target_date)
            return True
        except Exception as e:
            log.warning("FETCH_ATTEMPT_%d_FAILED %s: %s", attempt, target_date, e)
            if attempt == MAX_FETCH_RETRIES:
                error_msg = str(e)[:500]
                try:
                    with db.get_conn(DB_PATH) as conn:
                        db.insert_sync_log(
                            conn,
                            date=target_date,
                            step="fetch",
                            status="failure",
                            error_message=error_msg,
                        )
                except Exception as db_err:
                    log.error("记录 fetch 失败日志到数据库时出错: %s", db_err)
                log.error("FETCH_FAILED %s after %d attempts", target_date, MAX_FETCH_RETRIES)
                return False


def _log_step(target_date: str, step_name: str, success: bool, error_message: str | None = None):
    """记录单个步骤的执行结果到 sync_logs。"""
    try:
        with db.get_conn(DB_PATH) as conn:
            db.insert_sync_log(
                conn,
                date=target_date,
                step=step_name,
                status="success" if success else "failure",
                error_message=error_message,
            )
    except Exception as db_err:
        log.error("记录 %s 日志到数据库时出错: %s", step_name, db_err)


def _run_step(target_date: str, step_name: str, func, *args, **kwargs) -> bool:
    """执行单个步骤并记录 sync_logs，失败不影响后续流程。"""
    try:
        result = func(*args, **kwargs)
        # 若函数返回非零整数（如 subprocess 的 returncode），视为失败
        if type(result) is int and result != 0:
            error_msg = f"returncode={result}"
            _log_step(target_date, step_name, False, error_msg)
            log.warning("STEP_FAILED %s: %s", step_name, error_msg)
            return False
        _log_step(target_date, step_name, True)
        log.info("STEP_SUCCESS %s", step_name)
        return True
    except Exception as e:
        error_msg = str(e)[:500]
        _log_step(target_date, step_name, False, error_msg)
        log.warning("STEP_FAILED %s: %s", step_name, e)
        return False


def run_pipeline(test_mode: bool = False, retry_days: int = 7, target_date: str | None = None):
    """执行每日完整流水线。"""
    day = target_date or date.today().isoformat()
    yesterday = (datetime.strptime(day, "%Y-%m-%d").date() - timedelta(days=1)).isoformat()

    log.info("PIPELINE_START day=%s test_mode=%s", day, test_mode)

    # 1. 历史失败重试
    try:
        with db.get_conn(DB_PATH) as conn:
            failed_dates = db.query_failed_sync_dates(conn, since_days=retry_days, step="fetch")
        if failed_dates:
            log.info("发现历史失败日期，准备重试: %s", failed_dates)
            for d in failed_dates:
                fetch_and_log(d)
    except Exception as e:
        log.warning("查询历史失败日期时出错: %s", e)

    if test_mode:
        log.info("TEST_MODE: 仅生成测试报告")
        generator = AdvancedDailyReportGenerator(db_path=DB_PATH)
        report = generator.generate_report(day, save=True, test_mode=True)
        log.info("测试报告:\n%s", report)
        return

    # 2. 拉取昨天
    fetch_and_log(yesterday)

    # 3. 拉取今天
    fetch_and_log(day)

    # 3.5 拉取当天日历（缓存到 DB，供日报使用）
    try:
        fetch_calendar(day, db_path=DB_PATH)
        _log_step(day, "calendar_fetch", True)
        log.info("日历采集完成: %s", day)
    except Exception as e:
        _log_step(day, "calendar_fetch", False, str(e)[:500])
        log.warning("日历采集失败: %s", e)

    # 4. 生成高级日报
    generator = AdvancedDailyReportGenerator(db_path=DB_PATH)
    _run_step(day, "report", generator.generate_report, day, save=True, test_mode=False)

    # 5. 发送微信
    _run_step(day, "send", send_garmin_report.send_report, day)

    # 6. 自动反馈（评估昨天）
    _run_step(yesterday, "auto_feedback", auto_feedback.run, yesterday, DB_PATH)

    # 7. 效果追踪与策略学习（早上直接执行，不依赖 nightly_update）
    try:
        tracker = EffectTracker(DB_PATH)
        effects = tracker.track_recent_exercises(days=14, run_date=day)
        tracker.write_effects_to_db(effects, run_date=day)
        _log_step(day, "effect_tracker", True)
        log.info("effect_tracker 完成，写回 %d 条", len(effects))
    except Exception as e:
        _log_step(day, "effect_tracker", False, str(e)[:500])
        log.warning("EffectTracker 失败: %s", e)

    try:
        learner = StrategyLearner(DB_PATH)
        # 加载活跃目标以传递给策略学习器
        active_goals = []
        try:
            goal_mgr_tmp = GoalManager(DB_PATH)
            with db.get_conn(DB_PATH) as conn:
                active_goals = goal_mgr_tmp.get_active_goals(conn)
        except Exception:
            pass
        result = learner.run_full_analysis(days=180, active_goals=active_goals)
        _log_step(day, "strategy_learner", True)
        if result.get("preference_updates"):
            log.info("策略更新 %s", result["preference_updates"])
    except Exception as e:
        _log_step(day, "strategy_learner", False, str(e)[:500])
        log.warning("StrategyLearner 失败: %s", e)

    # 8. 目标进度追踪（在效果追踪后、预约提醒前）
    try:
        goal_mgr = GoalManager(DB_PATH)
        goal_mgr.track_daily_progress(day)
        _log_step(day, "goals_tracker", True)
        log.info("goals_tracker 完成")
    except Exception as e:
        _log_step(day, "goals_tracker", False, str(e)[:500])
        log.warning("GoalManager 失败: %s", e)

    # 8.5 实验评估（检查活跃实验是否到期）
    try:
        from superhealth.feedback.experiment_manager import ExperimentManager

        exp_mgr = ExperimentManager(DB_PATH)
        exp_mgr.check_and_evaluate(day)
        _log_step(day, "experiment_eval", True)
    except Exception as e:
        _log_step(day, "experiment_eval", False, str(e)[:500])
        log.warning("ExperimentManager 失败: %s", e)

    # 9. 预约提醒
    _run_step(day, "appointments", appointment_scheduler.refresh_appointments)
    _run_step(day, "appointments_notifier", reminder_notifier.check_and_notify)

    # 10. 周报（每周日生成）
    try:
        day_dt = datetime.strptime(day, "%Y-%m-%d").date()
        if day_dt.weekday() == 6:  # Sunday
            weekly_gen = LLMInsightsGenerator(db_path=DB_PATH)
            report = weekly_gen.generate_weekly_report(end_date=day, save=True)
            _log_step(day, "weekly_report", True)
            log.info("周报已生成: %s", day)
        else:
            log.debug("非周日，跳过周报生成")
    except Exception as e:
        _log_step(day, "weekly_report", False, str(e)[:500])
        log.warning("周报生成失败: %s", e)

    log.info("PIPELINE_DONE day=%s", day)


def main():
    from superhealth.log_config import setup_logging

    setup_logging()
    def _valid_date(s: str):
        try:
            from datetime import datetime
            datetime.strptime(s, "%Y-%m-%d")
            return s
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"日期格式错误，应为 YYYY-MM-DD: {s}") from exc

    ap = argparse.ArgumentParser(description="每日健康数据流水线编排器")
    ap.add_argument(
        "--test-mode", action="store_true", help="测试模式：仅生成高级日报测试文件，不写DB"
    )
    ap.add_argument("--retry-days", type=int, default=7, help="检查历史失败的天数，默认7")
    ap.add_argument("--date", type=_valid_date, help="指定业务日期 (YYYY-MM-DD)，默认今天")
    args = ap.parse_args()
    run_pipeline(test_mode=args.test_mode, retry_days=args.retry_days, target_date=args.date)


if __name__ == "__main__":
    main()
