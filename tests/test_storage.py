import sys
import tempfile
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from mobile_access import MobileAccess, is_loopback
from storage import MessageArchive


def received(index=7, status=0, text="验证码 1234", pdu="001122"):
    return {
        "index": index,
        "status": status,
        "pdu": pdu,
        "decoded": {
            "kind": "deliver",
            "peer": "10086",
            "timestamp": "2026-08-18 10:00:00",
            "text": text,
            "dcs": "0x08",
        },
    }


class MessageArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.archive = MessageArchive(Path(self.directory.name) / "messages.db")

    def tearDown(self):
        self.directory.cleanup()

    def test_ingest_is_deduplicated_and_searchable(self):
        self.archive.ingest([received()])
        self.archive.ingest([received(status=1)])
        result = self.archive.list(query="验证码")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["messages"][0]["status"], 1)

    def test_sent_messages_and_user_metadata(self):
        message_id = self.archive.record_sent("+8613800000000", "发送成功", "AABB")
        updated = self.archive.update(message_id, note="客户回复", starred=True)
        self.assertEqual(updated["message"]["note"], "客户回复")
        self.assertTrue(updated["message"]["starred"])
        self.assertEqual(self.archive.list(box="sent")["total"], 1)
        self.assertEqual(self.archive.list(box="starred")["total"], 1)

    def test_repeated_identical_sends_are_kept_separately(self):
        self.archive.record_sent("10086", "查询余额", "AABB")
        self.archive.record_sent("10086", "查询余额", "AABB")
        self.assertEqual(self.archive.list(box="sent")["total"], 2)

    def test_deleted_archive_is_not_reingested(self):
        message = received()
        self.archive.ingest([message])
        message_id = self.archive.list()["messages"][0]["archiveId"]
        self.archive.delete(message_id)
        self.archive.ingest([message])
        self.assertEqual(self.archive.list()["total"], 0)

    def test_detaching_modem_index_keeps_archive(self):
        self.archive.ingest([received(index=9)])
        self.archive.detach_modem_indices([9])
        message = self.archive.list()["messages"][0]
        self.assertIsNone(message["index"])


class MobileAccessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.account_path = Path(self.directory.name) / "mobile_access.json"

    def tearDown(self):
        self.directory.cleanup()

    def configured_access(self):
        access = MobileAccess("123456", self.account_path)
        access.configure("tester", "correct-horse")
        return access

    def test_loopback_is_authorized_without_token(self):
        access = MobileAccess("123456", self.account_path)
        self.assertTrue(access.authorized("", "127.0.0.1"))
        self.assertTrue(is_loopback("::1"))

    def test_remote_client_must_login_then_pair(self):
        access = self.configured_access()
        self.assertFalse(access.authorized("", "192.168.1.20"))
        challenge = access.login("tester", "correct-horse", "192.168.1.20")
        token = access.pair("123456", challenge, "192.168.1.20")
        self.assertTrue(access.authorized(f"Bearer {token}", "192.168.1.20"))

    def test_wrong_password_and_missing_challenge_are_rejected(self):
        access = self.configured_access()
        with self.assertRaises(PermissionError):
            access.login("tester", "wrong-password", "192.168.1.21")
        with self.assertRaises(PermissionError):
            access.pair("123456", "", "192.168.1.21")

    def test_pair_challenge_is_bound_to_address(self):
        access = self.configured_access()
        challenge = access.login("tester", "correct-horse", "192.168.1.20")
        with self.assertRaises(PermissionError):
            access.pair("123456", challenge, "192.168.1.21")

    def test_password_is_hashed_on_disk_and_account_change_revokes_tokens(self):
        access = self.configured_access()
        challenge = access.login("tester", "correct-horse", "192.168.1.20")
        token = access.pair("123456", challenge, "192.168.1.20")
        saved = self.account_path.read_text(encoding="utf-8")
        self.assertNotIn("correct-horse", saved)
        access.configure("new-user", "another-password")
        self.assertFalse(access.authorized(f"Bearer {token}", "192.168.1.20"))


if __name__ == "__main__":
    unittest.main()
