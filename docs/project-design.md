# SuperHealth 项目设计文档

> 本文档是项目设计与实现的权威参考，反映截至 2026-04-30 的代码实际状态。

---

## 1. 项目概述

个人健康数据管理系统，整合基因报告,体检报告,日常门诊, Garmin 手表、iOS Health Auto Export等多源数据，实现自动化采集、长期趋势分析、因果推断和个性化健康洞察。

### 示例病情

- **高尿酸血症**（示例）：每 6 月门诊复诊，查肝肾功能+尿酸+肾超声
- **青光眼**（示例）：每 3~4 月眼科复查，用药控制眼压

### 设计原则

- 全部数据本地存储（SQLite），不上传云端
- 最大限度自动化采集，减少手动录入
- 系统自动从数据中推导健康画像，不依赖人工预设
- LLM 建议仅供参考，不替代医生诊断
- 从相关性分析升级到因果推断，增强建议可信度

---

## 2. 三层数据架构

| 层级 | 数据类型 | 特点 | 存储位置 | 采集方式 |
|------|----------|------|----------|----------|
| **第一层** | 基因数据 | 不可变，终生有效 | `genetic-data/` | 手动上传 |
| **第二层** | 检查与测量数据 | 定期采集，低频变化 | `medical-records/`, `checkup-reports/`, SQLite | 手动 + 自动 |
| **第三层** | 生活习惯数据 | 高频变化，日常采集 | `activity-data/`, SQLite | 自动采集 |

---

## 3. 系统架构

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      Layer 1: 数据采集层                                 │
│  ┌───────────┐  ┌──────────────┐  ┌───────────┐  ┌──────────────────┐  │
│  │ Garmin    │  │ Health Auto  │  │ 手动录入   │  │ 和风天气 API     │  │
│  │ (Playwright)│ │ Export(REST) │  │ (CLI/DB)  │  │ (weather_collector)│ │
│  └─────┬─────┘  └──────┬───────┘  └─────┬─────┘  └────────┬─────────┘  │
│        └────────────────┼────────────────┼─────────────────┘            │
│                         ▼                ▼                              │
│               ┌─────────────────────────────────┐                      │
│               │   SQLite (20 张表)               │                      │
│               └───────────────┬─────────────────┘                      │
│  ┌───────────────────┐                                                 │
│  │ Outlook/Exchange  │──→ calendar_events 表 → LLM Prompt 日程上下文    │
│  │ (EWS/exchangelib) │                                                 │
│  └───────────────────┘                                                 │
└───────────────────────────────┼──────────────────────────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│            Layer 2: 健康画像与模型选择层                                  │
│  ┌────────────────────────┐    ┌─────────────────────────────────────┐ │
│  │ HealthProfileBuilder   │───▶│ ModelSelector                       │ │
│  │ 自动解析: 基因/体检/    │    │ 根据画像+目标动态选择评估模型        │ │
│  │ 门诊/用药/化验/眼科     │    │ Recovery/Cardiovascular/Metabolic/  │ │
│  │ + 活跃阶段性目标        │    │ Glaucoma/Lipid/Sleep/BodyComp/      │ │
│  │ 输出: HealthProfile     │    │ Stress/GeneticRisk                  │ │
│  └────────────────────────┘    └─────────────────────────────────────┘ │
└───────────────────────────────┼─────────────────────────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│            Layer 3: LLM 智能决策层                                       │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ llm_advisor.py (基类: Prompt构建 + 指南库 + JSON解析)             │  │
│  │   ├── claude_advisor.py   → Anthropic Claude (OpenAI 兼容代理)   │  │
│  │   └── baichuan_advisor.py → 百川医疗大模型 (OpenAI 兼容 API)     │  │
│  │ 注入: 目标段落(P1/P2优先级)                                    │  │
│  │ 注入: 日程上下文(CalendarSummary → busy_level/连续会议)           │  │
│  │ 注入: 活跃实验约束(active_experiment → learned_preferences)       │  │
│  │ mode: claude_only | baichuan_only | both (并行调用)              │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────┼─────────────────────────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│            Layer 4: 报告生成层                                           │
│  ┌──────────────────────┐    ┌──────────────────────────────────────┐ │
│  │ daily_report.py      │    │ advanced_daily_report.py             │ │
│  │ 基础日报（规则引擎）  │    │ 高级日报（画像+LLM建议+天气+雷达图） │ │
│  └──────────────────────┘    └──────────────────────────────────────┘ │
└───────────────────────────────┼─────────────────────────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│            Layer 5: 反馈闭环与学习层                                     │
│  auto_feedback.py → effect_tracker.py → strategy_learner.py            │
│  自动 compliance 评估 → 运动效果追踪 → 个人偏好学习                     │
│  quality_score = 0.30×compliance + 0.25×goal_progress                  │
│                + 0.25×effect + 0.20×user_rating                        │
│  输出: learned_preferences 表 → 反向影响 Layer 2 & 3                    │
│  偏好生命周期: active → committed(avg_q≥0.70) / reverted(avg_q≤0.30)   │
│                                                                           │
│  experiment_manager.py → N-of-1 干预实验框架                            │
│  draft → active → evaluating → completed/reverted                       │
│  实验结论固化/回退 → learned_preferences (experiment_conclusion)        │
│                                                                           │
│  pipeline_diff.py → 流水线差异追踪（before/after 快照对比）             │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│            Layer 6: 因果推断层（新增）                                   │
│  causal.py: Granger 因果检验 / 干预前后配对检验 / ITSA 间断时间序列       │
│  为 experiment_manager 提供统计评估工具，从关联升级到因果                 │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        输出层                                           │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────────────┐│
│  │ 微信日报(每日)│  │ 周报(每周)   │  │ Streamlit 仪表盘 (按需, 9页面)││
│  │ (openclaw)   │  │ (LLM洞察)    │  │ 概览/历史回顾/偏好/实验/趋势/ ││
│  │              │  │              │  │ 化验/相关性/预测/导出          ││
│  └──────────────┘  └──────────────┘  └───────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 4. 技术栈

