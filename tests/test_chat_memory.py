"""Unit tests: omaok chat memory — long conversation (short-term) + long-term facts.

Covers:
  - explicit memory/conversation commands (remember / forget / recall / end)
  - conversation thread: rollover by gap, rolling window + summary, persistence
  - long-term facts: retrieval scoring + hits bump, consolidate dedupe, forget
  - chat layer orchestration: persona offline, explicit commands, context-aware
    model reply, none/noise not persisted
"""

import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import chatmem, settings
from intent import chat as chat_layer


class ChatMemBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = settings.USER_CONFIG_DIR
        settings.USER_CONFIG_DIR = self.tmp

    def tearDown(self):
        settings.USER_CONFIG_DIR = self._orig


class TestExplicitCommands(ChatMemBase):
    def test_remember(self):
        self.assertEqual(chatmem.explicit_command("记住我住在上海"), ("remember", "我住在上海"))
        self.assertEqual(chatmem.explicit_command("帮我记住我喜欢喝咖啡"), ("remember", "我喜欢喝咖啡"))
        self.assertEqual(chatmem.explicit_command("remember I like dark theme"), ("remember", "I like dark theme"))

    def test_forget(self):
        self.assertEqual(chatmem.explicit_command("忘掉关于咖啡的事"), ("forget", "咖啡"))
        self.assertEqual(chatmem.explicit_command("忘记我叫什么"), ("forget", "叫什么"))
        self.assertEqual(chatmem.explicit_command("forget about the project"), ("forget", "the project"))

    def test_recall_and_end(self):
        self.assertEqual(chatmem.explicit_command("我上次说到哪了")[0], "recall")
        self.assertEqual(chatmem.explicit_command("我们聊到哪了")[0], "recall")
        self.assertEqual(chatmem.explicit_command("结束对话")[0], "end")
        self.assertEqual(chatmem.explicit_command("不聊了")[0], "end")

    def test_non_commands_none(self):
        self.assertIsNone(chatmem.explicit_command("打开微信"))
        self.assertIsNone(chatmem.explicit_command("今天天气怎么样"))
        self.assertIsNone(chatmem.explicit_command(""))


class TestShortTermThread(ChatMemBase):
    def test_session_rollover_by_gap(self):
        s = chatmem.empty_session()
        s["active"] = True
        s["last_turn_at"] = time.time() - chatmem.GAP_SECONDS - 1
        self.assertTrue(chatmem.should_rollover(s, time.time()))
        s["last_turn_at"] = time.time()
        self.assertFalse(chatmem.should_rollover(s, time.time()))

    def test_inactive_rolls_over(self):
        s = chatmem.empty_session()  # active=False
        self.assertTrue(chatmem.should_rollover(s, time.time()))

    def test_park_stashes_turns(self):
        s = chatmem.start_session()
        chatmem.append_turn(s, "user", "我们聊聊旅行")
        chatmem.append_turn(s, "assistant", "好啊")
        chatmem.save_session(s)
        chatmem.park()
        parked = chatmem.load_session()
        self.assertFalse(parked["active"])
        self.assertEqual(len(parked["turns"]), 0)
        self.assertEqual(len(parked["pending"]), 2)

    def test_rolling_window_overflow(self):
        s = chatmem.start_session()
        for i in range(chatmem.MAX_TURNS + 4):
            chatmem.append_turn(s, "user", f"t{i}")
        keep, overflow = chatmem.trimmed(s)
        self.assertEqual(len(keep), chatmem.MAX_TURNS)
        self.assertEqual(len(overflow), 4)
        # 12 turns t0..t11; keep = last 8 = t4..t11
        self.assertEqual(keep[0]["text"], "t4")
        self.assertEqual(overflow[-1]["text"], "t3")

    def test_persistence_roundtrip(self):
        s = chatmem.start_session()
        chatmem.append_turn(s, "user", "你好")
        chatmem.append_turn(s, "assistant", "你好呀")
        chatmem.save_session(s)
        loaded = chatmem.load_session()
        self.assertEqual([t["text"] for t in loaded["turns"]], ["你好", "你好呀"])
        self.assertTrue(loaded["active"])


class TestLongTermFacts(ChatMemBase):
    def _seed(self, texts):
        facts = []
        for i, t in enumerate(texts):
            chatmem.add_fact(facts, t, "fact", ts=time.time() - i * 100)
        chatmem.save_facts(facts)
        return facts

    def test_retrieve_ranks_and_bumps_hits(self):
        self._seed(["用户是产品经理，负责需求评审", "用户喜欢喝冰美式咖啡", "用户在准备周末的旅行"])
        facts = chatmem.load_facts()
        top = chatmem.retrieve("帮我记一下明天开需求评审会", facts)
        self.assertTrue(top)
        self.assertIn("需求评审", top[0]["text"])
        reloaded = chatmem.load_facts()
        self.assertGreater(reloaded[0]["hits"], 0)  # hits bumped + persisted

    def test_merge_consolidated_dedupes(self):
        facts = []
        chatmem.add_fact(facts, "用户是产品经理", "identity")
        new = [{"text": "用户是产品经理", "category": "identity"},   # near-dup -> merge
               {"text": "用户周末要去上海", "category": "fact"}]    # new -> add
        res = chatmem.merge_consolidated(facts, new)
        self.assertEqual(res["added"], 1)
        self.assertEqual(res["merged"], 1)
        self.assertEqual(len(chatmem.load_facts()), 2)

    def test_forget_removes(self):
        self._seed(["用户喜欢喝冰美式咖啡", "用户住在上海"])
        facts = chatmem.load_facts()
        removed = chatmem.forget("咖啡", facts)
        self.assertEqual(removed, 1)
        remaining = chatmem.load_facts()
        self.assertEqual(len(remaining), 1)
        self.assertIn("上海", remaining[0]["text"])

    def test_add_fact_category_default(self):
        facts = []
        f = chatmem.add_fact(facts, "用户习惯早起")
        self.assertEqual(f["category"], "fact")
        f2 = chatmem.add_fact(facts, "todo", "todo")
        self.assertEqual(f2["category"], "todo")


