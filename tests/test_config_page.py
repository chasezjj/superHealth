"""测试 dashboard/views/config_page.py 中的纯函数与进程管理逻辑。

页面渲染（render）依赖 streamlit runtime，不在此处测试；这里聚焦于：
- _derive_dashboard_password：密码 hash 推导
- _is_healthy_job / _parse_cron_line / _sanitize_cron_command：crontab 工具
- _get_crontab：读取 crontab 的容错处理
- _vitals_pid / _start_vitals_receiver / _stop_vitals_receiver：进程管理
"""
from __future__ import annotations

import signal
from unittest.mock import MagicMock, patch

import pytest

from superhealth import config as cfg
from superhealth.dashboard.views import config_page as page

# ---------------------------------------------------------------------------
# _derive_dashboard_password
# ---------------------------------------------------------------------------


class TestDeriveDashboardPassword:
    def test_empty_input_clears(self):
        assert page._derive_dashboard_password("", "any-stored") == ""

    def test_empty_input_clears_even_when_no_stored(self):
        assert page._derive_dashboard_password("", "") == ""

    def test_new_password_is_hashed(self):
        result = page._derive_dashboard_password("newpwd", "")
        assert result.startswith("pbkdf2:sha256:")
        assert cfg.verify_password("newpwd", result)

    def test_input_matching_stored_hash_keeps_stored(self):
        """页面再次提交时输入框可能拿回原密码，避免被双 hash。"""
        stored = cfg.hash_password("secret")
        # 用户输入原密码
        result = page._derive_dashboard_password("secret", stored)
        assert result == stored  # 没有重新 hash

    def test_input_matching_legacy_plaintext_keeps_stored(self):
        """legacy plaintext 场景：stored 是明文，输入相同明文应保留 stored。"""
        stored = "legacy_plain"
        result = page._derive_dashboard_password("legacy_plain", stored)
        assert result == stored

    def test_different_password_produces_new_hash(self):
        stored = cfg.hash_password("oldpass")
        result = page._derive_dashboard_password("newpass", stored)
        assert result != stored
        assert cfg.verify_password("newpass", result)
        assert not cfg.verify_password("oldpass", result)

    def test_new_password_when_stored_empty(self):
        result = page._derive_dashboard_password("fresh", "")
        assert result.startswith("pbkdf2:sha256:")
        assert cfg.verify_password("fresh", result)


# ---------------------------------------------------------------------------
# _is_healthy_job
# ---------------------------------------------------------------------------


class TestIsHealthyJob:
    def test_empty_line(self):
        assert page._is_healthy_job("") is False

    def test_whitespace_only(self):
        assert page._is_healthy_job("   \t  ") is False

    def test_comment_line(self):
        assert page._is_healthy_job("# 0 7 * * * superhealth.daily_pipeline") is False

    def test_comment_with_leading_whitespace(self):
        assert page._is_healthy_job("   # superhealth job") is False

    def test_line_referencing_superhealth(self):
        line = "0 7 * * * PYTHONPATH=src python -m superhealth.daily_pipeline"
        assert page._is_healthy_job(line) is True

    def test_line_without_superhealth(self):
        line = "0 7 * * * /usr/bin/uptime"
        assert page._is_healthy_job(line) is False

    def test_partial_path_match(self):
        # 任意位置出现 superhealth 字符串均视为 health 任务
        line = "0 7 * * * /home/user/superhealth-tools/run.sh"
        assert page._is_healthy_job(line) is True


# ---------------------------------------------------------------------------
# _parse_cron_line
# ---------------------------------------------------------------------------


