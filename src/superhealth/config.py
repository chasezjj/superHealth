"""配置管理：读取 ~/.superhealth/config.toml。

优先级统一为：config.toml > 环境变量 > 内置默认值。
即页面/文件中显式填写的值不会被环境变量覆盖；环境变量仅在 toml 对应字段
缺失或为空字符串时作为 fallback 生效。

配置文件示例（~/.superhealth/config.toml）：

    [garmin]
    email    = "your_email_or_phone"
    password = "your_password"

    [wechat]
    account_id = "your-bot-account-id"
    channel    = "your-channel"
    target     = "your-wechat-openid"

    [vitals]
    api_token = "your-secret-token"   # Health Auto Export 鉴权 token
    host      = "0.0.0.0"             # 监听地址（默认全部接口）
    port      = 5000                  # 监听端口

环境变量 fallback（仅在 toml 字段为空时生效）：
    HEALTHY_GARMIN_EMAIL / HEALTHY_GARMIN_PASSWORD
    HEALTHY_WECHAT_ACCOUNT_ID / HEALTHY_WECHAT_CHANNEL / HEALTHY_WECHAT_TARGET
    HEALTHY_VITALS_API_TOKEN / HEALTHY_VITALS_HOST / HEALTHY_VITALS_PORT
    SUPERHEALTH_DB                       # 数据库路径覆盖

    注：HEALTHY_* 前缀为历史遗留，新增配置建议使用 SUPERHEALTH_* 前缀。
    SUPERHEALTH_DB 仍以 env 为准（用于运行时切库，无对应 toml 字段）。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[import-not-found,no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

CONFIG_PATH = Path.home() / ".superhealth" / "config.toml"


def hash_password(password: str) -> str:
    """Hash a password with PBKDF2-HMAC-SHA256 (200k iterations, 32-byte salt)."""
    salt = os.urandom(32).hex()
    hashed = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), 200_000
    ).hex()
    return f"pbkdf2:sha256:200000${salt}${hashed}"


def verify_password(password: str, stored: str) -> bool:
    """Verify a password against a stored hash (pbkdf2$iter$salt$hash or legacy salt$hash)."""
    import hmac as _hmac

    if "$" not in stored:
        # Legacy plaintext — migrate on next save
        return _hmac.compare_digest(password, stored)

    # Support legacy single-round SHA-256 hashes (salt$hash)
    if stored.count("$") == 1:
        salt, hashed = stored.split("$", 1)
        check = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
        return _hmac.compare_digest(check, hashed)

    # PBKDF2 format: pbkdf2:sha256:iter$salt$hash
    prefix, salt, hashed = stored.split("$", 2)
    # Extract iteration count from prefix like "pbkdf2:sha256:200000"
    try:
        iterations = int(prefix.rsplit(":", 1)[-1])
    except ValueError:
        iterations = 200_000
    check = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), iterations
    ).hex()
    return _hmac.compare_digest(check, hashed)


def get_db_path() -> Path:
    """Return the database path, checking env var first, then default."""
    env = os.environ.get("SUPERHEALTH_DB")
    if env:
        return Path(env)
    return Path(__file__).parent.parent.parent / "health.db"


@dataclass
class GarminConfig:
    email: str = ""
    password: str = ""

    def is_complete(self) -> bool:
        return bool(self.email and self.password)


@dataclass
class WechatConfig:
    account_id: str = ""
    channel: str = ""
    target: str = ""

    def is_complete(self) -> bool:
        return bool(self.account_id and self.channel and self.target)


@dataclass
class VitalsConfig:
    api_token: str = ""  # Health Auto Export 鉴权 token（X-API-Key header）
    host: str = "0.0.0.0"
    port: int = 5000

    def is_complete(self) -> bool:
        return bool(self.api_token)


@dataclass
class ClaudeConfig:
    api_key: str = ""  # Anthropic API key
    model: str = "claude-sonnet-4-6"  # 默认模型（文本任务）
    vision_model: str = "claude-opus-4-7"  # 视觉/PDF 文档提取专用模型
    max_tokens: int = 1024  # 最大输出 token
    base_url: str = ""  # 自定义 endpoint（留空则用官方地址）

    def is_complete(self) -> bool:
        return bool(self.api_key)


@dataclass
class BaichuanConfig:
    api_key: str = ""  # 百川 API key
    model: str = "Baichuan-M3-Plus"  # 百川医疗模型名
    max_tokens: int = 1024  # 最大输出 token
    base_url: str = "https://api.baichuan-ai.com/v1"  # 百川 API endpoint

    def is_complete(self) -> bool:
        return bool(self.api_key)


@dataclass
class AdvisorConfig:
    # mode: claude_only | baichuan_only | both
    mode: str = "claude_only"


@dataclass
class WeatherConfig:
    api_key: str = ""  # 和风天气 API key
    city: str = ""  # 城市名称（如：北京）
    location_id: str = ""  # 和风天气城市ID（如：101010100）
    api_host: str = ""  # 私有 API host，如 n95rk72uwg.re.qweatherapi.com
    latitude: float = 39.92  # 纬度（用于空气质量 API）
    longitude: float = 116.41  # 经度（用于空气质量 API）

    def is_complete(self) -> bool:
        return bool(self.api_key)


@dataclass
class DashboardConfig:
    password: str = ""  # 仪表盘访问密码（空字符串=不设密码）
    session_token: str = ""  # 自动登录 token（query param 持久化）
    saved_password: str = ""  # 用户勾选“记住密码”后保存的密码


@dataclass
class OutlookConfig:
    username: str = ""  # Exchange 用户名
    email: str = ""  # 邮箱地址
    password: str = ""  # 密码
    timezone: str = "Asia/Shanghai"

    def is_complete(self) -> bool:
        return bool(self.username and self.email and self.password)


@dataclass
class AppConfig:
    garmin: GarminConfig = field(default_factory=GarminConfig)
    wechat: WechatConfig = field(default_factory=WechatConfig)
    vitals: VitalsConfig = field(default_factory=VitalsConfig)
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    weather: WeatherConfig = field(default_factory=WeatherConfig)
    baichuan: BaichuanConfig = field(default_factory=BaichuanConfig)
    advisor: AdvisorConfig = field(default_factory=AdvisorConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    outlook: OutlookConfig = field(default_factory=OutlookConfig)


def load(config_path: Path = CONFIG_PATH) -> AppConfig:
    """加载配置，优先级：config.toml > 环境变量 > 默认值。

    toml 中显式填写的非空值始终优先；env 仅在 toml 字段缺失或为空字符串时生效。
    数值字段（port/lat/lon/max_tokens）以 0 视作"未设置"，回退到 env 或默认值。
    """
    raw: dict[str, Any] = {}
    if config_path.exists():
        if tomllib is None:
            raise ImportError("需要 tomllib（Python 3.11+）或安装 tomli: pip install tomli")
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)

    garmin_raw = raw.get("garmin", {})
    garmin = GarminConfig(
        email=garmin_raw.get("email", "") or os.environ.get("HEALTHY_GARMIN_EMAIL", ""),
        password=garmin_raw.get("password", "") or os.environ.get("HEALTHY_GARMIN_PASSWORD", ""),
    )

    wechat_raw = raw.get("wechat", {})
    wechat = WechatConfig(
        account_id=wechat_raw.get("account_id", "")
        or os.environ.get("HEALTHY_WECHAT_ACCOUNT_ID", ""),
        channel=wechat_raw.get("channel", "") or os.environ.get("HEALTHY_WECHAT_CHANNEL", ""),
        target=wechat_raw.get("target", "") or os.environ.get("HEALTHY_WECHAT_TARGET", ""),
    )

    vitals_raw = raw.get("vitals", {})
    vitals = VitalsConfig(
        api_token=vitals_raw.get("api_token", "")
        or os.environ.get("HEALTHY_VITALS_API_TOKEN", ""),
        host=vitals_raw.get("host", "") or os.environ.get("HEALTHY_VITALS_HOST", "") or "0.0.0.0",
        port=int(
            vitals_raw.get("port", 0) or os.environ.get("HEALTHY_VITALS_PORT", 0) or 5000
        ),
    )

    claude_raw = raw.get("claude", {})
    claude = ClaudeConfig(
        api_key=claude_raw.get("api_key", "")
        or os.environ.get("ANTHROPIC_API_KEY", "")
        or os.environ.get("HEALTHY_CLAUDE_API_KEY", ""),
        model=claude_raw.get("model", "")
        or os.environ.get("HEALTHY_CLAUDE_MODEL", "")
        or "claude-sonnet-4-6",
        vision_model=claude_raw.get("vision_model", "")
        or os.environ.get("HEALTHY_CLAUDE_VISION_MODEL", "")
        or "claude-opus-4-7",
        max_tokens=int(
            claude_raw.get("max_tokens", 0)
            or os.environ.get("HEALTHY_CLAUDE_MAX_TOKENS", 0)
            or 1024
        ),
        base_url=claude_raw.get("base_url", "")
        or os.environ.get("ANTHROPIC_BASE_URL", "")
        or os.environ.get("HEALTHY_CLAUDE_BASE_URL", ""),
    )

    weather_raw = raw.get("weather", {})
    weather = WeatherConfig(
        api_key=weather_raw.get("api_key", "") or os.environ.get("HEALTHY_WEATHER_API_KEY", ""),
        city=weather_raw.get("city", "") or os.environ.get("HEALTHY_WEATHER_CITY", ""),
        location_id=weather_raw.get("location_id", "")
        or os.environ.get("HEALTHY_WEATHER_LOCATION_ID", ""),
        api_host=weather_raw.get("api_host", "")
        or os.environ.get("HEALTHY_WEATHER_API_HOST", ""),
        latitude=float(
            weather_raw.get("latitude", 0) or os.environ.get("HEALTHY_WEATHER_LAT", 0) or 39.92
        ),
        longitude=float(
            weather_raw.get("longitude", 0) or os.environ.get("HEALTHY_WEATHER_LON", 0) or 116.41
        ),
    )

    baichuan_raw = raw.get("baichuan", {})
    baichuan = BaichuanConfig(
        api_key=baichuan_raw.get("api_key", "")
        or os.environ.get("BAICHUAN_API_KEY", "")
        or os.environ.get("HEALTHY_BAICHUAN_API_KEY", ""),
        model=baichuan_raw.get("model", "Baichuan-M3-Plus"),
        max_tokens=int(baichuan_raw.get("max_tokens", 1024)),
        base_url=baichuan_raw.get("base_url", "https://api.baichuan-ai.com/v1"),
    )

    advisor_raw = raw.get("advisor", {})
    advisor = AdvisorConfig(
        mode=os.environ.get("HEALTHY_ADVISOR_MODE", "")
        or advisor_raw.get("mode", "")
        or "claude_only",
    )

    dashboard_raw = raw.get("dashboard", {})
    dashboard = DashboardConfig(
        password=os.environ.get("HEALTHY_DASHBOARD_PASSWORD", "")
        or dashboard_raw.get("password", ""),
        session_token=dashboard_raw.get("session_token", ""),
        saved_password=dashboard_raw.get("saved_password", ""),
    )

    outlook_raw = raw.get("outlook", {})
    outlook = OutlookConfig(
        username=os.environ.get("HEALTHY_OUTLOOK_USERNAME", "")
        or outlook_raw.get("username", ""),
        email=os.environ.get("HEALTHY_OUTLOOK_EMAIL", "") or outlook_raw.get("email", ""),
        password=os.environ.get("HEALTHY_OUTLOOK_PASSWORD", "")
        or outlook_raw.get("password", ""),
        timezone=os.environ.get("HEALTHY_OUTLOOK_TIMEZONE", "")
        or outlook_raw.get("timezone", "")
        or "Asia/Shanghai",
    )

    return AppConfig(
        garmin=garmin,
        wechat=wechat,
        vitals=vitals,
        claude=claude,
        weather=weather,
        baichuan=baichuan,
        advisor=advisor,
        dashboard=dashboard,
        outlook=outlook,
    )


def save_garmin(email: str, password: str, config_path: Path = CONFIG_PATH) -> None:
    """将 Garmin 凭据写入 config.toml（保留其他 section）。"""
    try:
        import tomli_w  # type: ignore[import-not-found]
    except ImportError:
        raise ImportError("需要 tomli-w: pip install tomli-w")

    raw: dict[str, Any] = {}
    if config_path.exists() and tomllib is not None:
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)

    raw.setdefault("garmin", {})
    raw["garmin"]["email"] = email
    raw["garmin"]["password"] = password

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_bytes(tomli_w.dumps(raw).encode())
    config_path.chmod(0o600)


def save_dashboard_session_token(token: str, config_path: Path = CONFIG_PATH) -> None:
    """将仪表盘自动登录 token 写入 config.toml（保留其他 section）。"""
    try:
        import tomli_w  # type: ignore[import-not-found]
    except ImportError:
        raise ImportError("需要 tomli-w: pip install tomli-w")

    raw: dict[str, Any] = {}
    if config_path.exists() and tomllib is not None:
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)

    raw.setdefault("dashboard", {})
    raw["dashboard"]["session_token"] = token

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_bytes(tomli_w.dumps(raw).encode())
    config_path.chmod(0o600)


def save_dashboard_password(password: str, config_path: Path = CONFIG_PATH) -> None:
    """将用户记住的密码 hash 写入 config.toml（保留其他 section）。"""
    try:
        import tomli_w  # type: ignore[import-not-found]
    except ImportError:
        raise ImportError("需要 tomli-w: pip install tomli-w")

    raw: dict[str, Any] = {}
    if config_path.exists() and tomllib is not None:
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)

    raw.setdefault("dashboard", {})
    if password:
        raw["dashboard"]["saved_password"] = hash_password(password)
    else:
        raw["dashboard"].pop("saved_password", None)

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_bytes(tomli_w.dumps(raw).encode())
    config_path.chmod(0o600)


def save_config(config: AppConfig, config_path: Path = CONFIG_PATH) -> None:
    """将完整 AppConfig 写入 config.toml（保留其他 section 和未知字段）。"""
    try:
        import tomli_w  # type: ignore[import-not-found]
    except ImportError:
        raise ImportError("需要 tomli-w: pip install tomli-w")

    raw: dict[str, Any] = {}
    if config_path.exists() and tomllib is not None:
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)

    raw["garmin"] = {
        "email": config.garmin.email,
        "password": config.garmin.password,
    }
    raw["wechat"] = {
        "account_id": config.wechat.account_id,
        "channel": config.wechat.channel,
        "target": config.wechat.target,
    }
    raw["vitals"] = {
        "api_token": config.vitals.api_token,
        "host": config.vitals.host,
        "port": config.vitals.port,
    }
    raw["claude"] = {
        "api_key": config.claude.api_key,
        "model": config.claude.model,
        "max_tokens": config.claude.max_tokens,
        "base_url": config.claude.base_url,
    }
    raw["baichuan"] = {
        "api_key": config.baichuan.api_key,
        "model": config.baichuan.model,
        "max_tokens": config.baichuan.max_tokens,
        "base_url": config.baichuan.base_url,
    }
    raw["advisor"] = {
        "mode": config.advisor.mode,
    }
    raw["weather"] = {
        "api_key": config.weather.api_key,
        "city": config.weather.city,
        "location_id": config.weather.location_id,
        "api_host": config.weather.api_host,
        "latitude": config.weather.latitude,
        "longitude": config.weather.longitude,
    }

    raw.setdefault("dashboard", {})
    raw["dashboard"]["password"] = config.dashboard.password
    if config.dashboard.session_token:
        raw["dashboard"]["session_token"] = config.dashboard.session_token
    else:
        raw["dashboard"].pop("session_token", None)
    if config.dashboard.saved_password:
        raw["dashboard"]["saved_password"] = config.dashboard.saved_password
    else:
        raw["dashboard"].pop("saved_password", None)

    raw["outlook"] = {
        "username": config.outlook.username,
        "email": config.outlook.email,
        "password": config.outlook.password,
        "timezone": config.outlook.timezone,
    }

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_bytes(tomli_w.dumps(raw).encode())
    config_path.chmod(0o600)
