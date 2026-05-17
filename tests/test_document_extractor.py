from __future__ import annotations

from superhealth.config import ClaudeConfig
from superhealth.core.document_extractor import (
    DocumentExtractor,
    ExtractionResult,
    _build_pdf_text_block,
    _infer_doc_type_from_payload,
    _merge_extraction_results,
    _parse_json_payload,
    resolve_document_model,
)
from superhealth.dashboard.views.upload import _has_extracted_content


class StubExtractor(DocumentExtractor):
    def __init__(self) -> None:
        pass

    def extract(self, files, *, hint_doc_type=None, max_tokens=4096):
        filename = files[0][0]
        return ExtractionResult(
            doc_type=hint_doc_type or "lab",
            doc_date="2026-05-01",
            institution="测试医院",
            department="检验科",
            doctor="",
            title=filename,
            observations=[
                {
                    "category": "lab",
                    "item_name": f"{filename} 项目",
                    "value_num": 1,
                    "is_abnormal": 0,
                }
            ],
            conditions_inferred=[],
            markdown_summary=f"摘要 {filename}",
            raw={"title": filename},
        )


def test_extract_in_batches_merges_results_and_source_notes():
    extractor = StubExtractor()
    progress_calls = []

    result = extractor.extract_in_batches(
        [("a.pdf", b"a"), ("b.pdf", b"b")],
        hint_doc_type="lab",
        files_per_request=1,
        progress_callback=lambda done, total, names: progress_calls.append((done, total, names)),
    )

    assert result.title == "批量上传 2 个文档"
    assert result.doc_type == "lab"
    assert len(result.observations) == 2
    assert result.observations[0]["note"] == "来源文件：a.pdf"
    assert result.observations[1]["note"] == "来源文件：b.pdf"
    assert "### a.pdf" in result.markdown_summary
    assert result.raw["batch_extraction"] == {"file_count": 2}
    assert progress_calls == [(1, 2, ["a.pdf"]), (2, 2, ["b.pdf"])]


def test_extract_in_batches_uses_user_doc_type_hint():
    extractor = StubExtractor()

    result = extractor.extract_in_batches(
        [("gene.pdf", b"gene")],
        hint_doc_type="genetic",
        files_per_request=1,
    )

    assert result.doc_type == "genetic"


def test_resolve_document_model_uses_custom_endpoint_model_when_vision_is_default():
    cfg = ClaudeConfig(
        model="deepseek-v4-pro",
        vision_model="claude-opus-4-7",
        base_url="https://api.deepseek.com/anthropic/v1",
    )

    assert resolve_document_model(cfg) == "deepseek-v4-pro"


def test_custom_endpoint_pdf_is_sent_as_extracted_text(monkeypatch):
    import superhealth.core.document_extractor as module

    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)

            class TextBlock:
                type = "text"
                text = (
                    '{"doc_type":"lab","doc_date":"","institution":"","department":"",'
                    '"doctor":"","title":"PDF","observations":[{"category":"lab",'
                    '"item_name":"尿酸","value_num":480,"unit":"umol/L"}],'
                    '"conditions_inferred":[],"markdown_summary":"尿酸 480 umol/L"}'
                )

            class Message:
                content = [TextBlock()]

            return Message()

    class FakeClient:
        messages = FakeMessages()

    class FakeExtractor(DocumentExtractor):
        def _get_client(self):
            return FakeClient()

    monkeypatch.setattr(module, "_extract_pdf_text", lambda filename, data: "尿酸 480 umol/L")
    extractor = FakeExtractor(
        ClaudeConfig(
            api_key="key",
            model="kimi-k2.6",
            vision_model="claude-opus-4-7",
            base_url="https://api.example.com/anthropic/v1",
        )
    )

    result = extractor.extract([("report.pdf", b"%PDF")])

    content = captured["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert "PDF 文本抽取结果" in content[0]["text"]
    assert all(block.get("type") != "document" for block in content)
    assert result.observations[0]["item_name"] == "尿酸"


def test_extract_uses_configured_document_timeout():
    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)

            class TextBlock:
                type = "text"
                text = (
                    '{"doc_type":"lab","doc_date":"","institution":"","department":"",'
                    '"doctor":"","title":"PDF","observations":[],'
                    '"conditions_inferred":[],"markdown_summary":"ok"}'
                )

            class Message:
                content = [TextBlock()]

            return Message()

    class FakeClient:
        messages = FakeMessages()

    class FakeExtractor(DocumentExtractor):
        def _get_client(self):
            return FakeClient()

    extractor = FakeExtractor(ClaudeConfig(api_key="key", document_timeout_seconds=600))

    extractor.extract([("report.png", b"image")])

    assert captured["timeout"] == 600


def test_extract_retries_transient_connection_errors(monkeypatch):
    import superhealth.core.document_extractor as module

    attempts = {"count": 0}
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)

    class FakeMessages:
        def create(self, **kwargs):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise RuntimeError("Connection error.")

            class TextBlock:
                type = "text"
                text = (
                    '{"doc_type":"lab","doc_date":"","institution":"","department":"",'
                    '"doctor":"","title":"PDF","observations":[],'
                    '"conditions_inferred":[],"markdown_summary":"ok"}'
                )

            class Message:
                content = [TextBlock()]

            return Message()

    class FakeClient:
        messages = FakeMessages()

    class FakeExtractor(DocumentExtractor):
        def _get_client(self):
            return FakeClient()

    extractor = FakeExtractor(ClaudeConfig(api_key="key"))

    extractor.extract([("report.png", b"image")])

    assert attempts["count"] == 3


