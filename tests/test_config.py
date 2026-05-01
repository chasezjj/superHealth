"""测试配置管理模块。"""
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
