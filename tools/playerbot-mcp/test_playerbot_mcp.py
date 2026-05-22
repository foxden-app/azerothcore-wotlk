#!/usr/bin/env python3

import base64
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import hermes_relay
import server
import wow_common


class CommonTests(unittest.TestCase):
    def test_sql_quote_escapes_text(self):
        self.assertEqual(wow_common.sql_quote("a'b\\c\n"), "'a\\'b\\\\c\\n'")

    def test_json_from_b64_repairs_legacy_teamid_control_bytes(self):
        text = '{"group_members":[{"name":"联盟","team":\x00},{"name":"部落","team":\x01}]}'
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")

        payload = wow_common.json_from_b64(encoded)

        self.assertEqual(payload["group_members"][0]["team"], 0)
        self.assertEqual(payload["group_members"][1]["team"], 1)

    def test_fetch_events_can_filter_unprocessed(self):
        class FakeDb:
            sql = ""

            def query_rows(self, sql):
                self.sql = sql
                return []

        db = FakeDb()
        events = wow_common.fetch_events(db, after_id=7, limit=500, unprocessed_only=True)

        self.assertEqual(events, [])
        self.assertIn("`id` > 7", db.sql)
        self.assertIn("`processed_at` IS NULL", db.sql)
        self.assertIn("LIMIT 100", db.sql)

    def test_relay_skip_backlog_on_start_marks_unprocessed_rows(self):
        class FakeDb:
            executed = []

            def scalar_int(self, sql):
                if "COUNT(*)" in sql:
                    return 3
                if "WHERE `processed_at` IS NOT NULL" in sql:
                    return 0
                if "MAX(`id`)" in sql:
                    return 15
                return 0

            def execute(self, sql):
                self.executed.append(sql)

        with tempfile.TemporaryDirectory() as tmp:
            db = FakeDb()
            state_path = pathlib.Path(tmp) / "state.json"
            state_path.write_text('{"last_id":7}', encoding="utf-8")
            relay = hermes_relay.Relay(
                db,
                client=object(),
                state_path=state_path,
                skip_backlog_on_start=True,
            )

        self.assertEqual(relay.last_id, 15)
        self.assertEqual(relay.skipped_backlog, 3)
        self.assertEqual(len(db.executed), 1)
        self.assertIn("`id` > 7", db.executed[0])
        self.assertIn("`id` <= 15", db.executed[0])
        self.assertIn("`processed_at` IS NULL", db.executed[0])

    def test_combat_summary_compact_keeps_key_facts(self):
        facts = {
            "totals": {
                "kills": 1,
                "deaths": 0,
                "damage_done": 1200,
                "damage_taken": 300,
                "healing_done": 250,
                "bot_threat_events": 2,
                "healer_threat_events": 1,
            },
            "group_leader": {"guid": 556, "name": "Wuya"},
            "members": [
                {
                    "name": "Dailea",
                    "role": "healer",
                    "is_bot": True,
                    "damage_done": 100,
                    "damage_taken": 0,
                    "healing_done": 250,
                    "healing_received": 0,
                    "deaths": 0,
                    "min_health_pct": 100,
                    "hostile_hits_taken": 0,
                },
                {
                    "name": "Palei",
                    "role": "dps",
                    "is_bot": True,
                    "damage_done": 900,
                    "damage_taken": 300,
                    "healing_done": 0,
                    "healing_received": 240,
                    "deaths": 0,
                    "min_health_pct": 18.4,
                    "hostile_hits_taken": 5,
                },
            ],
            "enemies": [{"name": "Defias", "level": 19, "killed": True, "damage_done": 300, "damage_taken": 1200}],
            "timeline": ["combat started", "Defias killed by Palei"],
        }
        row = [
            "42",
            "2026-05-19 21:07:23",
            "",
            "556",
            "Wuya",
            "0",
            "85",
            "161",
            "13024",
            "1",
            "0",
            base64.b64encode(json.dumps(facts).encode()).decode(),
            base64.b64encode("打得稳定".encode()).decode(),
        ]

        summary = wow_common.combat_summary_row_to_dict(row)
        compact = wow_common.compact_combat_summary(summary)

        self.assertEqual(summary["facts"]["totals"]["damage_done"], 1200)
        self.assertEqual(compact["group_leader"]["name"], "Wuya")
        self.assertEqual(compact["top_damage"][0]["name"], "Palei")
        self.assertEqual(compact["top_healing"][0]["name"], "Dailea")
        self.assertEqual(compact["danger"][0]["name"], "Palei")
        self.assertTrue(wow_common.combat_summary_has_fight(summary))

    def test_combat_summary_filters_recovery_only(self):
        facts = {"totals": {"damage_done": 0, "damage_taken": 0, "healing_done": 137, "kills": 0, "deaths": 0}}
        row = [
            "43",
            "2026-05-19 21:40:45",
            "",
            "556",
            "Wuya",
            "189",
            "796",
            "796",
            "5097",
            "0",
            "0",
            base64.b64encode(json.dumps(facts).encode()).decode(),
            "",
        ]

        self.assertFalse(wow_common.combat_summary_has_fight(wow_common.combat_summary_row_to_dict(row)))

    def test_quest_text_and_progress_helpers(self):
        self.assertEqual(wow_common.clean_wow_text("你好$N$B去找$r战士"), "你好{玩家}\n去找{种族}战士")
        quest = {
            "objectives": {
                "creatures_or_gameobjects": [{"slot": 1, "required": 2}],
                "items": [{"slot": 2, "required": 4}],
            }
        }
        status = {"mob_counts": [2, 0, 0, 0], "item_counts": [0, 3, 0, 0, 0, 0]}

        updated = wow_common.apply_quest_progress(quest, status)

        self.assertTrue(updated["objectives"]["creatures_or_gameobjects"][0]["complete"])
        self.assertFalse(updated["objectives"]["items"][0]["complete"])
        self.assertEqual(updated["objectives"]["items"][0]["progress"], 3)

    def test_conversation_for_party(self):
        event = {"channel": "party", "speaker_guid": 10, "bot_guid": None, "group_leader_guid": 99}
        self.assertEqual(hermes_relay.conversation_for(event), "wow-party-99")

    def test_conversation_for_whisper(self):
        event = {"channel": "whisper", "speaker_guid": 10, "bot_guid": 20, "group_leader_guid": 99}
        self.assertEqual(hermes_relay.conversation_for(event), "wow-whisper-10-20")

    def test_conversation_for_solo(self):
        event = {"channel": "say", "speaker_guid": 10, "bot_guid": None, "group_leader_guid": None}
        self.assertEqual(hermes_relay.conversation_for(event), "wow-player-10")

    def test_clean_playerbot_command_strips_prefix(self):
        self.assertEqual(server.clean_playerbot_command_line(".playerbots bot remove Gessa"), "remove Gessa")
        self.assertEqual(server.clean_playerbot_command_line(".bot list"), "list")

    def test_clean_playerbot_command_rejects_controls(self):
        with self.assertRaises(ValueError):
            server.clean_playerbot_command_line("list\nremove Gessa")

    def test_chinese_class_race_gender_normalization(self):
        self.assertEqual(server.normalize_class_hint("牧师", "healer"), "priest")
        self.assertEqual(server.normalize_class_hint("小德", "healer"), "druid")
        self.assertEqual(server.normalize_race_hint("人类"), 1)
        self.assertEqual(server.normalize_race_hint("暗夜精灵"), 4)
        self.assertEqual(server.normalize_gender("女"), "female")
        self.assertEqual(server.gender_id("female"), 1)

    def test_select_bot_names_group_and_role(self):
        event = {
            "context": {
                "bots": [
                    {"name": "Tanky", "role": "tank", "bot_kind": "group"},
                    {"name": "Priesty", "role": "healer", "bot_kind": "owned"},
                    {"name": "Worldy", "role": "dps", "bot_kind": "random_world"},
                ]
            }
        }
        self.assertEqual(server.select_bot_names(event, "group"), ["Tanky", "Priesty"])
        self.assertEqual(server.select_bot_names(event, "healers"), ["Priesty"])
        self.assertEqual(server.select_bot_names(event, "tanks"), ["Tanky"])

    def test_select_bot_names_tank_fallback(self):
        event = {
            "context": {
                "bots": [
                    {"name": "Magey", "role": "dps", "bot_kind": "group"},
                    {"name": "Priesty", "role": "healer", "bot_kind": "owned"},
                ]
            }
        }
        self.assertEqual(
            server.select_bot_names(event, "", default_selector="tanks", fallback_first=True),
            ["Magey"],
        )

    def test_select_mage_bot_name(self):
        event = {
            "context": {
                "bots": [
                    {"name": "Tanky", "class": 1, "bot_kind": "group"},
                    {"name": "Magey", "class": 8, "bot_kind": "owned"},
                ]
            }
        }
        self.assertEqual(server.select_mage_bot_name(event), "Magey")
        self.assertEqual(server.select_mage_bot_name(event, "Magey"), "Magey")

    def test_bot_profile_reads_spec_and_strategy_context(self):
        bot = {
            "guid": 2,
            "name": "黑化观音",
            "class": 5,
            "race": 1,
            "level": 27,
            "role": "healer",
            "bot_kind": "owned",
            "spec_name": "holy",
            "activeTalentGroup": 0,
            "talentGroupsCount": 2,
            "ai_state": "combat",
            "active_strategy_text": "Strategies: holy heal, buff, cure",
        }
        profile = server.bot_profile_from_context(bot)

        self.assertEqual(profile["class"]["zh"], "牧师")
        self.assertEqual(profile["spec"]["zh"], "神圣")
        self.assertEqual(profile["active_strategies"], ["holy heal", "buff", "cure"])
        self.assertIn("shadow", profile["supported_roles"])

    def test_bot_profile_enriches_talent_groups_from_character_db(self):
        bot = {
            "guid": 541,
            "name": "令狐冲",
            "class": 1,
            "race": 4,
            "level": 30,
            "role": "tank",
            "bot_kind": "owned",
            "spec_name": "prot",
            "active_strategy_text": "Strategies: tank assist",
        }
        row = {
            "guid": 541,
            "name": "令狐冲",
            "race": 4,
            "class": 1,
            "level": 30,
            "online": True,
            "active_talent_group": 0,
            "talent_groups_count": 1,
        }

        with patch("server.fetch_character_identity", return_value=row):
            profile = server.bot_profile_from_context(bot)

        self.assertEqual(profile["talent_groups_count"], 1)
        self.assertTrue(profile["talent_groups"]["known"])
        self.assertFalse(profile["talent_groups"]["has_second"])
        self.assertIn("当前只有1套天赋", profile["talent_groups"]["second_unavailable_reason"])
        self.assertIn("40级", profile["talent_groups"]["second_unavailable_reason"])

    def test_talent_group_profile_marks_existing_second_spec(self):
        profile = server.talent_group_profile(level=30, active_talent_group=1, talent_groups_count=2)

        self.assertTrue(profile["known"])
        self.assertTrue(profile["has_second"])
        self.assertEqual(profile["active_label"], 2)
        self.assertEqual(profile["second_unavailable_reason"], "")

    def test_talent_group_profile_distinguishes_unknown_count(self):
        profile = server.talent_group_profile(level=30, active_talent_group=0, talent_groups_count=0)

        self.assertFalse(profile["known"])
        self.assertFalse(profile["has_second"])
        self.assertEqual(profile["second_unavailable_reason"], "当前没有拿到天赋页数量")

    def test_strategy_and_role_support_are_class_aware(self):
        self.assertTrue(server.strategy_supported_for_class(server.CLASS_IDS["priest"], "+shadow"))
        self.assertFalse(server.strategy_supported_for_class(server.CLASS_IDS["priest"], "+bear"))

        priest_plan = server.role_plan_for_class(server.CLASS_IDS["priest"], "暗牧")
        druid_plan = server.role_plan_for_class(server.CLASS_IDS["druid"], "熊坦")

        self.assertEqual(priest_plan["key"], "shadow")
        self.assertIn("+shadow", priest_plan["changes"])
        self.assertEqual(druid_plan["key"], "tank")
        self.assertIn("+bear", druid_plan["changes"])

    def test_admin_audited_playerbot_command_prefixes(self):
        self.assertTrue(server.command_requires_admin_audit("reload"))
        self.assertTrue(server.command_requires_admin_audit("initself=epic"))
        self.assertFalse(server.command_requires_admin_audit("remove 黑化观音"))

    def test_admin_permission_requires_speaker_gm_level_and_optional_name(self):
        class FakeAuthDb:
            def scalar_int(self, sql):
                return 3 if "`id` = 1" in sql else 0

        with (
            patch("server.auth_db", return_value=FakeAuthDb()),
            patch.dict(
                "os.environ",
                {"PLAYERBOT_MCP_ADMIN_GM_LEVEL": "3", "PLAYERBOT_MCP_ADMIN_PLAYER_NAMES": "Wuya"},
                clear=False,
            ),
        ):
            self.assertTrue(server.event_speaker_is_admin({"speaker_account": 1, "speaker_name": "Wuya"}))
            self.assertFalse(server.event_speaker_is_admin({"speaker_account": 1, "speaker_name": "瓦小狸"}))
            self.assertFalse(server.event_speaker_is_admin({"speaker_account": 2, "speaker_name": "Wuya"}))

    def test_consumable_stack_normalization(self):
        self.assertEqual(server.normalize_consumable_stacks(-1, 1), 0)
        self.assertEqual(server.normalize_consumable_stacks(3, 1), 3)
        self.assertEqual(server.normalize_consumable_stacks(9, 1), 5)
        self.assertTrue(server.is_safe_player_name("Wuya"))
        self.assertFalse(server.is_safe_player_name("bad;name"))

    def test_static_place_resolution_and_route_status(self):
        candidates = server.static_place_candidates("血色", limit=3)
        self.assertTrue(candidates)
        self.assertEqual(candidates[0]["key"], "scarlet_monastery")

        event = {
            "speaker_guid": 1,
            "speaker_name": "Wuya",
            "context": {
                "speaker": {"guid": 1, "name": "Wuya", "map_id": 0, "location": {"map_id": 0, "x": 0, "y": 0}},
                "group_members": [
                    {"guid": 1, "name": "Wuya", "map_id": 0, "alive": True},
                    {"guid": 2, "name": "Nearbot", "map_id": 0, "distance": 30, "alive": True, "is_bot": True},
                    {"guid": 3, "name": "Farbot", "map_id": 0, "distance": 300, "alive": True, "is_bot": True},
                    {"guid": 4, "name": "Deadbot", "map_id": 0, "distance": 10, "alive": False, "is_bot": True},
                    {"guid": 5, "name": "Othermap", "map_id": 1, "distance": None, "alive": True},
                ],
            },
        }
        status = server.group_travel_status_from_event(event)
        self.assertEqual(status["phase"], "needs_regroup")
        self.assertEqual({item["name"]: item["state"] for item in status["dropped"]}, {
            "Farbot": "lagging",
            "Deadbot": "dead",
            "Othermap": "different_map",
        })

    def test_quest_guide_entry_builds_next_step(self):
        quest = {
            "id": 42,
            "title": "清理狗头人",
            "details": "矿洞里出了麻烦。",
            "objectives_text": "杀死 2 个狗头人。",
            "objectives": {
                "creatures_or_gameobjects": [
                    {"slot": 1, "name": "狗头人", "required": 2, "progress": 1, "complete": False}
                ],
                "items": [],
            },
            "player_status": {"status": 3, "status_label": "进行中"},
            "enders": [{"name": "治安官"}],
        }
        guide = wow_common.build_quest_guide_entry(quest, "guide")
        self.assertEqual(guide["title"], "清理狗头人")
        self.assertIn("狗头人: 1/2", guide["progress"][0])
        self.assertIn("先完成未完成目标", guide["next_step"])

    def test_auction_recommendation_is_advice_only(self):
        item = {
            "item_id": 2589,
            "name": "亚麻布",
            "class": 7,
            "quality": 1,
            "bonding": 0,
            "count": 20,
            "sell_price": wow_common.money_dict(13),
        }
        auctions = [
            {"unit_buyout": wow_common.money_dict(120), "unit_bid": wow_common.money_dict(80)},
            {"unit_buyout": wow_common.money_dict(100), "unit_bid": wow_common.money_dict(75)},
        ]
        estimate = wow_common.estimate_item_value_from_auctions(item, auctions)
        recommendation = wow_common.auction_sale_recommendation(item, estimate, policy="safe")
        self.assertEqual(estimate["source"], "auction_median_buyout")
        self.assertEqual(recommendation["action"], "auction")
        self.assertGreater(recommendation["total_value"]["copper"], recommendation["vendor_total"]["copper"])

    def test_fetch_auction_rows_empty_item_filter_does_not_scan_all(self):
        class FailingDb:
            def query_rows(self, sql):
                raise AssertionError("query should not run for an empty explicit item filter")

        self.assertEqual(wow_common.fetch_auction_rows(FailingDb(), FailingDb(), item_ids=set()), [])

    def test_reply_bot_falls_back_from_stale_requested_bot(self):
        event = {
            "context": {
                "bots": [
                    {"name": "Jeshas", "role": "dps", "bot_kind": "owned"},
                ]
            }
        }
        self.assertEqual(
            server.resolve_reply_bot_name(event, "party", "Gessa"),
            ("Jeshas", "requested_offline_fallback"),
        )

    def test_reply_bot_preserves_unverified_new_bot(self):
        event = {"context": {"bots": []}}
        self.assertEqual(
            server.resolve_reply_bot_name(event, "say", "Jislenk"),
            ("Jislenk", "requested_unverified"),
        )

    def test_reply_bot_prefers_anchor_for_party_default(self):
        event = {
            "context": {
                "bots": [
                    {"name": "Jeshas", "role": "healer", "bot_kind": "owned"},
                    {"name": "瓦小狸", "role": "dps", "bot_kind": "anchor_world"},
                ]
            }
        }
        with patch.dict("os.environ", {"PLAYERBOT_AGENT_ANCHOR_BOT_NAME": "瓦小狸"}, clear=False):
            self.assertEqual(
                server.resolve_reply_bot_name(event, "party", ""),
                ("瓦小狸", "anchor_default"),
            )
            self.assertEqual(
                server.resolve_reply_bot_name(event, "party", "Gessa"),
                ("瓦小狸", "anchor_requested_offline_fallback"),
            )

    def test_relay_selects_fallback_reply_bot_from_event(self):
        event = {"context": {"bots": [{"name": "Jeshas", "bot_kind": "owned"}]}}
        results = [{"action_type": "reply", "status": "error", "bot_name": "Gessa", "error": "bot is not online"}]
        self.assertEqual(
            hermes_relay.select_reply_bot(event, results, requested="Gessa", channel="party", avoid={"Gessa"}),
            "Jeshas",
        )

    def test_relay_selects_reply_bot_from_roster_result(self):
        event = {"context": {"bots": []}}
        results = [{"result": "Bot roster: +Jislenk Warlock, +Jeshas Priest, -中年狼 Priest"}]
        self.assertEqual(hermes_relay.roster_online_names(results), ["Jislenk", "Jeshas"])
        self.assertEqual(hermes_relay.select_reply_bot(event, results, channel="say"), "Jislenk")

    def test_relay_prefers_anchor_reply_bot(self):
        event = {
            "context": {
                "bots": [
                    {"name": "Jeshas", "bot_kind": "owned"},
                    {"name": "瓦小狸", "bot_kind": "anchor_world"},
                ]
            }
        }
        with patch.dict("os.environ", {"PLAYERBOT_AGENT_ANCHOR_BOT_NAME": "瓦小狸"}, clear=False):
            self.assertEqual(hermes_relay.select_reply_bot(event, [], channel="party"), "瓦小狸")

    def test_relay_visible_reply_does_not_truncate(self):
        event = {
            "id": 123,
            "context": {"bots": [{"name": "瓦小狸", "bot_kind": "anchor_world"}]},
        }
        long_text = "这是完整回复，" + ("继续说明" * 80)
        captured = {}

        def fake_enqueue_action(db, **kwargs):
            captured.update(kwargs)
            return {"ok": True, "action_id": 456}

        relay = object.__new__(hermes_relay.Relay)
        relay.db = object()
        with (
            patch.dict("os.environ", {"PLAYERBOT_AGENT_ANCHOR_BOT_NAME": "瓦小狸"}, clear=False),
            patch("hermes_relay.enqueue_action", side_effect=fake_enqueue_action),
            patch("hermes_relay.log_event"),
        ):
            relay.enqueue_visible_reply(event, long_text, channel="party")

        self.assertGreater(len(long_text), 240)
        self.assertEqual(captured["text"], long_text)

    def test_relay_skips_unaddressed_say_only(self):
        with patch.dict(
            "os.environ",
            {
                "PLAYERBOT_AGENT_ANCHOR_BOT_NAME": "瓦小狸",
                "PLAYERBOT_AGENT_ANCHOR_ALIASES": "小狸",
                "PLAYERBOT_HERMES_IGNORE_UNADDRESSED_SAY": "1",
            },
            clear=False,
        ):
            self.assertEqual(
                hermes_relay.should_skip_event({"channel": "say", "message": "天气真好"}),
                (True, "unaddressed_say"),
            )
            self.assertEqual(
                hermes_relay.should_skip_event({"channel": "say", "message": "小狸，现在谁在线"}),
                (False, ""),
            )
            self.assertEqual(
                hermes_relay.should_skip_event({"channel": "party", "message": "天气真好"}),
                (False, ""),
            )

    def test_relay_skips_quest_progress_noise(self):
        with patch.dict(
            "os.environ",
            {
                "PLAYERBOT_AGENT_ANCHOR_BOT_NAME": "瓦小狸",
                "PLAYERBOT_HERMES_IGNORE_QUEST_PROGRESS_CHAT": "1",
            },
            clear=False,
        ):
            self.assertTrue(hermes_relay.is_quest_progress_noise("任务进度: [狗头人的蜡烛] 蜡烛 3/8"))
            self.assertTrue(hermes_relay.is_quest_progress_noise("[狗头人的蜡烛]: 蜡烛 3/8"))
            self.assertFalse(hermes_relay.is_quest_progress_noise("小狸，任务进度怎么样？"))
            self.assertEqual(
                hermes_relay.should_skip_event({"channel": "party", "message": "Questie: [Kobold Candles] 3/8"}),
                (True, "quest_progress_noise"),
            )

    def test_relay_generic_confirmation_for_command(self):
        results = [{"action_type": "command", "status": "done", "command": "attack"}]
        self.assertTrue(hermes_relay.needs_generic_confirmation(results))
        self.assertEqual(hermes_relay.generic_confirmation_text(results), "已执行：attack。")

    def test_enqueue_action_rejects_processed_event(self):
        class FakeDb:
            def scalar_int(self, sql):
                self.sql = sql
                return 1

        event = {"id": 380, "speaker_guid": 556, "speaker_name": "Wuya"}
        with patch("wow_common.log_event") as log_event:
            result = wow_common.enqueue_action(
                FakeDb(),
                event=event,
                action_type="reply",
                bot_name="瓦小狸",
                channel="party",
                text="收到",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "stale_event_id")
        log_event.assert_called_once()

    def test_relay_parse_consumable_requests(self):
        self.assertEqual(hermes_relay.parse_consumable_request("给点水"), {"water_stacks": 1, "food_stacks": 0})
        self.assertEqual(hermes_relay.parse_consumable_request("帮做点水"), {"water_stacks": 1, "food_stacks": 0})
        self.assertEqual(hermes_relay.parse_consumable_request("帮做点吃的"), {"water_stacks": 0, "food_stacks": 1})
        self.assertEqual(hermes_relay.parse_consumable_request("吃喝"), {"water_stacks": 1, "food_stacks": 1})
        self.assertEqual(hermes_relay.parse_consumable_request("小狸吃喝"), {"water_stacks": 1, "food_stacks": 1})
        self.assertEqual(hermes_relay.parse_consumable_request("一组水"), {"water_stacks": 1, "food_stacks": 0})
        self.assertEqual(hermes_relay.parse_consumable_request("两组面包"), {"water_stacks": 0, "food_stacks": 2})
        self.assertEqual(hermes_relay.parse_consumable_request("来两组水和面包"), {"water_stacks": 2, "food_stacks": 2})
        self.assertIsNone(hermes_relay.parse_consumable_request("不用给水"))

    def test_relay_detects_offline_group_bots(self):
        event = {
            "context": {
                "group_members": [
                    {"name": "Wuya", "online": True, "is_bot": False},
                    {"name": "中年狼", "online": False, "offline_in_group": True, "is_bot": True, "bot_kind": "group_offline"},
                    {"name": "真实队友", "online": False, "offline_in_group": True, "is_bot": False},
                    {"name": "乌鸦", "offline_in_group": True, "bot_kind": "group_offline"},
                ]
            }
        }
        self.assertTrue(hermes_relay.parse_team_online_request("把队友叫回来"))
        self.assertTrue(hermes_relay.parse_team_online_request("让小队的人上线"))
        self.assertEqual(hermes_relay.offline_group_bot_names(event), ["中年狼", "乌鸦"])

    def test_relay_selects_mage_from_target_or_group_context(self):
        event = {
            "context": {
                "target_bot_context": {"name": "冰箱没关", "class": 8, "bot_kind": "owned", "alive": True},
                "bots": [{"name": "八戒", "class": 2, "bot_kind": "owned"}],
                "group_members": [{"name": "乌鸦", "class": 8, "bot_kind": "owned", "alive": True}],
            }
        }
        self.assertEqual(hermes_relay.select_mage_bot_name(event), "冰箱没关")


if __name__ == "__main__":
    unittest.main()
