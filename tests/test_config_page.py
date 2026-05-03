"""测试 dashboard/views/config_page.py 中的纯函数与进程管理逻辑。

页面渲染（render）依赖 streamlit runtime，不在此处测试；这里聚焦于：
- _derive_dashboard_password：密码 hash 推导
- _is_healthy_job / _is_daily_pipeline_job / _parse_cron_line / _sanitize_cron_command：crontab 工具
- _get_crontab / _ensure_path_header / _save_crontab：crontab 读写
- _cron_log_path / _split_cmd_redirect / _attach_log_redirect / _line_with_log_redirect / _read_log_tail：日志重定向辅助
- _vitals_pid / _start_vitals_receiver / _stop_vitals_receiver：进程管理
"""
from __future__ import annotations

import signal
from pathlib import Path
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
# _is_daily_pipeline_job
# ---------------------------------------------------------------------------


class TestIsDailyPipelineJob:
    def test_matches_daily_pipeline_job(self):
        line = "0 7 * * * python3 -m superhealth.daily_pipeline"
        assert page._is_daily_pipeline_job(line) is True

    def test_matches_with_log_redirect(self):
        line = (
            "0 7 * * * python3 -m superhealth.daily_pipeline "
            ">> /home/u/.superhealth/logs/cron/abc.log 2>&1"
        )
        assert page._is_daily_pipeline_job(line) is True

    def test_other_superhealth_job_not_matched(self):
        # 其他 superhealth 任务（非 daily_pipeline）不应识别为核心任务
        line = "*/30 * * * * python3 -m superhealth.collectors.weather_collector"
        assert page._is_daily_pipeline_job(line) is False

    def test_empty_line(self):
        assert page._is_daily_pipeline_job("") is False

    def test_comment_line_with_keyword_not_matched(self):
        # 即使注释里写了 daily_pipeline 也不算
        line = "# 0 7 * * * python -m superhealth.daily_pipeline"
        assert page._is_daily_pipeline_job(line) is False

    def test_line_without_superhealth_keyword(self):
        # 即使含有 daily_pipeline 字样，但未出现 superhealth 仍不算
        line = "0 7 * * * /usr/local/bin/daily_pipeline.sh"
        assert page._is_daily_pipeline_job(line) is False

    def test_command_with_args(self):
        line = "0 7 * * * python -m superhealth.daily_pipeline --no-wechat"
        assert page._is_daily_pipeline_job(line) is True


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
# _ensure_path_header / _save_crontab
# ---------------------------------------------------------------------------


class TestEnsurePathHeader:
    def test_empty_content_gets_path(self):
        result = page._ensure_path_header("")
        assert result == page._CRON_PATH_LINE + "\n"

    def test_content_without_path_gets_prefixed(self):
        original = "0 7 * * * /usr/bin/uptime\n"
        result = page._ensure_path_header(original)
        assert result.startswith(page._CRON_PATH_LINE + "\n")
        assert "0 7 * * * /usr/bin/uptime" in result

    def test_existing_path_preserved(self):
        original = "PATH=/custom/bin:/usr/bin\n0 7 * * * cmd\n"
        result = page._ensure_path_header(original)
        # 不重复注入
        assert result == original
        assert result.count("PATH=") == 1

    def test_path_after_comments_still_recognized(self):
        original = "# header comment\n\nPATH=/x:/y\n0 7 * * * cmd\n"
        result = page._ensure_path_header(original)
        assert result == original
        assert page._CRON_PATH_LINE not in result

    def test_only_comments_gets_path_injected(self):
        # 全是注释也应注入 PATH（注释不算 PATH 设置）
        original = "# only a comment\n"
        result = page._ensure_path_header(original)
        assert result.startswith(page._CRON_PATH_LINE + "\n")
        assert "# only a comment" in result

    def test_ensures_trailing_newline(self):
        # 原内容没有结尾换行也应补全
        result = page._ensure_path_header("0 7 * * * cmd")
        assert result.endswith("\n")


class TestSaveCrontab:
    def test_injects_path_header_when_missing(self):
        with patch.object(page.subprocess, "run") as mock_run:
            page._save_crontab("0 7 * * * cmd\n")
        args, kwargs = mock_run.call_args
        assert args[0] == ["crontab", "-"]
        sent = kwargs["input"]
        assert sent.startswith(page._CRON_PATH_LINE + "\n")
        assert "0 7 * * * cmd" in sent

    def test_preserves_existing_path_header(self):
        original = "PATH=/custom\n0 7 * * * cmd\n"
        with patch.object(page.subprocess, "run") as mock_run:
            page._save_crontab(original)
        sent = mock_run.call_args.kwargs["input"]
        assert sent == original  # 未被重复修改


# ---------------------------------------------------------------------------
# 日志路径与重定向辅助函数
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_cron_log_dir(tmp_path, monkeypatch):
    """将 _CRON_LOG_DIR 指向 tmp，避免污染 ~/.superhealth/logs/cron。"""
    fake = tmp_path / "cron-logs"
    monkeypatch.setattr(page, "_CRON_LOG_DIR", fake)
    return fake


