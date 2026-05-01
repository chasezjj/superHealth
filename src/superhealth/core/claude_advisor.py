"""Claude 健康建议引擎：调用 Anthropic Claude API 生成个性化健康建议。"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from superhealth.config import load as load_config
from superhealth.core.llm_advisor import BaseHealthAdvisor

if TYPE_CHECKING:
    from superhealth.core.health_profile_builder import HealthProfile
    from superhealth.core.assessment_models import AssessmentResult

log = logging.getLogger(__name__)


class ClaudeHealthAdvisor(BaseHealthAdvisor):
    """基于 Anthropic Claude API 的健康建议引擎。"""

    def __init__(self, config_path: Path = None):
        cfg = load_config() if config_path is None else load_config(config_path)
        self.claude_cfg = cfg.claude
        self._client = None

    def _get_client(self):
        """延迟初始化 Anthropic 客户端。"""
        if self._client is None:
            try:
                import anthropic
                kwargs = {"api_key": self.claude_cfg.api_key}
                if self.claude_cfg.base_url:
                    kwargs["base_url"] = self.claude_cfg.base_url
                self._client = anthropic.Anthropic(**kwargs)
            except ImportError:
                raise ImportError("需要 anthropic SDK：pip install anthropic")
        return self._client

    def build_system_prompt(
        self,
        guide_keys: list[str],
        profile: "HealthProfile",
        assessment_results: list["AssessmentResult"] = None,
    ) -> str:
        """在基类 prompt 基础上，追加 Claude 专属角色说明。"""
        base_prompt = super().build_system_prompt(guide_keys, profile, assessment_results)
        exercise_role = (
            "\n\n## 你的专属角色\n"
            "你负责**运动处方和个性化生活方式建议**，这是你的核心职责。"
            "临床风险识别和医学提醒由专业医疗模型负责，你无需重复覆盖。\n"
            "请专注于：\n"
            "1. 基于今日生理状态（HRV/Body Battery/睡眠）制定精准运动处方\n"
            "2. 结合近7天运动历史判断偏好、疲劳积累和强度趋势\n"
            "3. 给出具体可执行的生活方式建议（营养时机、恢复节律、日常习惯）\n"
            "建议要具体、可操作，避免泛泛而谈。\n\n"
            "## 运动多样性强制规则（必须遵守）\n"
            "- 每周至少安排1-2天主动恢复（拉伸/瑜伽/泡沫轴/冥想）或完全休息，不可每天都推运动训练\n"
            "- 运动大类必须主动轮换：有氧 → 力量 → 柔韧/平衡 → 有氧，禁止连续2天停留在同一类别\n"
            "- 用户BMI<19接近偏瘦，力量训练（轻量/中等强度，每组12-15次）每周至少安排2次，不可长期仅推荐有氧运动\n"
            "- 如果近3天内已有2天及以上同类具体运动（如跑步/骑行/游泳等），今日必须推荐不同具体类型的运动或恢复训练\n"
            "- 若用户已连续2天以上进行同类具体运动，即使生理指标良好，也应优先推荐恢复或换类型\n"
            "- 禁止连续2天推荐完全相同的运动类型"
        )
        return base_prompt + exercise_role

    def _is_config_complete(self) -> bool:
        return self.claude_cfg.is_complete()

    def _call_api(self, system_prompt: str, user_prompt: str) -> str:
        client = self._get_client()
        message = client.messages.create(
            model=self.claude_cfg.model,
            max_tokens=self.claude_cfg.max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        # content 可能包含 ThinkingBlock + TextBlock，取第一个 TextBlock
        return next(b.text for b in message.content if b.type == "text").strip()