| 依赖 | 版本 | 用途 |
|------|------|------|
| `requests` | >=2.31 | HTTP 请求（天气 API、Health Auto Export） |
| `playwright` | >=1.44 | Garmin Connect CN 数据拉取（浏览器自动化） |
| `pydantic` | >=2.0 | 数据模型验证 |
| `openai` | >=1.0 | LLM 调用（Anthropic 代理端点 + 百川 OpenAI 兼容 API） |
| `flask` | >=3.0 | Health Auto Export REST 接收端 |
| `streamlit` | >=1.32,<1.41 | Web 仪表盘框架（锁定上限修复下拉框渲染 bug） |
| `plotly` | >=5.20 | 交互式图表 |
| `pandas` | >=2.0 | 数据处理 |
| `scikit-learn` | >=1.4 | 趋势预测（线性回归） |
| `python-dateutil` | >=2.8 | 日期解析 |
| `tomli` / `tomli-w` | >=2.0 / >=1.0 | TOML 配置读写（Python < 3.11 回退） |
| `exchangelib` | >=5.0 | Exchange/Outlook 日历集成（EWS 协议） |
| `numpy` | >=1.24 | 因果推断矩阵运算（scikit-learn 间接引入） |

**关于 OpenAI SDK**：项目通过 `openai` Python 包统一访问多个 LLM：
- Anthropic Claude：通过 `base_url` 指向 Anthropic 代理端点
- 百川医疗大模型：通过百川的 OpenAI 兼容 API (`api.baichuan-ai.com/v1`)

运行环境：Python 3.11+，pytest 配置 `pythonpath = src`。

---

## 5. 数据模型

### 5.1 数据库表（20 张）

| 表名 | 用途 | 数据来源 |
|------|------|----------|
| `daily_health` | Garmin 每日数据（睡眠/心率/HRV/Body Battery/压力/血氧/呼吸/活动） | Playwright 采集 |
| `daily_health_audit` | 核心指标变更审计（追溯 effect_tracker 评估变化原因） | upsert 时自动写入 |
| `exercises` | 运动记录（含 start_time, details） | Playwright 采集 |
| `lab_results` | 门诊化验（按 item_name 存储，含参考范围和异常标记） | 手动录入 |
| `vitals` | 血压/体重/体脂率 | Health Auto Export 自动推送 |
| `medications` | 用药记录（示例药物等） | 手动录入 |
| `medication_effects` | 用药与化验/检查效果关联 | 手动关联 |
| `eye_exams` | 眼科复查（IOP/CD ratio/视力/处方） | 手动录入 |
| `kidney_ultrasounds` | 肾脏彩超（大小/发现/结论） | 手动录入 |
| `annual_checkups` | 年度体检（完整面板：肝肾脂糖血甲状腺肿瘤标志物等） | 手动录入 |
| `weather` | 天气（条件/温度/风力/AQI/户外适宜度/温度区间） | 和风天气 API |
| `calendar_events` | 日历事件（Outlook/Exchange，主题/时间段/忙碌程度） | EWS 采集 |
| `sync_logs` | 流水线各步骤执行日志（用于历史失败重试） | daily_pipeline 自动写入 |
| `user_profile` | 用户档案 key-value（身高/性别/出生日期等） | 手动设定 |
| `recommendation_feedback` | 建议执行反馈（compliance + 实际行动 + 效果追踪 + 用户文字反馈 + 评分 + 质量评分 + 目标关联） | auto_feedback + 手动 |
| `learned_preferences` | 学习到的个人偏好（类型/值/置信度/证据数 + 生命周期状态 + 目标关联 + 最后有效时间） | strategy_learner |
| `appointments` | 就医预约提醒（病情/医院/科室/应诊日期/状态） | appointment_scheduler |
| `goals` | 阶段性目标（名称/优先级/指标/方向/基线/目标值/状态） | CLI 手动设定 |
| `goal_progress` | 每日目标进度快照（当前值/基线变化/进度百分比） | goals_tracker 自动写入 |
| `experiments` | 干预实验（N-of-1 Self-Experimentation：假设/干预/状态/日期/结论） | experiment_manager |