class TestCronLogPath:
    def test_deterministic_for_same_command(self, isolated_cron_log_dir):
        cmd = "python3 -m superhealth.daily_pipeline"
        assert page._cron_log_path(cmd) == page._cron_log_path(cmd)

    def test_different_commands_get_distinct_paths(self, isolated_cron_log_dir):
        a = page._cron_log_path("python3 -m superhealth.daily_pipeline")
        b = page._cron_log_path("python3 -m superhealth.collectors.weather_collector")
        assert a != b

    def test_path_lives_under_cron_log_dir(self, isolated_cron_log_dir):
        result = page._cron_log_path("anything")
        # 解析符号链接以避免 macOS /var → /private/var 等不一致
        assert result.parent.resolve() == isolated_cron_log_dir.resolve()

    def test_filename_is_12_hex_plus_log(self, isolated_cron_log_dir):
        result = page._cron_log_path("anything")
        stem, ext = result.stem, result.suffix
        assert ext == ".log"
        assert len(stem) == 12
        # SHA1 的 hexdigest 仅含 0-9a-f
        assert all(c in "0123456789abcdef" for c in stem)

    def test_handles_unicode_command(self, isolated_cron_log_dir):
        # 中文命令也应能稳定生成路径，不抛错
        cmd = "python3 -m superhealth.daily_pipeline  # 每日运行"
        result = page._cron_log_path(cmd)
        assert result.suffix == ".log"
        # 同一中文输入 → 同一路径
        assert result == page._cron_log_path(cmd)


class TestSplitCmdRedirect:
    def test_command_without_redirect(self):
        cmd, log = page._split_cmd_redirect("python3 -m superhealth.daily_pipeline")
        assert cmd == "python3 -m superhealth.daily_pipeline"
        assert log is None

    def test_command_with_redirect_split(self):
        cmd, log = page._split_cmd_redirect(
            "python3 -m superhealth.daily_pipeline >> /tmp/abc.log 2>&1"
        )
        assert cmd == "python3 -m superhealth.daily_pipeline"
        assert log == Path("/tmp/abc.log")

    def test_redirect_with_extra_internal_whitespace(self):
        # `>>` 与路径之间允许多空格，路径与 `2>&1` 之间也允许多空格
        cmd, log = page._split_cmd_redirect("cmd  >>   /tmp/x.log    2>&1")
        assert cmd == "cmd"
        assert log == Path("/tmp/x.log")

    def test_only_redirect_no_stderr_suffix_unchanged(self):
        # 没有 `2>&1` 后缀不应触发剥离
        original = "cmd >> /tmp/x.log"
        cmd, log = page._split_cmd_redirect(original)
        assert cmd == original
        assert log is None

    def test_only_stderr_redirect_no_match(self):
        original = "cmd 2>&1"
        cmd, log = page._split_cmd_redirect(original)
        assert cmd == original
        assert log is None

    def test_trailing_whitespace_tolerated(self):
        # 尾部空白也应剥离
        cmd, log = page._split_cmd_redirect("cmd >> /tmp/y.log 2>&1   ")
        assert cmd == "cmd"
        assert log == Path("/tmp/y.log")

    def test_redirect_in_middle_not_stripped(self):
        # 中间出现 `>>` 但末尾不是合法重定向 → 不剥离
        original = "cmd >> middle >> /tmp/x.log notend"
        cmd, log = page._split_cmd_redirect(original)
        assert cmd == original
        assert log is None


class TestAttachLogRedirect:
    def test_appends_redirect_suffix(self, isolated_cron_log_dir):
        full, path = page._attach_log_redirect("python3 -m superhealth.daily_pipeline")
        assert full.startswith("python3 -m superhealth.daily_pipeline")
        assert full.endswith(" 2>&1")
        assert " >> " in full
        assert str(path) in full

    def test_returned_path_matches_cron_log_path(self, isolated_cron_log_dir):
        cmd = "python3 -m superhealth.daily_pipeline"
        _full, path = page._attach_log_redirect(cmd)
        assert path == page._cron_log_path(cmd)

    def test_creates_log_parent_dir(self, isolated_cron_log_dir):
        assert not isolated_cron_log_dir.exists()
        page._attach_log_redirect("cmd")
        assert isolated_cron_log_dir.is_dir()

    def test_roundtrip_with_split(self, isolated_cron_log_dir):
        clean = "python3 -m superhealth.daily_pipeline"
        full, attached_path = page._attach_log_redirect(clean)
        recovered, log_path = page._split_cmd_redirect(full)
        assert recovered == clean
        assert log_path == attached_path

    def test_idempotent_path_for_same_command(self, isolated_cron_log_dir):
        # 多次调用同一命令应得到同一日志路径
        _, p1 = page._attach_log_redirect("cmd")
        _, p2 = page._attach_log_redirect("cmd")
        assert p1 == p2


