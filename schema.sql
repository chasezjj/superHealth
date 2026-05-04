-- SuperHealth 项目 Schema
-- 运行方式（幂等，可反复执行）：
--   python -c "from superhealth.database import init_db; init_db()"
-- 或直接：
--   sqlite3 health.db < schema.sql
--
-- 规则：
--   - 新增表：在下方加 CREATE TABLE IF NOT EXISTS 块
--   - 新增列：在文件末尾 [Column Migrations] 区加 ALTER TABLE ... ADD COLUMN IF NOT EXISTS
--   - 不删列、不改列类型（SQLite 限制），需要时建新表迁移

-- ─── Drop legacy disease-specific tables ─────────────────────────────
-- Replaced by medical_observations (generic) + medical_documents + medical_conditions
DROP TABLE IF EXISTS eye_exams;
DROP TABLE IF EXISTS kidney_ultrasounds;
DROP TABLE IF EXISTS annual_checkups;
DROP TABLE IF EXISTS lab_results;

-- ─── Tables ───────────────────────────────────────────────────────────

-- Garmin 每日健康数据
CREATE TABLE IF NOT EXISTS daily_health (
    date                     TEXT PRIMARY KEY,  -- YYYY-MM-DD
    -- 睡眠
    sleep_total_seconds      INTEGER,
    sleep_deep_seconds       INTEGER,
    sleep_light_seconds      INTEGER,
    sleep_rem_seconds        INTEGER,
    sleep_awake_seconds      INTEGER,
    sleep_score              REAL,
    -- 压力
    stress_average           REAL,
    stress_max               REAL,
    stress_rest_seconds      INTEGER,
    stress_low_seconds       INTEGER,
    stress_medium_seconds    INTEGER,
    stress_high_seconds      INTEGER,
    -- 心率
    hr_resting               REAL,
    hr_min                   REAL,
    hr_max                   REAL,
    hr_avg7_resting          REAL,
    -- Body Battery
    bb_highest               REAL,
    bb_lowest                REAL,
    bb_charged               REAL,
    bb_drained               REAL,
    bb_at_wake               REAL,
    -- 血氧
    spo2_average             REAL,
    spo2_lowest              REAL,
    spo2_latest              REAL,
    -- 呼吸
    resp_waking_avg          REAL,
    resp_highest             REAL,
    resp_lowest              REAL,
    -- 活动
    steps                    INTEGER,
    distance_meters          REAL,
    active_calories          REAL,
    floors_ascended          REAL,
    -- HRV
    hrv_last_night_avg       REAL,
    hrv_last_night_5min_high REAL,
    hrv_weekly_avg           REAL,
    hrv_baseline_low         REAL,
    hrv_baseline_high        REAL,
    hrv_status               TEXT,
    -- 原始 JSON（备用）
    raw_json                 TEXT
);

-- 运动记录（每天多条）
CREATE TABLE IF NOT EXISTS exercises (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    date             TEXT NOT NULL,  -- YYYY-MM-DD
    name             TEXT NOT NULL DEFAULT '未知活动',
    type_key         TEXT,
    start_time       TEXT,           -- "HH:MM" 本地时间
    distance_meters  REAL,
    duration_seconds REAL,
    avg_hr           REAL,
    max_hr           REAL,
    avg_speed        REAL,
    calories         REAL,
    details          TEXT,            -- 力量训练动作明细
    FOREIGN KEY (date) REFERENCES daily_health(date)
);
CREATE INDEX IF NOT EXISTS idx_exercises_date ON exercises(date);

-- 血压/体重/体脂率（Health Auto Export 自动推送）
CREATE TABLE IF NOT EXISTS vitals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    measured_at  TEXT NOT NULL,
    source       TEXT DEFAULT 'health_auto_export',
    systolic     INTEGER,
    diastolic    INTEGER,
    heart_rate   INTEGER,
    weight_kg    REAL,
    body_fat_pct REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_vitals_measured_at ON vitals(measured_at);

-- 用药记录
CREATE TABLE IF NOT EXISTS medications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    condition  TEXT,
    start_date TEXT,
    end_date   TEXT,
    dosage     TEXT,
    frequency  TEXT,
    note       TEXT
);

