"""阶段性目标子系统：目标存储、指标追踪、达成判定。"""

from superhealth.goals.manager import GoalManager
from superhealth.goals.metrics import GoalMetricRegistry

__all__ = ["GoalManager", "GoalMetricRegistry"]