class TestChatLayer(ChatMemBase):
    def _cfg(self):
        return {"ai": {"enabled": True, "backend": "pi-rpc", "timeout_secs": 60},
                "chat": {"enabled": True}}

    def test_persona_offline_no_model(self):
        with mock.patch("intent.ai.chat_analyze") as ca:
            r = chat_layer.process("你是谁", self._cfg())
            ca.assert_not_called()
        self.assertEqual(r["kind"], "answer")
        self.assertEqual(r["via"], "persona")
        self.assertIn("omaok", r["reply"])

    def test_remember_command_adds_fact(self):
        with mock.patch("intent.ai.chat_analyze") as ca:
            r = chat_layer.process("记住我住在上海", self._cfg())
            ca.assert_not_called()
        self.assertIn("记住了", r["reply"])
        texts = [f["text"] for f in chatmem.load_facts()]
        self.assertIn("我住在上海", texts)

    def test_forget_command_removes(self):
        facts = chatmem.load_facts()
        chatmem.add_fact(facts, "用户住在上海")
        chatmem.save_facts(facts)
        r = chat_layer.process("忘掉关于上海的事", self._cfg())
        self.assertIn("忘掉", r["reply"])
        self.assertEqual(chatmem.load_facts(), [])

    def test_end_parks_session(self):
        with mock.patch("intent.ai.chat_analyze", return_value={"kind": "answer", "reply": "你好呀"}):
            chat_layer.process("你好", self._cfg())
        r = chat_layer.process("结束对话", self._cfg())
        self.assertIn("先聊到这儿", r["reply"])
        s = chatmem.load_session()
        self.assertFalse(s["active"])
        self.assertEqual(len(s["turns"]), 0)

    def test_context_aware_reply_and_turn_persisted(self):
        with mock.patch("intent.ai.chat_analyze") as ca:
            ca.return_value = {"kind": "answer", "reply": "旅行计划我们可以从预算聊起"}
            r = chat_layer.process("我们聊聊旅行计划", self._cfg())
            ca.assert_called_once()
            ctx = ca.call_args.kwargs["context"]
            self.assertIsInstance(ctx, dict)
        self.assertEqual(r["reply"], "旅行计划我们可以从预算聊起")
        s = chatmem.load_session()
        self.assertEqual([t["text"] for t in s["turns"]], ["我们聊聊旅行计划", "旅行计划我们可以从预算聊起"])

    def test_followup_gets_context(self):
        with mock.patch("intent.ai.chat_analyze",
                        return_value={"kind": "answer", "reply": "旅行计划我们从预算开始吧"}):
            chat_layer.process("我们聊聊旅行计划", self._cfg())
        with mock.patch("intent.ai.chat_analyze") as ca:
            ca.return_value = {"kind": "answer", "reply": "预算大概一万左右比较合适"}
            r = chat_layer.process("那预算多少合适呢", self._cfg())
            ctx = ca.call_args.kwargs["context"]
            texts = [t["text"] for t in ctx["turns"]]
            self.assertIn("我们聊聊旅行计划", texts)
        self.assertEqual(r["reply"], "预算大概一万左右比较合适")

    def test_defer_still_works(self):
        with mock.patch("intent.ai.chat_analyze",
                        return_value={"kind": "defer", "reply": "帮我查一下并讨论"}):
            r = chat_layer.process("量子计算的原理是什么", self._cfg())
        self.assertEqual(r["kind"], "defer")
        self.assertEqual(r["reply"], "帮我查一下并讨论")

    def test_none_not_persisted(self):
        with mock.patch("intent.ai.chat_analyze",
                        return_value={"kind": "none", "reply": ""}):
            r = chat_layer.process("嗯", self._cfg())
        self.assertEqual(r["kind"], "none")
        self.assertEqual(chatmem.load_session()["turns"], [])

    def test_model_failure_returns_none(self):
        with mock.patch("intent.ai.chat_analyze", return_value=None):
            self.assertIsNone(chat_layer.process("随便聊聊", self._cfg()))

    def test_rollover_consolidates_pending(self):
        # Park a finished thread, then the next chat should consolidate it.
        s = chatmem.start_session()
        chatmem.append_turn(s, "user", "我喜欢喝冰美式")
        chatmem.append_turn(s, "assistant", "好呀")
        chatmem.save_session(s)
        chatmem.park()
        with mock.patch("intent.ai.consolidate_facts",
                        return_value=[{"text": "用户喜欢喝冰美式", "category": "preference"}]) as cf, \
             mock.patch("intent.ai.chat_analyze",
                        return_value={"kind": "answer", "reply": "好呀，我们聊点别的"}):
            r = chat_layer.process("那聊点别的吧", self._cfg())
        cf.assert_called_once()
        texts = [f["text"] for f in chatmem.load_facts()]
        self.assertIn("用户喜欢喝冰美式", texts)
        self.assertEqual(r["kind"], "answer")


if __name__ == "__main__":
    unittest.main()