class TestParseCronLine:
    def test_standard_six_fields(self):
        result = page._parse_cron_line("0 7 * * * cmd")
        assert result == ("0", "7", "*", "*", "*", "cmd")

    def test_command_with_multiple_words(self):
        line = "*/5 * * * * python -m superhealth.daily_pipeline --all"
        m, h, dom, mon, dow, cmd = page._parse_cron_line(line)
        assert m == "*/5"
        assert h == "*"
        assert dom == "*"
        assert mon == "*"
        assert dow == "*"
        assert cmd == "python -m superhealth.daily_pipeline --all"

    def test_command_with_env_prefix(self):
        line = "0 7 * * * PYTHONPATH=src python -m superhealth.daily_pipeline"
        _, _, _, _, _, cmd = page._parse_cron_line(line)
        assert cmd == "PYTHONPATH=src python -m superhealth.daily_pipeline"

    def test_returns_none_for_short_line(self):
        assert page._parse_cron_line("0 7 * *") is None
        assert page._parse_cron_line("") is None
        assert page._parse_cron_line("just-a-word") is None

    def test_returns_none_for_five_fields_only(self):
        # 没有命令字段
        assert page._parse_cron_line("0 7 * * *") is None

    def test_strips_leading_trailing_whitespace(self):
        result = page._parse_cron_line("  0 7 * * * cmd  ")
        assert result == ("0", "7", "*", "*", "*", "cmd")

    def test_collapses_internal_whitespace(self):
        # tab / 多个空格也应正确切分
        result = page._parse_cron_line("0\t7  *\t* * cmd  arg")
        assert result is not None
        m, h, dom, mon, dow, cmd = result
        assert (m, h, dom, mon, dow) == ("0", "7", "*", "*", "*")
        assert cmd == "cmd arg"

    def test_complex_cron_expression(self):
        result = page._parse_cron_line("0,30 8-18 * 1-12 1-5 cmd")
        assert result == ("0,30", "8-18", "*", "1-12", "1-5", "cmd")


# ---------------------------------------------------------------------------
# _sanitize_cron_command
# ---------------------------------------------------------------------------


class TestSanitizeCronCommand:
    def test_safe_command_passes(self):
        cmd = "PYTHONPATH=src python -m superhealth.daily_pipeline"
        assert page._sanitize_cron_command(cmd) == cmd

    def test_command_with_path_passes(self):
        cmd = "/usr/local/bin/python3 /home/user/script.py --flag"
        assert page._sanitize_cron_command(cmd) == cmd

    @pytest.mark.parametrize(
        "ch",
        [";", "|", "&", "<", ">", "`", "$", "\\", "(", ")", "{", "}", "[", "]"],
    )
    def test_dangerous_characters_rejected(self, ch):
        cmd = f"python script.py {ch} other"
        assert page._sanitize_cron_command(cmd) is None

    def test_semicolon_chain_rejected(self):
        assert page._sanitize_cron_command("ls ; rm -rf /") is None

    def test_pipe_rejected(self):
        assert page._sanitize_cron_command("ls | grep secret") is None

    def test_redirect_rejected(self):
        assert page._sanitize_cron_command("cmd > /tmp/out.log") is None

    def test_backtick_rejected(self):
        assert page._sanitize_cron_command("echo `whoami`") is None

    def test_dollar_substitution_rejected(self):
        assert page._sanitize_cron_command("echo $HOME") is None

    def test_empty_command_passes(self):
        # 空字符串不含危险字符
        assert page._sanitize_cron_command("") == ""

    def test_command_with_dashes_and_dots_passes(self):
        cmd = "python -m my.pkg.module --a-b --no-color"
        assert page._sanitize_cron_command(cmd) == cmd


# ---------------------------------------------------------------------------
# _get_crontab
# ---------------------------------------------------------------------------


class TestGetCrontab:
    def test_returns_stdout_when_command_succeeds(self):
        fake = MagicMock(returncode=0, stdout="0 7 * * * superhealth\n")
        with patch.object(page.subprocess, "run", return_value=fake):
            assert page._get_crontab() == "0 7 * * * superhealth\n"

    def test_returns_empty_when_command_fails(self):
        # crontab 未配置时 returncode != 0（如 "no crontab for user"）
        fake = MagicMock(returncode=1, stdout="")
        with patch.object(page.subprocess, "run", return_value=fake):
            assert page._get_crontab() == ""

    def test_returns_empty_even_with_stdout_when_failed(self):
        fake = MagicMock(returncode=1, stdout="some error noise")
        with patch.object(page.subprocess, "run", return_value=fake):
            assert page._get_crontab() == ""


# ---------------------------------------------------------------------------
# _vitals_pid / _start_vitals_receiver / _stop_vitals_receiver
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_pid_file(tmp_path, monkeypatch):
    """将 _VITALS_PID_FILE 指向 tmp 目录，避免测试影响真实环境。"""
    fake = tmp_path / "vitals_receiver.pid"
    monkeypatch.setattr(page, "_VITALS_PID_FILE", fake)
    return fake


