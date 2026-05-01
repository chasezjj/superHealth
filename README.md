# SuperHealth

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/chasezjj/superhealth/actions/workflows/ci.yml/badge.svg)](https://github.com/chasezjj/superhealth/actions/workflows/ci.yml)

个人健康数据管理系统，整合可穿戴设备、血压计、体脂秤等多源数据，实现自动化采集、长期趋势分析、因果推断和 LLM 驱动的个性化健康洞察。

![Dashboard](superHealth.png)

## 特性

- **多源数据采集**：Garmin Connect、Health Auto Export（血压/体重）、和风天气 API
- **本地优先存储**：SQLite，数据不上传云端
- **LLM 健康顾问**：支持 Anthropic Claude / 百川医疗大模型
- **智能评估引擎**：9 个维度的健康评估模型，自动构建健康画像
- **阶段性目标追踪**：目标驱动的建议-追踪-学习闭环
- **风险预测模型**：尿酸、高血压、高血脂、高血糖风险评分（基于权威临床指南）
- **Web 仪表盘**：Streamlit 可视化，支持趋势图表、化验对比、PDF 导出
- **就医提醒系统**：自动推算复诊日期，到期提醒
- **反馈闭环学习**：自动采集执行反馈，持续优化建议策略
- **N-of-1 干预实验框架**：自我实验设计、执行与效果评估

## 目录结构

```
superhealth/
├── src/superhealth/          # Python 核心包
│   ├── config.py             # 配置管理
│   ├── models.py             # Pydantic 数据模型
│   ├── database.py           # SQLite 存储层（20 张表）
│   ├── collectors/           # 数据采集层
│   ├── api/                  # 数据接收端
│   ├── analysis/             # 基础分析工具
│   ├── core/                 # 健康画像与决策引擎
│   ├── reports/              # 报告生成
│   ├── goals/                # 阶段性目标子系统
│   ├── feedback/             # 反馈闭环与学习层
│   ├── insights/             # 周期性洞察
│   ├── reminders/            # 就医提醒系统
│   ├── tracking/             # 用药追踪
│   └── dashboard/            # Web 仪表盘（Streamlit）
├── tests/                    # pytest 测试
├── examples/                 # 示例配置与脱敏数据
│   ├── config.example.toml
│   └── sample_data.sql
├── scripts/                  # cron 脚本
├── docs/                     # 项目文档
├── schema.sql                # 数据库 Schema（单一来源）
└── pyproject.toml            # 项目元数据与依赖
```

## 快速开始

### 安装

```bash
git clone https://github.com/chasezjj/superhealth.git
cd superhealth
pip install -e ".[all]"        # 安装所有可选依赖
playwright install chromium    # Garmin 数据采集需要
```

<details>
<summary>按需安装可选依赖</summary>

```bash
pip install -e "."               # 仅核心功能
pip install -e ".[garmin]"       # + Garmin 数据采集
pip install -e ".[claude]"       # + Claude AI 建议
pip install -e ".[baichuan]"     # + 百川 AI 建议
pip install -e ".[dev]"          # + 开发工具 (pytest, ruff, mypy)
```

</details>

### 配置

复制示例配置并填写你的凭据：

```bash
mkdir -p ~/.superhealth
cp examples/config.example.toml ~/.superhealth/config.toml
chmod 600 ~/.superhealth/config.toml
# 编辑 config.toml 填入实际值
```

### 初始化数据库

```bash
python -c "from superhealth.database import init_db; init_db()"
```

### 导入示例数据（可选）

```bash
sqlite3 health.db < examples/sample_data.sql
```

### 启动 Web 仪表盘

```bash
PYTHONPATH=src streamlit run src/superhealth/dashboard/app.py --server.port=8505
# 浏览器访问 http://localhost:8505
```

### Docker 部署

```bash
docker compose up -d
# 浏览器访问 http://localhost:8505
```

## 常用命令

```bash
# 每日流水线（cron 主入口）
PYTHONPATH=src python -m superhealth.daily_pipeline

# 单独运行数据采集
PYTHONPATH=src python -m superhealth.collectors.fetch_garmin
PYTHONPATH=src python -m superhealth.collectors.weather_collector

# 生成日报
PYTHONPATH=src python -m superhealth.reports.daily_report --date 2025-04-01
PYTHONPATH=src python -m superhealth.reports.advanced_daily_report --date 2025-04-01

# 阶段性目标管理
PYTHONPATH=src python -m superhealth.goals list
PYTHONPATH=src python -m superhealth.goals add --name "降血压" --priority 1 \
  --metric bp_systolic_mean_7d --direction decrease --target 120

# 趋势分析与相关性
PYTHONPATH=src python -m superhealth.analysis.trends
PYTHONPATH=src python -m superhealth.analysis.correlation

# 就医提醒
PYTHONPATH=src python -m superhealth.reminders.appointment_scheduler --dry-run
PYTHONPATH=src python -m superhealth.reminders.reminder_notifier --dry-run
```

## 数据流

```
可穿戴设备 / 血压计 / 体脂秤
      │
      ▼
collectors/fetch_garmin.py     → activity-data/{date}.md + json/
api/vitals_receiver.py         → health.db vitals 表
      │
      ▼
daily_pipeline.py              → 编排所有步骤
      │
      ▼
core/health_profile_builder.py → HealthProfile（条件/基因/风险/体成分/活跃目标）
core/model_selector.py         → 选中评估模型（目标驱动强激活）
core/assessment_models.py      → 各维度评分
core/llm_advisor.py            → LLM 个性化建议
      │
      ▼
reports/advanced_daily_report.py → 高级日报 Markdown
      │
      ▼
collectors/send_garmin_report.py → 微信/邮件日报（可选）
```

## 开发

```bash
pip install -e ".[dev]"
pytest
ruff check src/
mypy src/ --ignore-missing-imports
```

## 贡献

欢迎贡献！请参阅 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 常见问题

<details>
<summary>Garmin 登录失败怎么办？</summary>

确保使用中国区 Garmin Connect 账号。运行 `python -m superhealth.collectors.fetch_garmin --login` 进行交互式登录。如果 Playwright 安装失败，尝试 `playwright install-deps chromium` 安装系统依赖。

</details>

<details>
<summary>如何只使用部分功能？</summary>

所有集成模块都是可选的。未配置的模块会静默跳过。例如，不配置 Garmin 凭据则跳过数据采集，不配置 Claude API 则跳过 LLM 建议。

</details>

<details>
<summary>数据存在哪里？</summary>

所有数据保存在本地 `health.db`（SQLite）中，不会上传到任何云服务。LLM API 调用会发送当日健康摘要以获取建议，但不发送历史原始数据。

</details>

## 许可证

[MIT](LICENSE)

## 免责声明

本系统提供的所有健康建议仅供参考，不替代专业医疗诊断。如有健康问题，请咨询专业医生。
