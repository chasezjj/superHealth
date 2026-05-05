"""医疗文档上传页：PDF / 图片 → Claude 提取 → 用户确认 → 落盘 markdown + SQLite。"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import streamlit as st

# 项目根目录：src/superhealth/dashboard/views/ → 上四级
_PROJECT_ROOT = Path(__file__).parents[4]
_DATA_ROOT = _PROJECT_ROOT / "data"

DOC_TYPE_LABELS = {
    "auto":           "自动识别",
    "annual_checkup": "年度体检",
    "lab":            "门诊化验单",
    "outpatient":     "门诊病历",
    "imaging":        "影像/超声报告",
    "discharge":      "出院小结",
    "genetic":        "基因检测报告",
    "other":          "其他",
}

DOC_TYPE_FOLDER = {
    "genetic":        "genetic-data",
    "annual_checkup": "checkup-reports",
    "lab":            "medical-records/lab",
    "outpatient":     "medical-records/outpatient",
    "imaging":        "medical-records/imaging",
    "discharge":      "medical-records/discharge",
    "other":          "medical-records/other",
}

ACCEPTED_MIME = ["application/pdf", "image/png", "image/jpeg", "image/webp", "image/gif"]
DOCUMENT_EXTRACT_MAX_TOKENS = 8192


# ── helpers ─────────────────────────────────────────────────────────────────

def _slug(text: str) -> str:
    text = re.sub(r"[^\w一-鿿]+", "-", text).strip("-")
    return text[:40] if text else "document"


def _resolve_markdown_path(doc_date: str, doc_type: str, title: str) -> Path:
    folder = DOC_TYPE_FOLDER.get(doc_type, "medical-records/other")
    target_dir = _DATA_ROOT / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    date_prefix = doc_date or datetime.today().strftime("%Y-%m-%d")
    slug = _slug(title or doc_type)
    return target_dir / f"{date_prefix}-{slug}.md"


def _save_original(filename: str, data: bytes) -> str:
    """保存原始文件到 data/uploads/YYYY/filename，返回相对路径字符串。"""
    year = datetime.today().strftime("%Y")
    dest = _DATA_ROOT / "uploads" / year
    dest.mkdir(parents=True, exist_ok=True)
    # 避免覆盖同名文件
    target = dest / filename
    counter = 1
    while target.exists():
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        target = dest / f"{stem}_{counter}{suffix}"
        counter += 1
    target.write_bytes(data)
    return str(target.relative_to(_PROJECT_ROOT))


def _file_bytes(uploaded_file) -> bytes:
    """Read uploaded file bytes without leaving the stream at EOF."""
    if hasattr(uploaded_file, "getvalue"):
        return cast(bytes, uploaded_file.getvalue())
    try:
        pos = uploaded_file.tell()
    except Exception:
        pos = None
    data = uploaded_file.read()
    if pos is not None:
        uploaded_file.seek(pos)
    return cast(bytes, data)


def _uploaded_files_hash(uploaded_files) -> str:
    """Build a stable SHA-256 fingerprint for one upload batch."""
    return _bytes_batch_hash([_file_bytes(f) for f in uploaded_files])


def _bytes_batch_hash(files_data: list[bytes]) -> str:
    """Build a stable SHA-256 fingerprint for one or more files."""
    digest = hashlib.sha256()
    for data in files_data:
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _find_existing_upload(file_hash: str) -> dict[str, Any] | None:
    from superhealth.database import get_conn, query_medical_document_by_file_hash

    if not file_hash:
        return None
    with get_conn() as conn:
        existing = query_medical_document_by_file_hash(conn, file_hash)
        if existing:
            return existing

        rows = conn.execute(
            """SELECT * FROM medical_documents
               WHERE file_hash IS NULL
                 AND original_path IS NOT NULL
                 AND original_path != ''
               ORDER BY id DESC"""
        ).fetchall()
        for row in rows:
            paths = [p.strip() for p in str(row["original_path"]).split(";") if p.strip()]
            try:
                files_data = [(_PROJECT_ROOT / p).read_bytes() for p in paths]
            except OSError:
                continue
            if _bytes_batch_hash(files_data) != file_hash:
                continue
            try:
                conn.execute(
                    "UPDATE medical_documents SET file_hash = ? WHERE id = ?",
                    (file_hash, row["id"]),
                )
            except sqlite3.IntegrityError:
                pass
            return query_medical_document_by_file_hash(conn, file_hash) or dict(row)
    return None


def _build_markdown(result, doc_date: str, obs_rows: list[dict]) -> str:
    lines: list[str] = []
    title = result.get("title") or DOC_TYPE_LABELS.get(result.get("doc_type", ""), "文档")
    lines.append(f"# {title}")
    lines.append("")
    meta = [
        ("日期", doc_date),
        ("文档类型", DOC_TYPE_LABELS.get(result.get("doc_type", ""), result.get("doc_type", ""))),
        ("医院/机构", result.get("institution", "")),
        ("科室", result.get("department", "")),
        ("医生", result.get("doctor", "")),
    ]
    for k, v in meta:
        if v:
            lines.append(f"**{k}**：{v}")
    lines.append("")

    # 观测项表格
    valid_rows = [r for r in obs_rows if r.get("item_name")]
    if valid_rows:
        lines.append("## 检查项目")
        lines.append("")
        lines.append("| 项目 | 数值 | 单位 | 参考范围 | 异常 |")
        lines.append("|------|------|------|---------|------|")
        for row in valid_rows:
            val = str(row.get("value_num", "") or row.get("value_text", "") or "")
            unit = row.get("unit", "") or ""
            lo = row.get("ref_low", "") or ""
            hi = row.get("ref_high", "") or ""
            ref = f"{lo}–{hi}" if lo or hi else ""
            abnormal = "⚠️" if row.get("is_abnormal") else ""
            lines.append(
                f"| {row['item_name']} | {val} | {unit} | {ref} | {abnormal} |"
            )
        lines.append("")

    # LLM 摘要（如果提取到）
    summary = result.get("markdown_summary", "")
    if summary:
        lines.append("## 原文摘要")
        lines.append("")
        lines.append(summary)
        lines.append("")

    return "\n".join(lines)


def _has_extracted_content(result: Any) -> bool:
    empty_markers = (
        "未检测到文档内容",
        "无法识别文档内容",
        "没有检测到文档内容",
        "未识别到文档内容",
        "未提供可识别的医学文档内容",
        "no document content",
        "no readable content",
    )
    summary = str(result.markdown_summary or "").strip()
    summary_is_empty = not summary or any(marker.lower() in summary.lower() for marker in empty_markers)
    return bool(
        result.observations
        or result.conditions_inferred
        or not summary_is_empty
    )


# ── main render ─────────────────────────────────────────────────────────────

def render() -> None:
    st.title("文档上传")
    st.caption("支持 PDF、PNG、JPG、WEBP —— 用 AI 自动提取内容，确认后保存到数据库和 Markdown 文件")

    # ── 步骤 1：上传文件 + 选类型 ─────────────────────────────────
    st.subheader("上传文件")
    with st.container(border=True):
        col_upload, col_type = st.columns([3, 1])
        with col_upload:
            uploaded = st.file_uploader(
                "选择文件（可多选，同一份报告分多页时一次全选；尽量将一次门诊的资料放在一起，比如一次高血压门诊，尽量把病历、化验报告、超声放一起上传）",
                type=["pdf", "png", "jpg", "jpeg", "webp", "gif"],
                accept_multiple_files=True,
                key="upload_files",
            )
        with col_type:
            doc_type_choice = st.selectbox(
                "文档类型",
                list(DOC_TYPE_LABELS.keys()),
                format_func=lambda k: DOC_TYPE_LABELS[k],
                key="doc_type_hint",
            )

    if not uploaded:
        st.info("请先上传文件")
        return

    upload_hash = _uploaded_files_hash(uploaded)
    existing_upload = _find_existing_upload(upload_hash)
    if existing_upload:
        st.warning(
            "这份文件已经导入过，本次不会重复保存。"
            f"\n\n- 已有记录：`#{existing_upload['id']} {existing_upload.get('title') or ''}`"
            f"\n- 报告日期：`{existing_upload.get('doc_date') or '未知'}`"
            f"\n- Markdown：`{existing_upload.get('markdown_path') or '无'}`"
        )
        return

    # ── 步骤 2：AI 提取（点击触发或 session_state 缓存） ────────────
    extract_key = "extracted_result"
    files_key = "uploaded_names"

    current_names = [f"{f.name}:{getattr(f, 'size', 0)}" for f in uploaded] + [upload_hash]
    if st.session_state.get(files_key) != current_names:
        # 文件变了，清旧结果
        st.session_state.pop(extract_key, None)
        st.session_state[files_key] = current_names

    if extract_key not in st.session_state:
        st.divider()
        st.subheader("AI 提取内容")
        if st.button("🤖 AI 提取内容", type="primary"):
            from superhealth.config import load as load_config
            from superhealth.core.document_extractor import (
                DocumentExtractor,
                resolve_document_model,
            )

            cfg = load_config()
            extractor = DocumentExtractor(cfg.claude)
            if not extractor.is_ready():
                st.error("Claude API key 未配置，请在系统配置页或环境变量 ANTHROPIC_API_KEY 中填写。")
                return

            files_data = [(f.name, f.read()) for f in uploaded]
            hint = None if doc_type_choice == "auto" else doc_type_choice

            model_name = resolve_document_model(cfg.claude) or "AI"
            file_count = len(files_data)
            total_mb = sum(len(data) for _, data in files_data) / 1024 / 1024
            progress = st.progress(0, text="准备提取文档…") if file_count > 1 else None
            spinner_text = (
                f"正在调用 {model_name} 分批提取 {file_count} 个文件（共 {total_mb:.1f} MB）……"
                if file_count > 1
                else f"正在调用 {model_name} 提取……（PDF 较大时需要约 15–30 秒）"
            )
            with st.spinner(spinner_text):
                try:
                    if file_count > 1:
                        def update_progress(done: int, total: int, names: list[str]) -> None:
                            if progress is None:
                                return
                            current = "、".join(names)
                            progress.progress(
                                done / total,
                                text=f"已提取 {done}/{total}：{current}",
                            )

                        result = extractor.extract_in_batches(
                            files_data,
                            hint_doc_type=hint,
                            max_tokens=DOCUMENT_EXTRACT_MAX_TOKENS,
                            files_per_request=1,
                            progress_callback=update_progress,
                        )
                    else:
                        result = extractor.extract(
                            files_data,
                            hint_doc_type=hint,
                            max_tokens=DOCUMENT_EXTRACT_MAX_TOKENS,
                        )
                    if not _has_extracted_content(result):
                        if progress:
                            progress.empty()
                        st.warning(
                            "AI 没有从这次上传中识别出可保存的医学内容。"
                            "请确认 PDF 不是加密/空白文件，扫描页是否清晰，或当前配置的模型/接口是否支持 PDF/图片识别。"
                        )
                        if cfg.claude.base_url:
                            st.caption(
                                f"当前使用自定义 Anthropic endpoint：`{cfg.claude.base_url}`\n\n"
                                "如果该接口不支持视觉或 PDF，可能会返回空结果。"
                            )
                        with st.expander("查看 AI 原始返回", expanded=False):
                            st.json(result.raw)
                        return
                    st.session_state[extract_key] = result
                    if progress:
                        progress.empty()
                    st.rerun()
                except Exception as e:
                    if progress:
                        progress.empty()
                    message = str(e)
                    if "413" in message or "Request Entity Too Large" in message:
                        st.error(
                            "提取失败：单个文件仍超过服务端请求体限制。"
                            "请先压缩 PDF/图片，或把这个文件拆成更小的文件后再上传。"
                        )
                        st.caption(message[:500])
                    else:
                        st.error(f"提取失败：{e}")
                    return
        return

    result = st.session_state[extract_key]
    raw = result.raw

    # ── 步骤 3：可编辑确认表单 ───────────────────────────────────
    st.divider()
    st.subheader("确认提取结果")

    col1, col2 = st.columns(2)
    with col1:
        doc_date = st.text_input("报告日期（YYYY-MM-DD）", value=result.doc_date, key="conf_date")
        final_doc_type = st.selectbox(
            "文档类型",
            [k for k in DOC_TYPE_LABELS if k != "auto"],
            format_func=lambda k: DOC_TYPE_LABELS[k],
            index=max(0, [k for k in DOC_TYPE_LABELS if k != "auto"].index(result.doc_type))
            if result.doc_type in DOC_TYPE_LABELS else 0,
            key="conf_type",
        )
    with col2:
        institution = st.text_input("医院/机构", value=result.institution, key="conf_inst")
        department = st.text_input("科室", value=result.department, key="conf_dept")
        doctor = st.text_input("医生", value=result.doctor, key="conf_doc")

    title = st.text_input(
        "报告标题（留空自动生成）",
        value=result.title or "",
        key="conf_title",
    )

    # ── 观测项表格（可编辑） ─────────────────────────────────────
    st.markdown("#### 检查项目（可直接修改、删除行）")

    import pandas as pd

    obs_df = pd.DataFrame(result.observations) if result.observations else pd.DataFrame(
        columns=["category", "item_name", "value_num", "value_text", "unit",
                 "ref_low", "ref_high", "is_abnormal", "body_site", "laterality", "note"]
    )
    # 确保列存在
    for col in ["category", "item_name", "value_num", "value_text", "unit",
                "ref_low", "ref_high", "is_abnormal", "body_site", "laterality", "note"]:
        if col not in obs_df.columns:
            obs_df[col] = None

    edited_df = st.data_editor(
        obs_df,
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "category": st.column_config.SelectboxColumn(
                "类别", options=["lab","vital","imaging","eye","ultrasound","ecg","genetic","other"]
            ),
            "is_abnormal": st.column_config.CheckboxColumn("异常"),
            "value_num": st.column_config.NumberColumn("数值"),
            "ref_low": st.column_config.NumberColumn("参考下限"),
            "ref_high": st.column_config.NumberColumn("参考上限"),
        },
        key="obs_editor",
    )
    obs_rows: list[dict] = edited_df.dropna(subset=["item_name"]).to_dict(orient="records")

    # ── 病情推断 ──────────────────────────────────────────────────
    if result.conditions_inferred:
        st.markdown("#### AI 推断病情（勾选需要录入数据库的）")
        cond_checks: list[dict[str, Any]] = []
        for i, cond in enumerate(result.conditions_inferred):
            checked = st.checkbox(
                f"{cond.get('name', '未知')}（{cond.get('status', 'active')}）",
                value=True,
                key=f"cond_{i}",
            )
            if checked:
                cond_checks.append(cond)
    else:
        cond_checks = []

    # ── Markdown 预览 ─────────────────────────────────────────────
    with st.expander("预览将保存的 Markdown", expanded=False):
        preview_raw = {**raw, "title": title, "doc_type": final_doc_type}
        md_preview = _build_markdown(preview_raw, doc_date, obs_rows)
        st.markdown(md_preview)

    # ── 确认保存 ──────────────────────────────────────────────────
    st.divider()
    if st.button("✅ 确认并保存", type="primary"):
        _save_confirmed(
            uploaded_files=uploaded,
            file_hash=upload_hash,
            doc_date=doc_date,
            doc_type=final_doc_type,
            institution=institution,
            department=department,
            doctor=doctor,
            title=title,
            obs_rows=obs_rows,
            conditions=cond_checks,
            extracted_json=raw,
        )


def _save_confirmed(
    *,
    uploaded_files,
    file_hash: str,
    doc_date: str,
    doc_type: str,
    institution: str,
    department: str,
    doctor: str,
    title: str,
    obs_rows: list[dict],
    conditions: list[dict],
    extracted_json: dict,
) -> None:
    from superhealth.database import (
        bulk_insert_observations,
        get_conn,
        insert_medical_document,
        upsert_medical_condition,
    )

    effective_title = title or f"{doc_date} {DOC_TYPE_LABELS.get(doc_type, doc_type)}"

    # 0. 判重：保存前再查一次，避免重复点击或多窗口并发。
    existing_upload = _find_existing_upload(file_hash)
    if existing_upload:
        st.warning(
            "这份文件已经导入过，本次没有重复保存。"
            f"\n\n- 已有记录：`#{existing_upload['id']} {existing_upload.get('title') or ''}`"
            f"\n- Markdown：`{existing_upload.get('markdown_path') or '无'}`"
        )
        return

    # 1. 保存原始文件
    original_paths: list[str] = []
    for f in uploaded_files:
        p = _save_original(f.name, _file_bytes(f))
        original_paths.append(p)
    original_path = "; ".join(original_paths) if original_paths else None

    # 2. 生成 markdown 并写盘
    preview_raw = {
        "title": effective_title,
        "doc_type": doc_type,
        "institution": institution,
        "department": department,
        "doctor": doctor,
        "markdown_summary": extracted_json.get("markdown_summary", ""),
    }
    md_content = _build_markdown(preview_raw, doc_date, obs_rows)
    md_path = _resolve_markdown_path(doc_date, doc_type, effective_title)
    md_path.write_text(md_content, encoding="utf-8")

    # 3. 写 SQLite
    with get_conn() as conn:
        doc_kwargs: dict[str, Any] = {
            "doc_date": doc_date,
            "doc_type": doc_type,
            "file_hash": file_hash,
            "markdown_path": str(md_path.relative_to(_PROJECT_ROOT)),
            "title": effective_title,
            "confirmed_at": datetime.now().isoformat(),
            "extracted_json": json.dumps(extracted_json, ensure_ascii=False),
        }
        if institution:
            doc_kwargs["institution"] = institution
        if department:
            doc_kwargs["department"] = department
        if doctor:
            doc_kwargs["doctor"] = doctor
        if original_path:
            doc_kwargs["original_path"] = original_path

        try:
            doc_id = insert_medical_document(conn, **doc_kwargs)
        except sqlite3.IntegrityError as e:
            if "file_hash" not in str(e):
                raise
            st.warning("这份文件已经导入过，本次没有重复保存。")
            return

        valid_obs = [r for r in obs_rows if r.get("item_name")]
        for obs in valid_obs:
            obs["document_id"] = doc_id
            obs["obs_date"] = doc_date
            if not obs.get("category"):
                obs["category"] = "other"
        if valid_obs:
            bulk_insert_observations(conn, valid_obs)

        for cond in conditions:
            name = cond.get("name", "").strip()
            if name:
                upsert_medical_condition(
                    conn,
                    name=name,
                    status=cond.get("status", "active"),
                    icd10_code=cond.get("icd10_code") or None,
                    source_document_id=doc_id,
                )

    # 4. 清 session 并提示
    st.session_state.pop("extracted_result", None)
    st.session_state.pop("uploaded_names", None)
    st.success(
        f"✅ 已保存！\n\n"
        f"- Markdown：`{md_path.relative_to(_PROJECT_ROOT)}`\n"
        f"- 原始文件：`{original_path or '（无）'}`\n"
        f"- 观测项：{len(valid_obs)} 条 | 病情：{len(conditions)} 条"
    )
    if conditions:
        st.info("病情已录入 `medical_conditions` 表，健康画像将在下次分析时读取。")
