"""医疗文档上传页：PDF / 图片 → Claude 提取 → 用户确认 → 落盘 markdown + SQLite。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

# 项目根目录：src/superhealth/dashboard/views/ → 上四级
_PROJECT_ROOT = Path(__file__).parents[5]
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


# ── main render ─────────────────────────────────────────────────────────────

def render() -> None:
    st.title("文档上传")
    st.caption("支持 PDF、PNG、JPG、WEBP —— 用 AI 自动提取内容，确认后保存到数据库和 Markdown 文件")

    # ── 步骤 1：上传文件 + 选类型 ─────────────────────────────────
    with st.container(border=True):
        col_upload, col_type = st.columns([3, 1])
        with col_upload:
            uploaded = st.file_uploader(
                "选择文件（可多选，同一份报告分多页时一次全选）",
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

    # ── 步骤 2：AI 提取（点击触发或 session_state 缓存） ────────────
    extract_key = "extracted_result"
    files_key = "uploaded_names"

    current_names = [f.name for f in uploaded]
    if st.session_state.get(files_key) != current_names:
        # 文件变了，清旧结果
        st.session_state.pop(extract_key, None)
        st.session_state[files_key] = current_names

    if extract_key not in st.session_state:
        if st.button("🤖 AI 提取内容", type="primary"):
            from superhealth.config import load as load_config
            from superhealth.core.document_extractor import DocumentExtractor

            cfg = load_config()
            extractor = DocumentExtractor(cfg.claude)
            if not extractor.is_ready():
                st.error("Claude API key 未配置，请在系统配置页或环境变量 ANTHROPIC_API_KEY 中填写。")
                return

            files_data = [(f.name, f.read()) for f in uploaded]
            hint = None if doc_type_choice == "auto" else doc_type_choice

            with st.spinner("正在调用 Claude 提取……（PDF 较大时需要约 15–30 秒）"):
                try:
                    result = extractor.extract(files_data, hint_doc_type=hint)
                    st.session_state[extract_key] = result
                    st.rerun()
                except Exception as e:
                    st.error(f"提取失败：{e}")
                    return
        return

    result = st.session_state[extract_key]
    raw = result.raw

    # ── 步骤 3：可编辑确认表单 ───────────────────────────────────
    st.divider()
    st.subheader("② 确认提取结果")

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

    # 1. 保存原始文件
    original_paths: list[str] = []
    for f in uploaded_files:
        f.seek(0)
        p = _save_original(f.name, f.read())
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

        doc_id = insert_medical_document(conn, **doc_kwargs)

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
