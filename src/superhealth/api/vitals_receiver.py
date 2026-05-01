#!/usr/bin/env python3
"""Health Auto Export 数据接收服务。

接收 iPhone Health Auto Export 推送的血压/体重/体脂率数据，写入 SQLite。

启动方式（开发/测试）：
    cd superhealth/
    PYTHONPATH=src python -m superhealth.api.vitals_receiver

生产环境（systemd + gunicorn）：
    pip install gunicorn
    gunicorn -w 1 -b 0.0.0.0:5000 'superhealth.api.vitals_receiver:create_app()'

Health Auto Export 配置：
    自动化类型: REST API
    URL: https://<your-server>/health_data
    添加表头: X-API-Key = <config.toml 中的 api_token>
    数据类型: 健康指标（血压 + 体重 + 体脂率）
    导出格式: JSON v2，批量请求开启

JSON v2 格式示例（Health Auto Export 推送内容）：
    {
      "data": {
        "metrics": [
          {
            "name": "blood_pressure_systolic",
            "units": "mmHg",
            "data": [{"date": "2026-03-29 08:15:00 +0800", "qty": 128}]
          },
          {
            "name": "blood_pressure_diastolic",
            "units": "mmHg",
            "data": [{"date": "2026-03-29 08:15:00 +0800", "qty": 82}]
          },
          {
            "name": "weight_body_mass",
            "units": "kg",
            "data": [{"date": "2026-03-29 08:15:00 +0800", "qty": 68.5}]
          },
          {
            "name": "body_fat_percentage",
            "units": "%",
            "data": [{"date": "2026-03-29 08:15:00 +0800", "qty": 22.3}]
          }
        ]
      }
    }
"""

from __future__ import annotations

import hmac
import logging
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request

from superhealth import database as db
from superhealth.config import load as load_config

log = logging.getLogger(__name__)

_PKG_DIR = Path(__file__).parent.parent
DB_PATH = _PKG_DIR.parent.parent / "health.db"
VITALS_DIR = _PKG_DIR.parent.parent / "vitals"
BP_MD_PATH = VITALS_DIR / "blood-pressure.md"
WEIGHT_MD_PATH = VITALS_DIR / "weight.md"

# Health Auto Export 指标名称 → vitals 表字段的映射
_METRIC_MAP = {
    # 血压（单独字段）
    "blood_pressure_systolic": "systolic",
    "blood_pressure_diastolic": "diastolic",
    # 血压（某些版本合并字段，data 条目有 Systolic/Diastolic 键）
    "blood_pressure": None,  # 特殊处理
    # 体重
    "weight_body_mass": "weight_kg",
    "body_mass": "weight_kg",
    "lean_body_mass": None,  # 忽略
    # 体脂率
    "body_fat_percentage": "body_fat_pct",
    "body_fat_percent": "body_fat_pct",
    # 心率（暂时忽略，数据库表无此字段）
    "heart_rate": None,
}


def _parse_payload(payload: dict) -> list[dict]:
    """解析 Health Auto Export v2 JSON，返回按 measured_at 聚合的体征列表。

    每个元素是一个字典，key 为 vitals 表字段名（systolic/diastolic/
    weight_kg/body_fat_pct/heart_rate），外加 measured_at。
    """
    metrics_list = (
        payload.get("data", {}).get("metrics", [])
        # v1 格式兼容
        or payload.get("metrics", [])
    )

    # 按 measured_at 聚合：{measured_at: {field: value}}
    by_time: dict[str, dict] = defaultdict(dict)

    for metric in metrics_list:
        name: str = metric.get("name", "").lower().replace(" ", "_")
        data_points = metric.get("data", [])

        if name == "blood_pressure":
            # 合并格式：每个 data 点有 systolic/diastolic 键（小写）
            for pt in data_points:
                ts = _normalize_ts(pt.get("date", ""))
                if not ts:
                    continue
                # Health Auto Export 使用小写键名
                if "systolic" in pt:
                    by_time[ts]["systolic"] = int(pt["systolic"])
                if "diastolic" in pt:
                    by_time[ts]["diastolic"] = int(pt["diastolic"])
            continue

        field = _METRIC_MAP.get(name)
        if field is None:
            continue  # 忽略未知或不需要的指标

        for pt in data_points:
            ts = _normalize_ts(pt.get("date", ""))
            qty = pt.get("qty")
            if not ts or qty is None:
                continue
            if field in ("systolic", "diastolic", "heart_rate"):
                by_time[ts][field] = int(qty)
            else:
                by_time[ts][field] = float(qty)

    result = []
    for ts, fields in by_time.items():
        if not fields:
            continue
        entry = {"measured_at": ts}
        entry.update(fields)
        result.append(entry)

    return sorted(result, key=lambda x: x["measured_at"])


