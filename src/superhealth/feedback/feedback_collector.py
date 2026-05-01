"""用户反馈收集器：用于手动提交文字反馈到 recommendation_feedback。

用法：
    python -m superhealth.feedback.feedback_collector --date 2026-04-08 --feedback "今天肌肉酸痛，没做无氧"

功能：
    - 将用户反馈文字写入指定日期的 recommendation_feedback 记录
    - 若该日期无记录，会自动创建一条空记录（仅含 user_feedback）
    - 不触发日报重新生成，不修改 compliance
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from superhealth import database as db

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent.parent / "health.db"


def submit_feedback(
    target_date: str,
    feedback: str,
    recommendation_type: str = "exercise",
    rating: int | None = None,
    db_path: Path = DB_PATH,
) -> bool:
    """提交用户反馈到指定日期的记录。

    Args:
        target_date: 日期 YYYY-MM-DD
        feedback: 用户反馈文字
        recommendation_type: 建议类型，默认 exercise
        rating: 可选的主观评分 1-5
        db_path: 数据库路径

    Returns:
        True 表示成功，False 表示失败
    """
    with db.get_conn(db_path) as conn:
        # 检查是否已有记录
        existing = conn.execute(
            """SELECT id, user_feedback FROM recommendation_feedback
               WHERE date = ? AND recommendation_type = ?""",
            (target_date, recommendation_type),
        ).fetchone()

        if existing:
            # 更新现有记录
            conn.execute(
                "UPDATE recommendation_feedback SET user_feedback = ?, user_rating = ? WHERE id = ?",
                (feedback, rating, existing["id"]),
            )
            log.info("已更新 %s 的用户反馈", target_date)
        else:
            # 创建新记录（仅含 user_feedback，其他字段为空）
            report_id = f"{target_date}-advanced-daily-report"
            db.insert_recommendation_feedback(
                conn,
                date=target_date,
                report_id=report_id,
                recommendation_type=recommendation_type,
                recommendation_content=None,
                compliance=None,
                actual_action=None,
                tracked_metrics=None,
            )
            # 更新 user_feedback 和 user_rating
            conn.execute(
                """UPDATE recommendation_feedback
                   SET user_feedback = ?, user_rating = ?
                   WHERE date = ? AND recommendation_type = ?""",
                (feedback, rating, target_date, recommendation_type),
            )
            log.info("已创建 %s 的新反馈记录", target_date)

        return True


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    ap = argparse.ArgumentParser(description="提交用户反馈到 recommendation_feedback")
    ap.add_argument(
        "--date",
        type=str,
        required=True,
        help="日期 (YYYY-MM-DD)，反馈对应的日期",
    )
    ap.add_argument(
        "--feedback",
        type=str,
        required=True,
        help="用户反馈文字（如：今天肌肉酸痛，没做无氧训练）",
    )
    ap.add_argument(
        "--type",
        type=str,
        default="exercise",
        help="建议类型，默认 exercise",
    )
    ap.add_argument(
        "--rating",
        type=int,
        default=None,
        help="可选的主观评分 1-5 星",
    )
    args = ap.parse_args()

    ok = submit_feedback(
        target_date=args.date,
        feedback=args.feedback,
        recommendation_type=args.type,
        rating=args.rating,
    )
    print("反馈已提交" if ok else "提交失败")


if __name__ == "__main__":
    main()
