"""统一日志配置。

所有 CLI / 模块入口统一调用 setup_logging()，避免每个文件各自 basicConfig。
日志级别可通过环境变量 SUPERHEALTH_LOG_LEVEL 覆盖（默认 INFO）。
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

DEFAULT_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"
DEFAULT_LEVEL = logging.INFO

_ENV_VAR = "SUPERHEALTH_LOG_LEVEL"


def _parse_level(level: Optional[str]) -> int:
    if not level:
        return DEFAULT_LEVEL
    try:
        return int(level)
    except ValueError:
        return getattr(logging, level.upper(), DEFAULT_LEVEL)


def setup_logging(
    level: Optional[int] = None,
    fmt: Optional[str] = None,
    datefmt: Optional[str] = None,
) -> None:
    """配置根日志记录器。

    参数显式传入时优先级最高；否则读取 SUPERHEALTH_LOG_LEVEL 环境变量；
    否则使用默认值 INFO。

    多次调用安全：若根记录器已有处理器则跳过（不重复添加）。
    """
    root = logging.getLogger()
    if root.handlers:
        return

    effective_level = level if level is not None else _parse_level(os.environ.get(_ENV_VAR))
    effective_fmt = fmt or DEFAULT_FORMAT
    effective_datefmt = datefmt or DEFAULT_DATEFMT

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(effective_fmt, datefmt=effective_datefmt))
    root.addHandler(handler)
    root.setLevel(effective_level)