### 5.2 Schema 管理

- Schema 单一来源：`schema.sql`
- 新增表 → `CREATE TABLE IF NOT EXISTS ...`
- 新增列 → 文件末尾 `[Column Migrations]` 区 `ALTER TABLE ... ADD COLUMN`
- 同步命令：`python -c "from superhealth.database import init_db; init_db()"`

### 5.3 主要列迁移

| 表 | 新增列 | 用途 |
|----|--------|------|
| `exercises` | `start_time`, `details` | 运动开始时间、动作明细（如力量训练组数×次数） |
| `weather` | `temp_max`, `temp_min` | 全天温度区间（来自 3 日预报） |
| `recommendation_feedback` | `user_feedback`, `user_rating`, `goal_id`, `quality_score` | 用户文字反馈、1-5 星评分、关联目标、综合建议质量评分（0-1） |
| `daily_health` | `fetched_at` | Garmin 拉取时间（区分早间不完整数据 vs 晚间完整数据） |
| `learned_preferences` | `status`, `goal_id`, `last_effective_at` | 偏好生命周期状态（active/committed/reverted）、关联阶段性目标、最后有效时间 |

---

## 6. 模块详解

```
src/superhealth/
├── config.py                    # 配置管理（9 个 dataclass，环境变量覆盖）
├── models.py                    # Pydantic 数据模型（DailyHealth, Exercise 等）
├── database.py                  # SQLite 存储层（20 张表的全部 CRUD 操作）
├── daily_pipeline.py            # 每日流水线编排器（cron 主入口）
│
├── goals/                       # 阶段性目标子系统
│   ├── models.py                # Goal/GoalProgress Pydantic 模型
│   ├── metrics.py               # GoalMetricRegistry 指标白名单（11个）与聚合器
│   ├── manager.py               # CRUD + 生命周期 + 达成/异常判定
│   ├── cli.py                   # CLI 入口（python -m superhealth.goals）
│   └── __main__.py              # 使 python -m superhealth.goals 可直接运行
│
├── collectors/                  # Layer 1: 数据采集
│   ├── fetch_garmin.py          # Garmin Connect CN 数据拉取（Playwright）
│   ├── send_garmin_report.py    # 微信日报发送（openclaw 命令）
│   ├── weather_collector.py     # 和风天气采集（天气+AQI+户外适宜度）
│   └── outlook_collector.py     # Outlook/Exchange 日历采集（EWS，日程上下文）
│
├── api/                         # Layer 1: 数据接收
│   └── vitals_receiver.py       # Health Auto Export REST 接收端（Flask）
│
├── analysis/                    # 基础分析工具（被 core/ 调用）
│   ├── analyze_garmin.py        # Garmin 数据解析与评分
│   ├── trends.py                # 趋势分析（滚动均值/基线/异常检测）
│   ├── correlation.py           # 相关性分析
│   └── causal.py                # 因果推断引擎（Granger/配对检验/ITSA）
│
├── core/                        # Layer 2-3: 健康画像与决策引擎
│   ├── health_profile_builder.py  # 自动构建健康画像（从 DB 读取全量数据）
│   ├── model_selector.py          # 动态模型选择（根据画像选最多 9 个模型）
│   ├── assessment_models.py       # 评估模型库（9 个模型，统一接口）
│   ├── llm_advisor.py             # LLM 建议引擎基类（Prompt 构建 + 指南库）
│   ├── claude_advisor.py          # Anthropic Claude 子类
│   └── baichuan_advisor.py        # 百川医疗大模型子类
│
├── reports/                     # Layer 4: 报告生成
│   ├── daily_report.py            # 基础日报（规则引擎，无需 LLM，作为回退）
│   └── advanced_daily_report.py   # 高级日报（画像+LLM建议+天气+雷达图）
│
├── feedback/                    # Layer 5: 反馈闭环与学习
│   ├── auto_feedback.py           # 自动反馈采集（每日 morning 触发，LLM 评估 compliance）
│   ├── effect_tracker.py          # 运动效果追踪（对照日对比，污染检测，CRS 评分）
│   ├── strategy_learner.py        # 策略学习引擎（贝叶斯收缩+安全约束+偏好生命周期）
│   ├── experiment_manager.py      # N-of-1 干预实验框架（Goal 关联，因果评估）
│   ├── pipeline_diff.py           # 流水线差异追踪（快照 before/after 对比）
│   └── feedback_collector.py      # 用户手动反馈提交（CLI）
│
├── reminders/                   # 就医提醒系统
│   ├── reminder_config.py         # 病情规则配置（ReminderRule dataclass）
│   ├── appointment_scheduler.py   # 自动推算下次应诊日期 + 写入 DB
│   └── reminder_notifier.py       # 微信通知（14天/7天阈值）+ 日报板块
│
├── insights/                    # 周期性洞察
│   └── llm_insights.py            # 周报生成（趋势+相关性+LLM 洞察）
│
├── tracking/                    # 用药追踪
│   └── medication_tracker.py      # 用药记录管理
│
└── dashboard/                   # Web 仪表盘（Streamlit）
    ├── app.py                     # 入口（密码保护 + 9 页导航）
    ├── data_loader.py             # 统一 DB 查询层（带 @st.cache_data 缓存）
    ├── views/                     # 9 个页面模块
    │   ├── overview.py            #   今日概览（5 KPI + 血压 + 体重 + 提醒 + AI 摘要）
    │   ├── historical_review.py   #   历史回顾（近3天建议执行回顾 + 目标进度快照）
    │   ├── preferences_page.py    #   个人偏好（策略学习偏好展示，含生命周期状态）
    │   ├── experiment_page.py     #   实验追踪（N-of-1 干预实验管理）
    │   ├── trends.py              #   趋势图表（HRV/血压/体重/压力/运动甘特图）
    │   ├── lab_results.py         #   化验趋势（统一视图/单指标/多指标对比）
    │   ├── correlations.py        #   相关性分析（热力图 + 散点回归）
    │   ├── prediction.py          #   预测分析（4 种风险评分 + 趋势外推 + 就医推荐）
    ├── components/                # 可复用组件
    │   ├── charts.py              #   Plotly 图表封装
    │   └── gauges.py              #   圆形仪表盘 + 因子贡献条
    └── prediction/                # 预测模型
        ├── uric_acid_risk.py      #   尿酸发作风险（6 因子加权评分）
        ├── hypertension_risk.py   #   高血压风险（5 因子加权评分）
        ├── hyperlipidemia_risk.py #   高血脂风险（5 因子加权评分）
        ├── hyperglycemia_risk.py  #   高血糖风险（中国2型糖尿病防治指南 2024）
        └── trend_predictor.py     #   短期趋势预测（14 天线性外推 7 天）
```

