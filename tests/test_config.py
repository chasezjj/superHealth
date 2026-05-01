"""测试配置管理模块。"""
import hashlib
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from superhealth import config as cfg


class TestLoadConfig:
    def test_load_from_toml(self, tmp_path):
        toml_file = tmp_path / "config.toml"
        toml_file.write_text(
            '[wechat]\naccount_id = "test-bot"\nchannel = "test-ch"\ntarget = "test-target"\n'
        )
        conf = cfg.load(toml_file)
        assert conf.wechat.account_id == "test-bot"
        assert conf.wechat.channel == "test-ch"
        assert conf.wechat.target == "test-target"

    def test_env_vars_override_toml(self, tmp_path):
        toml_file = tmp_path / "config.toml"
        toml_file.write_text(
            '[wechat]\naccount_id = "from-file"\nchannel = "from-file"\ntarget = "from-file"\n'
        )
        env = {
            "HEALTHY_WECHAT_ACCOUNT_ID": "from-env",
            "HEALTHY_WECHAT_CHANNEL": "from-env-ch",
            "HEALTHY_WECHAT_TARGET": "from-env-target",
        }
        with patch.dict(os.environ, env):
            conf = cfg.load(toml_file)
        assert conf.wechat.account_id == "from-env"
        assert conf.wechat.channel == "from-env-ch"
        assert conf.wechat.target == "from-env-target"

    def test_partial_env_override(self, tmp_path):
        toml_file = tmp_path / "config.toml"
        toml_file.write_text(
            '[wechat]\naccount_id = "from-file"\nchannel = "from-file"\ntarget = "from-file"\n'
        )
        with patch.dict(os.environ, {"HEALTHY_WECHAT_ACCOUNT_ID": "from-env"}):
            conf = cfg.load(toml_file)
        assert conf.wechat.account_id == "from-env"
        assert conf.wechat.channel == "from-file"  # 未被覆盖

    def test_missing_config_file_returns_defaults(self, tmp_path):
        conf = cfg.load(tmp_path / "nonexistent.toml")
        assert conf.wechat.account_id == ""
        assert conf.wechat.channel == ""
        assert conf.wechat.target == ""

    def test_env_vars_work_without_file(self, tmp_path):
        env = {
            "HEALTHY_WECHAT_ACCOUNT_ID": "env-only",
            "HEALTHY_WECHAT_CHANNEL": "env-ch",
            "HEALTHY_WECHAT_TARGET": "env-target",
        }
        with patch.dict(os.environ, env):
            conf = cfg.load(tmp_path / "nonexistent.toml")
        assert conf.wechat.account_id == "env-only"

    def test_load_garmin_from_toml(self, tmp_path):
        toml_file = tmp_path / "config.toml"
        toml_file.write_text(
            '[garmin]\nemail = "user@test.com"\npassword = "pw"\n'
        )
        conf = cfg.load(toml_file)
        assert conf.garmin.email == "user@test.com"
        assert conf.garmin.password == "pw"

    def test_load_garmin_env_overrides(self, tmp_path):
        toml_file = tmp_path / "config.toml"
        toml_file.write_text('[garmin]\nemail = "file"\npassword = "filepw"\n')
        with patch.dict(
            os.environ,
            {"HEALTHY_GARMIN_EMAIL": "envuser", "HEALTHY_GARMIN_PASSWORD": "envpw"},
        ):
            conf = cfg.load(toml_file)
        assert conf.garmin.email == "envuser"
        assert conf.garmin.password == "envpw"

    def test_load_vitals_with_defaults(self, tmp_path):
        # 文件中只填 api_token，host/port 应使用默认值
        toml_file = tmp_path / "config.toml"
        toml_file.write_text('[vitals]\napi_token = "tok"\n')
        conf = cfg.load(toml_file)
        assert conf.vitals.api_token == "tok"
        assert conf.vitals.host == "0.0.0.0"
        assert conf.vitals.port == 5000

    def test_load_vitals_port_coerced_to_int(self, tmp_path):
        # 文件里写字符串端口，应被转成 int
        toml_file = tmp_path / "config.toml"
        toml_file.write_text('[vitals]\napi_token = "t"\nhost = "127.0.0.1"\nport = 8080\n')
        conf = cfg.load(toml_file)
        assert conf.vitals.port == 8080
        assert isinstance(conf.vitals.port, int)

    def test_load_vitals_env_port_coerced_to_int(self, tmp_path):
        toml_file = tmp_path / "config.toml"
        with patch.dict(
            os.environ,
            {
                "HEALTHY_VITALS_API_TOKEN": "tok",
                "HEALTHY_VITALS_HOST": "10.0.0.1",
                "HEALTHY_VITALS_PORT": "9000",
            },
        ):
            conf = cfg.load(toml_file)
        assert conf.vitals.host == "10.0.0.1"
        assert conf.vitals.port == 9000
        assert isinstance(conf.vitals.port, int)

    def test_load_claude_anthropic_env_takes_priority_over_healthy(self, tmp_path):
        toml_file = tmp_path / "config.toml"
        toml_file.write_text('[claude]\napi_key = "from-toml"\n')
        with patch.dict(
            os.environ,
            {"ANTHROPIC_API_KEY": "anth", "HEALTHY_CLAUDE_API_KEY": "healthy"},
            clear=False,
        ):
            conf = cfg.load(toml_file)
        assert conf.claude.api_key == "anth"

    def test_load_claude_healthy_env_takes_priority_over_toml(self, tmp_path):
        toml_file = tmp_path / "config.toml"
        toml_file.write_text('[claude]\napi_key = "from-toml"\n')
        # 清除 ANTHROPIC_API_KEY 以隔离测试
        with patch.dict(os.environ, {"HEALTHY_CLAUDE_API_KEY": "healthy"}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            conf = cfg.load(toml_file)
        assert conf.claude.api_key == "healthy"

    def test_load_claude_falls_back_to_toml_when_no_env(self, tmp_path):
        toml_file = tmp_path / "config.toml"
        toml_file.write_text('[claude]\napi_key = "from-toml"\nmodel = "x-model"\n')
        with patch.dict(os.environ, {}, clear=True):
            conf = cfg.load(toml_file)
        assert conf.claude.api_key == "from-toml"
        assert conf.claude.model == "x-model"

    def test_load_claude_default_model_when_empty(self, tmp_path):
        # toml 留空 model，应回退到默认值
        toml_file = tmp_path / "config.toml"
        toml_file.write_text('[claude]\napi_key = "k"\nmodel = ""\n')
        with patch.dict(os.environ, {}, clear=True):
            conf = cfg.load(toml_file)
        assert conf.claude.model == "claude-sonnet-4-6"

    def test_load_claude_defaults(self, tmp_path):
        toml_file = tmp_path / "config.toml"
        with patch.dict(os.environ, {}, clear=True):
            conf = cfg.load(toml_file)
        assert conf.claude.api_key == ""
        assert conf.claude.model == "claude-sonnet-4-6"
        assert conf.claude.max_tokens == 1024
        assert conf.claude.base_url == ""

    def test_load_claude_base_url_anthropic_env(self, tmp_path):
        toml_file = tmp_path / "config.toml"
        with patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "https://proxy.x"}, clear=False):
            conf = cfg.load(toml_file)
        assert conf.claude.base_url == "https://proxy.x"

    def test_load_baichuan_toml_takes_priority_over_env(self, tmp_path):
        # 注意：baichuan 的优先级与其他 section 不同（toml 优先于 env）
        toml_file = tmp_path / "config.toml"
        toml_file.write_text('[baichuan]\napi_key = "from-toml"\nmodel = "M1"\n')
        with patch.dict(os.environ, {"BAICHUAN_API_KEY": "from-env"}, clear=False):
            conf = cfg.load(toml_file)
        assert conf.baichuan.api_key == "from-toml"
        assert conf.baichuan.model == "M1"

    def test_load_baichuan_env_used_when_toml_empty(self, tmp_path):
        toml_file = tmp_path / "config.toml"
        toml_file.write_text("")  # 无 [baichuan]
        with patch.dict(os.environ, {"BAICHUAN_API_KEY": "env-key"}, clear=False):
            conf = cfg.load(toml_file)
        assert conf.baichuan.api_key == "env-key"

    def test_load_baichuan_healthy_env_fallback(self, tmp_path):
        # BAICHUAN_API_KEY 缺失但 HEALTHY_BAICHUAN_API_KEY 存在
        toml_file = tmp_path / "config.toml"
        with patch.dict(os.environ, {"HEALTHY_BAICHUAN_API_KEY": "healthy"}, clear=True):
            conf = cfg.load(toml_file)
        assert conf.baichuan.api_key == "healthy"

    def test_load_baichuan_defaults(self, tmp_path):
        toml_file = tmp_path / "config.toml"
        with patch.dict(os.environ, {}, clear=True):
            conf = cfg.load(toml_file)
        assert conf.baichuan.model == "Baichuan-M3-Plus"
        assert conf.baichuan.max_tokens == 1024
        assert conf.baichuan.base_url == "https://api.baichuan-ai.com/v1"

    def test_load_advisor_default(self, tmp_path):
        toml_file = tmp_path / "config.toml"
        with patch.dict(os.environ, {}, clear=True):
            conf = cfg.load(toml_file)
        assert conf.advisor.mode == "claude_only"

    def test_load_advisor_env_override(self, tmp_path):
        toml_file = tmp_path / "config.toml"
        toml_file.write_text('[advisor]\nmode = "claude_only"\n')
        with patch.dict(os.environ, {"HEALTHY_ADVISOR_MODE": "both"}):
            conf = cfg.load(toml_file)
        assert conf.advisor.mode == "both"

    def test_load_weather_lat_lon_floats(self, tmp_path):
        toml_file = tmp_path / "config.toml"
        toml_file.write_text(
            '[weather]\napi_key = "k"\ncity = "上海"\nlatitude = 31.23\nlongitude = 121.47\n'
        )
        conf = cfg.load(toml_file)
        assert conf.weather.api_key == "k"
        assert conf.weather.city == "上海"
        assert conf.weather.latitude == pytest.approx(31.23)
        assert conf.weather.longitude == pytest.approx(121.47)

    def test_load_weather_env_lat_lon_coerced_to_float(self, tmp_path):
        toml_file = tmp_path / "config.toml"
        with patch.dict(
            os.environ,
            {"HEALTHY_WEATHER_LAT": "22.5", "HEALTHY_WEATHER_LON": "114.0"},
        ):
            conf = cfg.load(toml_file)
        assert conf.weather.latitude == pytest.approx(22.5)
        assert conf.weather.longitude == pytest.approx(114.0)
        assert isinstance(conf.weather.latitude, float)

    def test_load_weather_defaults_to_beijing(self, tmp_path):
        toml_file = tmp_path / "config.toml"
        with patch.dict(os.environ, {}, clear=True):
            conf = cfg.load(toml_file)
        # 默认坐标 ≈ 北京
        assert conf.weather.latitude == pytest.approx(39.92)
        assert conf.weather.longitude == pytest.approx(116.41)
        assert conf.weather.api_host == ""

    def test_load_dashboard_session_token_and_saved_password(self, tmp_path):
        toml_file = tmp_path / "config.toml"
        toml_file.write_text(
            '[dashboard]\npassword = "p"\nsession_token = "tok"\nsaved_password = "saved"\n'
        )
        conf = cfg.load(toml_file)
        assert conf.dashboard.password == "p"
        assert conf.dashboard.session_token == "tok"
        assert conf.dashboard.saved_password == "saved"

    def test_load_dashboard_password_env_override(self, tmp_path):
        toml_file = tmp_path / "config.toml"
        toml_file.write_text('[dashboard]\npassword = "file"\n')
        with patch.dict(os.environ, {"HEALTHY_DASHBOARD_PASSWORD": "env"}):
            conf = cfg.load(toml_file)
        assert conf.dashboard.password == "env"

    def test_load_outlook_defaults(self, tmp_path):
        toml_file = tmp_path / "config.toml"
        with patch.dict(os.environ, {}, clear=True):
            conf = cfg.load(toml_file)
        assert conf.outlook.username == ""
        assert conf.outlook.email == ""
        assert conf.outlook.password == ""
        assert conf.outlook.timezone == "Asia/Shanghai"

    def test_load_outlook_env_overrides(self, tmp_path):
        toml_file = tmp_path / "config.toml"
        toml_file.write_text('[outlook]\nusername = "fu"\nemail = "fe"\npassword = "fp"\n')
        env = {
            "HEALTHY_OUTLOOK_USERNAME": "envu",
            "HEALTHY_OUTLOOK_EMAIL": "enve",
            "HEALTHY_OUTLOOK_PASSWORD": "envp",
            "HEALTHY_OUTLOOK_TIMEZONE": "UTC",
        }
        with patch.dict(os.environ, env):
            conf = cfg.load(toml_file)
        assert conf.outlook.username == "envu"
        assert conf.outlook.email == "enve"
        assert conf.outlook.password == "envp"
        assert conf.outlook.timezone == "UTC"

    def test_load_returns_app_config_with_all_sections(self, tmp_path):
        """空配置文件应返回包含所有 section 默认值的 AppConfig。"""
        conf = cfg.load(tmp_path / "missing.toml")
        assert isinstance(conf, cfg.AppConfig)
        assert isinstance(conf.garmin, cfg.GarminConfig)
        assert isinstance(conf.wechat, cfg.WechatConfig)
        assert isinstance(conf.vitals, cfg.VitalsConfig)
        assert isinstance(conf.claude, cfg.ClaudeConfig)
        assert isinstance(conf.weather, cfg.WeatherConfig)
        assert isinstance(conf.baichuan, cfg.BaichuanConfig)
        assert isinstance(conf.advisor, cfg.AdvisorConfig)
        assert isinstance(conf.dashboard, cfg.DashboardConfig)
        assert isinstance(conf.outlook, cfg.OutlookConfig)


