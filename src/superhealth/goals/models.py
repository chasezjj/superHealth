"""阶段性目标 Pydantic 模型。"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class Goal(BaseModel):
    """阶段性目标。"""

    id: Optional[int] = None
    name: str
    description: Optional[str] = None
    status: str = "active"
    metric_key: str
    direction: str = Field(description="decrease/increase/stabilize")
    baseline_value: Optional[float] = None
    target_value: Optional[float] = None
    start_date: str
    target_date: Optional[str] = None
    achieved_date: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class GoalProgress(BaseModel):
    """每日目标进度快照。"""

    id: Optional[int] = None
    goal_id: int
    date: str
    current_value: Optional[float] = None
    delta_from_baseline: Optional[float] = None
    progress_pct: Optional[float] = None
    note: Optional[str] = None


VALID_STATUSES = {"active", "off_track", "achieved", "paused", "abandoned", "superseded"}
VALID_DIRECTIONS = {"decrease", "increase", "stabilize"}