---

## 7. 每日流水线

`daily_pipeline.py` 是 cron 主入口，编排以下步骤：

```
run_garmin_daily.sh (cron 7:00, 带 flock 防并发)
  │
  └─► daily_pipeline.py
       │
       ├─ [1] 历史重试: 查 sync_logs 获取近 N 天失败日期，逐日重试拉取
       │
       ├─ [2] Garmin 拉取: 拉取昨天 + 今天数据（自动登录，3 次重试）
       │       → daily_health 表 + exercises 表（含 details）+ daily_health_audit
       │
       ├─ [3] 日历采集: fetch_calendar → calendar_events 表
       │       → CalendarSummary（busy_level / 连续会议 / 时间区间）
       │
       ├─ [4] 高级日报: HealthProfile → ModelSelector → AssessmentModels
       │       → LLM 建议（注入日程上下文 + 活跃实验约束）→ 生成报告文件 + 预写 recommendation_feedback
       │
       ├─ [5] 微信发送: 查找报告文件 → openclaw 发送（nonce 防缓存）
       │
       ├─ [6] 自动反馈: 读昨日建议 → 检查 exercises → 计算 compliance
       │       → 对比基线计算 perceived_effect → 更新 recommendation_feedback
       │
       ├─ [7] 效果追踪 + 策略学习:
       │       EffectTracker → 追踪运动后 HRV/睡眠/压力变化（对照日对比）
       │       StrategyLearner → 更新 learned_preferences（贝叶斯收缩 + 目标绑定 + 生命周期）
       │
       ├─ [8] 实验评估:
       │       ExperimentManager.check_and_evaluate() → 到期实验自动评估
       │       → causal.py 配对检验 / ITSA → completed（固化偏好）或 reverted
       │
       ├─ [9] 目标进度追踪:
       │       GoalManager.track_daily_progress() → 对每个 active goal
       │       计算当前值 → 写入 goal_progress（低频指标跳过）
       │
       └─ [10] 预约提醒: 刷新 appointments 表 → 检查 14/7 天阈值 → 微信通知
```

每一步记录 `sync_logs`，单步失败不阻断后续步骤。
高级日报的 LLM Prompt 自动注入：
- 活跃目标段落（P1/P2 优先级指令）
- 每日 CalendarSummary 日程上下文
- 活跃实验约束（`active_experiment` preference → LLM 遵循实验干预方案）

---

## 8. 配置参考

配置文件 `~/.superhealth/config.toml`（权限 0o600），环境变量覆盖。

```toml
[garmin]
email    = "your_email_or_phone"
password = "your_password"

[wechat]
account_id = "your-bot-account-id"
channel    = "openclaw-weixin"
target     = "openid@im.wechat"

[vitals]
api_token = "your-secret-token"
host      = "0.0.0.0"
port      = 5000

[claude]
api_key   = "sk-ant-..."
model     = "claude-sonnet-4-6"
max_tokens = 1024
base_url  = ""                    # 留空用官方，或填代理地址

[baichuan]
api_key   = "your-baichuan-key"
model     = "Baichuan-M3-Plus"
max_tokens = 1024
base_url  = "https://api.baichuan-ai.com/v1"

[advisor]
mode = "claude_only"              # claude_only | baichuan_only | both

[weather]
api_key     = "your_qweather_key"
city        = "北京"
location_id = "101010100"
api_host    = "xxxx.re.qweatherapi.com"
latitude    = 39.92
longitude   = 116.41

[dashboard]
password = ""                     # 空=不设密码

[outlook]
email    = "your@outlook.com"
password = "your-exchange-password"
server   = "outlook.office365.com"
```

