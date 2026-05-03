"""用户档案读写工具 — 使用 data/profile/profile.md（YAML frontmatter）存储。

profile.md 格式示例：
---
name: 张三
birthdate: 1985-06-15
gender: male
height_cm: 175.0
---
"""

from __future__ import annotations

import re
from pathlib import Path

_PKG_DIR = Path(__file__).parent
BASE_DIR = _PKG_DIR.parent.parent  # superhealth/ (project root)

PROFILE_DIR = BASE_DIR / "data" / "profile"
PROFILE_PATH = PROFILE_DIR / "profile.md"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(\n|$)", re.DOTALL)
_KV_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+)$")


def read_profile() -> dict:
    """读取 profile.md，返回 {key: value} 字典（值均为字符串），不存在则返回空字典。"""
    if not PROFILE_PATH.exists():
        return {}
    text = PROFILE_PATH.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    result: dict[str, str] = {}
    for line in m.group(1).splitlines():
        km = _KV_RE.match(line.strip())
        if km:
            result[km.group(1)] = km.group(2).strip()
    return result


def write_profile(data: dict) -> None:
    """将字典写入 profile.md（YAML frontmatter 格式），自动创建目录。"""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for key, value in data.items():
        if value is not None and str(value).strip():
            lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    PROFILE_PATH.write_text("\n".join(lines), encoding="utf-8")
