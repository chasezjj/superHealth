"""P6: 报告导出 — 生成复诊 PDF。"""

from __future__ import annotations

import io
from datetime import date, timedelta

import streamlit as st

from superhealth.dashboard.components.charts import chart_bp, chart_hrv_bb, chart_weight_fat
from superhealth.dashboard.data_loader import (
    get_latest_ai_summary,
    load_daily_health,
    load_lab_results,
    load_vitals,
)


def _fig_to_png_bytes(fig) -> bytes:
    """Plotly 图转 PNG bytes（需要 kaleido）。"""
    return fig.to_image(format="png", width=800, height=300)


def _build_pdf(
    date_start: date,
    date_end: date,
    include_bp: bool,
    include_weight: bool,
    include_hrv: bool,
    include_lab: bool,
    include_ai: bool,
    patient_name: str,
    hospital: str,
) -> bytes:
    import os

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    # 注册中文字体（使用系统字体）
    font_paths = [
        # Linux（wqy-zenhei 同时支持中英文和数字）
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode MS.ttf",
    ]
    font_registered = False
    for fp in font_paths:
        if os.path.exists(fp):
            try:
                pdfmetrics.registerFont(TTFont("CJK", fp))
                font_registered = True
                break
            except Exception:
                continue

    font_name = "CJK" if font_registered else "Helvetica"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )
    cjk_normal = ParagraphStyle("cjk", fontName=font_name, fontSize=10, leading=16)
    cjk_title = ParagraphStyle("cjkt", fontName=font_name, fontSize=18, leading=24, spaceAfter=8)
    cjk_h2 = ParagraphStyle(
        "cjkh2",
        fontName=font_name,
        fontSize=13,
        leading=20,
        spaceAfter=6,
        textColor=colors.darkblue,
    )

    story = []

    # 封面
    story.append(Paragraph("个人健康复诊报告", cjk_title))
    story.append(Paragraph(f"姓名：{patient_name}", cjk_normal))
    story.append(Paragraph(f"报告日期：{date.today()}", cjk_normal))
    story.append(Paragraph(f"数据范围：{date_start} ~ {date_end}", cjk_normal))
    story.append(Paragraph(f"就诊医院：{hospital}", cjk_normal))
    story.append(Spacer(1, 0.5 * cm))

    days = (date_end - date_start).days + 1

    # 趋势图
    df_vitals = load_vitals(days)
    df_dh = load_daily_health(days)

    if include_bp and not df_vitals.empty and df_vitals["systolic"].notna().any():
        story.append(Paragraph("血压趋势", cjk_h2))
        try:
            png = _fig_to_png_bytes(chart_bp(df_vitals))
            story.append(Image(io.BytesIO(png), width=16 * cm, height=6 * cm))
        except Exception:
            story.append(Paragraph("（图表生成失败，请确认已安装 kaleido）", cjk_normal))
        story.append(Spacer(1, 0.3 * cm))

    if include_weight and not df_vitals.empty and df_vitals["weight_kg"].notna().any():
        story.append(Paragraph("体重趋势", cjk_h2))
        try:
            png = _fig_to_png_bytes(chart_weight_fat(df_vitals))
            story.append(Image(io.BytesIO(png), width=16 * cm, height=6 * cm))
        except Exception:
            story.append(Paragraph("（图表生成失败）", cjk_normal))
        story.append(Spacer(1, 0.3 * cm))

    if include_hrv and not df_dh.empty:
        story.append(Paragraph("心率变异 & 身体电量趋势", cjk_h2))
        try:
            png = _fig_to_png_bytes(chart_hrv_bb(df_dh))
            story.append(Image(io.BytesIO(png), width=16 * cm, height=6 * cm))
        except Exception:
            story.append(Paragraph("（图表生成失败）", cjk_normal))
        story.append(Spacer(1, 0.3 * cm))

    # 化验对比表（近3次）
    if include_lab:
        story.append(Paragraph("近期化验结果", cjk_h2))
        df_lab = load_lab_results()
        KEY_ITEMS = [
            ("尿酸", "UA"),
            ("肌酐", "Cr(E)"),
            ("甘油三酯", "TG"),
            ("低密度脂蛋白胆固醇", "LDL-C"),
            ("丙氨酸氨基转移酶", "ALT"),
            ("天冬氨酸氨基转移酶", "AST"),
        ]
        table_data = [["指标", "第3次", "第2次", "最新值", "单位", "参考范围"]]
        for item_name, item_code in KEY_ITEMS:
            mask = df_lab["item_name"].str.contains(item_name, case=False, na=False) | (
                df_lab["item_code"].str.upper() == item_code.upper()
            )
            sub = df_lab[mask].sort_values("date").tail(3)
            vals = sub["value"].tolist()
            while len(vals) < 3:
                vals.insert(0, "—")
            unit = sub["unit"].iloc[-1] if not sub.empty and "unit" in sub.columns else ""
            ref = ""
            if not sub.empty:
                rl = sub["ref_low"].iloc[-1]
                rh = sub["ref_high"].iloc[-1]
                if rl is not None and rh is not None:
                    ref = f"{rl}~{rh}"
            row = [item_name] + [str(v) if v != "—" else "—" for v in vals] + [unit, ref]
            table_data.append(row)

        tbl = Table(table_data, colWidths=[3.5 * cm, 2.5 * cm, 2.5 * cm, 2.5 * cm, 2 * cm, 3 * cm])
        tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightblue),
                    ("FONTNAME", (0, 0), (-1, -1), font_name),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.lightyellow]),
                ]
            )
        )
        story.append(tbl)
        story.append(Spacer(1, 0.3 * cm))

    # AI 摘要
    if include_ai:
        story.append(Paragraph("AI 健康建议摘要", cjk_h2))
        ai_text = get_latest_ai_summary(max_chars=500)
        story.append(Paragraph(ai_text, cjk_normal))

    doc.build(story)
    return buf.getvalue()


