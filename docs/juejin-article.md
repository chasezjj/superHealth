# 我做了一个本地优先的个人健康 AutoResearch 系统：架构、算法与一周降压 7.9% 的实测

> 一个开源的个人健康闭环系统：把 Garmin、体检 PDF、化验单、日程、天气全部喂进本地 SQLite，让 LLM 围绕你的目标跑 N-of-1 实验、做贝叶斯偏好学习、做匹配对照效果归因。MIT，Python 3.10+，GitHub: [chasezjj/superhealth](https://github.com/chasezjj/superhealth)。

## 起因：可穿戴设备的"数据黑洞"

我有一只 Garmin、一台血压计、一个 Health Auto Export，每天产出几百条数据点。问题是：

- Garmin Connect 只告诉我"昨天恢复 72 分"，**不会告诉我下周该练还是该躺**；
- 体检报告 PDF 一年看一次，**没有任何东西把它和今天的睡眠数据关联起来**；
- 所有"个性化健康 App"给的建议都是 *"建议每天步行 8000 步、保持心情愉悦"*，**和千人一面没差别**。

我想要的是一个能回答 *"基于我过去 90 天的真实数据 + 我现在最在乎的目标，今天我应该做什么"* 的系统，而且这件事不应该把数据上传到任何商业服务器。于是有了 SuperHealth。

跑了一段时间，**目标设为降低舒张压时，按系统建议执行一周后舒张压从 81.4 → 74.9 mmHg（-7.9%）**。这也是我决定把它开源出来的原因。

---

## 一、整体架构：AutoResearch 闭环

SuperHealth 的核心是一个五段闭环，每一步都对应一个独立模块：

```
        你的目标（BP / 血糖 / 血脂 / 尿酸 / HRV / 睡眠 / 体重 / 压力）
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

和市面上"采集→展示"的健康 App 不同，SuperHealth 多出来的是 **④跟踪 + ⑤学习** 这两段。系统会问自己：*"上周我让用户多睡 30 分钟，HRV 有没有真的变好？"* 然后用这个答案去修正下周的策略。

技术栈很朴素：

- **存储**：SQLite（单文件，一切核心数据都在 `health.db`）
- **应用层**：Python 3.10+，Streamlit 做仪表盘
- **采集**：`garminconnect` + Playwright（Garmin CN 登录走浏览器）+ FastAPI 接收 Health Auto Export 的 REST 推送
- **LLM**：可选 Claude / 百川医疗模型，**未启用时整个系统纯本地运行**
- **打包**：`pyproject.toml` + Docker Compose

---

## 二、数据模型：从"疾病专表"到"通用医疗文档"

最早一版我犯过一个典型错误：给眼科、肾脏、年度体检各做了一张表。结果加一个新科室就要改 schema，化验项稍微换个口径就出问题。

后来重构成"通用医疗文档 + 结构化观测项"两层模型：

```sql
medical_documents          -- 一份 PDF / 影像报告 / 出院小结
  ├─ id, type, source_date, raw_markdown
medical_observations       -- 从文档里抽出来的每一条观测
  ├─ document_id, metric_code, value, unit, ref_range, observed_at
medical_conditions         -- 既往史 / 现患疾病
condition_metric_mappings  -- 疾病 → 应该被监控的指标
```

这样无论是 LDL-C、尿酸还是 HbA1c，都是 `medical_observations` 里的一行；查询逻辑只关心 `metric_code`，不关心来源是哪张体检单。

整个 schema 的单一来源是仓库里的 [`schema.sql`](https://github.com/chasezjj/superhealth/blob/main/schema.sql)，核心表大概是这些：

| 层级       | 表                                                                       | 频率           |
| ---------- | ------------------------------------------------------------------------ | -------------- |
| 基因层     | `medical_documents`（type=genetic）                                      | 一次          |
| 医疗层     | `medical_documents` / `medical_observations` / `medications`             | 月/季/年      |
| 日常层     | `daily_health` / `exercises` / `vitals`                                  | 每日多次      |
| 上下文层   | `weather` / `calendar_events`                                            | 每日          |
| 闭环层     | `goals` / `goal_progress` / `experiments` / `recommendation_feedback` / `learned_preferences` | 实时 |

---

## 三、把体检 PDF 喂给 LLM：医疗文档结构化的踩坑

这是 SuperHealth 最被开发者问的部分。流程是：

1. 用户把体检 PDF / 化验单照片拖进仪表盘；
2. 调用 Claude Vision，prompt 要求按固定 JSON Schema 输出；
3. 校验 → 写入 `medical_observations` + 原始 Markdown 留档；
4. 失败/低置信度的字段标红，进人工修正队列。

踩过的坑总结成几条经验：

**1）一定要让模型先输出原始 Markdown，再输出结构化 JSON。**
直接让模型一步到位输出 JSON，幻觉率显著高于"先复述、后抽取"。Markdown 留档还能在事后做 diff 对比。

**2）Schema 要带 `source_text` 字段。**
每一条观测项都让模型回填它来自原文哪一段。后期人工抽查时省掉一半时间。

**3）单位归一化要在系统侧做，不要让模型做。**
`mmol/L` 和 `mg/dL`、`mmHg` 和 `kPa`，让代码做，确定性更高。

**4）参考值范围要存原文给的那一套，不要自己写死。**
不同医院化验科参考区间不同，按文档来更准。

**5）一份 PDF 拆 page-by-page 调用，再合并。**
长文档容易在中段开始幻觉，分页 + 并发更稳更快。

---

## 四、自适应评估引擎：评分锚定个人基线

很多健康打分 App 的"睡眠 80 分"是和**人群平均**比的——这对个体几乎没意义。SuperHealth 的做法是 [`core/model_selector.py`](https://github.com/chasezjj/superhealth/blob/main/src/superhealth/core/model_selector.py) 根据健康画像动态激活评估模型，每个模型尽量锚定**你过去 90 天的个人基线**：

- 恢复力模型：HRV、Body Battery、压力
- 心血管：BP、静息心率、运动响应
- 代谢/血脂/血糖：化验趋势 + 体重 + 饮食回顾
- 睡眠 / 体成分 / 压力 / 遗传风险（基因报告存在时才激活）

> 一个具体例子：如果你的 HRV 长期就是 35–40ms（偏低但稳定），系统不会每天报警；但你若突然连续 3 天滑到 28ms 以下，会触发"恢复负债"提示，并影响今天给你的运动强度建议。

---

## 五、匹配对照效果归因：怎么知道运动"真的有用"

这是最容易被忽略但最有意思的一块。问题是：你周二跑了 5km，周三睡眠分上升了 8 分。**这是跑步的功劳，还是因为周三日程更轻？**

[`feedback/effect_tracker.py`](https://github.com/chasezjj/superhealth/blob/main/src/superhealth/feedback/effect_tracker.py) 的做法是 **matched-control**：

1. 把每一个运动日 D，沿着历史回看 90 天；
2. 找出**没有运动**的日子 D'，且 D' 与 D 在以下维度足够接近：
   - HRV（前一晚）
   - 睡眠分（前一晚）
   - 当日日程繁忙度（来自 Outlook）
   - 压力均值（前一晚）
3. 用匹配到的 K 个对照日做加权均值，作为 *"如果当天没运动会发生什么"* 的反事实基线；
4. 用 D 当天的实际指标减去基线，得到净效应；
5. 同时排除**污染日**：高压力、明显饮酒（如果手动记录了）、生病（API 标记）。

这远不如 RCT 严谨，但它给的是**只属于你的相关性证据**，比"运动有益健康"这种大众结论实用得多。

---

## 六、N-of-1 干预实验：在自己身上跑 14–28 天小实验

[`feedback/experiment_manager.py`](https://github.com/chasezjj/superhealth/blob/main/src/superhealth/feedback/experiment_manager.py) 是把"医学研究方法"挪到个人尺度上：

- **生成候选**：根据当前目标 + 健康画像，LLM 给出 3–5 个候选干预（如"晚 9 点后不喝咖啡 14 天"、"每周三次 Zone 2 跑步 30 分钟"）。
- **一次只激活一个**，避免变量耦合。
- **到期评估**：综合 Granger 因果检验 + 间断时间序列 (ITS) + Welch t 检验 + 效果量。
- **结论二选一**：成功 → 写入 `learned_preferences` 固化为长期偏好；失败/反向 → 回退，且在未来一段时间内不再推荐相似干预。

特别要说的是 **回退机制**：很多个人 AI 工具只会"学到一个偏好"，但不会"忘掉一个偏好"。我们让 `learned_preferences` 有完整的生命周期（生效中 / 待评估 / 已推翻 / 已过期），这样系统不会越跑越奇怪。

---

## 七、贝叶斯偏好学习：把"用户反馈"变成"先验"

[`feedback/strategy_learner.py`](https://github.com/chasezjj/superhealth/blob/main/src/superhealth/feedback/strategy_learner.py) 学习的维度包括：运动类型 / 时长 / 心率区间 / 时间段 / 当日 HRV 状态。每个维度上维护一组 Beta 分布参数，反馈来源有四个：

1. 用户显式评分（"今天的建议有用吗"）
2. 目标进展（BP 是不是真的降了）
3. 匹配对照算出的运动净效应
4. 执行依从度（建议的事是不是做了）

每次产生新建议时，从后验里抽样去做 Thompson Sampling 风格的策略选择，让系统在"利用已知有效偏好"和"探索没试过的组合"之间保持平衡。

---

## 八、隐私与本地优先：很多人会问的问题

开源出来后被问最多的不是算法，是**数据流向**。SuperHealth 的设计原则：

- 健康数据**只在本机 SQLite**；
- 所有 LLM 调用是**显式启用**的（`config.toml` 里有 `[claude]` `[baichuan]` 段，不配就不调）；
- 启用 LLM 时，发出去的内容是当天**摘要**或你**主动上传的文档**，而不是完整数据库；
- 配置文件 `chmod 600`，密码字段建议用环境变量；
- Streamlit 仪表盘可选密码保护。

这点对国内用户尤其重要——血压、化验、基因这些数据放在第三方 SaaS 我自己也不放心。

---

## 九、一个真实的日常：今天系统让我做什么

举一个实际产出的样本（脱敏后）：

```
今日概览（2026-05-05）
  恢复：72/100，相对你 90 天均值偏低 9%
  睡眠分：78（昨晚 7h12m，深睡占比 18%）
  压力均值：32，较高
  Body Battery 起床值：61

目标：降低舒张压（7 天均值 ≤ 75 mmHg）
  当前：76.4 mmHg → 距目标差 1.4
  本周已执行 Zone 2 跑步 2 次（计划 3 次）

今日建议
  1. 上午 10:30 你有 2h 空档（来自 Outlook），适合 30 分钟 Zone 2 跑步
     依据：本周第 3 次跑步在你历史上和 -1.8 mmHg 相关（matched-control，n=14）
  2. 今晚 21 点后不要摄入咖啡因
     当前实验：N-of-1 #3，剩余 6 天
  3. 注意：本周日程繁忙度比上周 +28%，注意主动减压
```

每条建议都标注了"凭什么"。这对我自己来说是把"AI 建议"从玄学变成可调试的工程产物的关键。

---

## 十、如何上手

```bash
git clone https://github.com/chasezjj/superhealth.git
cd superhealth
pip3 install -e ".[all,dev]"
playwright install chromium

# 配置
mkdir -p ~/.superhealth
cp examples/config.example.toml ~/.superhealth/config.toml
chmod 600 ~/.superhealth/config.toml

# 初始化数据库 + 灌入示例数据
python3 -c "from superhealth.database import init_db; init_db()"
sqlite3 health.db < examples/sample_data.sql

# 启动仪表盘
python3 -m superhealth dashboard --server.port=8505
```

或者一行 Docker：

```bash
docker compose up -d
```

Garmin、Outlook、Claude、百川都是**可选**的，最小可用形态只需要 Python 和 SQLite。

---

## 写在最后

我做 SuperHealth 的初衷不是想再造一个"AI 健康助手"，而是想验证一个观点：

> **个人 AI 不应该停在"建议"，而要走完"建议 → 执行 → 测量 → 学习 → 修正"的闭环。**

这个闭环的难点不在 LLM，而在数据模型、效果归因、实验设计和生命周期管理这些工程基本功。希望这篇文章对正在做 personal-AI / quantified-self / 或者只是想把自己的健康数据用起来的同学有点帮助。

仓库地址：**[github.com/chasezjj/superhealth](https://github.com/chasezjj/superhealth)**，MIT 协议，欢迎 issue / PR / star。

> 免责声明：本系统提供的所有健康建议仅供参考，不替代专业医疗诊断。涉及个人健康决策时请咨询医生。
