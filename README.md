# SuperHealth

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/chasezjj/superhealth/actions/workflows/ci.yml/badge.svg)](https://github.com/chasezjj/superhealth/actions/workflows/ci.yml)

**AutoResearch × 个人智能健康管理系统**

> 不是千人一面的建议，而是一个围绕你的目标、数据和反馈持续学习的本地健康闭环。

SuperHealth 整合 Garmin、Health Auto Export、Outlook 日历、天气、体检/门诊/影像/基因文档和手动录入数据，在本地 SQLite 中形成长期健康档案。系统会自动构建健康画像、选择相关评估模型、生成 LLM 个性化建议，并通过目标进度、执行反馈、运动效果归因和 N-of-1 实验不断修正策略。

> **真实验证**：目标为降低舒张压时，按照系统建议执行一周后，舒张压从 81.4 mmHg 降至 74.9 mmHg（下降 7.9%）。

![Dashboard](examples/indexPage.png)

示例输出：[每日健康日报](examples/daily_report_example.md)

---

## 当前能力

- **多源数据采集**：Garmin Connect CN、Health Auto Export REST、和风天气、Outlook/Exchange 日历。
- **结构化医疗档案**：支持 PDF/图片上传，调用大模型提取体检、门诊、化验、影像、出院小结、基因报告，保存为 Markdown + 结构化观测项。
- **目标闭环**：阶段性目标、实验追踪、执行回顾、个人偏好学习。
- **健康画像与评估模型**：自动汇总疾病、遗传风险、异常指标、用药、禁忌和活跃目标，并动态选择评估维度。
- **慢性病风险与趋势预测**：高血压、高尿酸、高血脂、高血糖风险模型，以及 7 天趋势预测。
- **反馈学习**：自动/手动反馈、匹配对照运动效果归因、贝叶斯偏好学习、偏好生命周期管理。
- **N-of-1 干预实验**：为目标生成干预候选，支持激活、取消、到期评估和结论固化/回退。
- **日报与周报**：高级日报结合画像、天气、日程和 LLM 建议；每周生成 LLM 趋势洞察。
- **Web 仪表盘**：Streamlit 仪表盘，带可选密码保护、文档上传、Garmin 数据管理和系统配置页。
- **本地优先**：核心健康数据存放在本机 SQLite；LLM 调用只在启用相关功能时发生。

---

## AutoResearch 闭环

```
        你的目标（血压 / 血糖 / 血脂 / 尿酸 / HRV / 睡眠 / 体重 / 压力）
                             │
        ┌────────────────────▼────────────────────┐
        │                                          │
   ① 采集                                    ⑤ 学习改进
 Garmin / 血压 / 体重                      贝叶斯偏好学习
 天气 / 日历 / 医疗文档                    N-of-1 干预实验
 基因 / 体检 / 门诊                        策略生命周期管理
        │                                          │
        ▼                                          ▲
   ② 分析                                    ④ 跟踪
 健康画像自动构建                          执行反馈采集
 评估模型动态激活                          目标进展快照
 因果与趋势分析                            运动效果归因
        │                                          │
        └─────────────► ③ 建议 ◄─────────────────┘
                      权威指南 × 个人基线
                      LLM 个性化建议
                      日历感知 × 实验约束
```

支持的阶段目标指标包括：7 天收缩压/舒张压均值、晨起 Body Battery、睡眠分、HRV、静息心率、体重、体脂率、步数和压力均值。

---

## 数据架构

SuperHealth 现在使用通用医疗文档模型承载低频医疗数据，不再依赖眼科、肾脏、年度体检等疾病专用表。

| 层级 | 数据内容 | 更新频率 | 采集方式 |
|------|----------|---------|---------|
| 基因层 | 基因检测报告、遗传风险标记 | 通常一次 | 文档上传 / Markdown |
| 医疗层 | 年度体检、门诊病历、化验单、影像/超声、出院小结、用药和疾病清单 | 月 / 季度 / 年 | 文档上传 + AI 结构化 + 手动修正 |
| 日常层 | Garmin 睡眠、HRV、心率、压力、步数、Body Battery，血压、体重、体脂 | 每日 / 多次每日 | 自动采集 / REST 接收 |
| 上下文层 | 天气、空气质量、日历忙碌程度 | 每日 | API / EWS |

核心 SQLite 表包括：

- `daily_health`、`exercises`、`vitals`、`weather`、`calendar_events`
- `medical_documents`、`medical_observations`、`medical_conditions`、`condition_metric_mappings`
- `medications`、`medication_effects`
- `goals`、`goal_progress`、`experiments`
- `recommendation_feedback`、`learned_preferences`
- `appointments`、`sync_logs`、`daily_health_audit`

Schema 单一来源是 [schema.sql](schema.sql)。

---

## 核心算法

### 健康画像构建

`core/health_profile_builder.py`

从日常数据、医疗文档、观测项、疾病清单、用药、目标和学习偏好中构建统一健康画像。画像会影响评估模型选择、LLM 提示词、风险判断和实验建议。

### 自适应评估引擎

`core/model_selector.py` + `core/assessment_models.py`

系统根据画像和目标动态激活恢复力、心血管、代谢、血脂、青光眼、睡眠、体成分、压力和遗传风险等评估模型。评分尽量锚定个人历史基线，而不是简单对比人群平均值。

### 慢性病风险量化

`dashboard/prediction/`

- 高血压：血压分级、危险因素、器官损害、合并症。
- 高尿酸：尿酸水平、诱发因素、合并症。
- 高血脂：TG、LDL-C、趋势、体重变化、检测间隔。
- 高血糖 / T2DM：空腹血糖、HbA1c、家族史、肥胖、年龄。
- 7 天趋势预测：基于近 14 天线性回归，输出预测值和置信区间。

### 匹配对照效果归因

`feedback/effect_tracker.py`

系统会把运动日与历史上 HRV、睡眠、压力、日程繁忙度相似的非运动日做对照，估算净运动效果，并排除高压力、饮酒、疾病等污染日。目标相关指标会获得更高权重。

### 贝叶斯偏好学习

`feedback/strategy_learner.py`

从执行反馈、目标进展、运动效果和用户评分中学习个人偏好，覆盖运动类型、时长、心率区间、时间段和当日 HRV 状态。学习到的偏好会回写 `learned_preferences`，并注入后续建议。

### N-of-1 干预实验

`feedback/experiment_manager.py`

系统为目标生成 14-28 天个人实验，尽量一次只验证一个干预。到期后结合 Granger 因果检验、间断时间序列、Welch t 检验和效果量判断结论，成功的策略会固化为偏好，反向结果会回退。

---

## Web 仪表盘

当前 Streamlit 主导航是 7 个组合页：

| 页面 | 主要内容 |
|------|----------|
| 今日概览 | 当日恢复、睡眠、压力、活动、目标摘要 |
| 目标闭环 | 阶段目标、实验追踪、执行回顾、个人偏好 |
| 趋势分析 | 状态趋势、相关性分析、预测分析 |
| 化验与风险 | 化验趋势、慢性病风险 |
| 数据管理 | 医疗文档上传、Garmin 数据管理 |
| 健康档案 | 自动构建的健康画像 |
| 系统配置 | 配置文件、Health Auto Export 服务、crontab 定时任务 |

仪表盘支持可选密码保护，密码写入 `~/.superhealth/config.toml` 的 `[dashboard] password` 字段。

---

## 快速开始

### 安装

```bash
git clone https://github.com/chasezjj/superhealth.git
cd superhealth
pip3 install -e ".[all,dev]"
playwright install chromium
```

按需安装：

```bash
pip3 install -e "."             # 核心功能
pip3 install -e ".[garmin]"     # Garmin 数据采集
pip3 install -e ".[claude]"     # Claude 建议与文档提取
pip3 install -e ".[baichuan]"   # 百川医疗模型
pip3 install -e ".[dev]"        # pytest / ruff / mypy
```

### 配置

```bash
mkdir -p ~/.superhealth
cp examples/config.example.toml ~/.superhealth/config.toml
chmod 600 ~/.superhealth/config.toml
```

常用配置段：

- `[garmin]`：Garmin Connect CN 登录信息。
- `[vitals]`：Health Auto Export 接收端 token、host、port。
- `[claude]`：高级日报、AI 建议和医疗文档提取。
- `[baichuan]` + `[advisor]`：百川医疗模型和模型选择模式。
- `[weather]`：和风天气。
- `[outlook]`：Exchange/Outlook 日历。
- `[wechat]`：OpenClaw 微信推送，字段为 `account_id`、`channel`、`target`。
- `[dashboard]`：仪表盘访问密码。

### 初始化数据库

```bash
python3 -c "from superhealth.database import init_db; init_db()"
```

数据库默认创建在项目根目录 `health.db`。可以通过 `SUPERHEALTH_DB` 指定其他路径：

```bash
export SUPERHEALTH_DB=~/.superhealth/health.db
```

### 启动仪表盘

```bash
python3 -m superhealth dashboard --server.port=8505
```

也可以直接运行 Streamlit：

```bash
PYTHONPATH=src streamlit run src/superhealth/dashboard/app.py --server.port=8505
```

访问 `http://localhost:8505`。

### 导入示例数据

```bash
sqlite3 health.db < examples/sample_data.sql
```

### Docker 部署

```bash
docker compose up -d
```

默认访问 `http://localhost:8505`。

---

## 常用命令

```bash
# 每日流水线
python3 -m superhealth pipeline
python3 -m superhealth.daily_pipeline --date 2026-05-05
python3 -m superhealth.daily_pipeline --test-mode

# Garmin 数据采集
python3 -m superhealth.collectors.fetch_garmin --login
python3 -m superhealth.collectors.fetch_garmin --date 2026-05-05
python3 -m superhealth.collectors.fetch_garmin --range 2026-05-01 2026-05-05

# Health Auto Export REST 接收端
python3 -m superhealth.api.vitals_receiver

# 天气与日历
python3 -m superhealth.collectors.weather_collector --date 2026-05-05

# 日报 / 周报
python3 -m superhealth.reports.daily_report --date 2026-05-05
python3 -m superhealth.reports.advanced_daily_report --date 2026-05-05
python3 -m superhealth.insights.llm_insights --date 2026-05-05

# 阶段目标
python3 -m superhealth goals metrics
python3 -m superhealth goals list
python3 -m superhealth goals add --name "降低舒张压" \
  --metric bp_diastolic_mean_7d --direction decrease --target 75
python3 -m superhealth goals progress 1 --days 30

# 实验
python3 -m superhealth goals experiment suggest --goal-id 1
python3 -m superhealth goals experiment list

# 反馈与学习
python3 -m superhealth.feedback.auto_feedback --date 2026-05-04
python3 -m superhealth.feedback.strategy_learner --days 180
python3 -m superhealth.feedback.pipeline_diff snapshot before

# 分析与提醒
python3 -m superhealth.analysis.trends
python3 -m superhealth.analysis.correlation
python3 -m superhealth.reminders.appointment_scheduler --dry-run
python3 -m superhealth.reminders.reminder_notifier --dry-run
```

安装后也会提供脚本入口：

```bash
superhealth-pipeline
superhealth-goals list
```

---

## 目录结构

```text
superhealth/
├── src/superhealth/
│   ├── api/                  # Health Auto Export REST 接收端
│   ├── analysis/             # 趋势、相关性、因果推断
│   ├── collectors/           # Garmin / 天气 / 日历 / 微信发送
│   ├── core/                 # 健康画像、文档提取、模型选择、LLM 建议
│   ├── dashboard/            # Streamlit 仪表盘
│   ├── feedback/             # 反馈、效果追踪、策略学习、实验
│   ├── goals/                # 阶段目标子系统
│   ├── insights/             # 周期性 LLM 洞察
│   ├── reminders/            # 就医提醒
│   ├── reports/              # 基础日报和高级日报
│   ├── tracking/             # 用药追踪
│   ├── config.py             # 配置管理
│   ├── database.py           # SQLite 存储层
│   └── daily_pipeline.py     # 每日流水线
├── tests/                    # pytest 测试
├── examples/                 # 示例配置、示例数据、截图
├── scripts/                  # cron 辅助脚本
├── docs/                     # 设计文档
├── schema.sql                # 数据库 Schema 单一来源
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

---

## 开发

```bash
pip3 install -e ".[dev]"
pytest
ruff check src/ tests/
mypy src/ --ignore-missing-imports
```

## 常见问题

<details>
<summary>Garmin 登录失败怎么办？</summary>

确认使用 Garmin Connect CN 账号，并先运行：

```bash
python3 -m superhealth.collectors.fetch_garmin --login
```

如果 Playwright 浏览器未安装，运行：

```bash
playwright install chromium
```

</details>

<details>
<summary>如何只使用部分功能？</summary>

大多数集成都是可选的。未配置 Garmin 会跳过采集，未配置 Outlook 会跳过日历，未配置 Claude/百川会跳过对应 LLM 功能。基础日报、数据库和部分仪表盘页面仍可使用。

</details>

<details>
<summary>数据存在哪里？</summary>

默认数据库是项目根目录下的 `health.db`，医疗文档和上传文件保存在项目 `data/` 目录。可以用 `SUPERHEALTH_DB` 改数据库路径。

LLM 功能启用时，会把相关摘要或上传文档发送给配置的模型服务用于生成建议或结构化提取；未启用 LLM 时不会发生这些调用。

</details>

---

## 贡献

欢迎贡献，请参阅 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

[MIT](LICENSE)

## 免责声明

本系统提供的所有健康建议仅供参考，不替代专业医疗诊断。如有健康问题，请咨询专业医生。

---

<details>
<summary>English Introduction</summary>

## SuperHealth — AutoResearch × Personal AI Health System

SuperHealth is a local-first personal health system that connects daily wearable data, medical records, lab reports, calendar context, weather, goals, feedback, and LLM-based advice into a continuous learning loop.

It is designed around one idea: recommendations should not stay generic. The system tracks whether each recommendation worked, learns from your own responses, and keeps refining future strategies around your active goals.

### What It Does

- **Multi-source collection**: Garmin Connect CN, Health Auto Export REST, QWeather, Outlook/Exchange calendar, and manual records.
- **Structured medical records**: Upload PDF/images for checkups, outpatient notes, labs, imaging reports, discharge summaries, and genetic reports. Claude Vision can extract them into Markdown plus structured observations.
- **Goal-driven loop**: Stage goals, daily progress snapshots, experiment tracking, historical review, and learned preferences.
- **Adaptive health profile**: Builds a unified profile from conditions, observations, medications, genetic risks, goals, and feedback.
- **Risk and trend models**: Hypertension, hyperuricemia, dyslipidemia, hyperglycemia/T2DM, plus 7-day trend prediction.
- **Feedback learning**: Compliance feedback, matched-control exercise attribution, Bayesian preference learning, and preference lifecycle management.
- **N-of-1 experiments**: Personal intervention experiments with activation, cancellation, expiry evaluation, and conclusion rollback/commit.
- **Reports and dashboard**: Daily advanced reports, weekly LLM insights, and a Streamlit dashboard with optional password protection.
- **Local-first storage**: Core health data is stored in local SQLite. LLM calls only happen when related features are enabled.

### AutoResearch Loop

```text
        Your Goal (BP / Glucose / Lipids / Uric Acid / HRV / Sleep / Weight / Stress)
                                      |
        +-----------------------------v-----------------------------+
        |                                                           |
   1. Collect                                                 5. Learn
 Garmin / BP / Weight                                  Bayesian preference learning
 Weather / Calendar / Medical docs                     N-of-1 experiments
 Genes / Checkups / Visits                             Strategy lifecycle
        |                                                           |
        v                                                           ^
   2. Analyze                                                 4. Track
 Auto health profile                                    Feedback collection
 Adaptive assessment models                             Goal progress snapshots
 Trends and causal analysis                             Exercise effect attribution
        |                                                           |
        +----------------------> 3. Advise <------------------------+
                         Clinical guidelines x personal baseline
                         LLM-personalized advice
                         Calendar-aware and experiment-constrained
```

Supported goal metrics include 7-day averages for systolic/diastolic blood pressure, morning Body Battery, sleep score, HRV, resting heart rate, weight, body fat percentage, steps, and stress.

### Data Model

SuperHealth now uses a generic medical-document model for low-frequency clinical data instead of disease-specific tables. The main SQLite entities include:

- `daily_health`, `exercises`, `vitals`, `weather`, `calendar_events`
- `medical_documents`, `medical_observations`, `medical_conditions`, `condition_metric_mappings`
- `medications`, `medication_effects`
- `goals`, `goal_progress`, `experiments`
- `recommendation_feedback`, `learned_preferences`
- `appointments`, `sync_logs`, `daily_health_audit`

The schema source of truth is [schema.sql](schema.sql).

### Dashboard

The current Streamlit dashboard has 7 main sections:

| Page | Content |
|------|---------|
| Today Overview | Daily recovery, sleep, stress, activity, goal summary |
| Goal Loop | Stage goals, experiments, execution review, learned preferences |
| Trend Analysis | Trends, correlations, prediction |
| Labs and Risk | Lab trends and chronic disease risk |
| Data Management | Medical document upload and Garmin data management |
| Health Profile | Automatically built health profile |
| System Config | Config file, Health Auto Export service, crontab jobs |

### Quick Start

```bash
git clone https://github.com/chasezjj/superhealth.git
cd superhealth
pip3 install -e ".[all,dev]"
playwright install chromium

mkdir -p ~/.superhealth
cp examples/config.example.toml ~/.superhealth/config.toml
chmod 600 ~/.superhealth/config.toml

python3 -c "from superhealth.database import init_db; init_db()"
python3 -m superhealth dashboard --server.port=8505
```

Then open `http://localhost:8505`.

MIT licensed. Health recommendations are for reference only and do not replace professional medical diagnosis or treatment.

</details>