def test_extract_wraps_connection_error_after_retries(monkeypatch):
    import superhealth.core.document_extractor as module

    attempts = {"count": 0}
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)

    class FakeMessages:
        def create(self, **kwargs):
            attempts["count"] += 1
            raise RuntimeError("Connection error.")

    class FakeClient:
        messages = FakeMessages()

    class FakeExtractor(DocumentExtractor):
        def _get_client(self):
            return FakeClient()

    extractor = FakeExtractor(ClaudeConfig(api_key="key"))

    try:
        extractor.extract([("report.png", b"image")])
    except RuntimeError as e:
        assert "已自动重试 3 次" in str(e)
        assert "Connection error" in str(e)
    else:
        raise AssertionError("expected connection error")
    assert attempts["count"] == 3


def test_pdf_text_block_rejects_scanned_pdf(monkeypatch):
    import superhealth.core.document_extractor as module

    monkeypatch.setattr(module, "_extract_pdf_text", lambda filename, data: "")

    try:
        _build_pdf_text_block("scan.pdf", b"%PDF")
    except ValueError as e:
        assert "扫描件/图片型 PDF" in str(e)
    else:
        raise AssertionError("expected scanned PDF error")


def test_parse_json_payload_repairs_missing_comma_before_key():
    payload = _parse_json_payload(
        '{"doc_type":"annual_checkup","title":"体检报告" "observations":[]}'
    )

    assert payload["title"] == "体检报告"
    assert payload["observations"] == []


def test_parse_json_payload_extracts_balanced_object_from_text():
    payload = _parse_json_payload(
        '说明文字 {"doc_type":"lab","observations":[{"item_name":"尿酸"}]} 后续文字'
    )

    assert payload["doc_type"] == "lab"
    assert payload["observations"][0]["item_name"] == "尿酸"


def test_extract_reclassifies_glaucoma_eye_clinic_as_outpatient():
    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)

            class TextBlock:
                type = "text"
                text = (
                    '{"doc_type":"other","doc_date":"2026-05-05","institution":"测试医院",'
                    '"department":"眼科","doctor":"王医生","title":"青光眼复诊",'
                    '"observations":[{"category":"eye","item_name":"眼压","value_num":18,"unit":"mmHg"}],'
                    '"conditions_inferred":[{"name":"青光眼","status":"active","icd10_code":"H40"}],'
                    '"markdown_summary":"眼科门诊复诊，诊断青光眼，医嘱继续用药。"}'
                )

            class Message:
                content = [TextBlock()]

            return Message()

    class FakeClient:
        messages = FakeMessages()

    class FakeExtractor(DocumentExtractor):
        def _get_client(self):
            return FakeClient()

    extractor = FakeExtractor(ClaudeConfig(api_key="key", vision_model="claude-opus-4-7"))

    result = extractor.extract([("clinic.jpg", b"img")])

    assert captured["model"] == "claude-opus-4-7"
    assert result.doc_type == "outpatient"


def test_eye_exam_terms_alone_do_not_reclassify_as_outpatient():
    payload = {
        "doc_type": "other",
        "department": "眼科",
        "title": "OCT 检查报告",
        "observations": [
            {"category": "eye", "item_name": "眼压", "value_num": 18, "unit": "mmHg"},
            {"category": "eye", "item_name": "视野", "value_text": "未见明显异常"},
        ],
        "conditions_inferred": [],
        "markdown_summary": "OCT、眼压、视野检查结果。",
    }

    assert _infer_doc_type_from_payload(payload) == "other"


def test_merge_reclassifies_mixed_glaucoma_pages_as_outpatient():
    first = ExtractionResult(
        doc_type="other",
        doc_date="2026-05-05",
        institution="测试医院",
        department="眼科",
        doctor="",
        title="门诊记录",
        observations=[
            {"category": "eye", "item_name": "眼压", "value_num": 18, "unit": "mmHg"}
        ],
        conditions_inferred=[],
        markdown_summary="眼科门诊，青光眼复诊。",
        raw={},
    )
    second = ExtractionResult(
        doc_type="other",
        doc_date="2026-05-05",
        institution="测试医院",
        department="眼科",
        doctor="",
        title="处方",
        observations=[],
        conditions_inferred=[{"name": "青光眼", "status": "active"}],
        markdown_summary="诊断青光眼，医嘱继续用药，定期复诊。",
        raw={},
    )

    result = _merge_extraction_results([(["page1.jpg"], first), (["page2.jpg"], second)])

    assert result.doc_type == "outpatient"


def test_has_extracted_content_rejects_empty_result():
    result = ExtractionResult(
        doc_type="other",
        doc_date="",
        institution="",
        department="",
        doctor="",
        title="",
        observations=[],
        conditions_inferred=[],
        markdown_summary="",
        raw={},
    )

    assert not _has_extracted_content(result)


def test_has_extracted_content_rejects_no_content_summary():
    result = ExtractionResult(
        doc_type="lab",
        doc_date="",
        institution="",
        department="",
        doctor="",
        title="门诊化验单",
        observations=[],
        conditions_inferred=[],
        markdown_summary="## 报告摘要\n未检测到文档内容。",
        raw={"markdown_summary": "## 报告摘要\n未检测到文档内容。"},
    )

    assert not _has_extracted_content(result)
