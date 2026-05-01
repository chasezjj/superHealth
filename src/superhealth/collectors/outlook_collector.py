"""Outlook/Exchange 日历采集器。

通过 EWS (Exchange Web Services) 协议拉取当天日历事件，提炼为 CalendarSummary
供 LLM 建议引擎参考。支持先读 DB 缓存、后连 Exchange 的模式，与 weather_collector
风格一致。

使用方式：
    from superhealth.collectors.outlook_collector import fetch_calendar
    summary = fetch_calendar("2026-04-23")
    if summary:
        print(summary.busy_level)  # low / medium / high
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from superhealth import database as db
from superhealth.config import get_db_path, load as load_config

log = logging.getLogger(__name__)

DEFAULT_DB_PATH = get_db_path()


@dataclass
class CalendarSummary:
    """一天日历事件的摘要，供 LLM Prompt 使用。"""

    date: str
    event_count: int = 0
    total_meeting_min: int = 0
    first_event_start: str | None = None  # "HH:MM"
    last_event_end: str | None = None  # "HH:MM"
    busiest_period: str | None = None  # 如 "09:00-12:00"
    back_to_back_count: int = 0  # 连续会议组数（重叠或间隔≤15min的连续块算1组）
    has_all_day: bool = False
    events: list[dict] = field(default_factory=list)

    @property
    def busy_level(self) -> str:
        """基于总会议时长和密度判定忙碌等级。"""
        if self.has_all_day or self.total_meeting_min > 240 or self.back_to_back_count >= 3:
            return "high"
        if self.total_meeting_min > 120 or self.back_to_back_count >= 2:
            return "medium"
        return "low"

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "event_count": self.event_count,
            "total_meeting_min": self.total_meeting_min,
            "first_event_start": self.first_event_start,
            "last_event_end": self.last_event_end,
            "busiest_period": self.busiest_period,
            "back_to_back_count": self.back_to_back_count,
            "has_all_day": self.has_all_day,
            "busy_level": self.busy_level,
            "events": self.events,
        }


def _dedup_by_time(events: list[dict]) -> list[dict]:
    """去重：start_time + end_time 完全相同的事件只保留第一个。"""
    seen = set()
    result = []
    for e in events:
        if e.get("is_all_day"):
            result.append(e)
        else:
            key = (e.get("start_time"), e.get("end_time"))
            if key not in seen:
                seen.add(key)
                result.append(e)
    return result


def _total_meeting_min(timed_events: list[dict]) -> int:
    """计算会议时间区间的并集总时长（去掉重叠部分）。"""
    intervals = []
    for e in timed_events:
        st = e.get("start_time")
        et = e.get("end_time")
        if not st or not et:
            continue
        try:
            st_h, st_m = map(int, st.split(":"))
            et_h, et_m = map(int, et.split(":"))
            st_min = st_h * 60 + st_m
            et_min = et_h * 60 + et_m
            if et_min < st_min:  # 跨天会议，截断到当天 24:00
                et_min = 1440
            intervals.append((st_min, et_min))
        except ValueError:
            continue

    if not intervals:
        return 0

    intervals.sort()
    merged = [list(intervals[0])]
    for start, end in intervals[1:]:
        if start <= merged[-1][1]:  # 重叠或相邻
            if end > merged[-1][1]:
                merged[-1][1] = end
        else:
            merged.append([start, end])

    return sum(end - start for start, end in merged)


def _build_summary(date_str: str, events: list[dict]) -> CalendarSummary:
    """从原始事件列表构建 CalendarSummary。"""
    if not events:
        return CalendarSummary(date=date_str, event_count=0)

    # 过滤掉全天事件（仅计入 has_all_day，不计入时长统计）
    timed_events = [e for e in events if not e.get("is_all_day", False)]
    all_day_events = [e for e in events if e.get("is_all_day", False)]

    total_min = _total_meeting_min(timed_events)

    # 按开始时间排序
    sorted_events = sorted(timed_events, key=lambda e: e.get("start_time") or "")

    first_start = sorted_events[0].get("start_time") if sorted_events else None
    last_end = sorted_events[-1].get("end_time") if sorted_events else None

    # 计算连续会议组数：重叠或间隔 <= 15min 的事件构成一个连续块，每块算 1 组
    back_to_back = 0
    in_block = False
    for i in range(len(sorted_events) - 1):
        curr_end = sorted_events[i].get("end_time")
        next_start = sorted_events[i + 1].get("start_time")
        if curr_end and next_start:
            try:
                c_h, c_m = map(int, curr_end.split(":"))
                n_h, n_m = map(int, next_start.split(":"))
                gap = (n_h * 60 + n_m) - (c_h * 60 + c_m)
                if gap <= 15:  # 重叠或间隔 <= 15min
                    if not in_block:
                        back_to_back += 1
                        in_block = True
                else:
                    in_block = False
            except ValueError:
                in_block = False

    # 计算最忙时段：找事件最多的连续紧凑会议块
    # 连续定义：下一个事件与当前块重叠，或间隔 <= 15min（与 back_to_back 一致）
    busiest_period = None
    if len(sorted_events) >= 2:
        max_count = 0
        best_start = None
        best_end_min = 0
        for i in range(len(sorted_events)):
            s = sorted_events[i].get("start_time")
            ee = sorted_events[i].get("end_time")
            if not s or not ee:
                continue
            try:
                s_h, s_m = map(int, s.split(":"))
                ee_h, ee_m = map(int, ee.split(":"))
                block_end_min = ee_h * 60 + ee_m
            except ValueError:
                continue

            count = 1
            for j in range(i + 1, len(sorted_events)):
                ns = sorted_events[j].get("start_time")
                ne = sorted_events[j].get("end_time")
                if not ns or not ne:
                    continue
                try:
                    ns_h, ns_m = map(int, ns.split(":"))
                    ns_min = ns_h * 60 + ns_m
                    ne_h, ne_m = map(int, ne.split(":"))
                    ne_min = ne_h * 60 + ne_m
                except ValueError:
                    continue

                gap = ns_min - block_end_min
                if gap <= 15:  # 重叠或间隔 <= 15min
                    count += 1
                    if ne_min > block_end_min:
                        block_end_min = ne_min
                else:
                    break

            if count > max_count or (count == max_count and block_end_min > best_end_min):
                max_count = count
                best_start = s
                best_end_min = block_end_min

        if best_start and max_count >= 2:
            e_h, e_m = best_end_min // 60, best_end_min % 60
            busiest_period = f"{best_start}-{e_h:02d}:{e_m:02d}"

    return CalendarSummary(
        date=date_str,
        event_count=len(events),
        total_meeting_min=total_min,
        first_event_start=first_start,
        last_event_end=last_end,
        busiest_period=busiest_period,
        back_to_back_count=back_to_back,
        has_all_day=bool(all_day_events),
        events=events,
    )


def _fetch_from_exchange(date_str: str, cfg) -> list[dict]:
    """连接 Exchange 服务器拉取指定日期的日历事件。"""
    try:
        from zoneinfo import ZoneInfo

        from exchangelib import Account, Credentials
    except ImportError:
        raise ImportError("需要 exchangelib：pip install exchangelib")

    credentials = Credentials(cfg.username, cfg.password)
    account = Account(cfg.email, credentials=credentials, autodiscover=True)

    tz = ZoneInfo(cfg.timezone)
    day = datetime.strptime(date_str, "%Y-%m-%d").date()
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    end = start + timedelta(days=1)

    events = []
    skipped = 0
    for item in account.calendar.view(start=start, end=end):
        subject = item.subject or "(无标题)"
        if getattr(item, "is_cancelled", False):
            log.debug("日历跳过 [cancelled]: %s", subject)
            skipped += 1
            continue

        # 过滤掉已拒绝/不显示的会议（Outlook 默认不显示 Free 状态的会议）
        free_busy = getattr(item, "legacy_free_busy_status", None)
        if free_busy and str(free_busy) == "Free":
            log.debug("日历跳过 [Free]: %s", subject)
            skipped += 1
            continue

        # 过滤掉已明确拒绝的会议
        response_type = getattr(item, "my_response_type", None)
        if response_type and str(response_type) == "Decline":
            log.debug("日历跳过 [Decline]: %s", subject)
            skipped += 1
            continue

        # EWSDate 类型没有 astimezone 方法（常见于全天事件或跨天事件）
        s = item.start.astimezone(tz) if item.start and hasattr(item.start, "astimezone") else None
        e = item.end.astimezone(tz) if item.end and hasattr(item.end, "astimezone") else None

        if item.is_all_day or (s is None and item.start):
            events.append(
                {
                    "subject": item.subject or "(无标题)",
                    "start_time": None,
                    "end_time": None,
                    "duration_min": 0,
                    "is_all_day": 1,
                    "location": item.location or "",
                    "organizer": str(item.organizer) if item.organizer else "",
                    "is_recurring": 1 if hasattr(item, "is_recurring") and item.is_recurring else 0,
                }
            )
        elif s and e:
            # 截断到查询当天的有效区间，避免跨天事件被误算为当天 22:00~24:00
            effective_s = max(s, start)
            effective_e = min(e, end)
            if effective_s >= effective_e:
                continue
            duration_min = int((effective_e - effective_s).total_seconds() / 60)
            events.append(
                {
                    "subject": item.subject or "(无标题)",
                    "start_time": effective_s.strftime("%H:%M"),
                    "end_time": effective_e.strftime("%H:%M"),
                    "duration_min": duration_min,
                    "is_all_day": 0,
                    "location": item.location or "",
                    "organizer": str(item.organizer) if item.organizer else "",
                    "is_recurring": 1 if hasattr(item, "is_recurring") and item.is_recurring else 0,
                }
            )

    # 去重：相同主题+开始时间+结束时间的会议（防止邮件组重复邀请导致同一会议出现两次）
    seen = set()
    unique_events = []
    for ev in events:
        key = (ev["subject"], ev.get("start_time"), ev.get("end_time"))
        if key not in seen:
            seen.add(key)
            unique_events.append(ev)

    if skipped or len(unique_events) != len(events):
        log.info(
            "Exchange 原始 %d 条，跳过 %d 条，去重 %d 条，最终 %d 条",
            len(events) + skipped,
            skipped,
            len(events) - len(unique_events),
            len(unique_events),
        )

    return unique_events


def fetch_calendar(date_str: str, db_path: Path = DEFAULT_DB_PATH) -> Optional[CalendarSummary]:
    """获取指定日期的日历摘要。

    策略：
    - 今天及以前：先查 DB 缓存，有数据则直接返回（历史数据不会变）
    - 明天及以后：始终从 Exchange 重新拉取（未来日程可能随时变动）
    - 无缓存/配置不完整/连接失败时返回 None（降级）
    """

    target_day = date.fromisoformat(date_str)
    is_past_or_today = target_day <= date.today()

    # 1. 历史/今天：尝试从 DB 读取缓存
    if is_past_or_today:
        try:
            with db.get_conn(db_path) as conn:
                rows = db.query_calendar_events(conn, date_str)
                if rows:
                    log.info("日历缓存命中: %s", date_str)
                    return _build_summary(date_str, rows)
        except Exception as e:
            log.warning("读取日历缓存失败: %s", e)

    # 2. 从 Exchange 拉取（未来日期总是重新拉；历史日期无缓存时也拉）
    cfg = load_config().outlook
    if not cfg.is_complete():
        log.info("Outlook 配置不完整，跳过日历采集")
        return None

    try:
        events = _fetch_from_exchange(date_str, cfg)
        log.info("Exchange 拉取完成: %s, %d 个事件", date_str, len(events))
    except Exception as e:
        log.warning("Exchange 日历拉取失败: %s", e)
        return None

    # 3. 写入 DB（覆盖旧缓存）
    try:
        with db.get_conn(db_path) as conn:
            db.insert_calendar_events(conn, date=date_str, events=events)
    except Exception as e:
        log.warning("日历缓存写入失败: %s", e)

    return _build_summary(date_str, events)
