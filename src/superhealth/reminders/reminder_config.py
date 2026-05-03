"""就医提醒规则配置。

每条规则描述一种病情的复诊间隔和数据来源。
新增病情：在 REMINDER_RULES 列表末尾追加 ReminderRule 即可。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ReminderRule:
    condition: str  # 内部标识，如 'glaucoma'
    label: str  # 展示名称，如 '青光眼复查'
    hospital: Optional[str]  # 医院名
    department: Optional[str]  # 科室名
    interval_months: int  # 复诊间隔（月）
    source_table: str  # 查询最近一次记录的表名
    date_field: str  # 该表中日期字段名
    item_filter: dict = field(default_factory=dict)
    # item_filter 用于 medical_observations 等需按字段值过滤的场景
    # 例：{"item_name": "尿酸"} → WHERE item_name = '尿酸'


REMINDER_RULES: list[ReminderRule] = [
    ReminderRule(
        condition="glaucoma",
        label="青光眼复查",
        hospital=None,
        department=None,
        interval_months=3,
        source_table="medical_observations",
        date_field="obs_date",
        item_filter={"category": "eye"},
    ),
    ReminderRule(
        condition="hyperuricemia",
        label="高尿酸复诊",
        hospital=None,
        department=None,
        interval_months=6,
        source_table="medical_observations",
        date_field="obs_date",
        item_filter={"item_name": "尿酸"},
    ),
    ReminderRule(
        condition="annual_checkup",
        label="年度体检",
        hospital=None,
        department=None,
        interval_months=12,
        source_table="medical_documents",
        date_field="doc_date",
        item_filter={"doc_type": "annual_checkup"},
    ),
]
