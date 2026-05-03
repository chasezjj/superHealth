"""Tests for shared LLM advisor prompt construction."""

from superhealth.core.health_profile_builder import HealthProfile
from superhealth.core.llm_advisor import BaseHealthAdvisor


class DummyAdvisor(BaseHealthAdvisor):
    def __init__(self):
        self.last_user_prompt = ""

    def _is_config_complete(self) -> bool:
        return True

    def _call_api(self, system_prompt: str, user_prompt: str) -> str:
        self.last_user_prompt = user_prompt
        return (
            '{"summary":"ok","recommendation_type":"rest",'
            '"exercise":{},"recovery":{"needed":true,"actions":[]},'
            '"lifestyle":[],"risk_alerts":[]}'
        )


def test_advise_injects_user_context_at_top_of_prompt():
    advisor = DummyAdvisor()

    advisor.advise(
        daily_data={"sleep_score": 80},
        profile=HealthProfile(),
        guide_keys=[],
        reference_date="2025-04-01",
        user_context="今天晚上有聚餐，不方便剧烈运动",
    )

    prompt_lines = advisor.last_user_prompt.splitlines()
    assert prompt_lines[0] == "【用户特别说明 — 最高优先级】"
    assert prompt_lines[1] == "今天晚上有聚餐，不方便剧烈运动"
    assert "优先级高于运动多样性、天气、日历" in prompt_lines[2]
    assert "今日日期：2025-04-01" in prompt_lines[4]


def test_build_user_prompt_omits_empty_user_context_block():
    advisor = DummyAdvisor()

    prompt = advisor.build_user_prompt(
        daily_data={"sleep_score": 80},
        reference_date="2025-04-01",
        is_weekday=True,
        user_context="  ",
    )

    assert "【用户特别说明 — 最高优先级】" not in prompt
    assert prompt.startswith("今日日期：2025-04-01")
