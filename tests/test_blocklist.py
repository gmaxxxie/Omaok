"""Blocklist tests: hidden/sensitive paths must never resolve or execute."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import blocklist
from config import settings
from executor import actions as executor
from intent import rules
from policy import policy
from resolver import lookup

CFG = settings.load_config()
ALIASES = settings.load_aliases()


class TestBlocklistMatcher(unittest.TestCase):
    def test_hidden_paths_blocked(self):
        for p in ["~/.ssh", "~/.config/voxtype/config.toml", "~/.cache", "~/.local", "~/.gnupg", "~/.bash_history"]:
            self.assertTrue(blocklist.is_blocked_path(p), p)

    def test_sensitive_names_blocked(self):
        self.assertTrue(blocklist.is_blocked_path("/home/max/auth.json"))
        self.assertTrue(blocklist.is_blocked_path("/home/max/private/key.pem"))
        self.assertTrue(blocklist.is_blocked_path("/home/max/backup-keys/id_rsa"))

    def test_normal_paths_allowed(self):
        for p in ["~/Downloads", "~/Documents", "~/项目/Omaok", "~/Desktop", "~/Pictures"]:
            self.assertFalse(blocklist.is_blocked_path(p), p)

    def test_user_override_merges(self):
        # block_hidden can be disabled; segments still enforced
        bl = {"block_hidden": False, "segments": ["secrets"], "file_names": ["auth.json"]}
        self.assertFalse(blocklist.is_blocked_path("~/Downloads", bl))
        self.assertTrue(blocklist.is_blocked_path("~/secrets/notes.txt", bl))
        self.assertTrue(blocklist.is_blocked_path("~/auth.json", bl))


class TestResolverBlocklist(unittest.TestCase):
    def test_hidden_folder_never_resolves(self):
        # even an explicit attempt to open a hidden folder is refused
        draft = rules.parse("打开文件夹 ~/.ssh")
        a = lookup.resolve_action(dict(draft), CFG, ALIASES) if draft else None
        self.assertIsNone(a)

    def test_hidden_file_never_resolves(self):
        draft = rules.parse("打开文件 ~/.bash_history")
        a = lookup.resolve_action(dict(draft), CFG, ALIASES) if draft else None
        self.assertIsNone(a)

    def test_search_never_returns_hidden(self):
        # searching "ssh" may match a legit Go module dir, but must never
        # resolve to the blocked ~/.ssh or any blocked path
        a = lookup.resolve_action({"type": "open_folder", "raw_target": "ssh", "confidence": 0.9}, CFG, ALIASES)
        if a is not None:
            path = a["target"]["path"]
            self.assertNotEqual(path, os.path.expanduser("~/.ssh"))
            self.assertFalse(blocklist.is_blocked_path(path))


class TestPolicyAndExecutorBlocklist(unittest.TestCase):
    def test_policy_denies_blocked_path(self):
        action = {"type": "open_folder", "target": {"path": os.path.expanduser("~/.ssh")}, "confidence": 0.9}
        v = policy.verdict(action, CFG)
        self.assertFalse(v["allowed"])
        self.assertIn("blocked", v["reason"])

    def test_executor_refuses_blocked_path(self):
        ok, msg = executor._open_path(os.path.expanduser("~/.config/voxtype/config.toml"))
        self.assertFalse(ok)
        self.assertIn("blocked", msg)


if __name__ == "__main__":
    unittest.main()
