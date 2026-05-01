"""测试配置保存函数。"""
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from superhealth import config as cfg


def _file_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


class TestSaveGarmin:
    def test_save_garmin_creates_config(self, tmp_path):
        config_path = tmp_path / "config.toml"
        cfg.save_garmin("test@example.com", "secret123", config_path=config_path)
        assert config_path.exists()
        content = config_path.read_text()
        assert "test@example.com" in content
        assert "secret123" in content
        # 权限应为 0o600
        assert _file_mode(config_path) == 0o600

    def test_save_garmin_preserves_other_sections(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text('[wechat]\naccount_id = "old-bot"\n')
        cfg.save_garmin("new@example.com", "newpass", config_path=config_path)
        content = config_path.read_text()
        assert "old-bot" in content
        assert "new@example.com" in content

    def test_save_garmin_missing_tomli_w(self, tmp_path):
        config_path = tmp_path / "config.toml"
        with patch.dict("sys.modules", {"tomli_w": None}):
            with pytest.raises(ImportError):
                cfg.save_garmin("a", "b", config_path=config_path)

    def test_save_garmin_creates_parent_directory(self, tmp_path):
        config_path = tmp_path / "nested" / "deep" / "config.toml"
        cfg.save_garmin("a", "b", config_path=config_path)
        assert config_path.exists()

    def test_save_garmin_overwrite_keeps_permissions(self, tmp_path):
        config_path = tmp_path / "config.toml"
        cfg.save_garmin("a", "b", config_path=config_path)
        cfg.save_garmin("c", "d", config_path=config_path)
        # 即便覆盖也应保持 0o600
        assert _file_mode(config_path) == 0o600
        loaded = cfg.load(config_path)
        assert loaded.garmin.email == "c"
        assert loaded.garmin.password == "d"

    def test_save_garmin_loads_back_via_load(self, tmp_path):
        config_path = tmp_path / "config.toml"
        cfg.save_garmin("u@v.com", "pwd!@#$", config_path=config_path)
        with patch.dict(os.environ, {}, clear=True):
            loaded = cfg.load(config_path)
        assert loaded.garmin.email == "u@v.com"
        assert loaded.garmin.password == "pwd!@#$"


class TestSaveConfig:
    def _make_full(self) -> cfg.AppConfig:
        return cfg.AppConfig(
            garmin=cfg.GarminConfig(email="g@test.com", password="gp"),
            wechat=cfg.WechatConfig(account_id="aid", channel="ch", target="tgt"),
            vitals=cfg.VitalsConfig(api_token="tok", host="127.0.0.1", port=8080),
            claude=cfg.ClaudeConfig(api_key="ck", model="m", max_tokens=512, base_url=""),
            baichuan=cfg.BaichuanConfig(api_key="bk", model="bm", max_tokens=256),
            advisor=cfg.AdvisorConfig(mode="both"),
            weather=cfg.WeatherConfig(
                api_key="wk",
                city="北京",
                location_id="101010100",
                api_host="myhost.example",
                latitude=40.0,
                longitude=116.5,
            ),
            dashboard=cfg.DashboardConfig(password="dp", session_token="st"),
            outlook=cfg.OutlookConfig(
                username="ou", email="oe", password="op", timezone="Asia/Tokyo"
            ),
        )

    def test_save_config_roundtrip(self, tmp_path):
        config_path = tmp_path / "config.toml"
        app_cfg = self._make_full()
        cfg.save_config(app_cfg, config_path=config_path)

        with patch.dict(os.environ, {}, clear=True):
            loaded = cfg.load(config_path)
        assert loaded.garmin.email == "g@test.com"
        assert loaded.wechat.account_id == "aid"
        assert loaded.vitals.port == 8080
        assert loaded.claude.max_tokens == 512
        assert loaded.advisor.mode == "both"
        assert loaded.weather.city == "北京"
        assert loaded.weather.api_host == "myhost.example"
        assert loaded.weather.latitude == pytest.approx(40.0)
        assert loaded.dashboard.password == "dp"
        assert loaded.outlook.username == "ou"
        assert loaded.outlook.timezone == "Asia/Tokyo"

    def test_save_config_preserves_dashboard_saved_password(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text('[dashboard]\nsaved_password = "old_pwd"\n')
        app_cfg = cfg.AppConfig(
            dashboard=cfg.DashboardConfig(password="new_pwd", saved_password="saved_pwd")
        )
        cfg.save_config(app_cfg, config_path=config_path)
        content = config_path.read_text()
        assert "saved_pwd" in content

    def test_save_config_removes_empty_dashboard_tokens(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            '[dashboard]\nsession_token = "old"\nsaved_password = "old_pw"\n'
        )
        app_cfg = cfg.AppConfig(
            dashboard=cfg.DashboardConfig(password="", session_token="", saved_password="")
        )
        cfg.save_config(app_cfg, config_path=config_path)
        content = config_path.read_text()
        assert "session_token" not in content
        assert "saved_password" not in content

    def test_save_config_writes_session_token_when_set(self, tmp_path):
        config_path = tmp_path / "config.toml"
        app_cfg = cfg.AppConfig(
            dashboard=cfg.DashboardConfig(password="p", session_token="abc123")
        )
        cfg.save_config(app_cfg, config_path=config_path)
        content = config_path.read_text()
        assert "abc123" in content

    def test_save_config_creates_new_file(self, tmp_path):
        """没有现有文件时仍应能新建并写入。"""
        config_path = tmp_path / "subdir" / "config.toml"
        app_cfg = cfg.AppConfig(garmin=cfg.GarminConfig(email="a", password="b"))
        cfg.save_config(app_cfg, config_path=config_path)
        assert config_path.exists()

    def test_save_config_sets_0o600_permissions(self, tmp_path):
        config_path = tmp_path / "config.toml"
        cfg.save_config(cfg.AppConfig(), config_path=config_path)
        assert _file_mode(config_path) == 0o600

    def test_save_config_overwrite_keeps_permissions(self, tmp_path):
        config_path = tmp_path / "config.toml"
        cfg.save_config(cfg.AppConfig(), config_path=config_path)
        # 用户偶然将权限改宽（模拟编辑器副作用）
        config_path.chmod(0o644)
        cfg.save_config(cfg.AppConfig(), config_path=config_path)
        assert _file_mode(config_path) == 0o600

    def test_save_config_preserves_unknown_top_level_sections(self, tmp_path):
        """save_config 不应丢弃用户手工添加的未知 section。"""
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            '[custom]\nfoo = "bar"\nlevel = 9\n[experimental]\nflag = true\n'
        )
        cfg.save_config(cfg.AppConfig(), config_path=config_path)
        content = config_path.read_text()
        assert "[custom]" in content
        assert 'foo = "bar"' in content
        assert "[experimental]" in content
        assert "flag = true" in content

    def test_save_config_overwrites_known_section_values(self, tmp_path):
        """已知 section（如 garmin）应被新值完全覆盖。"""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[garmin]\nemail = "old"\npassword = "oldpw"\n')
        new_cfg = cfg.AppConfig(garmin=cfg.GarminConfig(email="new", password="newpw"))
        cfg.save_config(new_cfg, config_path=config_path)
        content = config_path.read_text()
        assert "old" not in content
        assert "oldpw" not in content
        assert "new" in content
        assert "newpw" in content

    def test_save_config_missing_tomli_w(self, tmp_path):
        config_path = tmp_path / "config.toml"
        with patch.dict("sys.modules", {"tomli_w": None}):
            with pytest.raises(ImportError):
                cfg.save_config(cfg.AppConfig(), config_path=config_path)

    def test_save_config_writes_all_known_sections_keys(self, tmp_path):
        """检查 toml 中包含全部 9 个 section 头。"""
        config_path = tmp_path / "config.toml"
        cfg.save_config(self._make_full(), config_path=config_path)
        content = config_path.read_text()
        for section in [
            "[garmin]",
            "[wechat]",
            "[vitals]",
            "[claude]",
            "[baichuan]",
            "[advisor]",
            "[weather]",
            "[dashboard]",
            "[outlook]",
        ]:
            assert section in content, f"missing section: {section}"

    def test_save_config_roundtrip_preserves_advisor_modes(self, tmp_path):
        config_path = tmp_path / "config.toml"
        for mode in ("claude_only", "baichuan_only", "both"):
            app_cfg = cfg.AppConfig(advisor=cfg.AdvisorConfig(mode=mode))
            cfg.save_config(app_cfg, config_path=config_path)
            with patch.dict(os.environ, {}, clear=True):
                loaded = cfg.load(config_path)
            assert loaded.advisor.mode == mode


class TestSaveDashboardSessionToken:
    def test_saves_token(self, tmp_path):
        config_path = tmp_path / "config.toml"
        cfg.save_dashboard_session_token("my_token_123", config_path=config_path)
        content = config_path.read_text()
        assert "my_token_123" in content

    def test_preserves_other_sections(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            '[garmin]\nemail = "u@x.com"\npassword = "pw"\n'
            '[wechat]\naccount_id = "abc"\n'
        )
        cfg.save_dashboard_session_token("tok", config_path=config_path)
        loaded = cfg.load(config_path)
        assert loaded.garmin.email == "u@x.com"
        assert loaded.wechat.account_id == "abc"
        assert loaded.dashboard.session_token == "tok"

    def test_overwrites_existing_token(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text('[dashboard]\nsession_token = "old"\n')
        cfg.save_dashboard_session_token("new", config_path=config_path)
        content = config_path.read_text()
        assert "new" in content
        assert "old" not in content

    def test_creates_parent_directory(self, tmp_path):
        config_path = tmp_path / "nested" / "config.toml"
        cfg.save_dashboard_session_token("t", config_path=config_path)
        assert config_path.exists()

    def test_sets_0o600_permissions(self, tmp_path):
        config_path = tmp_path / "config.toml"
        cfg.save_dashboard_session_token("t", config_path=config_path)
        assert _file_mode(config_path) == 0o600

    def test_missing_tomli_w(self, tmp_path):
        config_path = tmp_path / "config.toml"
        with patch.dict("sys.modules", {"tomli_w": None}):
            with pytest.raises(ImportError):
                cfg.save_dashboard_session_token("t", config_path=config_path)


class TestSaveDashboardPassword:
    def test_saves_password(self, tmp_path):
        config_path = tmp_path / "config.toml"
        cfg.save_dashboard_password("my_password", config_path=config_path)
        content = config_path.read_text()
        assert "saved_password" in content
        assert "pbkdf2" in content  # hashed

    def test_removes_empty_password(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text('[dashboard]\nsaved_password = "old"\n')
        cfg.save_dashboard_password("", config_path=config_path)
        content = config_path.read_text()
        assert "saved_password" not in content

    def test_saved_password_can_be_verified(self, tmp_path):
        config_path = tmp_path / "config.toml"
        cfg.save_dashboard_password("hunter2", config_path=config_path)
        loaded = cfg.load(config_path)
        assert loaded.dashboard.saved_password.startswith("pbkdf2:sha256:")
        assert cfg.verify_password("hunter2", loaded.dashboard.saved_password)
        assert not cfg.verify_password("wrong", loaded.dashboard.saved_password)

    def test_overwrite_creates_new_hash(self, tmp_path):
        config_path = tmp_path / "config.toml"
        cfg.save_dashboard_password("first", config_path=config_path)
        first_hash = cfg.load(config_path).dashboard.saved_password
        cfg.save_dashboard_password("second", config_path=config_path)
        second_hash = cfg.load(config_path).dashboard.saved_password
        assert first_hash != second_hash
        assert cfg.verify_password("second", second_hash)
        assert not cfg.verify_password("first", second_hash)

    def test_preserves_other_sections(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text('[garmin]\nemail = "g"\npassword = "gp"\n')
        cfg.save_dashboard_password("p", config_path=config_path)
        loaded = cfg.load(config_path)
        assert loaded.garmin.email == "g"
        assert loaded.garmin.password == "gp"

    def test_clearing_preserves_other_dashboard_keys(self, tmp_path):
        """清空 saved_password 不应影响 password / session_token。"""
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            '[dashboard]\npassword = "p"\nsession_token = "tok"\nsaved_password = "old"\n'
        )
        cfg.save_dashboard_password("", config_path=config_path)
        loaded = cfg.load(config_path)
        assert loaded.dashboard.password == "p"
        assert loaded.dashboard.session_token == "tok"
        assert loaded.dashboard.saved_password == ""

    def test_creates_parent_directory(self, tmp_path):
        config_path = tmp_path / "nested" / "config.toml"
        cfg.save_dashboard_password("p", config_path=config_path)
        assert config_path.exists()

    def test_sets_0o600_permissions(self, tmp_path):
        config_path = tmp_path / "config.toml"
        cfg.save_dashboard_password("p", config_path=config_path)
        assert _file_mode(config_path) == 0o600

    def test_missing_tomli_w(self, tmp_path):
        config_path = tmp_path / "config.toml"
        with patch.dict("sys.modules", {"tomli_w": None}):
            with pytest.raises(ImportError):
                cfg.save_dashboard_password("p", config_path=config_path)