-- 用药效果关联
CREATE TABLE IF NOT EXISTS medication_effects (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    medication_id   INTEGER NOT NULL,
    observation_id  INTEGER,
    checkup_date    TEXT,
    expected_effect TEXT,
    actual_effect   TEXT,
    is_effective    INTEGER,
    recorded_at     TEXT DEFAULT (datetime('now','localtime')),
    note            TEXT,
    FOREIGN KEY (medication_id) REFERENCES medications(id) ON DELETE CASCADE,
    FOREIGN KEY (observation_id) REFERENCES medical_observations(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_med_effects_med_id ON medication_effects(medication_id);
CREATE INDEX IF NOT EXISTS idx_med_effects_obs_id ON medication_effects(observation_id);

-- 天气记录
CREATE TABLE IF NOT EXISTS weather (
    date        TEXT PRIMARY KEY,
    condition   TEXT,
    temperature REAL,
    wind_scale  INTEGER,
    aqi         REAL,
    outdoor_ok  INTEGER
);

-- 同步/拉取日志（用于记录每日流水线各步骤执行结果及历史失败重试）
CREATE TABLE IF NOT EXISTS sync_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT NOT NULL,
    source        TEXT DEFAULT 'garmin',
    step          TEXT NOT NULL,
    status        TEXT NOT NULL,
    error_message TEXT,
    created_at    TIMESTAMP DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_sync_logs_date ON sync_logs(date);
CREATE INDEX IF NOT EXISTS idx_sync_logs_step_status ON sync_logs(step, status);

-- 建议执行反馈（Phase 4）
CREATE TABLE IF NOT EXISTS recommendation_feedback (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    date                   TEXT NOT NULL,
    report_id              TEXT NOT NULL,
    recommendation_type    TEXT,
    recommendation_content TEXT,
    compliance             INTEGER,                    -- 遵从度百分比，0-100（如 85 表示 85% 符合建议）
    actual_action          TEXT,
    tracked_metrics        TEXT,
    created_at             TIMESTAMP DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_feedback_date ON recommendation_feedback(date);
CREATE INDEX IF NOT EXISTS idx_feedback_type ON recommendation_feedback(recommendation_type);

-- 学习到的个人偏好（Phase 4）
CREATE TABLE IF NOT EXISTS learned_preferences (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    preference_type  TEXT NOT NULL,
    preference_key   TEXT NOT NULL,
    preference_value TEXT NOT NULL,
    confidence_score REAL DEFAULT 0.5,
    evidence_count   INTEGER DEFAULT 1,
    last_updated     TIMESTAMP DEFAULT (datetime('now','localtime')),
    UNIQUE(preference_type, preference_key)
);

-- 就医预约提醒（Phase 6）
CREATE TABLE IF NOT EXISTS appointments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    condition       TEXT NOT NULL,          -- 'glaucoma' / 'hyperuricemia' / 'annual_checkup'
    hospital        TEXT,                   -- '同仁医院' / '协和医院'
    department      TEXT,                   -- '眼科' / '肾内科'
    due_date        DATE NOT NULL,          -- 推算出的下次应诊日期
    interval_months INTEGER NOT NULL,       -- 复诊间隔（月）
    source_exam_id  INTEGER,                -- 触发推算的最近一次检查记录 ID
    source_table    TEXT,                   -- 'eye_exams' / 'lab_results' / 'annual_checkups'
    status          TEXT DEFAULT 'pending', -- 'pending' / 'reminded_14' / 'reminded_7' / 'completed' / 'snoozed'
    notes           TEXT,
    created_at      TIMESTAMP DEFAULT (datetime('now','localtime')),
    updated_at      TIMESTAMP DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_appointments_due_date ON appointments(due_date);
CREATE INDEX IF NOT EXISTS idx_appointments_condition ON appointments(condition);
CREATE UNIQUE INDEX IF NOT EXISTS idx_appointments_unique ON appointments(condition, due_date);

-- 阶段性目标主表（Goals 子系统）
CREATE TABLE IF NOT EXISTS goals (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active',
    metric_key        TEXT NOT NULL,
    direction         TEXT NOT NULL,
    baseline_value    REAL,
    target_value      REAL,
    start_date        TEXT NOT NULL,
    target_date       TEXT,
    achieved_date     TEXT,
    notes             TEXT,
    created_at        TIMESTAMP DEFAULT (datetime('now','localtime')),
    updated_at        TIMESTAMP DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);

-- 每日目标进度快照
CREATE TABLE IF NOT EXISTS goal_progress (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id       INTEGER NOT NULL,
    date          TEXT NOT NULL,
    current_value REAL,
    delta_from_baseline REAL,
    progress_pct  REAL,
    note          TEXT,
    FOREIGN KEY (goal_id) REFERENCES goals(id) ON DELETE CASCADE,
    UNIQUE(goal_id, date)
);
CREATE INDEX IF NOT EXISTS idx_goal_progress_goal_date ON goal_progress(goal_id, date);

-- 日历事件（Outlook/Exchange）
CREATE TABLE IF NOT EXISTS calendar_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    subject     TEXT,
    start_time  TEXT,
    end_time    TEXT,
    duration_min INTEGER,
    is_all_day  INTEGER DEFAULT 0,
    location    TEXT,
    organizer   TEXT,
    is_recurring INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_calendar_date ON calendar_events(date);

-- 干预实验（N-of-1 Self-Experimentation）
CREATE TABLE IF NOT EXISTS experiments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL,
    hypothesis        TEXT NOT NULL,
    goal_id           INTEGER,
    metric_key        TEXT NOT NULL,
    direction         TEXT NOT NULL DEFAULT 'increase',
    intervention      TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'draft',
    start_date        TEXT,
    end_date          TEXT,
    min_duration      INTEGER NOT NULL DEFAULT 14,
    baseline_start    TEXT,
    baseline_end      TEXT,
    conclusion        TEXT,
    conclusion_date   TEXT,
    notes             TEXT,
    created_at        TIMESTAMP DEFAULT (datetime('now','localtime')),
    updated_at        TIMESTAMP DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(status);

-- daily_health 核心指标变更审计（用于追溯 effect_tracker 评估变化原因）
CREATE TABLE IF NOT EXISTS daily_health_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT NOT NULL,
    field_name TEXT NOT NULL,
    old_value  REAL,
    new_value  REAL,
    changed_at TIMESTAMP DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_audit_date ON daily_health_audit(date);
CREATE INDEX IF NOT EXISTS idx_audit_field ON daily_health_audit(field_name);

-- 通用医疗文档（基因/年度体检/门诊/影像/化验单/出院小结等多模态上传产物）
CREATE TABLE IF NOT EXISTS medical_documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_date        TEXT NOT NULL,                  -- 报告/检查日期 YYYY-MM-DD
    doc_type        TEXT NOT NULL,                  -- 'genetic' | 'annual_checkup' | 'outpatient' | 'imaging' | 'lab' | 'discharge' | 'other'
    institution     TEXT,                           -- 医院/检测机构
    department      TEXT,                           -- 科室
    doctor          TEXT,
    title           TEXT,                           -- 用户可读标题
    original_path   TEXT,                           -- 原始 PDF/图片相对路径（保留可追溯）
    markdown_path   TEXT NOT NULL,                  -- 落盘 .md 路径（系统主消费源）
    extracted_json  TEXT,                           -- LLM 提取的完整 JSON
    confirmed_at    TIMESTAMP,                      -- 用户确认时间
    note            TEXT,
    created_at      TIMESTAMP DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_medical_documents_date ON medical_documents(doc_date);
CREATE INDEX IF NOT EXISTS idx_medical_documents_type ON medical_documents(doc_type);

-- 通用医学观测/指标表（取代 eye_exams、kidney_ultrasounds、annual_checkups 各列）
-- 一行 = 一个项目（化验项 / 眼压 / 肾长径 / 心电图结论 ...）
CREATE TABLE IF NOT EXISTS medical_observations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id   INTEGER REFERENCES medical_documents(id) ON DELETE CASCADE,
    obs_date      TEXT NOT NULL,                    -- YYYY-MM-DD
    category      TEXT NOT NULL,                    -- 'lab' | 'vital' | 'imaging' | 'eye' | 'ultrasound' | 'ecg' | 'genetic' | 'other'
    item_name     TEXT NOT NULL,                    -- 中文/原文名称
    item_code     TEXT,                             -- LOINC / 内部 code（可选）
    body_site     TEXT,                             -- 'eye' | 'kidney' | 'thyroid' | 'liver' | ...
    laterality    TEXT,                             -- 'left' | 'right' | 'bilateral'
    value_num     REAL,
    value_text    TEXT,                             -- 文字结论
    unit          TEXT,
    ref_low       REAL,
    ref_high      REAL,
    is_abnormal   INTEGER DEFAULT 0,
    note          TEXT
);
CREATE INDEX IF NOT EXISTS idx_obs_date ON medical_observations(obs_date);
CREATE INDEX IF NOT EXISTS idx_obs_item ON medical_observations(item_name);
CREATE INDEX IF NOT EXISTS idx_obs_cat_site ON medical_observations(category, body_site);
CREATE INDEX IF NOT EXISTS idx_obs_doc ON medical_observations(document_id);

-- 患者病情清单（取代以 markdown 文件名探测疾病的硬编码逻辑）
CREATE TABLE IF NOT EXISTS medical_conditions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT NOT NULL,               -- '原发性开角型青光眼' / '高尿酸血症'
    icd10_code         TEXT,
    status             TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'resolved' | 'suspected'
    onset_date         TEXT,
    source_document_id INTEGER REFERENCES medical_documents(id) ON DELETE SET NULL,
    notes              TEXT,
    updated_at         TIMESTAMP DEFAULT (datetime('now','localtime')),
    UNIQUE(name)
);
CREATE INDEX IF NOT EXISTS idx_conditions_status ON medical_conditions(status);

-- 病情关联的可趋势化数值指标配置
CREATE TABLE IF NOT EXISTS condition_metric_mappings (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_name TEXT NOT NULL,
    metric_key     TEXT NOT NULL,
    display_name   TEXT,
    enabled        INTEGER NOT NULL DEFAULT 1,
    priority       INTEGER NOT NULL DEFAULT 100,
    notes          TEXT,
    updated_at     TIMESTAMP DEFAULT (datetime('now','localtime')),
    UNIQUE(condition_name, metric_key)
);
CREATE INDEX IF NOT EXISTS idx_condition_metric_condition ON condition_metric_mappings(condition_name);
CREATE INDEX IF NOT EXISTS idx_condition_metric_enabled ON condition_metric_mappings(enabled);

-- ─── Column Migrations ────────────────────────────────────────────────
-- 新增列统一写在这里，格式：ALTER TABLE t ADD COLUMN IF NOT EXISTS col type;
-- 已在建表时包含的列无需重复列出

ALTER TABLE exercises ADD COLUMN start_time TEXT;
ALTER TABLE exercises ADD COLUMN details TEXT;

-- medical_conditions：复诊配置（由 appointment_scheduler 使用）
ALTER TABLE medical_conditions ADD COLUMN follow_up_months INTEGER;
ALTER TABLE medical_conditions ADD COLUMN follow_up_hospital TEXT;
ALTER TABLE medical_conditions ADD COLUMN follow_up_department TEXT;

-- 天气表：全天温度区间（来自3日预报接口）
ALTER TABLE weather ADD COLUMN temp_max REAL;
ALTER TABLE weather ADD COLUMN temp_min REAL;

-- recommendation_feedback：用户反馈（每天7点日报后用户可随时提交文字反馈）
ALTER TABLE recommendation_feedback ADD COLUMN user_feedback TEXT;

-- recommendation_feedback：可选的主观评分 1-5 星
ALTER TABLE recommendation_feedback ADD COLUMN user_rating INTEGER;

-- daily_health：记录最后一次从 Garmin 拉取的时间（用于区分早上不完整数据 vs 晚上完整数据）
ALTER TABLE daily_health ADD COLUMN fetched_at TEXT;

-- recommendation_feedback：关联阶段性目标
ALTER TABLE recommendation_feedback ADD COLUMN goal_id INTEGER;

-- recommendation_feedback：综合建议质量评分（0-1），统一 compliance/goal_progress/effect/rating 四个信号
ALTER TABLE recommendation_feedback ADD COLUMN quality_score REAL;

-- learned_preferences：生命周期状态管理（active/committed/reverted）
ALTER TABLE learned_preferences ADD COLUMN status TEXT DEFAULT 'active';

-- learned_preferences：关联阶段性目标 + 最后有效时间
ALTER TABLE learned_preferences ADD COLUMN goal_id INTEGER;
ALTER TABLE learned_preferences ADD COLUMN last_effective_at TEXT;

-- goals：CLI/模型兼容字段
ALTER TABLE goals ADD COLUMN target_date TEXT;

-- goals 表结构迁移：移除废弃字段并补齐缺失列
DROP TABLE IF EXISTS goals_new;
CREATE TABLE goals_new (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active',
    metric_key        TEXT NOT NULL,
    direction         TEXT NOT NULL,
    baseline_value    REAL,
    target_value      REAL,
    start_date        TEXT NOT NULL,
    target_date       TEXT,
    achieved_date     TEXT,
    notes             TEXT,
    created_at        TIMESTAMP DEFAULT (datetime('now','localtime')),
    updated_at        TIMESTAMP DEFAULT (datetime('now','localtime'))
);
INSERT INTO goals_new (id, name, status, metric_key, direction, baseline_value, target_value, start_date, target_date, achieved_date, notes, created_at, updated_at)
SELECT id, name, status, metric_key, direction, baseline_value, target_value, start_date, target_date, achieved_date, notes, created_at, updated_at FROM goals;
DROP TABLE goals;
ALTER TABLE goals_new RENAME TO goals;
CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);