class TestVitalsPid:
    def test_returns_none_when_pid_file_missing(self, isolated_pid_file):
        assert page._vitals_pid() is None

    def test_returns_none_when_pid_file_invalid(self, isolated_pid_file):
        isolated_pid_file.write_text("not-an-int")
        assert page._vitals_pid() is None
        # 无效文件应被清理
        assert not isolated_pid_file.exists()

    def test_returns_none_when_process_not_running(self, isolated_pid_file):
        isolated_pid_file.write_text("99999")  # 极不可能存在的 PID
        with patch.object(page.os, "kill", side_effect=ProcessLookupError):
            assert page._vitals_pid() is None
        assert not isolated_pid_file.exists()

    def test_returns_none_when_permission_denied(self, isolated_pid_file):
        # 进程存在但权限拒绝（其他用户的进程）
        isolated_pid_file.write_text("1")  # init
        with patch.object(page.os, "kill", side_effect=PermissionError):
            assert page._vitals_pid() is None
        assert not isolated_pid_file.exists()

    def test_returns_pid_when_alive(self, isolated_pid_file):
        isolated_pid_file.write_text("12345")
        with patch.object(page.os, "kill", return_value=None) as mock_kill:
            result = page._vitals_pid()
        assert result == 12345
        # 应使用 signal 0 仅检测进程
        mock_kill.assert_called_once_with(12345, 0)
        # 健康的 PID 文件不应被清理
        assert isolated_pid_file.exists()

    def test_pid_with_surrounding_whitespace(self, isolated_pid_file):
        # PID 文件可能有换行
        isolated_pid_file.write_text("  4242\n")
        with patch.object(page.os, "kill", return_value=None):
            assert page._vitals_pid() == 4242


class TestStartVitalsReceiver:
    def test_refuses_to_start_when_already_running(self, isolated_pid_file):
        isolated_pid_file.write_text("12345")
        with patch.object(page.os, "kill", return_value=None):
            ok, msg = page._start_vitals_receiver()
        assert ok is False
        assert "运行" in msg

    def test_starts_and_writes_pid(self, isolated_pid_file):
        fake_proc = MagicMock(pid=7890)
        with patch.object(page.subprocess, "Popen", return_value=fake_proc) as popen:
            ok, msg = page._start_vitals_receiver()
        assert ok is True
        assert "7890" in msg
        assert isolated_pid_file.read_text() == "7890"
        # 验证启动命令使用了正确的模块入口
        args, kwargs = popen.call_args
        cmd = args[0]
        assert cmd[-1] == "superhealth.api.vitals_receiver"
        assert cmd[-2] == "-m"
        # 后台分离 session
        assert kwargs.get("start_new_session") is True

    def test_returns_failure_when_popen_raises(self, isolated_pid_file):
        with patch.object(page.subprocess, "Popen", side_effect=OSError("boom")):
            ok, msg = page._start_vitals_receiver()
        assert ok is False
        assert "boom" in msg
        # 失败时不应留下 PID 文件
        assert not isolated_pid_file.exists()

    def test_creates_parent_dir_for_pid_file(self, tmp_path, monkeypatch):
        nested = tmp_path / "deep" / "nested" / "dir" / "vitals.pid"
        monkeypatch.setattr(page, "_VITALS_PID_FILE", nested)
        fake_proc = MagicMock(pid=123)
        with patch.object(page.subprocess, "Popen", return_value=fake_proc):
            ok, _ = page._start_vitals_receiver()
        assert ok is True
        assert nested.exists()
        assert nested.parent.is_dir()


class TestStopVitalsReceiver:
    def test_returns_failure_when_not_running(self, isolated_pid_file):
        ok, msg = page._stop_vitals_receiver()
        assert ok is False
        assert "未运行" in msg

    def test_sends_sigterm_and_clears_pid_file(self, isolated_pid_file):
        isolated_pid_file.write_text("4321")
        kill_calls: list[tuple[int, int]] = []

        def fake_kill(pid, sig):
            kill_calls.append((pid, sig))
            # 第一次（signal 0）用于探测，第二次为 SIGTERM
            return None

        with patch.object(page.os, "kill", side_effect=fake_kill):
            ok, msg = page._stop_vitals_receiver()

        assert ok is True
        assert "4321" in msg
        # 至少应触发一次 SIGTERM
        assert (4321, signal.SIGTERM) in kill_calls
        # PID 文件清理
        assert not isolated_pid_file.exists()

    def test_returns_failure_when_kill_raises(self, isolated_pid_file):
        isolated_pid_file.write_text("4321")

        call_count = {"n": 0}

        def fake_kill(pid, sig):
            call_count["n"] += 1
            if sig == 0:
                return None  # 探测调用通过
            raise PermissionError("denied")

        with patch.object(page.os, "kill", side_effect=fake_kill):
            ok, msg = page._stop_vitals_receiver()
        assert ok is False
        assert "denied" in msg
