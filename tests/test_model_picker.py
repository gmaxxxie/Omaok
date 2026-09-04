"""Unit tests: STT/AI model enumeration + `config set` persistence.

These cover the popup model pickers:
  - stt.list_installed_models() parses `voxtype info models` and the models dir
  - config set stt.model / ai.model / ai.thinking persist to the user config
  - ai.list_available_models() filters to ready providers
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from stt import provider as stt
from intent import ai as intent_ai


class TestSttModelEnumeration(unittest.TestCase):
    def test_parse_installed_models(self):
        text = """Model catalog  (/home/x/.local/share/voxtype/models)

whisper
             tiny
  installed  base
  installed  small (default)
sensevoice
  installed  small-fp32  (download: sensevoice-small-fp32)
"""
        out = stt._parse_installed_models(text)
        self.assertIn({"engine": "whisper", "model": "base"}, out)
        self.assertIn({"engine": "whisper", "model": "small"}, out)
        self.assertIn({"engine": "sensevoice", "model": "small-fp32"}, out)
        self.assertNotIn({"engine": "whisper", "model": "tiny"}, out)

    @mock.patch("stt.provider.VOXTYPE", "/usr/bin/voxtype")
    @mock.patch("stt.provider.MODELS_DIR", "/nonexistent-models")
    @mock.patch("stt.provider.subprocess.run")
    def test_list_installed_models_calls_voxtype(self, run):
        run.return_value = mock.Mock(
            returncode=0,
            stdout="whisper\n  installed  base\nsensevoice\n  installed  small-fp32\n",
        )
        out = stt.list_installed_models()
        self.assertTrue(any(m["model"] == "base" for m in out))

    @mock.patch("stt.provider.VOXTYPE", None)
    @mock.patch(
        "stt.provider.MODELS_DIR",
        os.path.join(tempfile.mkdtemp(), "models"),
    )
    def test_list_installed_models_dir_fallback(self):
        os.makedirs(stt.MODELS_DIR, exist_ok=True)
        open(os.path.join(stt.MODELS_DIR, "ggml-small.bin"), "wb").close()
        os.makedirs(os.path.join(stt.MODELS_DIR, "sensevoice-small-int8"), exist_ok=True)
        out = stt.list_installed_models()
        self.assertIn({"engine": "whisper", "model": "small"}, out)
        self.assertIn({"engine": "sensevoice", "model": "small-int8"}, out)


class TestAiModelEnumeration(unittest.TestCase):
    @mock.patch("intent.ai._configured_providers", return_value={"deepseek", "opencode", "volcengine-plan"})
    @mock.patch("intent.ai.subprocess.run")
    def test_probe_pi_models_filters_configured_providers(self, run, _cfg):
        run.return_value = mock.Mock(
            returncode=0,
            stdout="provider               model\n"
                   "deepseek               deepseek-v4-flash\n"
                   "volcengine-plan        deepseek-v4-pro\n"
                   "opencode               claude-5\n"
                   "xai                    grok-4\n",
        )
        out = intent_ai._probe_pi_models()
        ids = {m["id"] for m in out}
        self.assertIn("deepseek/deepseek-v4-flash", ids)
        self.assertIn("volcengine-plan/deepseek-v4-pro", ids)  # configured => included
        self.assertIn("opencode/claude-5", ids)
        self.assertNotIn("xai/grok-4", ids)  # not configured => excluded

    @mock.patch(
        "intent.ai.os.path.expanduser",
        side_effect=lambda p: "/tmp/pi/auth.json" if "auth.json" in p else p,
    )
    @mock.patch("intent.ai.open")
    def test_configured_providers_reads_auth_json(self, open_mock, _exp):
        import io
        open_mock.return_value = io.StringIO(
            '{"deepseek": {"type": "api_key", "key": "x"}, '
            '"opencode": {"type": "api_key", "key": "y"}, '
            '"nokey": {"type": "api_key"}}'
        )
        out = intent_ai._configured_providers()
        self.assertIn("deepseek", out)
        self.assertIn("opencode", out)
        self.assertNotIn("nokey", out)

    def test_thinking_level_fallback(self):
        self.assertEqual(intent_ai._thinking_level({"ai": {"thinking": "off"}}), "off")
        self.assertEqual(intent_ai._thinking_level({"ai": {"thinking": "high"}}), "high")
        self.assertEqual(intent_ai._thinking_level({"ai": {"thinking": "bogus"}}), "off")
        self.assertEqual(intent_ai._thinking_level({}), "off")


class TestConfigPersistence(unittest.TestCase):
    def setUp(self):
        self._orig = settings.USER_CONFIG_DIR
        self._tmp = tempfile.mkdtemp()
        settings.USER_CONFIG_DIR = self._tmp

    def tearDown(self):
        settings.USER_CONFIG_DIR = self._orig

    def test_save_user_config_merges(self):
        settings.save_user_config({"ai": {"thinking": "high"}})
        settings.save_user_config({"ai": {"model": "deepseek/deepseek-v4-flash"}})
        path = os.path.join(self._tmp, "config.json")
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["ai"]["thinking"], "high")
        self.assertEqual(data["ai"]["model"], "deepseek/deepseek-v4-flash")

    @mock.patch("cli.stt.list_installed_models", return_value=[{"engine": "sensevoice", "model": "small-int8"}])
    @mock.patch("cli.cmd_refresh_provider", return_value=0)
    def test_config_set_stt_model(self, _refresh, _models):
        import cli as cli_mod
        rc = cli_mod.cmd_config(["set", "stt.model", "sensevoice/small-int8"])
        self.assertEqual(rc, 0)
        path = os.path.join(self._tmp, "config.json")
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["stt"]["engine"], "sensevoice")
        self.assertEqual(data["stt"]["model"], "small-int8")

    @mock.patch("cli.intent_ai.list_available_models",
                return_value=[{"id": "deepseek/deepseek-v4-flash"}, {"id": "opencode/claude-5"}])
    @mock.patch("cli.intent_ai.restart_daemon")
    @mock.patch("cli.cmd_refresh_provider", return_value=0)
    def test_config_set_ai_model_restarts_daemon(self, _refresh, restart, _models):
        import cli as cli_mod
        rc = cli_mod.cmd_config(["set", "ai.model", "deepseek/deepseek-v4-flash"])
        self.assertEqual(rc, 0)
        restart.assert_called_once()
        path = os.path.join(self._tmp, "config.json")
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["ai"]["model"], "deepseek/deepseek-v4-flash")

    @mock.patch("cli.intent_ai.list_available_models",
                return_value=[{"id": "deepseek/deepseek-v4-flash"}])
    @mock.patch("cli.cmd_refresh_provider", return_value=0)
    def test_config_set_ai_model_rejects_unknown(self, _refresh, _models):
        import cli as cli_mod
        rc = cli_mod.cmd_config(["set", "ai.model", "nonsense/nope"])
        self.assertEqual(rc, 2)  # rejected

    @mock.patch("cli.intent_ai.restart_daemon")
    @mock.patch("cli.cmd_refresh_provider", return_value=0)
    def test_config_set_ai_thinking_validates(self, _refresh, restart):
        import cli as cli_mod
        self.assertEqual(cli_mod.cmd_config(["set", "ai.thinking", "off"]), 0)
        self.assertEqual(cli_mod.cmd_config(["set", "ai.thinking", "high"]), 0)
        self.assertEqual(cli_mod.cmd_config(["set", "ai.thinking", "turbo"]), 2)  # invalid


if __name__ == "__main__":
    unittest.main()
