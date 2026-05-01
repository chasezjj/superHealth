"""配置管理：读取 ~/.healthy/config.toml，环境变量优先。

配置文件示例（~/.healthy/config.toml）：

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

环境变量覆盖（优先级最高）：
    HEALTHY_GARMIN_EMAIL / HEALTHY_GARMIN_PASSWORD
    HEALTHY_WECHAT_ACCOUNT_ID / HEALTHY_WECHAT_CHANNEL / HEALTHY_WECHAT_TARGET
    HEALTHY_VITALS_API_TOKEN / HEALTHY_VITALS_HOST / HEALTHY_VITALS_PORT
"""

from __future__ import annotations

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

CONFIG_PATH = Path.home() / ".healthy" / "config.toml"


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
    api_token: str = ""   # Health Auto Export 鉴权 token（X-API-Key header）
    host: str = "0.0.0.0"
    port: int = 5000

    def is_complete(self) -> bool:
        return bool(self.api_token)


@dataclass
class ClaudeConfig:
    api_key: str = ""                    # Anthropic API key
    model: str = "claude-sonnet-4-6"     # 默认模型
    max_tokens: int = 1024               # 最大输出 token
    base_url: str = ""                   # 自定义 endpoint（留空则用官方地址）

    def is_complete(self) -> bool:
        return bool(self.api_key)


@dataclass
class BaichuanConfig:
    api_key: str = ""                            # 百川 API key
    model: str = "Baichuan-M3-Plus"              # 百川医疗模型名
    max_tokens: int = 1024                       # 最大输出 token
    base_url: str = "https://api.baichuan-ai.com/v1"  # 百川 API endpoint

    def is_complete(self) -> bool:
        return bool(self.api_key)


@dataclass
class AdvisorConfig:
    # mode: claude_only | baichuan_only | both
    mode: str = "claude_only"


@dataclass
class WeatherConfig:
    api_key: str = ""          # 和风天气 API key
    city: str = ""            # 城市名称（如：北京）
    location_id: str = ""     # 和风天气城市ID（如：101010100）
    api_host: str = ""         # 私有 API host，如 n95rk72uwg.re.qweatherapi.com
    latitude: float = 39.92   # 纬度（用于空气质量 API）
    longitude: float = 116.41  # 经度（用于空气质量 API）

    def is_complete(self) -> bool:
        return bool(self.api_key)


@dataclass
class DashboardConfig:
    password: str = ""       # 仪表盘访问密码（空字符串=不设密码）
    session_token: str = ""  # 自动登录 token（query param 持久化）
    saved_password: str = "" # 用户勾选“记住密码”后保存的密码


