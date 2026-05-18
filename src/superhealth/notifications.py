"""OpenClaw/QClaw message push helpers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

_QCLAW_OPENCLAW_BIN = (
    Path.home() / "Library/Application Support/QClaw/openclaw/config/bin/openclaw"
)
_QCLAW_OPENCLAW_MJS = (
    Path.home()
    / "Library/Application Support/QClaw/openclaw/node_modules/openclaw/openclaw.mjs"
)
_QCLAW_STATE_DIR = Path.home() / "Library/Application Support/QClaw/openclaw/config"
_QCLAW_CONFIG_PATH = _QCLAW_STATE_DIR / "openclaw.json"


def _openclaw_command() -> str:
    return str(_QCLAW_OPENCLAW_BIN) if _QCLAW_OPENCLAW_BIN.exists() else "openclaw"


def _openclaw_env() -> dict[str, str] | None:
    if not _QCLAW_OPENCLAW_BIN.exists():
        return None
    return {
        **os.environ,
        "QCLAW_CLI_NODE_BINARY": "/Applications/QClaw.app/Contents/Resources/node/node",
        "QCLAW_CLI_OPENCLAW_MJS": str(_QCLAW_OPENCLAW_MJS),
        "OPENCLAW_STATE_DIR": str(_QCLAW_STATE_DIR),
    }


def _run_openclaw(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_openclaw_command(), *args],
        text=True,
        capture_output=True,
        timeout=timeout,
        env=_openclaw_env(),
    )


def _read_openclaw_config() -> dict:
    if not _QCLAW_CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(_QCLAW_CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _wecom_config_needs_update(bot_id: str, secret: str) -> bool:
    wecom = (_read_openclaw_config().get("channels") or {}).get("wecom") or {}
    return (
        wecom.get("enabled") is not True
        or wecom.get("botId") != bot_id
        or wecom.get("secret") != secret
    )


def _ensure_wecom_config(bot_id: str, secret: str, timeout: int) -> int:
    if not bot_id or not secret:
        print("企业微信 bot_id/secret 未配置，无法推送", file=sys.stderr)
        return 1
    if not _wecom_config_needs_update(bot_id, secret):
        return 0

    config_commands = [
        ["config", "set", "channels.wecom.enabled", "true"],
        ["config", "set", "channels.wecom.botId", bot_id],
        ["config", "set", "channels.wecom.secret", secret],
    ]
    try:
        for args in config_commands:
            proc = _run_openclaw(args, timeout=timeout)
            if proc.returncode != 0:
                if proc.stdout:
                    print(proc.stdout, end="")
                if proc.stderr:
                    print(proc.stderr, end="", file=sys.stderr)
                return proc.returncode
        proc = _run_openclaw(["gateway", "restart"], timeout=timeout)
        if proc.returncode != 0:
            if proc.stdout:
                print(proc.stdout, end="")
            if proc.stderr:
                print(proc.stderr, end="", file=sys.stderr)
            return proc.returncode
    except FileNotFoundError as e:
        print(f"找不到 openclaw 命令: {e}", file=sys.stderr)
        return 127
    except subprocess.TimeoutExpired:
        print(f"企业微信配置同步超时（{timeout}s）", file=sys.stderr)
        return 124

    time.sleep(1)
    return 0


def send_push_message(
    *,
    channel: str,
    target: str,
    message: str,
    account_id: str = "",
    wecom_bot_id: str = "",
    wecom_secret: str = "",
    timeout: int = 60,
) -> int:
    """Send a message through OpenClaw, with QClaw desktop install support."""
    if channel == "wecom":
        rc = _ensure_wecom_config(wecom_bot_id, wecom_secret, timeout)
        if rc != 0:
            return rc

    cmd = [
        _openclaw_command(),
        "message",
        "send",
        "--channel",
        channel,
        "-t",
        target,
        "--message",
        message,
    ]
    if account_id and channel != "wecom":
        cmd.extend(["--account", account_id])

    try:
        proc = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=_openclaw_env(),
        )
    except FileNotFoundError as e:
        print(f"找不到 openclaw 命令: {e}", file=sys.stderr)
        return 127
    except subprocess.TimeoutExpired:
        print(f"消息推送超时（{timeout}s）", file=sys.stderr)
        return 124

    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    return proc.returncode
