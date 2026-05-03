-- SuperHealth 示例数据（脱敏伪造数据）
-- 用途：快速体验系统功能，展示 schema 用法
-- 使用方式：sqlite3 health.db < examples/sample_data.sql

-- 用户档案
INSERT OR REPLACE INTO user_profile (key, value) VALUES
('height_cm', '175'),
('gender', 'male'),
('birth_date', '1990-01-01');

-- 每日健康数据（最近 7 天）
INSERT OR REPLACE INTO daily_health (
    date, sleep_total_seconds, sleep_deep_seconds, sleep_light_seconds,
    sleep_rem_seconds, sleep_score, stress_average, hr_resting, hr_avg7_resting,
    steps, active_calories, distance_meters, hrv_last_night_avg,
    spo2_average, floors_ascended
) VALUES
('2025-04-25', 28800, 7200, 14400, 5400, 85, 28, 58, 59, 8500, 320, 6200, 45, 97, 8),
('2025-04-26', 27000, 6500, 13800, 5100, 78, 32, 60, 59, 7200, 280, 5400, 42, 96, 5),
('2025-04-27', 30600, 8100, 15000, 5700, 90, 25, 55, 58, 10200, 410, 7800, 48, 98, 12),
('2025-04-28', 25200, 5400, 12600, 4800, 72, 38, 62, 59, 5800, 210, 4200, 38, 95, 3),
('2025-04-29', 29400, 7500, 14700, 5400, 88, 26, 57, 58, 9800, 380, 7200, 46, 97, 10),
('2025-04-30', 27900, 6900, 14100, 5100, 82, 30, 59, 58, 8100, 300, 6100, 43, 96, 6),
('2025-05-01', 30000, 7800, 15000, 5400, 87, 24, 56, 57, 9500, 360, 7000, 47, 98, 9);

-- 运动记录
INSERT INTO exercises (date, name, type_key, duration_seconds, avg_hr, max_hr, calories)
VALUES
('2025-04-25', '晨跑', 'running', 1800, 145, 165, 280),
('2025-04-27', '游泳', 'swimming', 2700, 130, 155, 350),
('2025-04-29', '骑行', 'cycling', 3600, 125, 148, 420),
('2025-05-01', '健走', 'walking', 2400, 110, 130, 180);

-- 血压/体重记录
INSERT INTO vitals (measured_at, systolic, diastolic, heart_rate, weight_kg, body_fat_pct)
VALUES
('2025-04-25 08:00', 128, 82, 68, 72.5, 18.2),
('2025-04-26 08:00', 126, 80, 66, 72.3, 18.1),
('2025-04-27 08:00', 130, 84, 70, 72.6, 18.3),
('2025-04-28 08:00', 125, 78, 65, 72.2, 18.0),
('2025-04-29 08:00', 127, 81, 67, 72.4, 18.2),
('2025-04-30 08:00', 124, 79, 64, 72.1, 18.0),
('2025-05-01 08:00', 126, 80, 66, 72.3, 18.1);

-- 化验结果
INSERT INTO lab_results (date, source, item_name, value, unit, ref_low, ref_high)
VALUES
('2025-03-15', '门诊', '尿酸', 380, 'umol/L', 208, 428),
('2025-03-15', '门诊', '肌酐', 85, 'umol/L', 44, 133),
('2025-03-15', '门诊', '尿素', 5.2, 'mmol/L', 2.6, 7.5),
('2025-03-15', '门诊', 'ALT', 28, 'U/L', 9, 50),
('2025-03-15', '门诊', 'AST', 24, 'U/L', 15, 40);

-- 用药记录
INSERT INTO medications (name, condition, start_date, dosage, frequency, note)
VALUES
('示例滴眼液', 'glaucoma', '2023-06-01', '每晚1滴', '每日', '控制眼压'),
('示例降尿酸药', 'hyperuricemia', '2023-01-01', '每日1片', '每日', '控制尿酸水平');

-- 眼科检查
INSERT INTO eye_exams (date, hospital, od_iop, os_iop, od_cd_ratio, os_cd_ratio, note)
VALUES
('2025-03-15', '示例医院', 16.5, 17.0, 0.6, 0.6, '眼压控制良好');

-- 年度体检
INSERT INTO annual_checkups (
    checkup_date, institution, height_cm, weight_kg, bmi,
    systolic, diastolic, uric_acid, creatinine, total_cholesterol, fasting_glucose
) VALUES
('2025-01-15', '示例体检中心', 175, 72.0, 23.5, 125, 80, 375, 82, 4.8, 5.2);

-- 阶段性目标
INSERT INTO goals (
    name, description, status, metric_key, direction,
    baseline_value, target_value, start_date, target_date
) VALUES
(
    '降低静息心率', '通过规律有氧运动将静息心率降至55以下',
    'active', 'hr_resting', 'decrease',
    62, 55, '2025-01-01', '2025-06-30'
);

-- 天气记录
INSERT OR REPLACE INTO weather (date, condition, temperature, wind_scale, aqi, outdoor_ok)
VALUES
('2025-04-25', '晴', 22, 2, 45, 1),
('2025-04-26', '多云', 20, 3, 55, 1),
('2025-04-27', '小雨', 18, 4, 70, 0),
('2025-04-28', '阴', 19, 3, 60, 1),
('2025-04-29', '晴', 23, 2, 40, 1),
('2025-04-30', '晴', 24, 1, 35, 1),
('2025-05-01', '多云', 21, 2, 50, 1);
