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

    def test_world_bot_whisper_is_reply_only(self):
        rnd = agent_bridge.BotInfo(200, "路人牧", 5, 19, "healer", bot_kind="random_world")
        event = dataclasses_replace(
            self.event,
            channel="whisper",
            target_guid=200,
            target_name="路人牧",
            bot_guid=200,
            bot_name="路人牧",
            message="跟我，加我",
            bots=[rnd],
        )
        self.assertEqual(agent_bridge.available_skills(event, rnd), ["reply", "no_reply"])
        self.assertEqual(agent_bridge.rule_actions(event, rnd), [])

    def test_world_bot_llm_control_skills_are_dropped(self):
        rnd = agent_bridge.BotInfo(200, "路人牧", 5, 19, "healer", bot_kind="random_world")
        event = dataclasses_replace(
            self.event,
            channel="whisper",
            target_guid=200,
            target_name="路人牧",
            bot_guid=200,
            bot_name="路人牧",
            message="跟我",
            bots=[rnd],
        )
        payload = {
            "reply": {"channel": "whisper", "text": "我在这边逛逛"},
            "actions": [{"skill": "bot_follow", "args": {}}],
        }
        actions = agent_bridge.actions_from_llm_payload(payload, event, rnd)
        self.assertEqual([action.action_type for action in actions], ["reply"])

    def test_lifecycle_rule_summons_healer_for_owned_context(self):
        owned = agent_bridge.BotInfo(100, "小牧", 5, 19, "healer", bot_kind="owned")
        event = dataclasses_replace(self.event, message="加个奶", bots=[owned])
        actions = agent_bridge.lifecycle_rule_actions(event)
        self.assertEqual(actions[0].action_type, "summon_bot")
        self.assertIsNone(actions[0].bot)
        self.assertEqual(actions[0].payload["role"], "healer")
        self.assertEqual(actions[0].payload["class_hint"], "priest")

    def test_lifecycle_rule_ignores_random_world_context(self):
        rnd = agent_bridge.BotInfo(200, "路人牧", 5, 19, "healer", bot_kind="random_world")
        event = dataclasses_replace(self.event, message="加个奶", bots=[rnd])
        self.assertEqual(agent_bridge.lifecycle_rule_actions(event), [])

    def test_lifecycle_rule_summons_without_existing_bot_in_party(self):
        event = dataclasses_replace(self.event, message="加个坦", bots=[])
        actions = agent_bridge.lifecycle_rule_actions(event)
        self.assertEqual(actions[0].action_type, "summon_bot")
        self.assertEqual(actions[0].payload["role"], "tank")
        self.assertEqual(actions[0].payload["class_hint"], "warrior")

    def test_lifecycle_rule_group_setup(self):
        event = dataclasses_replace(self.event, message="我想下副本，组个稳一点的队", bots=[])
        actions = agent_bridge.lifecycle_rule_actions(event)
        self.assertEqual([action.action_type for action in actions], ["summon_bot", "summon_bot", "summon_bot"])
        self.assertEqual([action.payload["role"] for action in actions], ["tank", "healer", "dps"])

    def test_lifecycle_rule_group_setup_skips_existing_roles(self):
        owned_healer = agent_bridge.BotInfo(100, "小牧", 5, 19, "healer", bot_kind="owned")
        event = dataclasses_replace(self.event, message="我想下副本", bots=[owned_healer])
        actions = agent_bridge.lifecycle_rule_actions(event)
        self.assertEqual([action.payload["role"] for action in actions], ["tank", "dps"])

    def test_actionable_group_chat_selects_primary_responder(self):
        owned_healer = agent_bridge.BotInfo(100, "小牧", 5, 19, "healer", bot_kind="owned")
        owned_mage = agent_bridge.BotInfo(101, "小法", 8, 19, "dps", bot_kind="owned")
        event = dataclasses_replace(self.event, message="我想下副本", bots=[owned_healer, owned_mage])
        self.assertTrue(agent_bridge.looks_actionable_group_chat(event.message))
        self.assertEqual(agent_bridge.primary_responder(event), owned_healer)

    def test_social_question_is_actionable_group_chat(self):
        self.assertTrue(agent_bridge.looks_actionable_group_chat("你有什么背景故事吗"))

    def test_location_question_uses_context_location(self):
        event = dataclasses_replace(
            self.event,
            message="这是哪?",
            context={
                "environment": {
                    "location": {
                        "map_name": "Eastern Kingdoms",
                        "zone_name": "Stormwind City",
                        "area_name": "Stormwind City",
                    }
                }
            },
        )
        actions = agent_bridge.rule_actions(event, self.priest)
        self.assertEqual(actions[0].action_type, "reply")
        self.assertIn("Stormwind City", actions[0].text)
        self.assertIn("室内小区域没拿到", actions[0].text)

    def test_lifecycle_rule_lookup_and_list(self):
        event = dataclasses_replace(self.event, message="看看机器人池子", bots=[])
        self.assertEqual(agent_bridge.lifecycle_rule_actions(event)[0].action_type, "lookup_bot_pool")
        event = dataclasses_replace(self.event, message="我有哪些机器人", bots=[])
        self.assertEqual(agent_bridge.lifecycle_rule_actions(event)[0].action_type, "list_bots")

    def test_lifecycle_rule_targeted_maintenance(self):
        owned = agent_bridge.BotInfo(100, "小牧", 5, 19, "healer", bot_kind="owned")
        cases = [
            ("小牧初始化一下", "init_bot"),
            ("刷新小牧", "refresh_bot"),
            ("小牧同步等级", "level_bot"),
            ("给小牧初始化副本任务", "init_instance_quests"),
            ("小牧下线", "dismiss_bot"),
        ]
        for message, action_type in cases:
            with self.subTest(message=message):
                event = dataclasses_replace(self.event, message=message, bots=[owned])
                action = agent_bridge.lifecycle_rule_actions(event)[0]
                self.assertEqual(action.action_type, action_type)
                self.assertEqual(action.payload["bot"], "小牧")

    def test_lifecycle_rule_invite_player(self):
        event = dataclasses_replace(self.event, message="邀请张三进队", bots=[])
        action = agent_bridge.lifecycle_rule_actions(event)[0]
        self.assertEqual(action.action_type, "invite_player")
        self.assertEqual(action.payload["target_player"], "张三")

    def test_deepseek_payload_disables_thinking(self):
        client = agent_bridge.OpenAICompatibleClient(
            "https://api.deepseek.com",
            "secret",
            "deepseek-v4-flash",
            thinking="disabled",
        )
        payload = client.request_payload("hi")
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["max_tokens"], 512)

    def test_thinking_enable_alias_normalizes_to_enabled(self):
        self.assertEqual(
            agent_bridge.normalize_llm_thinking("enable", "https://api.deepseek.com", "deepseek-v4-flash"),
            "enabled",
        )
        client = agent_bridge.OpenAICompatibleClient(
            "https://api.deepseek.com",
            "secret",
            "deepseek-v4-flash",
            thinking="enabled",
        )
        self.assertEqual(client.request_payload("hi")["thinking"], {"type": "enabled"})

    def test_local_combat_summary(self):
        facts = {
            "duration_ms": 42000,
            "totals": {
                "damage_done": 1200,
                "damage_taken": 600,
                "healing_done": 500,
                "kills": 2,
                "deaths": 0,
                "healer_threat_events": 1,
            },
            "members": [
                {"name": "Wuya", "min_health_pct": 31, "min_mana_pct": None},
                {"name": "小牧", "min_health_pct": 80, "min_mana_pct": 54},
            ],
        }
        summary = agent_bridge.local_combat_summary(facts)
        self.assertIn("42秒", summary)
        self.assertIn("击杀2个目标", summary)
        self.assertIn("治疗被怪命中1次", summary)

    def test_prompt_includes_memory_and_action_results(self):
        memory = [agent_bridge.CombatMemory(1, "now", "Wuya", "上一场战斗很稳。")]
        actions = [{"id": 1, "status": "done", "type": "command", "command": "follow"}]
        prompt = agent_bridge.build_prompt(self.event_with("奶妈刚才怎么样"), self.priest, [], memory, actions)
        self.assertIn("recent_combat_summaries", prompt)
        self.assertIn("上一场战斗很稳", prompt)
        self.assertIn("follow", prompt)

    def test_prompt_includes_more_recent_messages_with_targets(self):
        history = [
            (1.0, "party", "Wuya", "", "第一句"),
            (2.0, "whisper", "Wuya", "小牧", "悄悄话"),
            (3.0, "party", "Wuya", "", "第三句"),
        ]
        prompt = agent_bridge.build_prompt(
            self.event_with("记得吗"),
            self.priest,
            history,
            recent_message_limit=3,
        )
        self.assertIn("第一句", prompt)
        self.assertIn("小牧", prompt)
        self.assertIn("悄悄话", prompt)


def dataclasses_replace(instance, **changes):
    import dataclasses

    return dataclasses.replace(instance, **changes)


if __name__ == "__main__":
    unittest.main()