def render():
    st.header("报告导出")
    st.caption("生成复诊 PDF，包含趋势图表和化验对比。")

    with st.form("export_form"):
        col1, col2 = st.columns(2)
        with col1:
            patient_name = st.text_input("姓名", value="本人")
            hospital = st.text_input("就诊医院", value="")
        with col2:
            date_start = st.date_input("数据开始日期", value=date.today() - timedelta(days=90))
            date_end = st.date_input("数据截止日期", value=date.today())

        st.markdown("**选择报告章节**")
        cc1, cc2, cc3 = st.columns(3)
        with cc1:
            include_bp = st.checkbox("血压趋势图", value=True)
            include_weight = st.checkbox("体重趋势图", value=True)
        with cc2:
            include_hrv = st.checkbox("心率变异 & 身体电量", value=True)
            include_lab = st.checkbox("化验结果对比表", value=True)
        with cc3:
            include_ai = st.checkbox("AI 建议摘要", value=True)

        submitted = st.form_submit_button("生成 PDF")

    if submitted:
        with st.spinner("生成 PDF 中，请稍候…"):
            try:
                pdf_bytes = _build_pdf(
                    date_start=date_start,
                    date_end=date_end,
                    include_bp=include_bp,
                    include_weight=include_weight,
                    include_hrv=include_hrv,
                    include_lab=include_lab,
                    include_ai=include_ai,
                    patient_name=patient_name,
                    hospital=hospital,
                )
                filename = f"health_report_{date.today()}.pdf"
                st.download_button(
                    label="下载 PDF 报告",
                    data=pdf_bytes,
                    file_name=filename,
                    mime="application/pdf",
                )
                st.success(f"PDF 已生成：{filename}")
            except ImportError as e:
                st.error(f"缺少依赖：{e}\n请运行 `pip install reportlab kaleido`")
            except Exception as e:
                st.error(f"生成失败：{e}")