class TestLineWithLogRedirect:
    def test_full_line_gets_redirect_appended(self, isolated_cron_log_dir):
        line = "0 7 * * * python3 -m superhealth.daily_pipeline"
        result = page._line_with_log_redirect(line)
        assert result.startswith("0 7 * * * python3 -m superhealth.daily_pipeline >> ")
        assert result.endswith(" 2>&1")

    def test_short_line_unchanged(self, isolated_cron_log_dir):
        # 字段数不足 6 不应处理，返回原样
        assert page._line_with_log_redirect("0 7 * * *") == "0 7 * * *"
        assert page._line_with_log_redirect("") == ""
        assert page._line_with_log_redirect("just a few words") == "just a few words"

    def test_preserves_complex_time_fields(self, isolated_cron_log_dir):
        line = "*/5 0,30 1 1-12 1-5 cmd arg1 arg2"
        result = page._line_with_log_redirect(line)
        # 时间前缀完整保留
        assert result.startswith("*/5 0,30 1 1-12 1-5 cmd arg1 arg2 >> ")

    def test_command_part_uses_correct_log_path(self, isolated_cron_log_dir):
        line = "0 7 * * * python3 -m superhealth.daily_pipeline"
        result = page._line_with_log_redirect(line)
        # 日志路径应基于纯命令部分（不含时间字段）
        expected = page._cron_log_path("python3 -m superhealth.daily_pipeline")
        assert str(expected) in result

    def test_idempotency_changes_log_path(self, isolated_cron_log_dir):
        # 注意：函数不去重，连续两次调用会再追加一次重定向（不是 bug，是契约）。
        # 这里仅验证函数对已含重定向的命令不会抛错，仍能产生新 line。
        line = "0 7 * * * cmd"
        once = page._line_with_log_redirect(line)
        twice = page._line_with_log_redirect(once)
        # twice 仍包含原始命令，但末尾有两段重定向（实际生产中应先 split 再 attach）
        assert "cmd" in twice
        assert twice.count(">>") >= 1


class TestReadLogTail:
    def test_missing_file_returns_empty(self, tmp_path):
        assert page._read_log_tail(tmp_path / "no-such.log") == ""

    def test_empty_file_returns_empty(self, tmp_path):
        f = tmp_path / "empty.log"
        f.write_text("")
        assert page._read_log_tail(f) == ""

    def test_small_file_returns_all_lines(self, tmp_path):
        f = tmp_path / "small.log"
        f.write_text("a\nb\nc\n")
        result = page._read_log_tail(f, lines=10)
        assert result.splitlines() == ["a", "b", "c"]

    def test_returns_only_last_n_lines(self, tmp_path):
        f = tmp_path / "many.log"
        f.write_text("\n".join(f"line-{i}" for i in range(50)) + "\n")
        result = page._read_log_tail(f, lines=5)
        assert result.splitlines() == [
            "line-45",
            "line-46",
            "line-47",
            "line-48",
            "line-49",
        ]

    def test_default_tail_size_is_200(self, tmp_path):
        f = tmp_path / "huge.log"
        f.write_text("\n".join(f"L{i}" for i in range(500)) + "\n")
        result = page._read_log_tail(f)
        # 默认 200 行
        lines = result.splitlines()
        assert len(lines) == 200
        assert lines[0] == "L300"
        assert lines[-1] == "L499"

    def test_handles_large_file_spanning_multiple_blocks(self, tmp_path):
        # 写入超过 64KB 的内容（每行 100 字节，写 1500 行 ≈ 150KB）
        f = tmp_path / "big.log"
        with f.open("w") as fp:
            for i in range(1500):
                fp.write(f"row-{i:08d}-" + "x" * 80 + "\n")
        result = page._read_log_tail(f, lines=10)
        rows = result.splitlines()
        assert len(rows) == 10
        assert rows[-1].startswith("row-00001499")
        assert rows[0].startswith("row-00001490")

    def test_handles_invalid_utf8_without_crash(self, tmp_path):
        f = tmp_path / "binary.log"
        # 嵌入非法 UTF-8 字节序列
        f.write_bytes(b"line1\n\xff\xfe bad bytes\nline3\n")
        result = page._read_log_tail(f, lines=10)
        # 不抛错，且能恢复出有效行
        assert "line1" in result
        assert "line3" in result

    def test_handles_unicode_content(self, tmp_path):
        f = tmp_path / "zh.log"
        f.write_text("第一行\n第二行\n第三行\n", encoding="utf-8")
        result = page._read_log_tail(f, lines=2)
        assert result.splitlines() == ["第二行", "第三行"]

    def test_returns_empty_on_oserror(self, tmp_path):
        # 让 path.open 抛 OSError，验证安全失败
        f = tmp_path / "x.log"
        f.write_text("hello\n")

        original_open = page.Path.open

        def bad_open(self, *args, **kwargs):
            if self == f:
                raise OSError("disk error")
            return original_open(self, *args, **kwargs)

        with patch.object(page.Path, "open", bad_open):
            assert page._read_log_tail(f) == ""

    def test_file_without_trailing_newline(self, tmp_path):
        f = tmp_path / "noeol.log"
        f.write_text("only-line")
        result = page._read_log_tail(f, lines=10)
        assert result.splitlines() == ["only-line"]


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
