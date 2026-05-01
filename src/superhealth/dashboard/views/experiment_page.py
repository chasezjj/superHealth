"""实验追踪 Dashboard 页面。"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

DB_PATH = Path(__file__).parent.parent.parent.parent / "health.db"


def render():
    st.header("实验追踪")
    st.caption("N-of-1 干预实验框架 · 基于阶段性目标的结构化干预")

    from superhealth.feedback.experiment_manager import ExperimentManager
    from superhealth.goals.metrics import METRIC_REGISTRY

    mgr = ExperimentManager(DB_PATH)

    direction_labels = {"decrease": "降低", "increase": "提升", "stabilize": "稳定"}

    # ── 活跃实验 ──────────────────────────────────────────────────────
    active = mgr.get_active_experiment()
    if active:
        st.subheader(f"进行中：{active['name']}")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("状态", "🟢 活跃")
            start = active.get("start_date", "")
            end = active.get("end_date", "")
            st.caption(f"{start} → {end}")
        with col2:
            if active.get("start_date") and active.get("end_date"):
                from datetime import date

                total = (
                    date.fromisoformat(active["end_date"])
                    - date.fromisoformat(active["start_date"])
                ).days + 1
                elapsed = (date.today() - date.fromisoformat(active["start_date"])).days + 1
                pct = min(100, max(0, elapsed / total * 100))
                st.metric("进度", f"{elapsed}/{total} 天")
                st.progress(int(pct))
            else:
                st.metric("进度", "-")
        with col3:
            spec = METRIC_REGISTRY.get(active["metric_key"])
            metric_label = spec.label if spec else active["metric_key"]
            st.metric("关联指标", metric_label)
            st.metric("方向", direction_labels.get(active["direction"], active["direction"]))

        st.info(f"**干预方案**：{active['intervention']}")
        st.caption(f"假设：{active['hypothesis']}")

        # 基线 vs 当前对比
        goal_id = active.get("goal_id")
        if goal_id and active.get("baseline_end"):
            from superhealth import database as db

            with db.get_conn(DB_PATH) as conn:
                # 优先使用目标创建时确定的基线值（与今日概览保持一致）
                goal_row = conn.execute(
                    "SELECT baseline_value FROM goals WHERE id = ?", (goal_id,)
                ).fetchone()
                baseline_row = conn.execute(
                    """SELECT AVG(current_value) as avg_val
                       FROM goal_progress
                       WHERE goal_id = ? AND date BETWEEN ? AND ?""",
                    (goal_id, active.get("baseline_start", ""), active["baseline_end"]),
                ).fetchone()
                current_row = conn.execute(
                    """SELECT AVG(current_value) as avg_val
                       FROM goal_progress
                       WHERE goal_id = ? AND date >= ?""",
                    (goal_id, active["start_date"]),
                ).fetchone()
            # 优先用 goals.baseline_value，回退到实验基线期实测均值
            goal_baseline = goal_row["baseline_value"] if goal_row else None
            exp_baseline = baseline_row["avg_val"] if baseline_row else None
            b = goal_baseline if goal_baseline is not None else exp_baseline
            c = current_row["avg_val"] if current_row else None
            if b is not None and c is not None:
                col_b, col_c = st.columns(2)
                col_b.metric("基线均值", f"{b:.1f}")
                col_c.metric("实验期均值", f"{c:.1f}", delta=f"{c - b:+.1f}")

        if st.button("取消实验", key="cancel_exp"):
            try:
                mgr.cancel(active["id"])
                st.success("实验已取消")
                st.rerun()
            except ValueError as e:
                st.error(str(e))

    # ── Goal 推荐干预 ────────────────────────────────────────────────
    else:
        st.info("当前无活跃实验。从目标推荐干预中选择一个创建实验。")

        from superhealth import database as db

        with db.get_conn(DB_PATH) as conn:
            goals = conn.execute(
                "SELECT id, name, metric_key, direction FROM goals WHERE status = 'active' ORDER BY priority"
            ).fetchall()

        if goals:
            for g in goals:
                candidates = mgr.suggest_for_goal(g["id"])

                spec = METRIC_REGISTRY.get(g["metric_key"])
                metric_label = spec.label if spec else g["metric_key"]
                with st.expander(f"Goal: {g['name']}（{metric_label}）", expanded=False):
                    if not candidates:
                        st.warning("暂无推荐干预方案")
                        continue

                    source_tag = "LLM 生成" if candidates[0].get("source") == "llm" else "内置方案"
                    col_header, col_regenerate = st.columns([4, 1])
                    with col_header:
                        st.caption(f"共 {len(candidates)} 个方案（{source_tag}）")
                    with col_regenerate:
                        if st.button("重新生成", key=f"regen_{g['id']}"):
                            with st.spinner("正在调用百川生成干预方案..."):
                                candidates = mgr.suggest_for_goal(g["id"], force_regenerate=True)
                            st.rerun()

                    # 已有草稿的方案名称，避免重复创建
                    existing_drafts = {d["name"] for d in mgr.list_experiments(status="draft")}

                    for c in candidates:
                        col_name, col_act = st.columns([3, 1])
                        with col_name:
                            is_first = c["index"] == 0
                            badge = " **[推荐]**" if is_first else ""
                            st.markdown(f"**{c['name']}**{badge}（{c['duration']}天）")
                            st.caption(c["intervention"])
                            if c.get("hypothesis"):
                                st.caption(f"假设：{c['hypothesis']}")
                            if c.get("evidence"):
                                st.caption(f"循证来源：{c['evidence']}")
                        with col_act:
                            already_created = c["name"] in existing_drafts
                            if already_created:
                                st.caption("已创建 ✓")
                            elif st.button("创建", key=f"create_{g['id']}_{c['index']}"):
                                with st.spinner("创建中..."):
                                    try:
                                        hypothesis = c.get("hypothesis")
                                        if not hypothesis:
                                            spec_c = METRIC_REGISTRY.get(c["metric_key"])
                                            metric_label_c = (
                                                spec_c.label if spec_c else c["metric_key"]
                                            )
                                            dir_label_c = direction_labels.get(
                                                c["direction"], c["direction"]
                                            )
                                            hypothesis = f"{c['intervention']} 对 {metric_label_c}（方向: {dir_label_c}）的效果"
                                        exp_id = mgr.create_draft(
                                            name=c["name"],
                                            hypothesis=hypothesis,
                                            goal_id=g["id"],
                                            metric_key=c["metric_key"],
                                            direction=c["direction"],
                                            intervention=c["intervention"],
                                            min_duration=c["duration"],
                                        )
                                        st.success(f"实验 #{exp_id} 已创建")
                                        st.rerun()
                                    except Exception as e:
                                        st.error(str(e))

    # ── 草稿列表 ─────────────────────────────────────────────────────
    drafts = mgr.list_experiments(status="draft")
    if drafts:
        st.subheader("待激活实验")
        for d in drafts:
            col_info, col_act = st.columns([4, 1])
            with col_info:
                st.markdown(f"**{d['name']}**")
                st.caption(f"干预: {d['intervention']}")
            with col_act:
                if not active:
                    if st.button("激活", key=f"activate_{d['id']}"):
                        try:
                            mgr.activate(d["id"])
                            st.success("实验已激活！")
                            st.rerun()
                        except ValueError as e:
                            st.error(str(e))
                if st.button("删除", key=f"delete_{d['id']}"):
                    try:
                        mgr.delete_draft(d["id"])
                        st.success("实验已删除")
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))

    # ── 历史实验 ─────────────────────────────────────────────────────
    completed = mgr.list_experiments(status="completed")
    reverted = mgr.list_experiments(status="reverted")
    history = completed + reverted
    if history:
        st.subheader("历史实验")
        for h in history:
            icon = "✓" if h["status"] == "completed" else "✗"
            with st.expander(f"{icon} {h['name']}（{h['status']}）"):
                st.markdown(f"**假设**：{h['hypothesis']}")
                st.markdown(f"**干预**：{h['intervention']}")
                if h.get("conclusion"):
                    st.markdown(f"**结论**：{h['conclusion']}")
                if h.get("conclusion_date"):
                    st.caption(f"结案日期: {h['conclusion_date']}")