class TestIsCompleteMethods:
    """覆盖各 *Config 的 is_complete() 行为。"""

    def test_garmin_complete(self):
        assert cfg.GarminConfig(email="u", password="p").is_complete() is True

    def test_garmin_missing_email(self):
        assert cfg.GarminConfig(email="", password="p").is_complete() is False

    def test_garmin_missing_password(self):
        assert cfg.GarminConfig(email="u", password="").is_complete() is False

    def test_garmin_default_empty(self):
        assert cfg.GarminConfig().is_complete() is False

    def test_vitals_complete_with_only_token(self):
        # api_token 是必需，host/port 有默认值
        assert cfg.VitalsConfig(api_token="t").is_complete() is True

    def test_vitals_incomplete_without_token(self):
        assert cfg.VitalsConfig(api_token="", host="x", port=1).is_complete() is False

    def test_claude_complete_with_only_api_key(self):
        assert cfg.ClaudeConfig(api_key="k").is_complete() is True

    def test_claude_incomplete_without_api_key(self):
        assert cfg.ClaudeConfig(api_key="", model="m").is_complete() is False

    def test_baichuan_complete_with_only_api_key(self):
        assert cfg.BaichuanConfig(api_key="k").is_complete() is True

    def test_baichuan_incomplete_without_api_key(self):
        assert cfg.BaichuanConfig(api_key="", model="m").is_complete() is False

    def test_weather_complete_with_only_api_key(self):
        assert cfg.WeatherConfig(api_key="k").is_complete() is True

    def test_weather_incomplete_without_api_key(self):
        assert cfg.WeatherConfig(api_key="", city="北京").is_complete() is False

    def test_outlook_complete(self):
        assert (
            cfg.OutlookConfig(username="u", email="e", password="p").is_complete() is True
        )

    @pytest.mark.parametrize(
        "u,e,p",
        [("", "e", "p"), ("u", "", "p"), ("u", "e", ""), ("", "", "")],
    )
    def test_outlook_incomplete_when_any_required_missing(self, u, e, p):
        assert cfg.OutlookConfig(username=u, email=e, password=p).is_complete() is False


