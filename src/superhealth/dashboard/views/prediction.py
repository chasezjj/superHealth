"""P5: 预测分析。"""

from __future__ import annotations

from datetime import date

import streamlit as st

from superhealth.dashboard.components import disclaimer
from superhealth.dashboard.components.charts import chart_trend_prediction
from superhealth.dashboard.components.gauges import factor_bar_chart, risk_gauge, score_label
from superhealth.dashboard.data_loader import (
    get_upcoming_appointments,
    load_eye_exams,
    load_lab_results,
)


def _render_risk_card(result: dict, title: str, labels: list[str] | None = None):
    """通用风险卡片：仪表盘 + 因子图 + 文字。"""
    col1, col2 = st.columns([1, 1])
    with col1:
        st.plotly_chart(
            risk_gauge(result["score"], title),
            use_container_width=True,
        )
        status = result.get("status_label") or score_label(result["score"], labels)
        if result["score"] < 40:
            st.success(f"状态：**{status}**")
        elif result["score"] < 70:
            st.warning(f"状态：**{status}**")
        else:
            st.error(f"状态：**{status}**")

    with col2:
        st.plotly_chart(
            factor_bar_chart(result["factors"], "各因子风险贡献"),
            width="stretch",
        )

    st.info(result["summary"])


def render():
    st.header("预测分析")

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
        ["尿酸风险", "高血压风险", "高血脂风险", "高血糖风险", "趋势预测", "就医时机"]
    )

    # ── Tab1：尿酸风险 ───────────────────────────────────────────────
    with tab1:
        st.subheader("尿酸风险评估（中国高尿酸血症与痛风诊疗指南 2019）")
        st.caption("基于尿酸分级 + 合并症 + 发作诱因的风险分层")
        with st.spinner("计算中…"):
            from superhealth.dashboard.prediction import uric_acid_risk

            ua_result = uric_acid_risk.compute()

        if ua_result.get("latest_ua") is None:
            st.info("暂无尿酸检测数据。补充尿酸化验数据后，系统将进行风险评估。")
        else:
            _render_risk_card(
                ua_result,
                "尿酸风险",
                labels=["低风险", "中等风险", "高风险", "极高风险"],
            )

            ua = ua_result["latest_ua"]
            st.caption(
                f"最新尿酸：{ua:.0f} μmol/L "
                f"（分级：{ua_result.get('ua_grade', '未知')}，"
                f"参考上限 420）"
            )

            comorbidities = ua_result.get("comorbidities", [])
            if comorbidities:
                st.markdown("**合并症：**")
                for c in comorbidities:
                    st.markdown(f"- {c}")

            triggers = ua_result.get("triggers", [])
            if triggers:
                st.markdown("**发作诱因：**")
                for t in triggers:
                    st.markdown(f"- {t}")

    # ── Tab2：高血压风险 ─────────────────────────────────────────────
    with tab2:
        st.subheader("高血压风险评估（中国高血压防治指南 2024）")
        st.caption("基于血压分级 + 危险因素 + 靶器官损害 + 合并症的风险分层")
        with st.spinner("计算中…"):
            from superhealth.dashboard.prediction import hypertension_risk

            ht_result = hypertension_risk.compute()

        sbp = ht_result.get("latest_systolic")
        dbp = ht_result.get("latest_diastolic")
        if sbp is None and dbp is None:
            st.info("暂无血压测量数据。补充血压数据后，系统将进行风险评估。")
        else:
            _render_risk_card(
                ht_result,
                "高血压风险",
                labels=["低危", "中危", "高危", "极高危"],
            )

            sbp_str = f"{sbp:.0f}" if sbp else "未知"
            dbp_str = f"{dbp:.0f}" if dbp else "未知"
            st.caption(
                f"最新血压：{sbp_str}/{dbp_str} mmHg （分级：{ht_result.get('bp_grade', '未知')}）"
            )

            risk_factors = ht_result.get("risk_factors", [])
            if risk_factors:
                st.markdown("**命中的危险因素：**")
                for rf in risk_factors:
                    st.markdown(f"- {rf}")

            organ_damages = ht_result.get("organ_damages", [])
            if organ_damages:
                st.markdown("**靶器官损害：**")
                for od in organ_damages:
                    st.markdown(f"- {od}")

    # ── Tab3：高血脂风险 ─────────────────────────────────────────────
    with tab3:
        st.subheader("血脂风险评估（中国血脂管理指南 2023）")
        st.caption("基于 ASCVD 风险分层 → LDL-C 目标值 → 达标评估")
        with st.spinner("计算中…"):
            from superhealth.dashboard.prediction import hyperlipidemia_risk

            hl_result = hyperlipidemia_risk.compute()

        ldl = hl_result.get("latest_ldl")
        tg = hl_result.get("latest_tg")
        hdl = hl_result.get("latest_hdl")
        tc = hl_result.get("latest_tc")
        if ldl is None and tg is None and hdl is None and tc is None:
            st.info("暂无血脂检测数据。补充血脂化验数据（LDL-C、TG、HDL-C、TC）后，系统将进行风险评估。")
        else:
            _render_risk_card(
                hl_result,
                "血脂风险",
                labels=["低危", "中危", "高危", "极高危"],
            )

            target = hl_result.get("ldl_target")
            status = hl_result.get("ldl_status")
            if ldl is not None:
                st.caption(f"LDL-C：{ldl:.2f} mmol/L（目标 <{target}，{status}）")

            non_hdl = hl_result.get("non_hdl_c")
            parts = []
            if tg is not None:
                parts.append(f"TG {tg:.2f}（{hl_result.get('tg_grade', '未知')}）")
            if hdl is not None:
                parts.append(f"HDL-C {hdl:.2f}")
            if tc is not None:
                parts.append(f"TC {tc:.2f}")
            if non_hdl is not None:
                parts.append(f"非HDL-C {non_hdl:.2f}")
            if parts:
                st.caption(" | ".join(parts))

            risk_factors = hl_result.get("risk_factors", [])
            if risk_factors:
                st.markdown(f"**心血管危险因素（{len(risk_factors)}项）：**")
                for rf in risk_factors:
                    st.markdown(f"- {rf}")

    # ── Tab4：高血糖风险 ─────────────────────────────────────────────
    with tab4:
        st.subheader("高血糖风险评估（中国2型糖尿病防治指南 2024）")
        st.caption("基于血糖分级 + 危险因素 + 代谢综合征的风险分层")
        with st.spinner("计算中…"):
            from superhealth.dashboard.prediction import hyperglycemia_risk

            hg_result = hyperglycemia_risk.compute()

        fbg = hg_result.get("latest_fbg")
        hba1c = hg_result.get("latest_hba1c")
        if fbg is None and hba1c is None:
            st.info("暂无血糖检测数据。补充空腹血糖或 HbA1c 数据后，系统将进行风险评估。")
        else:
            _render_risk_card(
                hg_result,
                "高血糖风险",
                labels=["低风险", "中等风险", "高风险", "极高风险"],
            )

            glu_parts = []
            if fbg is not None:
                glu_parts.append(f"空腹血糖 {fbg:.2f} mmol/L")
            if hba1c is not None:
                glu_parts.append(f"HbA1c {hba1c:.1f}%")
            if glu_parts:
                st.caption(f"{' | '.join(glu_parts)}（{hg_result.get('glu_grade', '未知')}）")

            trend = hg_result.get("glu_trend")
            if trend:
                st.caption(f"血糖趋势：{trend}")

            risk_factors = hg_result.get("risk_factors", [])
            if risk_factors:
                st.markdown(f"**危险因素（{len(risk_factors)}项）：**")
                for rf in risk_factors:
                    st.markdown(f"- {rf}")

            met_syn = hg_result.get("metabolic_syndrome", [])
            if met_syn:
                st.markdown(f"**代谢综合征（{hg_result.get('met_syn_count', 0)}/5项）：**")
                for item in met_syn:
                    st.markdown(f"- {item}")

    # ── Tab5：趋势预测 ───────────────────────────────────────────────
    with tab5:
        st.subheader("未来7天趋势预测（线性外推）")
        st.caption("基于近14天数据的线性回归，仅供参考。")

        with st.spinner("计算中…"):
            from superhealth.dashboard.prediction import trend_predictor

            preds = trend_predictor.predict_all()

        configs = [
            ("hrv", "心率变异 7日预测", "ms"),
            ("weight", "体重 7日预测", "kg"),
            ("systolic", "收缩压 7日预测", "mmHg"),
            ("diastolic", "舒张压 7日预测", "mmHg"),
        ]
        for key, title, unit in configs:
            p = preds.get(key)
            if not p:
                st.caption(f"{title}：数据不足")
                continue
            fig = chart_trend_prediction(
                p["hist_dates"],
                p["hist_values"],
                p["pred_dates"],
                p["pred_values"],
                p["pred_upper"],
                p["pred_lower"],
                title=title,
                unit=unit,
            )
            st.plotly_chart(fig, width="stretch")

    # ── Tab6：就医时机推荐 ───────────────────────────────────────────
    with tab6:
        st.subheader("就医时机推荐")

        recommendations = []

        # 尿酸：>400 + 距复诊>4个月
        df_lab = load_lab_results()
        ua_df = df_lab[df_lab["item_name"].str.contains("尿酸|Uric Acid|UA", case=False, na=False)]
        if not ua_df.empty:
            latest_ua = ua_df["value"].iloc[-1]
            last_ua_date = (
                ua_df["date"].iloc[-1].date()
                if hasattr(ua_df["date"].iloc[-1], "date")
                else ua_df["date"].iloc[-1]
            )
            days_since_ua = (date.today() - last_ua_date).days
            if latest_ua > 400 and days_since_ua > 120:
                recommendations.append(
                    {
                        "level": "warning",
                        "msg": f"尿酸最新值 {latest_ua:.0f} μmol/L（>400），距上次检测已 {days_since_ua} 天，建议尽快安排复查。",
                    }
                )

        # 眼压：IOP任一眼>20 + 距复查>2个月
        df_eye = load_eye_exams()
        if not df_eye.empty:
            latest_eye = df_eye.iloc[-1]
            last_eye_date = (
                latest_eye["date"].date()
                if hasattr(latest_eye["date"], "date")
                else latest_eye["date"]
            )
            days_since_eye = (date.today() - last_eye_date).days
            iop_left = latest_eye.get("iop_left")
            iop_right = latest_eye.get("iop_right")
            iop_max = (
                max(v for v in [iop_left, iop_right] if v is not None)
                if any(v is not None for v in [iop_left, iop_right])
                else None
            )
            if iop_max is not None and iop_max > 20 and days_since_eye > 60:
                recommendations.append(
                    {
                        "level": "warning",
                        "msg": f"上次眼压最高 {iop_max:.0f} mmHg（>20），距上次复查已 {days_since_eye} 天（>2个月），建议尽快安排眼科随访。",
                    }
                )

        # 收缩压风险>70 + 距体检>6个月
        from superhealth.dashboard.prediction import hypertension_risk

        ht = hypertension_risk.compute()
        if ht["score"] > 70:
            recommendations.append(
                {
                    "level": "error",
                    "msg": f"高血压风险评分 {ht['score']:.0f}，建议就医评估血压控制情况。",
                }
            )

        # 就医提醒表
        appts = get_upcoming_appointments(within_days=30)
        for a in appts:
            due = str(a["due_date"])[:10]
            days_left = (date.fromisoformat(due) - date.today()).days
            level = "error" if days_left <= 7 else "warning"
            recommendations.append(
                {
                    "level": level,
                    "msg": f"{a['condition']} — {a.get('hospital', '')} {a.get('department', '')}，应诊日期 {due}（还有 {days_left} 天）",
                }
            )

        if not recommendations:
            st.success("当前无紧急就医建议，继续保持健康管理节奏。")
        else:
            for r in recommendations:
                if r["level"] == "error":
                    st.error(r["msg"])
                else:
                    st.warning(r["msg"])

    disclaimer.render()
