from unittest.mock import patch

from superhealth import notifications


def test_send_push_message_omits_account_for_wecom():
    with (
        patch("superhealth.notifications._openclaw_command", return_value="openclaw"),
        patch("superhealth.notifications._openclaw_env", return_value=None),
        patch("superhealth.notifications._ensure_wecom_config", return_value=0) as ensure,
        patch("superhealth.notifications.subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""

        rc = notifications.send_push_message(
            channel="wecom",
            target="user-id",
            account_id="aid",
            wecom_bot_id="bot",
            wecom_secret="secret",
            message="hello",
        )

    assert rc == 0
    ensure.assert_called_once_with("bot", "secret", 60)
    cmd = run.call_args.args[0]
    assert "--account" not in cmd
    assert cmd == [
        "openclaw",
        "message",
        "send",
        "--channel",
        "wecom",
        "-t",
        "user-id",
        "--message",
        "hello",
    ]


def test_send_push_message_keeps_account_for_non_wecom_channel():
    with (
        patch("superhealth.notifications._openclaw_command", return_value="openclaw"),
        patch("superhealth.notifications._openclaw_env", return_value=None),
        patch("superhealth.notifications.subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""

        rc = notifications.send_push_message(
            channel="openclaw-weixin",
            target="openid",
            account_id="aid",
            message="hello",
        )

    assert rc == 0
    cmd = run.call_args.args[0]
    assert cmd[-2:] == ["--account", "aid"]


def test_send_push_message_fails_when_wecom_config_incomplete():
    rc = notifications.send_push_message(
        channel="wecom",
        target="user-id",
        message="hello",
    )

    assert rc == 1