class TestWechatConfigIsComplete:
    def test_complete(self):
        w = cfg.WechatConfig(account_id="a", channel="b", target="c")
        assert w.is_complete() is True

    def test_missing_one_field(self):
        w = cfg.WechatConfig(account_id="a", channel="b", target="")
        assert w.is_complete() is False

    def test_all_empty(self):
        w = cfg.WechatConfig()
        assert w.is_complete() is False


class TestPasswordHashing:
    def test_hash_and_verify(self):
        hashed = cfg.hash_password("mypassword")
        assert "$" in hashed
        assert cfg.verify_password("mypassword", hashed)

    def test_wrong_password_fails(self):
        hashed = cfg.hash_password("mypassword")
        assert not cfg.verify_password("wrong", hashed)

    def test_each_hash_is_unique(self):
        h1 = cfg.hash_password("same")
        h2 = cfg.hash_password("same")
        assert h1 != h2  # different salts
        assert cfg.verify_password("same", h1)
        assert cfg.verify_password("same", h2)

    def test_plaintext_migration(self):
        """Legacy plaintext passwords should still work."""
        assert cfg.verify_password("plain", "plain")

    def test_plaintext_wrong_password_rejected(self):
        assert cfg.verify_password("plain", "other") is False

    def test_hash_format_starts_with_pbkdf2(self):
        hashed = cfg.hash_password("p")
        assert hashed.startswith("pbkdf2:sha256:200000$")

    def test_hash_includes_three_dollar_signs(self):
        # 形如 pbkdf2:sha256:iter$salt$hash
        hashed = cfg.hash_password("p")
        # split("$", 2) -> 3 parts
        assert len(hashed.split("$")) == 3

    def test_legacy_single_round_sha256_hash(self):
        """旧格式 salt$hash 仍能验证。"""
        salt = "cafebabe"
        pwd = "legacypass"
        h = hashlib.sha256(f"{salt}{pwd}".encode()).hexdigest()
        stored = f"{salt}${h}"
        assert cfg.verify_password("legacypass", stored)
        assert cfg.verify_password("wrong", stored) is False

    def test_pbkdf2_with_custom_iteration_count(self):
        """支持自定义迭代次数（向前兼容更低成本的旧 hash）。"""
        salt = "deadbeef" * 8  # 64 hex chars = 32 bytes
        pwd = "x"
        iterations = 50_000
        h = hashlib.pbkdf2_hmac(
            "sha256", pwd.encode(), bytes.fromhex(salt), iterations
        ).hex()
        stored = f"pbkdf2:sha256:{iterations}${salt}${h}"
        assert cfg.verify_password("x", stored)

    def test_pbkdf2_invalid_iteration_count_falls_back_to_default(self):
        """迭代数解析失败时回退到 200_000。"""
        salt = "feed" * 16
        pwd = "y"
        # 用默认 200_000 计算的 hash，但前缀写非法
        h = hashlib.pbkdf2_hmac("sha256", pwd.encode(), bytes.fromhex(salt), 200_000).hex()
        stored = f"pbkdf2:sha256:not-a-number${salt}${h}"
        assert cfg.verify_password("y", stored)

    def test_hash_password_uses_unique_random_salts(self):
        """各次 hash_password 调用使用独立随机 salt。"""
        salts = set()
        for _ in range(5):
            hashed = cfg.hash_password("same")
            _, salt, _ = hashed.split("$", 2)
            salts.add(salt)
        # 5 次几乎必然产生 5 个不同 salt
        assert len(salts) == 5


