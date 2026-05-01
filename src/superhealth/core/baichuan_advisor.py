"""百川医疗大模型建议引擎：调用百川 Baichuan-M3-Plus 生成专业医学建议。

百川医疗大模型擅长：
- 慢病管理（高尿酸、青光眼、高血压）的临床指导
- 用药注意事项与药物相互作用
- 基于当日生理指标的就医/随访建议
- 风险预警（症状识别、何时需要就医）

API 格式：OpenAI 兼容（base_url = https://api.baichuan-ai.com/v1）
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from superhealth.config import load as load_config
from superhealth.core.llm_advisor import BaseHealthAdvisor

if TYPE_CHECKING:
    from superhealth.core.assessment_models import AssessmentResult
    from superhealth.core.health_profile_builder import HealthProfile

log = logging.getLogger(__name__)


class BaichuanMedicalAdvisor(BaseHealthAdvisor):
    """基于百川医疗大模型的专业医学建议引擎。"""

    def __init__(self, config_path: Path = None):
        cfg = load_config() if config_path is None else load_config(config_path)
        self.baichuan_cfg = cfg.baichuan
        self._client = None

    def _get_client(self):
        """延迟初始化 OpenAI 兼容客户端（指向百川 API）。"""
        if self._client is None:
            try:
                from openai import OpenAI

                self._client = OpenAI(
                    api_key=self.baichuan_cfg.api_key,
                    base_url=self.baichuan_cfg.base_url,
                )
            except ImportError:
                raise ImportError("需要 openai SDK：pip install openai")
        return self._client

    def build_system_prompt(
        self,
        guide_keys: list[str],
        profile: "HealthProfile",
        assessment_results: list["AssessmentResult"] = None,
    ) -> str:
        """在基类 prompt 基础上，追加百川医疗专属角色说明。"""
        base_prompt = super().build_system_prompt(guide_keys, profile, assessment_results)
        medical_role = (
            "\n\n## 你的专属角色（百川医疗大模型）\n"
            "你是百川医疗大模型，具备执业医师级别的临床知识。"
            "你的职责是**风险识别与医学提醒**，不负责运动处方和日常生活方式建议（由运动科学模型负责）。\n"
            "请专注于：\n"
            "1. 当前用药与今日生理状态的交互风险（如青光眼眼压控制效果）\n"
            "2. 慢病管理的临床预警（何时需要调整用药或提前复诊）\n"
            "3. 异常生理指标的医学意义（HRV 过低、血压波动等的临床解读）\n"
            "4. 基于症状/指标的就医时机判断\n"
            "风险提醒请简洁、具体、可操作，语言通俗易懂。"
        )
        return base_prompt + medical_role

    def _json_request_prompt(self) -> str:
        """百川版 JSON schema：只输出风险提醒，不输出运动处方和生活方式。"""
        return (
            "\n请根据以上今日状态和我的健康画像，从专业临床医学角度识别风险并给出提醒。"
            "运动处方和生活方式建议由其他模型负责，你只需输出风险与医学提醒。\n"
            "**重要约束**：\n"
            "1. 只输出今日数据中出现异常或值得关注的具体临床发现（如 SpO2 偏低、血压波动、HRV 异常等）。\n"
            "2. 不要重复已知慢病（高尿酸、青光眼）的通用注意事项——那些每天都一样、用户已熟知。\n"
            "3. 不要输出步数/活动量相关的提醒——活动量建议已由运动处方负责，不属于医学风险。\n"
            "4. 最多输出 3 条，每条 50 字以内，简洁可操作。\n"
            "5. 若今日数据无异常，可少于 3 条甚至输出空数组。\n"
            "请严格按照以下 JSON 格式回复（不要输出 JSON 以外的内容）：\n"
            "{\n"
            '  "risk_alerts": ["今日数据驱动的风险提醒，最多3条"]\n'
            "}"
        )

    def _is_config_complete(self) -> bool:
        return self.baichuan_cfg.is_complete()

    def _call_api(self, system_prompt: str, user_prompt: str) -> str:
        client = self._get_client()
        # 百川医疗 API 不支持 system role，将 system prompt 前置到首条 user 消息
        combined_user = f"{system_prompt}\n\n---\n\n{user_prompt}"
        response = client.chat.completions.create(
            model=self.baichuan_cfg.model,
            max_tokens=self.baichuan_cfg.max_tokens,
            messages=[
                {"role": "user", "content": combined_user},
            ],
        )
        return response.choices[0].message.content.strip()
