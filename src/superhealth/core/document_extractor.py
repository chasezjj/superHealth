"""多模态医疗文档提取器：PDF / 图片 / 文本 → 结构化 JSON + Markdown 摘要。

调用 Anthropic Claude vision 接口，把扫描件、化验单、体检报告等内容拆成
medical_documents + medical_observations 两层 schema 对应的结构。
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, cast

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

DEFAULT_CLAUDE_VISION_MODEL = "claude-opus-4-7"

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
   - outpatient：门诊病历 / 门诊记录 / 诊断证明 / 处方 / 复诊记录等，以就诊诊断、病情记录、医嘱用药为主。
   - lab：独立检验/化验报告单；imaging：独立影像、超声、心电等检查报告。
   - 如果一份资料包含复诊、诊断、处方、医嘱、主诉、现病史、就诊记录等门诊流程线索，整体 doc_type 应为 outpatient；专科检查结果仍按内容填写 observation category。
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


def _is_custom_endpoint(cfg: ClaudeConfig) -> bool:
    base_url = (cfg.base_url or "").strip().lower()
    return bool(base_url and "api.anthropic.com" not in base_url)


def _extract_pdf_text(filename: str, file_bytes: bytes) -> str:
    """从文字型 PDF 本地抽取文本，供不支持 Anthropic document block 的接口使用。"""
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise ImportError("需要 pypdf 才能用第三方模型提取 PDF：pip install pypdf") from e

    try:
        reader = PdfReader(io.BytesIO(file_bytes))
    except Exception as e:
        raise ValueError(f"{filename} 不是可读取的 PDF，或文件已损坏/加密：{e}") from e

    pages: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as e:
            log.warning("PDF page text extraction failed: %s page %s: %s", filename, index, e)
            text = ""
        text = text.strip()
        if text:
            pages.append(f"--- 第 {index} 页 ---\n{text}")

    return "\n\n".join(pages).strip()


def _build_pdf_text_block(filename: str, file_bytes: bytes) -> dict[str, str]:
    text = _extract_pdf_text(filename, file_bytes)
    if not text:
        raise ValueError(
            f"{filename} 未提取到文字。该 PDF 可能是扫描件/图片型 PDF；"
            "请改传清晰图片，或使用支持 Anthropic PDF/vision document block 的模型。"
        )
    return {
        "type": "text",
        "text": f"以下是文件 {filename} 的 PDF 文本抽取结果：\n\n{text}",
    }


def _strip_code_fence(text: str) -> str:
    """去掉 ```json ... ``` 包装（即使提示词要求不加，也容错）。"""
    text = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    return text


def _extract_balanced_json_object(text: str) -> str:
    """从混有说明文字的返回中取第一个完整 JSON 对象。"""
    start = text.find("{")
    if start < 0:
        return text

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]

    return text[start:]


def _repair_common_json_issues(text: str) -> str:
    """修复 LLM 常见 JSON 瑕疵：尾随逗号、值与下个 key 之间漏逗号。"""
    repaired = text.strip()
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    repaired = re.sub(r'([}\]"0-9])\s+(?="[^"]+"\s*:)', r"\1, ", repaired)
    repaired = re.sub(r'([}\]])\s*(?=\{)', r"\1, ", repaired)
    return repaired


def _parse_json_payload(text: str) -> dict[str, Any]:
    cleaned = _strip_code_fence(text)
    candidates = [
        cleaned,
        _extract_balanced_json_object(cleaned),
    ]
    candidates.extend(_repair_common_json_issues(candidate) for candidate in list(candidates))

    last_error: json.JSONDecodeError | None = None
    for candidate in dict.fromkeys(candidates):
        try:
            payload = json.loads(candidate)
            if not isinstance(payload, dict):
                raise ValueError("LLM 返回 JSON 不是对象")
            return cast(dict[str, Any], payload)
        except json.JSONDecodeError as e:
            last_error = e

    error_text = str(last_error) if last_error else "unknown JSON error"
    raise ValueError(f"LLM 返回非 JSON：{error_text}\n原文片段：{cleaned[:500]}") from last_error


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


def _text_contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _infer_doc_type_from_payload(payload: dict[str, Any], fallback: str = "other") -> str:
    """Use conservative textual clues when the model leaves doc_type as other.

    This is intentionally biased only for strong document-type signals. It fixes
    common OCR/vision misses such as outpatient photos being parsed as generic
    medical content.
    """
    fallback = _normalize_doc_type(fallback)
    text_parts: list[str] = []
    for key in ("title", "department", "institution", "doctor", "markdown_summary"):
        value = payload.get(key)
        if value:
            text_parts.append(str(value))

    for obs in payload.get("observations") or []:
        if not isinstance(obs, dict):
            continue
        for key in ("category", "item_name", "value_text", "body_site", "note"):
            value = obs.get(key)
            if value:
                text_parts.append(str(value))

    for condition in payload.get("conditions_inferred") or []:
        if isinstance(condition, dict):
            text_parts.extend(str(v) for v in condition.values() if v)

    text = "\n".join(text_parts)
    if not text.strip():
        return fallback

    if _text_contains_any(text, ("出院", "入院", "住院", "discharge")):
        return "discharge"
    if _text_contains_any(text, ("基因", "genetic", "snp", "rsid")):
        return "genetic"
    if _text_contains_any(text, ("体检", "健康检查", "annual checkup", "check-up")):
        return "annual_checkup"

    lab_signal = _text_contains_any(text, ("检验报告", "化验单", "检验科", "样本号", "参考范围"))
    imaging_signal = _text_contains_any(text, ("影像报告", "超声报告", "b超", "心电图")) or bool(
        re.search(r"(?<![a-z0-9])(?:ct|mri)(?![a-z0-9])", text.lower())
    )
    outpatient_signal = _text_contains_any(
        text,
        (
            "门诊", "病历", "就诊", "诊断", "主诉", "现病史", "处方", "医嘱", "复诊",
            "初诊", "随诊", "随访", "复查", "处理意见", "治疗意见", "用药",
        ),
    )

    if outpatient_signal and not (lab_signal and not _text_contains_any(text, ("门诊", "就诊", "诊断", "医嘱", "处方"))):
        return "outpatient"
    if lab_signal:
        return "lab"
    if imaging_signal:
        return "imaging"
    return fallback


def _resolve_doc_type(payload: dict[str, Any], hint_doc_type: Optional[str] = None) -> str:
    if hint_doc_type and hint_doc_type != "auto":
        return _normalize_doc_type(hint_doc_type)
    normalized = _normalize_doc_type(str(payload.get("doc_type") or ""))
    if normalized != "other":
        return normalized
    return _infer_doc_type_from_payload(payload, fallback=normalized)


def resolve_document_model(cfg: ClaudeConfig) -> str:
    """选择文档/PDF 提取模型。

    兼容 Anthropic 协议的第三方接口通常只配置 model；如果 vision_model 仍是默认 Claude
    模型且 base_url 指向自定义服务，优先使用用户显式填写的 model。
    """
    model = (cfg.model or "").strip()
    vision_model = (cfg.vision_model or "").strip()
    if cfg.base_url and model and vision_model == DEFAULT_CLAUDE_VISION_MODEL:
        return model
    return vision_model or model


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

        use_pdf_text_fallback = _is_custom_endpoint(self.cfg)
        content_blocks: list[dict[str, Any]] = []
        for filename, data in files:
            file_path = Path(filename)
            if use_pdf_text_fallback and file_path.suffix.lower() == ".pdf":
                content_blocks.append(_build_pdf_text_block(filename, data))
            else:
                content_blocks.append(_build_content_block(file_path, data))

        instruction = "请按系统提示提取上述医学文档为 JSON。"
        if use_pdf_text_fallback:
            instruction += " PDF 已在本地转成文本；请只基于这些文本提取，不要臆测。"
        if hint_doc_type and hint_doc_type != "auto":
            instruction += f" 用户提示文档类型为: {hint_doc_type}。"
        content_blocks.append({"type": "text", "text": instruction})

        client = self._get_client()
        message = client.messages.create(
            model=resolve_document_model(self.cfg),
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
            doc_type=_resolve_doc_type(payload, hint_doc_type),
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

    def extract_in_batches(
        self,
        files: list[tuple[str, bytes]],
        *,
        hint_doc_type: Optional[str] = None,
        max_tokens: int = 4096,
        files_per_request: int = 1,
        progress_callback: Callable[[int, int, list[str]], None] | None = None,
    ) -> ExtractionResult:
        """分批提取多个文件，避免一次请求体过大触发代理 413。"""
        if not files:
            raise ValueError("至少需要一个文件")
        if files_per_request < 1:
            raise ValueError("files_per_request 必须大于等于 1")
        if len(files) <= files_per_request:
            result = self.extract(files, hint_doc_type=hint_doc_type, max_tokens=max_tokens)
            if progress_callback:
                progress_callback(len(files), len(files), [name for name, _ in files])
            return result

        extracted: list[tuple[list[str], ExtractionResult]] = []
        total = len(files)
        done = 0
        for start in range(0, total, files_per_request):
            batch = files[start:start + files_per_request]
            names = [name for name, _ in batch]
            result = self.extract(batch, hint_doc_type=hint_doc_type, max_tokens=max_tokens)
            extracted.append((names, result))
            done += len(batch)
            if progress_callback:
                progress_callback(done, total, names)

        return _merge_extraction_results(extracted, hint_doc_type=hint_doc_type)


def _single_common_value(values: list[str]) -> str:
    non_empty = {v.strip() for v in values if v and v.strip()}
    return next(iter(non_empty)) if len(non_empty) == 1 else ""


def _merge_extraction_results(
    extracted: list[tuple[list[str], ExtractionResult]],
    *,
    hint_doc_type: Optional[str] = None,
) -> ExtractionResult:
    if not extracted:
        raise ValueError("至少需要一个提取结果")
    if len(extracted) == 1:
        return extracted[0][1]

    files_count = sum(len(names) for names, _ in extracted)
    results = [result for _, result in extracted]
    observations: list[dict[str, Any]] = []
    conditions: list[dict[str, Any]] = []
    document_payloads: list[dict[str, Any]] = []
    summaries: list[str] = []

    for names, result in extracted:
        source_label = ", ".join(names)
        for obs in result.observations:
            item = dict(obs)
            note = str(item.get("note") or "").strip()
            source_note = f"来源文件：{source_label}"
            item["note"] = f"{note}；{source_note}" if note else source_note
            observations.append(item)

        for condition in result.conditions_inferred:
            conditions.append(dict(condition))

        summary = result.markdown_summary.strip() or result.title.strip()
        if summary:
            summaries.append(f"### {source_label}\n\n{summary}")

        document_payloads.append({
            "source_files": names,
            "doc_type": result.doc_type,
            "doc_date": result.doc_date,
            "institution": result.institution,
            "department": result.department,
            "doctor": result.doctor,
            "title": result.title,
            "observations": result.observations,
            "conditions_inferred": result.conditions_inferred,
            "markdown_summary": result.markdown_summary,
            "raw": result.raw,
        })

    title = f"批量上传 {files_count} 个文档"
    markdown_summary = "\n\n".join(summaries)
    raw = {
        "doc_type": _single_common_value([r.doc_type for r in results]) or "other",
        "doc_date": _single_common_value([r.doc_date for r in results]),
        "institution": _single_common_value([r.institution for r in results]),
        "department": _single_common_value([r.department for r in results]),
        "doctor": _single_common_value([r.doctor for r in results]),
        "title": title,
        "observations": observations,
        "conditions_inferred": conditions,
        "markdown_summary": markdown_summary,
        "documents": document_payloads,
        "batch_extraction": {"file_count": files_count},
    }
    doc_type = _resolve_doc_type(raw, hint_doc_type)
    raw["doc_type"] = doc_type
    doc_date = str(raw["doc_date"])
    institution = str(raw["institution"])
    department = str(raw["department"])
    doctor = str(raw["doctor"])

    return ExtractionResult(
        doc_type=doc_type,
        doc_date=doc_date,
        institution=institution,
        department=department,
        doctor=doctor,
        title=title,
        observations=observations,
        conditions_inferred=conditions,
        markdown_summary=markdown_summary,
        raw=raw,
    )
