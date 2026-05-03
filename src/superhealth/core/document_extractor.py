"""多模态医疗文档提取器：PDF / 图片 / 文本 → 结构化 JSON + Markdown 摘要。

调用 Anthropic Claude vision 接口，把扫描件、化验单、体检报告等内容拆成
medical_documents + medical_observations 两层 schema 对应的结构。
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from superhealth.config import ClaudeConfig
from superhealth.config import load as load_config

log = logging.getLogger(__name__)

PDF_MEDIA_TYPE = "application/pdf"
IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

ALLOWED_DOC_TYPES = (
    "genetic", "annual_checkup", "outpatient", "imaging",
    "lab", "discharge", "other",
)
ALLOWED_CATEGORIES = (
    "lab", "vital", "imaging", "eye", "ultrasound", "ecg", "genetic", "other",
)

_SYSTEM_PROMPT = """你是医学文档结构化提取助手。任务：把用户上传的化验单 / 体检报告 / 门诊病历 / 影像报告 / 基因报告等医学文档逐项提取成严格 JSON。

规则：
1. 只输出一个 JSON 对象，不要多余文字、不要 markdown 代码栅栏。
2. 严格按文档原文抓取，**不做单位换算、不做临床推断**。识别不出的字段留空字符串或省略。
3. observations 数组中每行一个项目（化验项 / 眼压 / 肾长径 / 心电图结论 / 影像所见 ...）。
4. is_abnormal 仅当原单上有 ★ / ↑ / ↓ / "异常" / "偏高" / "偏低" 等明示标记时填 1，否则填 0。
5. 数值型放 value_num，文字结论放 value_text；两者可同时为空（仅有项目名时也保留）。
6. category 取值固定枚举：lab | vital | imaging | eye | ultrasound | ecg | genetic | other。
7. body_site 解剖位置（eye / kidney / thyroid / liver / lung / heart / breast / prostate / abdomen / brain / spine / 其他英文 slug 或留空）。
8. laterality: left | right | bilateral 或留空。
9. doc_type 取值固定枚举：genetic | annual_checkup | outpatient | imaging | lab | discharge | other。
10. markdown_summary 用规范 markdown 重排原文要点，便于人阅读，保留所有关键数值与结论。
11. conditions_inferred 仅当报告中明确写明诊断/印象时填入，避免"推断"。

