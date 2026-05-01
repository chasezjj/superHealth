"""干预实验框架（N-of-1 Self-Experimentation）：基于阶段性 Goal 的结构化干预实验。

核心职责：
- 根据 Goal 的 metric_key 推荐循证干预候选
- 管理实验生命周期（draft → active → evaluating → completed/reverted）
- 通过 learned_preferences 向 LLM 传递实验约束
- 复用 causal.py 的统计工具做自动评估

设计：
- 实验与 Goal 关联（goal_id），复用 metric_key 和 goal_progress 数据
- 一次只能有一个 active 实验
- 实验期间 strategy_learner 跳过该时间段的学习
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from superhealth import database as db

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent.parent / "health.db"

# ─── Goal → 干预候选映射（降级兜底）──────────────────────────────────

# metric_key → [(name, intervention_prompt, default_duration_days)]
GOAL_INTERVENTIONS: dict[str, list[tuple[str, str, int]]] = {
    "bp_systolic_mean_7d": [
        (
            "等长运动强化",
            "每日等长握力训练 4 组 × 3 分钟（30% MVC），组间休息 2 分钟；每周递增 10 秒（中国高血压防治指南 2024）",
            14,
        ),
        (
            "哈他瑜伽干预",
            "每周 5 次 45 分钟哈他瑜伽，含站立/平衡/倒箭式（AHA 证据：收缩压降 5-8 mmHg）",
            21,
        ),
        (
            "杨式太极拳干预",
            "每日 40 分钟杨式太极拳 24 式全套练习（Meta 分析：收缩压降 8-12 mmHg）",
            21,
        ),
        (
            "中等强度有氧累积",
            "每日累积 40 分钟快走（心率 110-130 bpm），可分 2 次完成，每周 5 天以上（AHA 推荐量 150 min/周）",
            14,
        ),
        (
            "靠墙静蹲+平板支撑组合",
            "每日 3 组靠墙静蹲（力竭）+ 3 组平板支撑（60 秒），组间休息 90 秒（等长抗阻降压证据最强）",
            14,
        ),
    ],
    "bp_diastolic_mean_7d": [
        ("等长运动强化", "每日等长握力训练 4 组 × 3 分钟（30% MVC），组间休息 2 分钟", 14),
        ("共振呼吸干预", "每日 2 次 15 分钟共振呼吸（5.5 bpm），晨起+睡前各一次", 14),
        (
            "有氧规律化干预",
            "每日 40 分钟中等强度有氧（快走/慢跑/骑车），固定时段，每周 5 天以上",
            21,
        ),
        (
            "靠墙静蹲+深蹲组合",
            "每日 4 组靠墙静蹲（力竭）+ 4 组徒手深蹲（15 次），组间休息 90 秒",
            14,
        ),
        (
            "渐进式肌肉放松",
            "每日睡前 20 分钟 Jacobson 渐进式肌肉放松（16 组肌群依次紧张-放松）",
            14,
        ),
    ],
    "hrv_mean_7d": [
        (
            "晨间有氧干预",
            "每日晨间 35 分钟中等强度有氧（心率 120-140 bpm），提升副交感神经张力",
            14,
        ),
        ("4-7-8 呼吸干预", "每日 3 次 4-7-8 呼吸法（吸气 4s-屏息 7s-呼气 8s），每次 8 个循环", 14),
        (
            "瑜伽恢复干预",
            "每周 4 次 45 分钟流瑜伽/阴瑜伽交替，低 HRV 日仅做阴瑜伽（前屈+扭转为主）",
            14,
        ),
        (
            "冷水暴露干预",
            "每日淋浴末 30 秒冷水（15-20°C），逐渐延长至 60 秒（激活迷走神经，Meta 分析 HRV 提升 15%）",
            14,
        ),
        ("太极+冥想组合", "每日 20 分钟太极拳 + 10 分钟正念冥想（呼吸锚定），晨起执行", 14),
    ],
    "resting_hr_mean_7d": [
        (
            "有氧规律化干预",
            "每日 40 分钟有氧运动（快走/慢跑/游泳），固定晨间时段，每周 5 天以上",
            21,
        ),
        (
            "睡眠优化干预",
            "固定 23:00 前入睡，睡前 90 分钟无蓝光屏幕，卧室温度 18-20°C，确保 7.5 小时以上",
            14,
        ),
        (
            "呼吸训练干预",
            "每日 2 次箱式呼吸（吸气 4s-屏 4s-呼 4s-屏 4s），每次 10 分钟，降低交感张力",
            14,
        ),
        (
            "高强度间歇训练",
            "每周 3 次 25 分钟 HIIT（30s 全力 + 60s 恢复 × 15 组），提升心肺效率降低静息心率",
            14,
        ),
        (
            "瑜伽 nidra 引导",
            "每日睡前 20 分钟瑜伽 nidra（身体扫描引导放松），降低皮质醇和静息心率",
            14,
        ),
    ],
    "sleep_score_mean_7d": [
        ("严格规律作息", "固定 23:00 入睡 + 6:30 起床，周末偏差不超过 15 分钟，包含节假日", 14),
        (
            "睡前 90 分钟仪式",
            "睡前 90 分钟：停止工作+蓝光 → 30 分钟拉伸 → 15 分钟 4-7-8 呼吸 → 15 分钟阅读（纸质书）",
            14,
        ),
        (
            "晨间光照干预",
            "起床后 30 分钟内接受 15-20 分钟户外自然光（或 10000 lux 光疗灯），稳固昼夜节律",
            14,
        ),
        (
            "午后禁咖啡因",
            "下午 2 点后完全禁咖啡/茶/可乐，改喝菊花茶或温水（咖啡因半衰期 5-6 小时干扰深睡）",
            14,
        ),
        (
            "睡前体温干预",
            "睡前 90 分钟热水浴（40-42.5°C，10-15 分钟），核心体温先升后降促进入睡",
            14,
        ),
    ],
    "stress_mean_7d": [
        (
            "共振呼吸干预",
            "每日 3 次 10 分钟共振呼吸（5.5 bpm），晨起/午休/睡前，配合心率变异性生物反馈",
            14,
        ),
        (
            "正念冥想渐进",
            "每日 20 分钟正念冥想（身体扫描→呼吸锚定→开放觉察），从 10 分钟递增至 20 分钟",
            14,
        ),
        ("自然暴露干预", "每周 5 次户外自然环境活动 30 分钟以上（森林浴/公园散步），远离手机", 14),
        (
            "运动减压组合",
            "每日 30 分钟中等强度有氧（心率 120-140 bpm）+ 拉伸 10 分钟，运动后内啡肽持续减压",
            14,
        ),
        (
            "日记书写干预",
            "每日睡前 15 分钟表达性书写（写下今日压力源+应对策略+感恩 3 件事），Meta 分析减压效果 d=0.5",
            14,
        ),
    ],
    "weight_kg_mean_7d": [
        ("空腹有氧干预", "每周 4 次晨起空腹快走 40-50 分钟（心率 110-125 bpm），加速脂肪氧化", 21),
        (
            "抗阻训练干预",
            "每周 4 次全身力量训练（推拉腿分化），每次 50 分钟，组间休息 60-90 秒",
            21,
        ),
        (
            "16:8 间歇断食",
            "每日进食窗口压缩至 8 小时（如 10:00-18:00），其余 16 小时仅饮水/黑咖啡",
            21,
        ),
    ],
    "body_fat_pct_mean_7d": [
        (
            "HIIT 干预",
            "每周 4 次 25 分钟高强度间歇（30s 全力 + 60s 恢复 × 15 组），运动后过量氧耗持续燃脂",
            21,
        ),
        (
            "抗阻训练干预",
            "每周 4 次全身力量训练（大肌群复合动作为主），每次 50 分钟，追求渐进超负荷",
            21,
        ),
        ("组合训练干预", "每周 3 次力量（40 分钟）+ 2 次有氧（35 分钟中等强度），力量日优先", 21),
    ],
    "body_battery_wake_mean_7d": [
        (
            "睡眠优化干预",
            "固定 23:00 前入睡，睡前 90 分钟无蓝光屏幕，确保 7.5-8 小时高质量睡眠",
            14,
        ),
        (
            "晨间恢复仪式",
            "起床后：5 分钟深呼吸 + 10 分钟动态拉伸 + 10 分钟低强度散步，启动 Body Battery 充电",
            14,
        ),
        (
            "日间微恢复",
            "每工作 90 分钟起身 5 分钟（深呼吸+拉伸+走动），防止 Body Battery 非必要耗尽",
            14,
        ),
    ],
    "steps_mean_7d": [
        (
            "阶梯递增干预",
            "每日步数在当前基础上增加 3000 步，分 2-3 次完成（如晨间+午休+晚餐后）",
            14,
        ),
        ("通勤步行干预", "往返各增加 1-2 站步行距离，每日确保至少 1 次连续 20 分钟以上步行", 14),
        ("会议步行化", "所有电话会议/语音会议改为边走边开，每次至少 10 分钟", 14),
    ],
}

# 指标中文名（用于 LLM prompt）
_METRIC_LABELS = {
    "bp_systolic_mean_7d": "收缩压（7日均值）",
    "bp_diastolic_mean_7d": "舒张压（7日均值）",
    "hrv_mean_7d": "HRV 心率变异性（7日均值）",
    "resting_hr_mean_7d": "静息心率（7日均值）",
    "sleep_score_mean_7d": "睡眠评分（7日均值）",
    "stress_mean_7d": "平均压力（7日均值）",
    "weight_kg_mean_7d": "体重（7日均值）",
    "body_fat_pct_mean_7d": "体脂率（7日均值）",
    "body_battery_wake_mean_7d": "晨起 Body Battery（7日均值）",
    "steps_mean_7d": "步数（7日均值）",
    "uric_acid_latest": "尿酸（最近化验）",
    "iop_mean_recent": "眼压均值",
}

# Goals metric_key → causal.py FIELD_MAP key（仅 daily_health 指标可复用 causal.py）
_METRIC_TO_CAUSAL_KEY = {
    "hrv_mean_7d": "hrv_avg",
    "resting_hr_mean_7d": "resting_hr",
    "sleep_score_mean_7d": "sleep_score",
    "stress_mean_7d": "avg_stress",
    "body_battery_wake_mean_7d": "body_battery_wake",
    "steps_mean_7d": "steps",
}


@dataclass
class Experiment:
    id: int
    name: str
    hypothesis: str
    goal_id: Optional[int]
    metric_key: str
    direction: str
    intervention: str
    status: str
    start_date: Optional[str]
    end_date: Optional[str]
    min_duration: int
    baseline_start: Optional[str]
    baseline_end: Optional[str]
    conclusion: Optional[str]
    conclusion_date: Optional[str]
    notes: Optional[str]


class ExperimentManager:
    """干预实验生命周期管理器。"""

    MAX_TOTAL_DURATION = 28
    EXTEND_DAYS = 7

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path

    def _get_conn(self):
        return db.get_conn(self.db_path)

    # ── 查询 ──────────────────────────────────────────────────────────

    def get_active_experiment(self) -> Optional[dict]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM experiments WHERE status = 'active' LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def list_experiments(self, status: Optional[str] = None) -> list[dict]:
        with self._get_conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM experiments WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM experiments ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def get_experiment(self, experiment_id: int) -> Optional[dict]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM experiments WHERE id = ?", (experiment_id,)
            ).fetchone()
        return dict(row) if row else None

    # ── 干预推荐 ──────────────────────────────────────────────────────

    def suggest_for_goal(self, goal_id: int, force_regenerate: bool = False) -> list[dict]:
        """根据 Goal 返回候选干预列表。首次调用时请求百川生成并缓存，后续读缓存。

        Args:
            goal_id: 目标 ID
            force_regenerate: 强制重新调用百川生成（忽略缓存）
        """
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT id, name, metric_key, direction FROM goals WHERE id = ?",
                (goal_id,),
            ).fetchone()
        if not row:
            return []

        mk = row["metric_key"]
        direction = row["direction"]

        # 尝试读取缓存
        if not force_regenerate:
            cached = self._load_cached_interventions(goal_id)
            if cached:
                return cached

        # 调用百川生成
        llm_interventions = self._generate_with_llm(goal_id, mk, direction, row["name"])

        if llm_interventions:
            self._cache_interventions(goal_id, llm_interventions)
            return llm_interventions

        # 降级到静态映射（自动生成假设）
        static = GOAL_INTERVENTIONS.get(mk, [])
        metric_label = _METRIC_LABELS.get(mk, mk)
        dir_label = {"decrease": "降低", "increase": "提升", "stabilize": "稳定"}.get(
            direction, direction
        )
        result = []
        for i, c in enumerate(static):
            auto_hypothesis = f"执行「{c[0]}」{c[2]}天可使{metric_label}{dir_label}（该方案基于循证指南，预期产生可测量改善）"
            result.append(
                {
                    "index": i,
                    "name": c[0],
                    "intervention": c[1],
                    "hypothesis": auto_hypothesis,
                    "duration": c[2],
                    "goal_id": goal_id,
                    "metric_key": mk,
                    "direction": direction,
                    "source": "static",
                }
            )
        if result:
            self._cache_interventions(goal_id, result)
        return result

    def _load_cached_interventions(self, goal_id: int) -> list[dict] | None:
        """从 learned_preferences 读取缓存的干预方案。"""
        try:
            with self._get_conn() as conn:
                row = conn.execute(
                    """SELECT preference_value FROM learned_preferences
                       WHERE preference_type = 'goal_interventions'
                         AND preference_key = ?""",
                    (f"goal_{goal_id}",),
                ).fetchone()
            if row:
                cached = json.loads(row["preference_value"])
                if cached and len(cached) > 0:
                    return cached
        except Exception:
            pass
        return None

    def _cache_interventions(self, goal_id: int, interventions: list[dict]) -> None:
        """缓存干预方案到 learned_preferences。"""
        try:
            with self._get_conn() as conn:
                conn.execute(
                    """INSERT INTO learned_preferences
                       (preference_type, preference_key, preference_value,
                        confidence_score, evidence_count, status)
                       VALUES ('goal_interventions', ?, ?, 0.5, 0, 'active')
                       ON CONFLICT(preference_type, preference_key) DO UPDATE SET
                           preference_value=excluded.preference_value,
                           last_updated=datetime('now','localtime')""",
                    (f"goal_{goal_id}", json.dumps(interventions, ensure_ascii=False)),
                )
        except Exception as e:
            log.warning("缓存干预方案失败: %s", e)

    def _generate_with_llm(
        self, goal_id: int, metric_key: str, direction: str, goal_name: str
    ) -> list[dict] | None:
        """调用百川生成干预方案。"""
        try:
            from superhealth.config import load as load_config

            cfg = load_config()
            if not cfg.baichuan or not cfg.baichuan.api_key:
                log.info("百川未配置，跳过 LLM 生成")
                return None

            from superhealth.core.baichuan_advisor import BaichuanMedicalAdvisor

            advisor = BaichuanMedicalAdvisor()

            # 加载用户健康背景
            conditions = []
            contraindications = []
            try:
                from superhealth.core.health_profile_builder import HealthProfileBuilder

                builder = HealthProfileBuilder(self.db_path)
                profile = builder.build()
                conditions = [c for c in profile.conditions] if profile.conditions else []
                contraindications = (
                    [c for c in profile.exercise_contraindications]
                    if profile.exercise_contraindications
                    else []
                )
            except Exception:
                pass

            metric_label = _METRIC_LABELS.get(metric_key, metric_key)
            dir_label = {"decrease": "降低", "increase": "提升", "stabilize": "稳定"}.get(
                direction, direction
            )

            prompt = (
                f"我正在设计一个 N-of-1 自我实验，目标是「{dir_label}{metric_label}」（目标名称：{goal_name}）。\n"
                f"请生成 5-8 个具体可执行的干预方案，每个方案需包含：\n"
                f'1. **方案名称**：简洁概括（如"中等强度有氧干预"）\n'
                f"2. **干预方案**：明确的具体执行参数（组数×时间/次数、强度、频率），不能模糊\n"
                f'3. **科学假设**：一个可证伪的预测，如"执行本方案{dir_label}{metric_label}约X单位（基于循证证据）"——必须是具体数值预测，不是干预方案的复述\n'
                f"4. **循证来源**：具体指南名称或 Meta 分析结论\n"
                f"5. **建议实验天数**：14-21 天\n\n"
                f"要求：\n"
                f"- 强度足够产生可测量的生理变化（14-21 天内）\n"
                f"- 方案之间有明显差异（运动类型/强度/时段/机制不同），便于 A/B 对比\n"
                f'- 假设必须与干预方案有区分度，假设是"预期发生什么"，干预是"具体做什么"\n'
            )

            if conditions:
                cond_labels = {"hyperuricemia": "高尿酸血症", "glaucoma": "青光眼"}
                cond_str = "、".join(cond_labels.get(c, c) for c in conditions)
                prompt += f"\n用户合并症：{cond_str}（方案须考虑合并症安全性）\n"

            if contraindications:
                contra_labels = {
                    "valsalva_maneuver": "憋气/Valsalva 动作",
                    "inverted_positions": "倒立体位",
                    "maximal_isometric": "最大等长收缩",
                }
                contra_str = "、".join(contra_labels.get(c, c) for c in contraindications)
                prompt += f"运动禁忌：{contra_str}\n"

            prompt += (
                "\n请严格按以下 JSON 格式回复（不要输出 JSON 以外的内容）：\n"
                "[\n"
                '  {"name": "方案名称", "intervention": "具体执行方案（含参数）", "hypothesis": "可证伪的科学假设（含预期效果数值）", "duration": 14, "evidence": "循证来源"},\n'
                "  ...\n"
                "]"
            )

            client = advisor._get_client()
            response = client.chat.completions.create(
                model=cfg.baichuan.model,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.choices[0].message.content.strip()
            parsed = self._extract_json_list(raw)

            if not parsed:
                log.warning("百川返回的 JSON 解析失败: %s", raw[:200])
                return None

            return [
                {
                    "index": i,
                    "name": item.get("name", f"干预方案 {i + 1}"),
                    "intervention": item.get("intervention", ""),
                    "hypothesis": item.get("hypothesis", ""),
                    "duration": item.get("duration", 14),
                    "evidence": item.get("evidence", ""),
                    "goal_id": goal_id,
                    "metric_key": metric_key,
                    "direction": direction,
                    "source": "llm",
                }
                for i, item in enumerate(parsed)
            ]

        except ImportError:
            log.info("openai SDK 未安装，跳过百川生成")
            return None
        except Exception as e:
            log.warning("百川生成干预方案失败: %s", e)
            return None

    @staticmethod
    def _extract_json_list(raw: str) -> list[dict]:
        """从 LLM 返回文本中提取 JSON 数组。"""
        import re

        if "```" in raw:
            m = re.search(r"```(?:json)?\s*([\s\S]+?)```", raw)
            if m:
                return json.loads(m.group(1).strip())

        start = raw.find("[")
        if start != -1:
            depth = 0
            for i, ch in enumerate(raw[start:], start):
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        return json.loads(raw[start : i + 1])

        return json.loads(raw) if raw.startswith("[") else []

    # ── CRUD + 生命周期 ───────────────────────────────────────────────

    def create_draft(
        self,
        *,
        name: str,
        hypothesis: str,
        goal_id: Optional[int],
        metric_key: str,
        direction: str,
        intervention: str,
        min_duration: int = 14,
        notes: Optional[str] = None,
    ) -> int:
        """创建实验草稿。同名 + 同 goal 的 draft 不重复创建。"""
        with self._get_conn() as conn:
            dup = conn.execute(
                "SELECT id FROM experiments WHERE name = ? AND goal_id = ? AND status = 'draft'",
                (name, goal_id),
            ).fetchone()
            if dup:
                raise ValueError(f"该目标已有同名草稿实验 #{dup['id']}（{name}），无需重复创建")
            cursor = conn.execute(
                """INSERT INTO experiments
                   (name, hypothesis, goal_id, metric_key, direction,
                    intervention, status, min_duration, notes)
                   VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?)""",
                (
                    name,
                    hypothesis,
                    goal_id,
                    metric_key,
                    direction,
                    intervention,
                    min_duration,
                    notes,
                ),
            )
            exp_id = cursor.lastrowid
        log.info("EXPERIMENT_DRAFT id=%d name=%s", exp_id, name)
        return exp_id

    def activate(self, experiment_id: int) -> None:
        """草稿 → 激活：设定日期、写 active_experiment preference。"""
        active = self.get_active_experiment()
        if active:
            raise ValueError(
                f"已有活跃实验 #{active['id']}（{active['name']}），同时只能运行一个实验"
            )

        exp = self.get_experiment(experiment_id)
        if not exp:
            raise ValueError(f"实验 #{experiment_id} 不存在")
        if exp["status"] != "draft":
            raise ValueError(f"实验 #{experiment_id} 状态为 {exp['status']}，只有 draft 可以激活")

        today = date.today()
        start = today.isoformat()
        baseline_end = (today - timedelta(days=1)).isoformat()
        baseline_start = (today - timedelta(days=14)).isoformat()
        end = (today + timedelta(days=exp["min_duration"] - 1)).isoformat()

        with self._get_conn() as conn:
            conn.execute(
                """UPDATE experiments
                   SET status='active', start_date=?, end_date=?,
                       baseline_start=?, baseline_end=?, updated_at=datetime('now','localtime')
                   WHERE id=?""",
                (start, end, baseline_start, baseline_end, experiment_id),
            )
            # 写入 learned_preferences 作为 LLM 约束信号
            conn.execute(
                """INSERT INTO learned_preferences
                   (preference_type, preference_key, preference_value,
                    confidence_score, evidence_count, status)
                   VALUES ('active_experiment', ?, ?, 1.0, 0, 'active')
                   ON CONFLICT(preference_type, preference_key) DO UPDATE SET
                       preference_value=excluded.preference_value,
                       confidence_score=excluded.confidence_score,
                       status=excluded.status,
                       last_updated=datetime('now','localtime')""",
                (f"active_exp_{experiment_id}", exp["intervention"]),
            )

        log.info(
            "EXPERIMENT_ACTIVE id=%d start=%s end=%s metric=%s",
            experiment_id,
            start,
            end,
            exp["metric_key"],
        )

    def cancel(self, experiment_id: int) -> None:
        """取消活跃实验（→ draft），清理 preference。"""
        exp = self.get_experiment(experiment_id)
        if not exp:
            raise ValueError(f"实验 #{experiment_id} 不存在")
        if exp["status"] != "active":
            raise ValueError(f"实验 #{experiment_id} 状态为 {exp['status']}，只有 active 可以取消")

        with self._get_conn() as conn:
            conn.execute(
                """UPDATE experiments
                   SET status='draft', start_date=NULL, end_date=NULL,
                       baseline_start=NULL, baseline_end=NULL,
                       updated_at=datetime('now','localtime')
                   WHERE id=?""",
                (experiment_id,),
            )
            conn.execute(
                "DELETE FROM learned_preferences WHERE preference_key = ?",
                (f"active_exp_{experiment_id}",),
            )

        log.info("EXPERIMENT_CANCELLED id=%d", experiment_id)

    def delete_draft(self, experiment_id: int) -> None:
        """删除草稿实验。"""
        exp = self.get_experiment(experiment_id)
        if not exp:
            raise ValueError(f"实验 #{experiment_id} 不存在")
        if exp["status"] != "draft":
            raise ValueError(f"实验 #{experiment_id} 状态为 {exp['status']}，只有 draft 可以删除")

        with self._get_conn() as conn:
            conn.execute("DELETE FROM experiments WHERE id = ?", (experiment_id,))
        log.info("EXPERIMENT_DELETED id=%d", experiment_id)

    # ── 自动评估（由 daily_pipeline 调用）───────────────────────────────

    def check_and_evaluate(self, run_date: str) -> None:
        """检查是否有到期的活跃实验，执行评估。"""
        active = self.get_active_experiment()
        if not active:
            return

        exp = active
        end_date = exp.get("end_date")
        if not end_date or run_date < end_date:
            return

        log.info("EXPERIMENT_EVALUATING id=%d metric=%s", exp["id"], exp["metric_key"])
        result = self._evaluate(exp)

        if result.get("inconclusive") and self._can_extend(exp):
            self._extend(exp)
            return

        if result.get("commit"):
            self._commit(exp, result)
        else:
            self._revert(exp, result)

    def _evaluate(self, exp: dict) -> dict:
        """运行统计评估，返回 verdict dict。"""
        metric_key = exp["metric_key"]
        start_date = exp["start_date"]
        min_duration = exp["min_duration"]

        # 尝试用 causal.py（仅 daily_health 指标）
        causal_key = _METRIC_TO_CAUSAL_KEY.get(metric_key)
        if causal_key:
            return self._evaluate_with_causal(exp, causal_key, start_date, min_duration)

        # vitals 类指标：直接从 goal_progress 读取前后对比
        return self._evaluate_from_goal_progress(exp)

    def _evaluate_with_causal(
        self, exp: dict, causal_key: str, start_date: str, period_days: int
    ) -> dict:
        """使用 causal.py 的 paired test + ITSA 评估。"""
        from superhealth.analysis.causal import CausalInferenceAnalyzer

        analyzer = CausalInferenceAnalyzer(self.db_path)

        paired = analyzer.paired_intervention_test(causal_key, start_date, period_days=period_days)
        itsa = analyzer.interrupted_time_series(
            causal_key, start_date, pre_days=14, post_days=period_days
        )

        direction_match = self._direction_matches(
            exp["direction"], paired.difference, paired.baseline_mean
        )

        if paired.p_value < 0.1 and direction_match and abs(paired.cohens_d) >= 0.3:
            verdict = "commit"
        elif paired.p_value < 0.1 and not direction_match:
            verdict = "revert"
        else:
            verdict = "inconclusive"

        conclusion = (
            f"配对检验：{paired.interpretation}\n"
            f"ITSA：{itsa.interpretation}\n"
            f"Cohen's d = {paired.cohens_d:.2f}，p = {paired.p_value:.4f}，"
            f"效应方向{'匹配' if direction_match else '不匹配'}"
        )

        return {
            "verdict": verdict,
            "commit": verdict == "commit",
            "inconclusive": verdict == "inconclusive",
            "conclusion": conclusion,
            "cohens_d": paired.cohens_d,
            "p_value": paired.p_value,
            "baseline_mean": paired.baseline_mean,
            "post_mean": paired.post_mean,
        }

    def _evaluate_from_goal_progress(self, exp: dict) -> dict:
        """从 goal_progress 读取基线期/实验期数据做简单对比。"""
        goal_id = exp.get("goal_id")
        if not goal_id:
            return {
                "verdict": "revert",
                "commit": False,
                "inconclusive": False,
                "conclusion": "无关联目标，无法评估",
            }

        start_date = exp["start_date"]
        baseline_end = exp.get("baseline_end")
        baseline_start = exp.get("baseline_start")

        with self._get_conn() as conn:
            # 基线期均值
            baseline_rows = conn.execute(
                """SELECT AVG(current_value) as avg_val
                   FROM goal_progress
                   WHERE goal_id = ? AND date BETWEEN ? AND ?""",
                (goal_id, baseline_start or start_date, baseline_end or start_date),
            ).fetchone()

            # 实验期均值
            post_rows = conn.execute(
                """SELECT AVG(current_value) as avg_val, COUNT(*) as n
                   FROM goal_progress
                   WHERE goal_id = ? AND date BETWEEN ? AND ?""",
                (goal_id, start_date, exp["end_date"]),
            ).fetchone()

        b_mean = baseline_rows["avg_val"] if baseline_rows else None
        p_mean = post_rows["avg_val"] if post_rows else None
        n = post_rows["n"] if post_rows else 0

        if b_mean is None or p_mean is None or n < 7:
            return {
                "verdict": "inconclusive",
                "commit": False,
                "inconclusive": True,
                "conclusion": f"数据不足（基线期或实验期有效数据 < 7 天，实际 n={n}）",
            }

        diff = p_mean - b_mean
        direction_match = self._direction_matches(exp["direction"], diff, b_mean)
        pct_change = abs(diff / b_mean * 100) if b_mean != 0 else 0

        if pct_change >= 3 and direction_match:
            verdict = "commit"
        elif pct_change >= 3 and not direction_match:
            verdict = "revert"
        else:
            verdict = "inconclusive"

        conclusion = (
            f"基线期均值 {b_mean:.2f}，实验期均值 {p_mean:.2f}，"
            f"变化 {diff:+.2f}（{pct_change:.1f}%），"
            f"n={n}，方向{'匹配' if direction_match else '不匹配'}"
        )

        return {
            "verdict": verdict,
            "commit": verdict == "commit",
            "inconclusive": verdict == "inconclusive",
            "conclusion": conclusion,
            "baseline_mean": b_mean,
            "post_mean": p_mean,
        }

    def _direction_matches(
        self, expected_direction: str, diff: float, baseline: float = None
    ) -> bool:
        if expected_direction == "increase":
            return diff > 0
        if expected_direction == "decrease":
            return diff < 0
        # stabilize: 5% 相对容差（至少 0.1 绝对值），避免浮点精确相等
        tolerance = max(abs(baseline) * 0.05, 0.1) if baseline else 0.1
        return abs(diff) <= tolerance

    def _can_extend(self, exp: dict) -> bool:
        """检查实验是否还可以延长。"""
        if not exp.get("start_date"):
            return False
        start = date.fromisoformat(exp["start_date"])
        total = (date.today() - start).days
        return total + self.EXTEND_DAYS <= self.MAX_TOTAL_DURATION

    def _extend(self, exp: dict) -> None:
        """延长实验 7 天。"""
        current_end = date.fromisoformat(exp["end_date"])
        new_end = (current_end + timedelta(days=self.EXTEND_DAYS)).isoformat()
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE experiments SET end_date=?, updated_at=datetime('now','localtime') WHERE id=?",
                (new_end, exp["id"]),
            )
        log.info("EXPERIMENT_EXTENDED id=%d new_end=%s", exp["id"], new_end)

    def _commit(self, exp: dict, result: dict) -> None:
        """实验结论：有效，固化为偏好。"""
        confidence = 0.6
        if result.get("cohens_d"):
            confidence = min(0.95, 0.6 + abs(result["cohens_d"]) * 0.15)

        metric_label = _METRIC_LABELS.get(exp["metric_key"], exp["metric_key"])
        d_label = {"decrease": "降低", "increase": "提升", "stabilize": "稳定"}.get(
            exp["direction"], exp["direction"]
        )
        cohens_desc = f"Cohen's d={result.get('cohens_d', 0):.2f}" if result.get("cohens_d") else ""
        p_desc = f"p={result.get('p_value', 1):.4f}" if result.get("p_value") else ""

        # LLM 可读的偏好描述：明确告诉 LLM 这个干预经过实验验证有效
        pref_value = (
            f"[实验验证有效·{exp['min_duration']}天·{cohens_desc}·{p_desc}] "
            f"{exp['intervention']}——已证实对{d_label}{metric_label}有显著效果，建议长期保持"
        )

        with self._get_conn() as conn:
            conn.execute(
                """UPDATE experiments
                   SET status='completed', conclusion=?, conclusion_date=date('now','localtime'),
                       updated_at=datetime('now','localtime')
                   WHERE id=?""",
                (result.get("conclusion", "实验有效"), exp["id"]),
            )
            # 固化为 committed preference（LLM 会作为"历史偏好"读取）
            conn.execute(
                """INSERT INTO learned_preferences
                   (preference_type, preference_key, preference_value,
                    confidence_score, evidence_count, status, goal_id)
                   VALUES ('experiment_conclusion', ?, ?, ?, ?, 'committed', ?)
                   ON CONFLICT(preference_type, preference_key) DO UPDATE SET
                       preference_value=excluded.preference_value,
                       confidence_score=excluded.confidence_score,
                       evidence_count=excluded.evidence_count,
                       status=excluded.status,
                       last_updated=datetime('now','localtime')""",
                (
                    f"commit_{exp['id']}_{exp['metric_key']}",
                    pref_value,
                    confidence,
                    exp["min_duration"],
                    exp.get("goal_id"),
                ),
            )
            # 删除 active_experiment preference
            conn.execute(
                "DELETE FROM learned_preferences WHERE preference_key = ?",
                (f"active_exp_{exp['id']}",),
            )

        log.info("EXPERIMENT_COMMITTED id=%d confidence=%.2f", exp["id"], confidence)

    def _revert(self, exp: dict, result: dict) -> None:
        """实验结论：无效或方向相反。"""
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE experiments
                   SET status='reverted', conclusion=?, conclusion_date=date('now','localtime'),
                       updated_at=datetime('now','localtime')
                   WHERE id=?""",
                (result.get("conclusion", "实验无效"), exp["id"]),
            )
            # 记录回退（低置信度）
            conn.execute(
                """INSERT INTO learned_preferences
                   (preference_type, preference_key, preference_value,
                    confidence_score, evidence_count, status, goal_id)
                   VALUES ('experiment_conclusion', ?, ?, 0.3, ?, 'reverted', ?)
                   ON CONFLICT(preference_type, preference_key) DO UPDATE SET
                       preference_value=excluded.preference_value,
                       confidence_score=excluded.confidence_score,
                       status=excluded.status,
                       last_updated=datetime('now','localtime')""",
                (
                    f"revert_{exp['id']}_{exp['metric_key']}",
                    f"实验无效: {result.get('conclusion', '无显著效果')}",
                    exp["min_duration"],
                    exp.get("goal_id"),
                ),
            )
            # 删除 active_experiment preference
            conn.execute(
                "DELETE FROM learned_preferences WHERE preference_key = ?",
                (f"active_exp_{exp['id']}",),
            )

        log.info("EXPERIMENT_REVERTED id=%d", exp["id"])