def _write_bp_markdown(conn: sqlite3.Connection) -> None:
    """将血压数据写入 blood-pressure.md（幂等：已存在的记录不会重复写入）。"""
    VITALS_DIR.mkdir(parents=True, exist_ok=True)

    # 读取现有文件中的记录（用于去重）
    existing_records: set[str] = set()
    header_lines = [
        "# 血压记录",
        "",
        "| 日期 | 时间 | 收缩压 (mmHg) | 舒张压 (mmHg) | 备注 |",
        "|------|------|---------------|---------------|------|",
    ]

    if BP_MD_PATH.exists():
        content = BP_MD_PATH.read_text(encoding="utf-8")
        for line in content.strip().split("\n"):
            # 解析已有记录行：| 日期 | 时间 | 收缩压 | 舒张压 | 备注 |
            if line.startswith("| 20") and "|" in line:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 5:
                    # 用日期+时间+收缩压+舒张压作为唯一键
                    key = f"{parts[1]}|{parts[2]}|{parts[3]}|{parts[4]}"
                    existing_records.add(key)

    # 查询新数据
    rows = conn.execute(
        """SELECT measured_at, systolic, diastolic
           FROM vitals
           WHERE systolic IS NOT NULL OR diastolic IS NOT NULL
           ORDER BY measured_at DESC"""
    ).fetchall()

    new_lines: list[str] = []
    skipped = 0
    added = 0

    for row in rows:
        ts = row["measured_at"]
        # 解析日期和时间
        if "T" in ts:
            date_part, time_part = ts.split("T", 1)
            time_part = time_part[:5]  # HH:MM
        else:
            date_part, time_part = ts, ""
        sys_val = str(row["systolic"]) if row["systolic"] else ""
        dia_val = str(row["diastolic"]) if row["diastolic"] else ""

        # 检查是否已存在
        key = f"{date_part}|{time_part}|{sys_val}|{dia_val}"
        if key in existing_records:
            skipped += 1
            continue

        new_lines.append(f"| {date_part} | {time_part} | {sys_val} | {dia_val} | |")
        added += 1

    if added == 0:
        log.info("血压 markdown 无新数据需要写入（已跳过 %d 条重复）", skipped)
        return

    # 合并：新数据在前，已有数据在后
    if BP_MD_PATH.exists():
        content = BP_MD_PATH.read_text(encoding="utf-8")
        existing_data_lines = []
        for line in content.strip().split("\n"):
            if line.startswith("| 20") and "|" in line:
                existing_data_lines.append(line)
        all_data_lines = new_lines + existing_data_lines
    else:
        all_data_lines = new_lines

    lines = header_lines + all_data_lines
    BP_MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("血压 markdown 已更新：新增 %d 条，跳过 %d 条重复", added, skipped)


def _write_weight_markdown(conn: sqlite3.Connection) -> None:
    """将体重数据写入 weight.md（幂等：已存在的记录不会重复写入）。"""
    VITALS_DIR.mkdir(parents=True, exist_ok=True)

    # 读取现有文件中的记录（用于去重）
    existing_records: set[str] = set()
    header_lines = [
        "# 体重记录",
        "",
        "| 日期 | 体重 (kg) | 体脂率 (%) | 备注 |",
        "|------|-----------|------------|------|",
    ]

    if WEIGHT_MD_PATH.exists():
        content = WEIGHT_MD_PATH.read_text(encoding="utf-8")
        for line in content.strip().split("\n"):
            # 解析已有记录行：| 日期 | 体重 | 体脂率 | 备注 |
            if line.startswith("| 20") and "|" in line:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 4:
                    # 用日期+体重+体脂率作为唯一键
                    key = f"{parts[1]}|{parts[2]}|{parts[3]}"
                    existing_records.add(key)

    # 查询新数据
    rows = conn.execute(
        """SELECT measured_at, weight_kg, body_fat_pct
           FROM vitals
           WHERE weight_kg IS NOT NULL
           ORDER BY measured_at DESC"""
    ).fetchall()

    new_lines: list[str] = []
    skipped = 0
    added = 0

    for row in rows:
        ts = row["measured_at"]
        # 解析日期
        date_part = ts.split("T")[0] if "T" in ts else ts
        wt = f"{row['weight_kg']:.2f}" if row["weight_kg"] else ""
        bf = f"{row['body_fat_pct']:.2f}" if row["body_fat_pct"] else ""

        # 检查是否已存在
        key = f"{date_part}|{wt}|{bf}"
        if key in existing_records:
            skipped += 1
            continue

        new_lines.append(f"| {date_part} | {wt} | {bf} | |")
        added += 1

    if added == 0:
        log.info("体重 markdown 无新数据需要写入（已跳过 %d 条重复）", skipped)
        return

    # 合并：新数据在前，已有数据在后
    if WEIGHT_MD_PATH.exists():
        content = WEIGHT_MD_PATH.read_text(encoding="utf-8")
        existing_data_lines = []
        for line in content.strip().split("\n"):
            if line.startswith("| 20") and "|" in line:
                existing_data_lines.append(line)
        all_data_lines = new_lines + existing_data_lines
    else:
        all_data_lines = new_lines

    lines = header_lines + all_data_lines
    WEIGHT_MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("体重 markdown 已更新：新增 %d 条，跳过 %d 条重复", added, skipped)