@dataclass
class OutlookConfig:
    username: str = ""   # Exchange 用户名
    email: str = ""      # 邮箱地址
    password: str = ""   # 密码
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
    """加载配置，优先级：环境变量 > config.toml > 默认值（空字符串）。"""
    raw: dict[str, Any] = {}
    if config_path.exists():
        if tomllib is None:
            raise ImportError(
                "需要 tomllib（Python 3.11+）或安装 tomli: pip install tomli"
            )
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)

    garmin_raw = raw.get("garmin", {})
    garmin = GarminConfig(
        email=os.environ.get(
            "HEALTHY_GARMIN_EMAIL", garmin_raw.get("email", "")
        ),
        password=os.environ.get(
            "HEALTHY_GARMIN_PASSWORD", garmin_raw.get("password", "")
        ),
    )

    wechat_raw = raw.get("wechat", {})
    wechat = WechatConfig(
        account_id=os.environ.get(
            "HEALTHY_WECHAT_ACCOUNT_ID", wechat_raw.get("account_id", "")
        ),
        channel=os.environ.get(
            "HEALTHY_WECHAT_CHANNEL", wechat_raw.get("channel", "")
        ),
        target=os.environ.get(
            "HEALTHY_WECHAT_TARGET", wechat_raw.get("target", "")
        ),
    )
    vitals_raw = raw.get("vitals", {})
    vitals = VitalsConfig(
        api_token=os.environ.get(
            "HEALTHY_VITALS_API_TOKEN", vitals_raw.get("api_token", "")
        ),
        host=os.environ.get(
            "HEALTHY_VITALS_HOST", vitals_raw.get("host", "0.0.0.0")
        ),
        port=int(os.environ.get(
            "HEALTHY_VITALS_PORT", vitals_raw.get("port", 5000)
        )),
    )

    claude_raw = raw.get("claude", {})
    # config.toml 优先级高于环境变量（用于支持代理配置）
    claude = ClaudeConfig(
        api_key=claude_raw.get("api_key", "")
        or os.environ.get("ANTHROPIC_API_KEY", "")
        or os.environ.get("HEALTHY_CLAUDE_API_KEY", ""),
        model=claude_raw.get("model", "")
        or os.environ.get("HEALTHY_CLAUDE_MODEL", "")
        or "claude-sonnet-4-6",
        max_tokens=int(
            claude_raw.get("max_tokens", 0)
            or os.environ.get("HEALTHY_CLAUDE_MAX_TOKENS", 1024)
        ),
        base_url=claude_raw.get("base_url", "")
        or os.environ.get("ANTHROPIC_BASE_URL", "")
        or os.environ.get("HEALTHY_CLAUDE_BASE_URL", ""),
    )

    weather_raw = raw.get("weather", {})
    weather = WeatherConfig(
        api_key=os.environ.get(
            "HEALTHY_WEATHER_API_KEY", weather_raw.get("api_key", "")
        ),
        city=os.environ.get(
            "HEALTHY_WEATHER_CITY", weather_raw.get("city", "")
        ),
        location_id=os.environ.get(
            "HEALTHY_WEATHER_LOCATION_ID", weather_raw.get("location_id", "")
        ),
        api_host=os.environ.get(
            "HEALTHY_WEATHER_API_HOST", weather_raw.get("api_host", "")
        ),
        latitude=float(os.environ.get(
            "HEALTHY_WEATHER_LAT", weather_raw.get("latitude", 39.92)
        )),
        longitude=float(os.environ.get(
            "HEALTHY_WEATHER_LON", weather_raw.get("longitude", 116.41)
        )),
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
        mode=os.environ.get(
            "HEALTHY_ADVISOR_MODE", advisor_raw.get("mode", "claude_only")
        ),
    )

    dashboard_raw = raw.get("dashboard", {})
    dashboard = DashboardConfig(
        password=os.environ.get(
            "HEALTHY_DASHBOARD_PASSWORD", dashboard_raw.get("password", "")
        ),
        session_token=dashboard_raw.get("session_token", ""),
        saved_password=dashboard_raw.get("saved_password", ""),
    )

    outlook_raw = raw.get("outlook", {})
    outlook = OutlookConfig(
        username=os.environ.get(
            "HEALTHY_OUTLOOK_USERNAME", outlook_raw.get("username", "")
        ),
        email=os.environ.get(
            "HEALTHY_OUTLOOK_EMAIL", outlook_raw.get("email", "")
        ),
        password=os.environ.get(
            "HEALTHY_OUTLOOK_PASSWORD", outlook_raw.get("password", "")
        ),
        timezone=os.environ.get(
            "HEALTHY_OUTLOOK_TIMEZONE", outlook_raw.get("timezone", "Asia/Shanghai")
        ),
    )

    return AppConfig(
        garmin=garmin, wechat=wechat, vitals=vitals, claude=claude,
        weather=weather, baichuan=baichuan, advisor=advisor,
        dashboard=dashboard, outlook=outlook,
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
    """将用户记住的密码写入 config.toml（保留其他 section）。"""
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
        raw["dashboard"]["saved_password"] = password
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