class TestGetDbPath:
    def test_default_path(self):
        with patch.dict(os.environ, {}, clear=True):
            path = cfg.get_db_path()
            assert path.name == "health.db"

    def test_env_override(self):
        with patch.dict(os.environ, {"SUPERHEALTH_DB": "/tmp/custom.db"}):
            path = cfg.get_db_path()
            assert str(path) == "/tmp/custom.db"

    def test_env_empty_string_falls_back_to_default(self):
        # 空字符串视作未设置（Python `or` 短路）
        with patch.dict(os.environ, {"SUPERHEALTH_DB": ""}):
            path = cfg.get_db_path()
            assert path.name == "health.db"

    def test_env_returns_path_object(self):
        with patch.dict(os.environ, {"SUPERHEALTH_DB": "/var/tmp/x.db"}):
            path = cfg.get_db_path()
            assert isinstance(path, Path)


class TestAppConfigDefaults:
    """AppConfig() 应返回各 dataclass 的默认值，且各字段独立（field default_factory）。"""

    def test_default_app_config_isolation(self):
        """两个独立 AppConfig 实例的 garmin 应是不同对象（验证 default_factory）。"""
        c1 = cfg.AppConfig()
        c2 = cfg.AppConfig()
        c1.garmin.email = "user1"
        assert c2.garmin.email == ""  # 未受 c1 修改影响

    def test_default_app_config_no_credentials(self):
        c = cfg.AppConfig()
        assert c.garmin.is_complete() is False
        assert c.wechat.is_complete() is False
        assert c.vitals.is_complete() is False
        assert c.claude.is_complete() is False
        assert c.baichuan.is_complete() is False
        assert c.weather.is_complete() is False
        assert c.outlook.is_complete() is False

    def test_default_advisor_mode(self):
        assert cfg.AppConfig().advisor.mode == "claude_only"

    def test_default_dashboard_empty(self):
        d = cfg.AppConfig().dashboard
        assert d.password == ""
        assert d.session_token == ""
        assert d.saved_password == ""

