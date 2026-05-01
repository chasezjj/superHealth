-- healthy 项目 Schema
-- 运行方式（幂等，可反复执行）：
--   python -c "from healthy.database import init_db; init_db()"
-- 或直接：
--   sqlite3 health.db < schema.sql
--
-- 规则：
--   - 新增表：在下方加 CREATE TABLE IF NOT EXISTS 块
--   - 新增列：在文件末尾 [Column Migrations] 区加 ALTER TABLE ... ADD COLUMN IF NOT EXISTS
--   - 不删列、不改列类型（SQLite 限制），需要时建新表迁移

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
    FOREIGN KEY (date) REFERENCES daily_health(date)
);
CREATE INDEX IF NOT EXISTS idx_exercises_date ON exercises(date);

-- 化验结果
CREATE TABLE IF NOT EXISTS lab_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    source      TEXT,
    item_name   TEXT NOT NULL,
    item_code   TEXT,
    value       REAL,
    unit        TEXT,
    ref_low     REAL,
    ref_high    REAL,
    is_abnormal INTEGER DEFAULT 0,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_lab_date ON lab_results(date);
CREATE INDEX IF NOT EXISTS idx_lab_item ON lab_results(item_name);

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
CREATE INDEX IF NOT EXISTS idx_vitals_measured_at ON vitals(measured_at);

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
    lab_result_id   INTEGER,
    eye_exam_id     INTEGER,
    checkup_date    TEXT,
    expected_effect TEXT,
    actual_effect   TEXT,
    is_effective    INTEGER,
    recorded_at     TEXT DEFAULT (datetime('now','localtime')),
    note            TEXT,
    FOREIGN KEY (medication_id) REFERENCES medications(id) ON DELETE CASCADE,
    FOREIGN KEY (lab_result_id) REFERENCES lab_results(id) ON DELETE SET NULL,
    FOREIGN KEY (eye_exam_id) REFERENCES eye_exams(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_med_effects_med_id ON medication_effects(medication_id);
CREATE INDEX IF NOT EXISTS idx_med_effects_lab_id ON medication_effects(lab_result_id);

-- 眼科检查
CREATE TABLE IF NOT EXISTS eye_exams (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    date         TEXT NOT NULL,
    doctor       TEXT,
    hospital     TEXT,
    od_vision    TEXT,
    od_iop       REAL,
    od_cd_ratio  REAL,
    os_vision    TEXT,
    os_iop       REAL,
    os_cd_ratio  REAL,
    fundus_note  TEXT,
    prescription TEXT,
    note         TEXT
);
CREATE INDEX IF NOT EXISTS idx_eye_date ON eye_exams(date);

-- 肾脏彩超
CREATE TABLE IF NOT EXISTS kidney_ultrasounds (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    right_length_cm REAL,
    right_finding   TEXT,
    left_length_cm  REAL,
    left_finding    TEXT,
    right_ureter    TEXT,
    left_ureter     TEXT,
    prostate        TEXT,
    conclusion      TEXT,
    doctor          TEXT
);
CREATE INDEX IF NOT EXISTS idx_kidney_date ON kidney_ultrasounds(date);

-- 年度体检报告
CREATE TABLE IF NOT EXISTS annual_checkups (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    checkup_date     TEXT NOT NULL UNIQUE,
    institution      TEXT,
    height_cm        REAL,
    weight_kg        REAL,
    bmi              REAL,
    systolic         INTEGER,
    diastolic        INTEGER,
    heart_rate       INTEGER,
    uric_acid        INTEGER,
    creatinine       INTEGER,
    urea             REAL,
    cystatin_c       REAL,
    total_cholesterol REAL,
    triglyceride     REAL,
    ldl_c            REAL,
    hdl_c            REAL,
    fasting_glucose  REAL,
    hba1c            REAL,
    alt              REAL,
    ast              REAL,
    ggt              REAL,
    wbc              REAL,
    rbc              REAL,
    hgb              INTEGER,
    hct              REAL,
    plt              INTEGER,
    t3               REAL,
    t4               REAL,
    tsh              REAL,
    afp              REAL,
    cea              REAL,
    t_psa            REAL,
    nse              REAL,
    cyfra211         REAL,
    vision_right     REAL,
    vision_left      REAL,
    iop_right        INTEGER,
    iop_left         INTEGER,
    cup_disc_ratio   TEXT,
    thyroid_note     TEXT,
    lung_note        TEXT,
    ultrasound_note  TEXT,
    abnormal_summary TEXT,
    raw_text         TEXT
);
CREATE INDEX IF NOT EXISTS idx_checkup_date ON annual_checkups(checkup_date);

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
    description        TEXT,
    priority          INTEGER NOT NULL,
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
CREATE INDEX IF NOT EXISTS idx_goals_priority ON goals(priority);

-- 用户档案（key-value，存储身高/性别/出生日期等）
CREATE TABLE IF NOT EXISTS user_profile (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

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

-- ─── Column Migrations ────────────────────────────────────────────────
-- 新增列统一写在这里，格式：ALTER TABLE t ADD COLUMN IF NOT EXISTS col type;
-- 已在建表时包含的列无需重复列出

ALTER TABLE exercises ADD COLUMN start_time TEXT;
ALTER TABLE exercises ADD COLUMN details TEXT;

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