| 环境变量 | 覆盖字段 |
|----------|----------|
| `HEALTHY_GARMIN_EMAIL` / `_PASSWORD` | garmin.email / .password |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` | claude.api_key / .base_url |
| `BAICHUN_API_KEY` | baichuan.api_key |
| `HEALTHY_ADVISOR_MODE` | advisor.mode |
| `HEALTHY_DASHBOARD_PASSWORD` | dashboard.password |

---

## 9. 仪表盘

Streamlit 应用，入口 `streamlit run src/superhealth/dashboard/app.py --server.port=8505`，端口 8505。

### 9.1 页面

| 页面 | 功能 | 数据源 |
|------|------|--------|
| **今日概览** | 5 KPI 卡片（Body Battery/静息心率/HRV/睡眠/压力）+ 血压 + 体重体脂 + 就医提醒 + AI 建议 | daily_health, vitals, appointments |
| **历史回顾** | 近3天建议执行回顾（compliance 评级）+ 目标进度快照 + 效果追踪详情 | recommendation_feedback, goal_progress |
| **个人偏好** | 策略学习偏好展示（按类型/状态过滤，含生命周期状态 active/committed/reverted） | learned_preferences |
| **实验追踪** | N-of-1 干预实验管理：活跃实验监控、Goal 关联干预推荐、草稿激活、历史实验回顾 | experiments, goals, goal_progress |
| **状态趋势** | HRV+BB 双轴、血压趋势、体重+体脂、压力指数、运动甘特图。支持 30/90/180/365 天范围 | daily_health, vitals, exercises |
| **化验趋势** | 三种视图（合并/单指标/多指标对比）。尿酸/肌酐/eGFR/肝功能/血脂/眼压。含就医时间线 | lab_results, eye_exams, annual_checkups |
| **相关性分析** | 指标间热力图 + 散点回归图。可选 HRV/BB/睡眠/血压/体重/体脂/步数/运动时长/压力 | daily_health, vitals, exercises |
| **预测分析** | 4 种风险评分（尿酸/高血压/高血脂/高血糖）+ 7 天趋势预测 + 就医时机推荐 | vitals, lab_results, weather, daily_health |

### 9.2 预测模型

**尿酸发作风险**（0-100 分）：

| 因子 | 权重 | 数据源 |
|------|------|--------|
| 尿酸最新值（vs 420 μmol/L） | 30% | lab_results |
| 尿酸趋势（近 3 次斜率） | 20% | lab_results |
| 本周高强度运动量 | 15% | exercises |
| 近 7 日体重变化 | 10% | vitals |
| 天气风险（低温/AQI） | 10% | weather |
| 距上次化验天数 | 15% | lab_results |

**高血压风险**（0-100 分）：收缩压最新值(35%) + 收缩压趋势(25%) + 压力指数(20%) + 静息心率(10%) + BMI(10%)

**高血脂风险**（0-100 分）：TG(30%) + LDL(30%) + 血脂趋势(20%) + 体重变化(10%) + 化验间隔(10%)

**高血糖风险**（0-100 分）：基于《中国2型糖尿病防治指南 2024》，多因子加权评分

**趋势预测**：对近 14 天数据用 `LinearRegression` 外推 7 天（HRV/体重/收缩压），含 95% 置信区间。

---

## 10. 反馈与学习系统（目标感知）

### 10.1 自动反馈 (`auto_feedback.py`)

每日 morning 模式全自动运行：
1. 读昨日 `recommendation_feedback`（Phase 1 写入的建议内容）
2. 从 `exercises` 表判断是否运动 → 计算 compliance（5 路判断逻辑）
3. 对比今日 `daily_health` 与 5 日基线（sleep_score / body_battery_at_wake / stress_average）→ 自动生成 perceived_effect
4. 更新 `recommendation_feedback` 表
5. **quality_score 计算**：`0.30×compliance + 0.25×goal_progress + 0.25×effect + 0.20×user_rating`

### 10.2 效果追踪 (`effect_tracker.py`)

追踪运动后 1-3 天的生理指标变化：
- 信号：HRV / 睡眠评分 / 压力指数 / Body Battery / 收缩压
- 对照组：找相似状态（HRV 区间、睡眠评分、运动负荷）的休息日作为基线
- 污染日检测：排除高压力日、酒精日、日历忙碌等干扰因素
- 输出写入 `tracked_metrics` JSON 字段
- **目标感知评分**：`compute_goal_aligned_score()` 对目标指标权重提升 1.5 倍，按目标视角评估运动效果

### 10.3 策略学习 (`strategy_learner.py`)

从反馈数据中学习个人偏好：
- 措施有效性：哪种运动类型对你恢复最好
- 个人响应模式：你的 HRV/睡眠对哪种运动最敏感
- 剂量-反应关系：最佳运动强度区间
- 时间偏好：哪个时段运动效果最好
- 使用贝叶斯收缩避免小样本过拟合
- 安全约束：医学常识兜底（如 HRV<30 不推荐高强度）
- **目标绑定**：偏好写入时带 `goal_id`（P1 目标优先），支持按目标查询最佳运动
- **偏好生命周期**：active → committed（avg_q ≥ 0.70，证据充分）或 reverted（avg_q ≤ 0.30）
  - 新偏好有 7 天保护期
  - committed 偏好进入 LLM Prompt 影响建议
  - reverted 偏好不再推荐但仍保留记录
- `top_exercises_for_goal(goal_id)` 返回对指定目标最有效的 top-3 运动
- 10 条反馈记录起效

---

## 11. N-of-1 干预实验框架

基于阶段性 Goal 的结构化自我实验系统，将"尝试某个干预"升级为可统计验证的实验流程。

### 11.1 核心设计

- **与 Goal 绑定**：每个实验关联一个 `goal_id`，复用 `metric_key` 和 `goal_progress` 数据
- **一次一个活跃实验**：系统保证同时仅有一个 `active` 实验，避免干预混淆
- **循证干预候选库**：`GOAL_INTERVENTIONS` 按 metric_key 预置 3-5 个经指南验证的干预方案
- **LLM 生成增强**：首次调用百川生成个性化干预方案并缓存，后续读取缓存；支持强制重新生成

### 11.2 实验生命周期

```
CLI/页面创建 → draft ──(用户激活)──→ active ──(每日 check_and_evaluate)──→ evaluating
                                                             │
                                    ├─ 显著有效 (p<0.1, Cohen's d≥0.3, 方向匹配) → completed
                                    │   → 固化为 learned_preferences (experiment_conclusion)
                                    │
                                    ├─ 显著无效/方向相反 → reverted
                                    │   → 记录回退偏好，不再推荐
                                    │
                                    └─ 不显著且可延长 → 自动延长 7 天（最多 28 天）
```

### 11.3 统计评估

实验到期时自动调用 `causal.py` 的统计工具：
- **配对检验**（`paired_intervention_test`）：基线期 vs 干预期均值差异，Cohen's d 效应量，Welch's t-test
- **ITSA 间断时间序列**（`interrupted_time_series`）：评估干预点是否带来水平跳跃和斜率变化
- **评估标准**：`p < 0.1` + `|Cohen's d| ≥ 0.3` + 方向匹配 → commit；方向相反 → revert；否则 inconclusive（可延长）

### 11.4 LLM 约束注入

活跃实验通过 `learned_preferences`（`preference_type='active_experiment'`）向 LLM 传递约束：
- 日报生成时，LLM 读到活跃实验偏好，建议内容遵循实验干预方案
- 实验完成后，结论偏好（`experiment_conclusion`）进入策略学习池，长期影响建议生成

---

## 12. 因果推断引擎

`causal.py` 提供从关联到因果的分析升级，纯 Python + numpy 实现，无需 scipy。

### 12.1 Granger 因果检验

判断 X 的过去值是否有助于预测 Y 的未来值：
- 受限模型：`Y_t ~ 1 + Y_{t-1}..Y_{t-p}`
- 非受限模型：`Y_t ~ 1 + Y_{t-1}..Y_{t-p} + X_{t-1}..X_{t-p}`
- F 统计量 + Wilson-Hilferty 近似 p 值
- 预设 8 组关键因果对（如睡眠评分→次日 HRV、步数→次日 HRV 等）

### 12.2 干预前后配对检验

评估某个干预点（如 goal 启动日、用药开始日）前后的指标变化：
- 基线期 vs 干预期日均值对比
- Welch's t-test（更稳健，不要求方差齐性）
- Cohen's d 效应量
- 支持自动从 `goals` 表读取 `start_date`

### 12.3 间断时间序列分析（ITSA）

评估干预点是否带来显著的水平/斜率变化：
- 模型：`Y_t = β0 + β1·time + β2·intervention + β3·time_after + ε`
- β2：水平变化（干预点跳跃）；β3：斜率变化（干预后趋势改变）
- 输出 R²、水平/斜率 p 值

---

## 13. 流水线差异追踪

`pipeline_diff.py` 用于追踪策略学习和效果追踪的运行前后差异，支持调试和回溯。

### 13.1 功能

- **快照**：捕获当前 `learned_preferences`、`tracked_metrics`、中间计算参数（personal_stds / global_stds / schedule_stds）
- **对比**：逐层对比两个快照，输出结构化 diff
- **报告**：格式化可读报告，含根因提示（窗口滑动、偏好衰减、对照组变化等）

### 13.2 用法

```bash
python -m superhealth.feedback.pipeline_diff snapshot before   # 快照当前状态
python -m superhealth.feedback.pipeline_diff snapshot after    # 快照新状态
python -m superhealth.feedback.pipeline_diff diff              # 对比最近两次快照
python -m superhealth.feedback.pipeline_diff run --days 180    # 自动：快照→跑全量→快照→对比
```

---

## 14. 就医提醒系统

### 14.1 规则

| 病情 | 医院 | 科室 | 复诊间隔 | 数据来源 |
|------|------|------|----------|----------|
| 青光眼 | 示例医院 | 眼科 | 3 个月 | eye_exams |
| 高尿酸 | 示例医院 | 肾内科 | 6 个月 | lab_results |
| 年度体检 | — | — | 12 个月 | annual_checkups |

### 14.2 提醒流程

1. `appointment_scheduler.py`：从最近一次检查记录推算下次应诊日期 → 写入 `appointments` 表
2. `reminder_notifier.py`：每日检查 `appointments`，距应诊日期 14 天 / 7 天时通过微信发送提醒
3. 高级日报末尾追加"近期就诊提醒"板块

---

## 15. 日历采集系统

### 15.1 Outlook/Exchange 集成

`outlook_collector.py` 通过 EWS (Exchange Web Services) 协议拉取当天日历事件：

- 事件提取：主题、时间、时长、是否全天/重复
- 忙碌等级计算（`CalendarSummary.busy_level`）：
  - `high`：全天事件 或 会议>4h 或 连续会议≥3组
  - `medium`：会议>2h 或 连续会议≥2组
  - `low`：其他
- 连续会议检测：间隔≤15min 的连续块归为一组
- DB 缓存：`calendar_events` 表，避免重复拉取

### 15.2 日程注入闭环

| Hook 位置 | 改造方式 |
|-----------|----------|
| daily_pipeline | Step [3] fetch_calendar → calendar_events 表 |
| advanced_daily_report | CalendarSummary 注入 LLM User Prompt |
| effect_tracker | 污染日检测：日历忙碌日排除在对照组外 |

---

## 16. 阶段性目标子系统（Goals）

目标驱动的健康闭环，让整个建议-追踪-学习流程围绕用户当前阶段最重要的指标运转。

### 16.1 指标白名单（11 个）

| metric_key | 说明 | 数据源 | 频率 |
|------------|------|--------|------|
| `bp_systolic_mean_7d` | 7天收缩压均值 | vitals.systolic | 每日 |
| `bp_diastolic_mean_7d` | 7天舒张压均值 | vitals.diastolic | 每日 |
| `body_battery_wake_mean_7d` | 晨起 BB 7日均值 | daily_health.bb_at_wake | 每日 |
| `sleep_score_mean_7d` | 睡眠分 7日均值 | daily_health.sleep_score | 每日 |
| `hrv_mean_7d` | HRV 7日均值 | daily_health.hrv_last_night_avg | 每日 |
| `resting_hr_mean_7d` | 静息心率 7日均值 | daily_health.hr_resting | 每日 |
| `weight_kg_mean_7d` | 体重 7日均值 | vitals.weight_kg | 每日 |
| `body_fat_pct_mean_7d` | 体脂率 7日均值 | vitals.body_fat_pct | 每日 |
| `steps_mean_7d` | 步数 7日均值 | daily_health.steps | 每日 |
| `stress_mean_7d` | 压力 7日均值 | daily_health.stress_average | 每日 |
| `uric_acid_latest` | 最近化验尿酸值 | lab_results | 低频 |
| `iop_mean_recent` | 眼压均值 | eye_exams | 低频 |

低频指标不参与每日 progress 快照，仅在检测到新数据时触发评估。非白名单指标在 CLI 中被拒绝。

### 16.2 生命周期

```
CLI add → active ──(每日 track_daily_progress)──→ goal_progress 表
                    │
                    ├─ 连续7个非空日达标 → 日报高亮"达成候选"，CLI achieve 确认
                    ├─ 30天无进展(反方向) → 日报标注 off_track
                    ├─ CLI pause → paused
                    └─ CLI abandon → abandoned
```

**关键约束**：系统仅提示，不自动改 status。所有状态变更必须 CLI 手动触发。

### 16.3 目标注入闭环

| Hook 位置 | 改造方式 |
|-----------|----------|
| HealthProfileBuilder | `build()` 末尾加载 `active_goals`（含最新进度） |
| ModelSelector | `select()` 按 metric_key → guide_key 映射强制激活目标相关模型 |
| LLMAdvisor | System Prompt 注入 `### 当前阶段性目标` 段落 + P1/P2 优先级指令 |
| EffectTracker | `compute_goal_aligned_score()` 对目标指标权重 ×1.5 |
| StrategyLearner | 偏好写入带 `goal_id`，`top_exercises_for_goal()` 按目标查最佳运动 |
| ExperimentManager | `suggest_for_goal()` 按目标 metric_key 推荐干预实验 |

---

## 17. 部署与运维

### 17.1 Cron 设置

```bash
# /etc/cron.d/superhealth
0 7 * * * root /root/superhealth/scripts/run_daily_pipeline.sh >> /root/superhealth/logs/daily_pipeline.log 2>&1
```

`run_garmin_daily.sh` 使用 `flock /tmp/flock_garmin_daily.lock` 防并发。

### 17.2 常用命令

```bash
# 每日流水线（cron 入口）
python -m superhealth.daily_pipeline
python -m superhealth.daily_pipeline --date 2026-04-14        # 指定业务日期
python -m superhealth.daily_pipeline --test-mode              # 仅重新生成高级日报，不写 DB
python -m superhealth.daily_pipeline --retry-days 14          # 检查历史失败天数（默认 7）

# 数据采集
python -m superhealth.collectors.fetch_garmin                 # 拉取今日 + 昨日
python -m superhealth.collectors.fetch_garmin --date 2026-04-01

# 天气采集
python -m superhealth.collectors.weather_collector

# 报告
python -m superhealth.reports.daily_report --date 2026-04-04          # 基础日报
python -m superhealth.reports.advanced_daily_report --date 2026-04-04 # 高级日报

# 用户反馈
python -m superhealth.feedback.feedback_collector --date 2026-04-08 --feedback "今天肌肉酸痛"

# 分析
python -m superhealth.analysis.trends                         # 趋势分析
python -m superhealth.analysis.correlation                    # 相关性分析
python -m superhealth.analysis.causal                         # 因果推断报告

# 学习
python -m superhealth.feedback.strategy_learner               # 策略学习
python -m superhealth.feedback.pipeline_diff run --days 180   # 全量差异追踪

# 实验管理（CLI）
python -m superhealth.feedback.experiment_manager             # 实验列表与管理

# 周报
python -m superhealth.insights.llm_insights --date 2026-04-04

# 就医提醒
python -m superhealth.reminders.appointment_scheduler --dry-run
python -m superhealth.reminders.reminder_notifier --dry-run

# 用药追踪
python -m superhealth.tracking.medication_tracker

# 阶段性目标（Goals 子系统）
python -m superhealth.goals list                                        # 列出当前目标
python -m superhealth.goals metrics                                     # 查看可用指标白名单
python -m superhealth.goals add --name "降血压" --priority 1 \
  --metric bp_diastolic_mean_7d --direction decrease \
  --target 75 --target-date 2026-07-31                              # 添加目标
python -m superhealth.goals progress 1                                  # 查看目标进度
python -m superhealth.goals achieve 1                                   # 标记达成
python -m superhealth.goals pause 1                                     # 暂停目标
python -m superhealth.goals abandon 1                                   # 废弃目标

# Web 仪表盘
PYTHONPATH=src streamlit run src/superhealth/dashboard/app.py --server.port=8505
# 浏览器访问 http://localhost:8505

# Schema 同步（幂等，可反复执行）
python -c "from superhealth.database import init_db; init_db()"
```

### 17.3 Schema 迁移

拉取新代码后执行一次 `init_db()`，幂等地创建缺失的表和列。

---

## 18. Phase 完成记录

| Phase | 内容 | 状态 | 完成日期 |
|-------|------|------|----------|
| 0 | 修复关键 Bug | ✅ 完成 | 2026-03-23 |
| 1 | 工程基础重构（包结构/Pydantic/配置管理/53 个测试） | ✅ 完成 | 2026-03-29 |
| 2 | SQLite 存储（10 张表 + 迁移脚本） | ✅ 完成 | 2026-03-29 |
| 3 | 体征自动采集（Health Auto Export → Flask → SQLite） | ✅ 完成 | 2026-03-29 |
| 4 | 智能健康决策引擎（画像+模型选择+LLM+天气+高级日报） | ✅ 完成 | 2026-04-04 |
| 5 | 自动反馈闭环（compliance+效果追踪+策略学习） | ✅ 完成 | 2026-04-04 |
| 6 | 就医提醒系统（3 种病情+自动推算+微信通知） | ✅ 完成 | 2026-04-06 |
| 7 | Web 可视化仪表盘（8 页面+5 预测模型） | ✅ 完成 | 2026-04-10 |
| 8 | 阶段性目标子系统（目标存储+指标追踪+目标注入+Dashboard） | ✅ 完成 | 2026-04-21 |
| 9 | Dashboard 概览 KPI 增强 + 策略学习鲁棒性提升 + 日历采集集成 | ✅ 完成 | 2026-04-27 |
| 10 | 因果推断引擎 + N-of-1 干预实验框架 + 流水线差异追踪 + Dashboard 实验页面 | ✅ 完成 | 2026-04-30 |

---

## 19. 安全考虑

- 配置文件 `~/.superhealth/config.toml` 权限 0o600，包含所有 API 密钥
- 数据库 `health.db` 仅本地存储，不上传云端（.gitignore 已排除）
- 仪表盘支持密码保护（可选，配置 `[dashboard].password`，含记住密码功能）
- 微信推送通过 openclaw 本地命令，不经第三方中转
- 医学建议由 LLM 生成，仅供数据洞察参考，不替代医生诊断
- `daily_health_audit` 表记录核心指标变更历史，支持追溯效果评估变化原因
- 实验框架的统计结论基于个人数据，样本量有限，不具普适性；显著性阈值放宽至 `p<0.1`，仅供个人决策参考