def _normalize_ts(date_str: str) -> str:
    """将 '2026-03-29 08:15:00 +0800' 标准化为 ISO 8601 字符串。"""
    if not date_str:
        return ""
    s = date_str.strip()
    try:
        # 已经是完整 ISO 格式（含时区冒号）直接返回
        if "T" in s and ":" in s[s.find("T") :]:
            # 修正 +HHMM → +HH:MM（如果缺少冒号）
            if len(s) > 5 and s[-3] != ":" and s[-5] in "+-" and s[-4:].isdigit():
                s = s[:-2] + ":" + s[-2:]
            return s

        # 分离时区（如果存在）
        tz_part = ""
        if " +" in s:
            s, tz_part = s.rsplit(" +", 1)
            tz_part = "+" + tz_part
        elif " -" in s:
            # 找到时间后的减号（日期部分不会出现在后面）
            idx = s.rfind(" -")
            if idx > 10:
                s, tz_part = s[:idx], s[idx + 1 :]

        # 用 datetime 解析日期时间，避免手动字符串操作
        dt = datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S")

        # 转换时区格式: +0800 → +08:00
        if tz_part and len(tz_part) == 5 and tz_part[-4:].isdigit():
            tz_part = tz_part[:3] + ":" + tz_part[3:]

        return dt.strftime("%Y-%m-%dT%H:%M:%S") + tz_part
    except Exception:
        return date_str


def create_app(config=None) -> Flask:
    cfg = config or load_config()
    app = Flask(__name__)

    db.init_db(DB_PATH)

    @app.before_request
    def _auth():
        """验证 X-API-Key header（或 Authorization: Bearer <token>）。"""
        # 健康检查端点跳过鉴权
        if request.path == "/health":
            return None

        if not cfg.vitals.api_token:
            log.warning("vitals.api_token 未配置，跳过鉴权（不安全！）")
            return None

        key = request.headers.get("X-API-Key", "")
        if not key:
            bearer = request.headers.get("Authorization", "")
            if bearer.startswith("Bearer "):
                key = bearer[7:]

        if not hmac.compare_digest(key, cfg.vitals.api_token):
            log.warning("鉴权失败，来源 IP: %s", request.remote_addr)
            return jsonify({"error": "Unauthorized"}), 401

        return None

    @app.post("/health_data")
    def receive_health_data():  # type: ignore[return]
        payload = request.get_json(silent=True)
        if not payload:
            return jsonify({"error": "Invalid JSON"}), 400

        # 调试：记录收到的指标类型（不记录具体数值，避免日志泄漏健康数据）
        log.debug("收到原始 JSON: %s", payload)

        # 调试：记录收到的指标名称
        metrics_list = payload.get("data", {}).get("metrics", [])
        metric_names = [m.get("name", "unknown") for m in metrics_list]
        log.info("收到指标: %s", metric_names)

        records = _parse_payload(payload)
        if not records:
            log.info("收到请求但未解析到有效数据点，payload keys: %s", list(payload.keys()))
            return jsonify({"saved": 0, "message": "no valid data points"}), 200

        saved = 0
        skipped = 0
        with db.get_conn(DB_PATH) as conn:
            for rec in records:
                ts = rec["measured_at"]
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO vitals
                        (measured_at, source, systolic, diastolic, weight_kg, body_fat_pct)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        ts,
                        "health_auto_export",
                        rec.get("systolic"),
                        rec.get("diastolic"),
                        rec.get("weight_kg"),
                        rec.get("body_fat_pct"),
                    ),
                )
                if cursor.rowcount == 0:
                    skipped += 1
                else:
                    saved += 1
                    log.info(
                        "保存体征记录: %s %s", ts, {k: v for k, v in rec.items() if k != "measured_at"}
                    )

            # 同步写入 markdown 文件
            if saved > 0:
                _write_bp_markdown(conn)
                _write_weight_markdown(conn)
                log.info("已更新 vitals markdown 文件")

        return jsonify({"saved": saved, "skipped": skipped}), 200

    @app.get("/health")
    def health_check():  # type: ignore[return]
        """健康检查（不需要鉴权）。"""
        return jsonify({"status": "ok"}), 200

    return app


def main():
    from superhealth.log_config import setup_logging

    setup_logging()
    cfg = load_config()
    if not cfg.vitals.is_complete():
        log.warning(
            "vitals.api_token 未配置！请在 ~/.superhealth/config.toml 中添加:\n"
            "  [vitals]\n"
            '  api_token = "your-secret-token"'
        )
    app = create_app(cfg)
    log.info("启动 vitals_receiver，监听 %s:%s", cfg.vitals.host, cfg.vitals.port)
    app.run(host=cfg.vitals.host, port=cfg.vitals.port)


if __name__ == "__main__":
    main()