输出 schema：
{
  "doc_type": "lab",
  "doc_date": "YYYY-MM-DD",
  "institution": "...",
  "department": "...",
  "doctor": "...",
  "title": "...",
  "observations": [
    {"category":"lab","item_name":"尿酸","item_code":"UA","value_num":480,"value_text":"","unit":"μmol/L","ref_low":208,"ref_high":428,"is_abnormal":1,"body_site":"","laterality":"","note":""}
  ],
  "conditions_inferred": [
    {"name":"原发性开角型青光眼","status":"active","icd10_code":"H40.1"}
  ],
  "markdown_summary": "## 报告摘要\\n..."
}
"""


@dataclass
class ExtractionResult:
    """提取结果（已经过基本字段规范化，但保留原文）。"""

    doc_type: str
    doc_date: str
    institution: str
    department: str
    doctor: str
    title: str
    observations: list[dict[str, Any]]
    conditions_inferred: list[dict[str, Any]]
    markdown_summary: str
    raw: dict[str, Any]  # 原始 LLM JSON，便于落 extracted_json 列


def _detect_media_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return PDF_MEDIA_TYPE
    if suffix in IMAGE_MEDIA_TYPES:
        return IMAGE_MEDIA_TYPES[suffix]
    raise ValueError(f"不支持的文件类型: {suffix}（支持 PDF / PNG / JPG / JPEG / WEBP / GIF）")


def _build_content_block(file_path: Path, file_bytes: bytes) -> dict[str, Any]:
    """把单个文件转成 Claude 接受的 content block。"""
    media_type = _detect_media_type(file_path)
    b64 = base64.standard_b64encode(file_bytes).decode("ascii")
    if media_type == PDF_MEDIA_TYPE:
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        }
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": b64},
    }


def _strip_code_fence(text: str) -> str:
    """去掉 ```json ... ``` 包装（即使提示词要求不加，也容错）。"""
    text = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    return text


def _parse_json_payload(text: str) -> dict[str, Any]:
    cleaned = _strip_code_fence(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        # 尝试从文本中抓出第一个完整 JSON 对象
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise ValueError(f"LLM 返回非 JSON：{e}\n原文片段：{cleaned[:300]}") from e


def _normalize_observation(obs: dict[str, Any]) -> dict[str, Any]:
    """把 LLM 输出的 obs 字典裁剪到 schema 列；空字符串归一为 None。"""
    out: dict[str, Any] = {}
    for key in (
        "category", "item_name", "item_code", "body_site", "laterality",
        "value_num", "value_text", "unit", "ref_low", "ref_high",
        "is_abnormal", "note",
    ):
        val = obs.get(key)
        if val is None or (isinstance(val, str) and val.strip() == ""):
            continue
        out[key] = val
    if "category" not in out:
        out["category"] = "other"
    if out.get("category") not in ALLOWED_CATEGORIES:
        out["category"] = "other"
    if "item_name" not in out:
        return {}  # 没有项目名直接丢弃
    if "is_abnormal" not in out:
        out["is_abnormal"] = 0
    return out


def _normalize_doc_type(value: str | None) -> str:
    if not value:
        return "other"
    v = value.strip().lower()
    return v if v in ALLOWED_DOC_TYPES else "other"


class DocumentExtractor:
    """复用 ClaudeConfig，调用 vision 模型提取医学文档。"""

    def __init__(self, claude_cfg: ClaudeConfig | None = None):
        if claude_cfg is None:
            claude_cfg = load_config().claude
        self.cfg = claude_cfg
        self._client = None

    def is_ready(self) -> bool:
        return bool(self.cfg.api_key)

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as e:
                raise ImportError("需要 anthropic SDK：pip install anthropic") from e
            kwargs: dict[str, Any] = {"api_key": self.cfg.api_key}
            if self.cfg.base_url:
                kwargs["base_url"] = self.cfg.base_url
            self._client = anthropic.Anthropic(**kwargs)
        return self._client

    def extract(
        self,
        files: list[tuple[str, bytes]],
        *,
        hint_doc_type: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> ExtractionResult:
        """提取文档。

        Args:
            files: 列表，每项为 (filename, raw_bytes)。同一份报告可能拆成多张图片。
            hint_doc_type: 用户预选的 doc_type；'auto' 或 None 让模型判断。
        """
        if not files:
            raise ValueError("至少需要一个文件")
        if not self.is_ready():
            raise RuntimeError(
                "Claude API key 未配置：请设置 ANTHROPIC_API_KEY 或在 ~/.superhealth/config.toml 的 [claude] 段填写 api_key"
            )

        content_blocks: list[dict[str, Any]] = []
        for filename, data in files:
            content_blocks.append(_build_content_block(Path(filename), data))

        instruction = "请按系统提示提取上述医学文档为 JSON。"
        if hint_doc_type and hint_doc_type != "auto":
            instruction += f" 用户提示文档类型为: {hint_doc_type}。"
        content_blocks.append({"type": "text", "text": instruction})

        client = self._get_client()
        message = client.messages.create(
            model=self.cfg.vision_model or self.cfg.model,
            max_tokens=max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content_blocks}],
        )
        text = next(b.text for b in message.content if b.type == "text")
        payload = _parse_json_payload(text)

        observations_raw = payload.get("observations") or []
        observations = [
            obs for obs in (
                _normalize_observation(o) for o in observations_raw if isinstance(o, dict)
            )
            if obs
        ]
        conditions = [c for c in (payload.get("conditions_inferred") or []) if isinstance(c, dict)]

        return ExtractionResult(
            doc_type=_normalize_doc_type(payload.get("doc_type") or hint_doc_type),
            doc_date=str(payload.get("doc_date") or "").strip(),
            institution=str(payload.get("institution") or "").strip(),
            department=str(payload.get("department") or "").strip(),
            doctor=str(payload.get("doctor") or "").strip(),
            title=str(payload.get("title") or "").strip(),
            observations=observations,
            conditions_inferred=conditions,
            markdown_summary=str(payload.get("markdown_summary") or "").strip(),
            raw=payload,
        )
