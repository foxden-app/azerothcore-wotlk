#!/usr/bin/env python3

import importlib.util
import pathlib
import sys
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name("agent_bridge.py")
spec = importlib.util.spec_from_file_location("agent_bridge", MODULE_PATH)
agent_bridge = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["agent_bridge"] = agent_bridge
spec.loader.exec_module(agent_bridge)


class RuleActionTests(unittest.TestCase):
    def setUp(self):
        self.priest = agent_bridge.BotInfo(100, "小牧", 5, 19, "healer")
        self.event = agent_bridge.ChatEvent(
            id=1,
            channel="party",
            speaker_guid=1,
            speaker_name="Wuya",
            target_guid=None,
            target_name=None,
            bot_guid=None,
            bot_name=None,
            message="",
            bots=[self.priest],
        )

    def event_with(self, message):
        return dataclasses_replace(self.event, message=message)

    def test_follow_rule(self):
        actions = agent_bridge.rule_actions(self.event_with("牧师跟我"), self.priest)
        self.assertEqual(actions[0].action_type, "command")
        self.assertEqual(actions[0].command, "follow")

    def test_greeting_rule(self):
        actions = agent_bridge.rule_actions(self.event_with("奶妈晚上好"), self.priest)
        self.assertEqual(actions[0].action_type, "reply")
        self.assertEqual(actions[0].text, "晚上好")

    def test_focus_heal_add_rule(self):
        actions = agent_bridge.rule_actions(self.event_with("小牧加我"), self.priest)
        commands = [action.command for action in actions if action.action_type == "command"]
        self.assertIn("focus heal +Wuya", commands)

    def test_healer_safe_rule(self):
        actions = agent_bridge.rule_actions(self.event_with("别输出安心奶"), self.priest)
        strategies = [action.strategy for action in actions if action.action_type == "strategy"]
        self.assertIn("-healer dps", strategies)

    def test_alias_detection(self):
        self.assertTrue(agent_bridge.contains_alias("牧师过来", self.priest))
        self.assertTrue(agent_bridge.contains_alias("治疗加我", self.priest))
        self.assertFalse(agent_bridge.contains_alias("法师过来", self.priest))

    def test_llm_skill_mapping(self):
        actions = agent_bridge.skill_to_actions("loot_gray", {}, self.event, self.priest)
        self.assertEqual(actions[0].strategy, "+loot")
        self.assertEqual(actions[1].command, "ll gray")


def dataclasses_replace(instance, **changes):
    import dataclasses

    return dataclasses.replace(instance, **changes)


if __name__ == "__main__":
    unittest.main()
