"""测试配置保存函数。"""
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from superhealth import config as cfg


class TestSaveGarmin:
    def test_save_garmin_creates_config(self, tmp_path):
        config_path = tmp_path / "config.toml"
        cfg.save_garmin("test@example.com", "secret123", config_path=config_path)
        assert config_path.exists()
        content = config_path.read_text()
        assert "test@example.com" in content
        assert "secret123" in content
        # 权限应为 0o600
        assert config_path.stat().st_mode & 0o777 == 0o600

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


class TestSaveConfig:
    def test_save_config_roundtrip(self, tmp_path):
        config_path = tmp_path / "config.toml"
        app_cfg = cfg.AppConfig(
            garmin=cfg.GarminConfig(email="g@test.com", password="gp"),
            wechat=cfg.WechatConfig(account_id="aid", channel="ch", target="tgt"),
            vitals=cfg.VitalsConfig(api_token="tok", host="127.0.0.1", port=8080),
            claude=cfg.ClaudeConfig(api_key="ck", model="m", max_tokens=512, base_url=""),
            baichuan=cfg.BaichuanConfig(api_key="bk", model="bm", max_tokens=256),
            advisor=cfg.AdvisorConfig(mode="both"),
            weather=cfg.WeatherConfig(api_key="wk", city="北京"),
            dashboard=cfg.DashboardConfig(password="dp", session_token="st"),
            outlook=cfg.OutlookConfig(username="ou", email="oe", password="op"),
        )
        cfg.save_config(app_cfg, config_path=config_path)

        loaded = cfg.load(config_path)
        assert loaded.garmin.email == "g@test.com"
        assert loaded.wechat.account_id == "aid"
        assert loaded.vitals.port == 8080
        assert loaded.claude.max_tokens == 512
        assert loaded.advisor.mode == "both"
        assert loaded.weather.city == "北京"
        assert loaded.dashboard.password == "dp"
        assert loaded.outlook.username == "ou"

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
        config_path.write_text('[dashboard]\nsession_token = "old"\nsaved_password = "old_pw"\n')
        app_cfg = cfg.AppConfig(
            dashboard=cfg.DashboardConfig(password="", session_token="", saved_password="")
        )
        cfg.save_config(app_cfg, config_path=config_path)
        content = config_path.read_text()
        assert "session_token" not in content
        assert "saved_password" not in content


class TestSaveDashboardSessionToken:
    def test_saves_token(self, tmp_path):
        config_path = tmp_path / "config.toml"
        cfg.save_dashboard_session_token("my_token_123", config_path=config_path)
        content = config_path.read_text()
        assert "my_token_123" in content


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
