import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import forwarding


def valid_config(**overrides):
    value = {
        "enabled": True,
        "smtpHost": "smtp.example.com",
        "smtpPort": 587,
        "security": "starttls",
        "username": "sender@example.com",
        "sender": "sender@example.com",
        "recipients": ["receiver@example.com"],
        "subjectPrefix": "[SMS]",
        "pollInterval": 15,
        "includePdu": False,
        "activationId": "activation-1",
    }
    value.update(overrides)
    config = forwarding.normalize_config(value)
    config["activationId"] = value["activationId"]
    return config


class ForwardingConfigTests(unittest.TestCase):
    def test_normalize_recipients_and_bounds(self):
        config = valid_config(recipients="a@example.com; b@example.com\na@example.com")
        self.assertEqual(config["recipients"], ["a@example.com", "b@example.com"])
        with self.assertRaises(forwarding.ForwardingError):
            valid_config(pollInterval=5)

    def test_email_contains_sms_without_pdu_by_default(self):
        message = {
            "index": 7,
            "pdu": "001122",
            "decoded": {"peer": "+8613800000000", "timestamp": "2026-08-15 12:00:00", "text": "验证码 1234"},
        }
        email = forwarding.build_forwarding_email(valid_config(), message)
        body = email.get_body(preferencelist=("plain",)).get_content()
        self.assertIn("验证码 1234", body)
        self.assertIn("+8613800000000", email["Subject"])
        self.assertNotIn("001122", body)

    def test_config_does_not_store_password(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "forwarding.json"
            state_path = Path(directory) / "state.json"
            service = forwarding.ForwardingService(lambda _box: {"messages": []})
            payload = valid_config()
            payload["password"] = "top-secret"
            with (
                mock.patch.object(forwarding, "CONFIG_PATH", config_path),
                mock.patch.object(forwarding, "STATE_PATH", state_path),
                mock.patch.object(forwarding, "keychain_password", return_value="stored-secret"),
                mock.patch.object(forwarding, "save_keychain_password") as save_password,
            ):
                service.update(payload)
            save_password.assert_called_once_with("top-secret")
            stored = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertNotIn("password", stored)
            self.assertTrue(stored["activationId"])


class ForwardingServiceTests(unittest.TestCase):
    def test_first_poll_builds_baseline_then_forwards_only_new_sms(self):
        messages = [
            {"index": 1, "status": 0, "pdu": "AA", "decoded": {"peer": "10086", "text": "历史短信"}},
        ]
        service = forwarding.ForwardingService(lambda box: {"box": box, "messages": list(messages)})
        config = valid_config()

        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            lock_path = Path(directory) / "forwarding.lock"
            with (
                mock.patch.object(forwarding, "STATE_PATH", state_path),
                mock.patch.object(forwarding, "LOCK_PATH", lock_path),
                mock.patch.object(forwarding, "keychain_password", return_value="secret"),
                mock.patch.object(forwarding, "send_smtp") as send,
            ):
                self.assertEqual(service._poll_once(config), 0)
                send.assert_not_called()

                messages.append(
                    {"index": 2, "status": 0, "pdu": "BB", "decoded": {"peer": "95555", "text": "新短信"}}
                )
                self.assertEqual(service._poll_once(config), 1)
                self.assertEqual(send.call_count, 1)
                messages[1]["status"] = 1
                self.assertEqual(service._poll_once(config), 0)
                self.assertEqual(send.call_count, 1)

    def test_starttls_smtp_flow(self):
        smtp = mock.MagicMock()
        smtp_context = mock.MagicMock()
        smtp_context.__enter__.return_value = smtp
        with mock.patch.object(forwarding.smtplib, "SMTP", return_value=smtp_context):
            forwarding.send_smtp(
                valid_config(),
                forwarding.build_forwarding_email(valid_config(), {}, test=True),
                password="secret",
            )
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("sender@example.com", "secret")
        smtp.send_message.assert_called_once()


if __name__ == "__main__":
    unittest.main()
