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


def b64_text(value):
    return base64.b64encode(str(value).encode("utf-8")).decode("ascii")


def b64_json(value):
    return base64.b64encode(json.dumps(value, ensure_ascii=False).encode("utf-8")).decode("ascii")


def event_row(
    *,
    event_id=101,
    created_at="2026-05-25 19:00:00",
    channel="whisper",
    speaker_guid=556,
    speaker_account=1,
    speaker_name="Wuya",
    target_guid=202,
    target_name="瓦小狸",
    bot_guid=202,
    bot_name="瓦小狸",
    group_leader_guid=556,
    meta=None,
    message="你好",
):
    return [
        str(event_id),
        created_at,
        channel,
        str(speaker_guid),
        str(speaker_account),
        speaker_name,
        str(target_guid) if target_guid is not None else None,
        target_name,
        str(bot_guid) if bot_guid is not None else None,
        bot_name,
        str(group_leader_guid) if group_leader_guid is not None else None,
        b64_json(meta or {}),
        b64_text(message),
    ]


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

    def test_fetch_events_can_scope_and_query_newest_first(self):
        class FakeDb:
            sql = ""

            def query_rows(self, sql):
                self.sql = sql
                return []

        db = FakeDb()
        events = wow_common.fetch_events(
            db,
            after_id=7,
            max_id=20,
            limit=5,
            speaker_guid=556,
            group_leader_guid=556,
            channel="whisper",
            newest_first=True,
        )

        self.assertEqual(events, [])
        self.assertIn("`id` > 7", db.sql)
        self.assertIn("`id` <= 20", db.sql)
        self.assertIn("`speaker_guid` = 556", db.sql)
        self.assertIn("`group_leader_guid` = 556", db.sql)
        self.assertIn("`channel` = 'whisper'", db.sql)
        self.assertIn("ORDER BY `id` DESC LIMIT 5", db.sql)

    def test_recent_events_limit_requires_anchor_for_wide_reads(self):
        self.assertEqual(server.recent_events_limit(10, anchored=True), 10)
        self.assertEqual(server.recent_events_limit(10, anchored=False), 3)
        self.assertEqual(server.recent_events_limit(0, anchored=False), 1)

    def test_compact_event_omits_full_context_by_default(self):
        meta = {
            "speaker": {
                "guid": 556,
                "name": "Wuya",
                "level": 70,
                "combat": False,
                "location": {"map_name": "艾泽拉斯", "zone_name": "荆棘谷", "area_name": "藏宝海湾"},
            },
            "environment": {
                "location": {"map_name": "艾泽拉斯", "zone_name": "荆棘谷", "area_name": "藏宝海湾"}
            },
            "group_members": [
                {"guid": 556, "name": "Wuya", "online": True},
                {"guid": 202, "name": "瓦小狸", "online": True, "bot_kind": "anchor_world"},
            ],
            "bots": [{"guid": 202, "name": "瓦小狸", "bot_kind": "anchor_world"}],
            "quest_log": [{"title": "很长的任务上下文", "body": "x" * 5000}],
        }
        event = wow_common.event_row_to_dict(event_row(meta=meta, message="x" * 500))

        compact = wow_common.compact_event(event)

        self.assertNotIn("context", compact)
        self.assertTrue(compact["context_available"])
        self.assertTrue(compact["message_truncated"])
        self.assertEqual(compact["context_summary"]["location"]["zone_name"], "荆棘谷")
        self.assertEqual(compact["context_summary"]["party"]["members"], 2)
        self.assertIn("瓦小狸", compact["context_summary"]["party"]["bots"])
        self.assertIn("quest_log", compact["context_summary"]["available_context_keys"])

    def test_compact_event_include_context_is_still_whitelisted(self):
        meta = {
            "speaker": {"guid": 556, "name": "Wuya", "location": {"zone_name": "荆棘谷"}},
            "group_members": [{"name": f"Bot{i}", "bot_kind": "group"} for i in range(12)],
            "inventory": {"items": [{"name": "huge", "text": "x" * 5000}]},
        }
        event = wow_common.event_row_to_dict(event_row(meta=meta))

        compact = wow_common.compact_event(event, include_context=True)

        self.assertTrue(compact["context_is_compacted"])
        self.assertIn("context", compact)
        self.assertIn("group_members", compact["context"])
        self.assertEqual(compact["context"]["group_members"][-1]["omitted"], 4)
        self.assertNotIn("inventory", compact["context"])
        self.assertIn("inventory", compact["context"]["omitted_context_keys"])
        self.assertLess(len(json.dumps(compact["context"], ensure_ascii=False)), 3000)

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

    def test_hermes_client_can_disable_response_store(self):
        captured = {}

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"id":"resp_test","status":"completed","output":[]}'

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        client = hermes_relay.HermesClient(
            "http://127.0.0.1:8642/v1/responses",
            api_key="",
            model="hermes-agent",
            timeout=1,
            store=False,
        )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client.send_event(conversation="wow-test", event={"id": 1}, action_results=[])

        self.assertFalse(captured["payload"]["store"])

    def test_hermes_client_can_enable_response_store_with_truncation(self):
        captured = {}

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"id":"resp_test","status":"completed","output":[]}'

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        client = hermes_relay.HermesClient(
            "http://127.0.0.1:8642/v1/responses",
            api_key="",
            model="hermes-agent",
            timeout=1,
            store=True,
            truncation_auto=True,
        )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client.send_event(conversation="wow-test", event={"id": 1}, action_results=[])

        self.assertTrue(captured["payload"]["store"])
        self.assertEqual(captured["payload"]["conversation"], "wow-test")
        self.assertEqual(captured["payload"]["truncation"], "auto")

    def test_hermes_client_minimal_payload_omits_context_and_recent_actions(self):
        captured = {}

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"id":"resp_test","status":"completed","output":[]}'

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        client = hermes_relay.HermesClient(
            "http://127.0.0.1:8642/v1/responses",
            api_key="",
            model="hermes-agent",
            timeout=1,
            store=False,
            payload_mode="minimal",
            include_recent_actions=False,
        )
        event = {
            "id": 11,
            "channel": "whisper",
            "speaker_guid": 556,
            "speaker_name": "Wuya",
            "bot_guid": 20,
            "bot_name": "瓦小狸",
            "message": "我在哪里",
            "context": {"group_members": [{"name": "中年狼"}], "combat": {"in_combat": True}},
        }

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client.send_event(conversation="wow-test", event=event, action_results=[{"id": 1}])

        envelope = json.loads(captured["payload"]["input"].split("\n", 1)[1])
        self.assertEqual(envelope["context_mode"], "on_demand")
        self.assertEqual(envelope["session"]["conversation"], "wow-test")
        self.assertFalse(envelope["session"]["store"])
        self.assertNotIn("recent_action_results", envelope)
        self.assertNotIn("context", envelope["event"])
        self.assertTrue(envelope["event"]["context_available"])
        self.assertEqual(envelope["event"]["message"], "我在哪里")

    def test_hermes_client_can_include_short_recent_chat_context(self):
        captured = {}

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"id":"resp_test","status":"completed","output":[]}'

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        client = hermes_relay.HermesClient(
            "http://127.0.0.1:8642/v1/responses",
            api_key="",
            model="hermes-agent",
            timeout=1,
            store=False,
        )
        recent = [{"event_id": 10, "speaker": "Wuya", "message": "坐哪艘船去荆棘谷?"}]

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client.send_event(conversation="wow-test", event={"id": 11}, action_results=[], recent_chat_context=recent)

        envelope = json.loads(captured["payload"]["input"].split("\n", 1)[1])
        self.assertEqual(envelope["recent_chat_context"], recent)

    def test_hermes_client_full_payload_can_include_recent_actions(self):
        captured = {}

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"id":"resp_test","status":"completed","output":[]}'

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        client = hermes_relay.HermesClient(
            "http://127.0.0.1:8642/v1/responses",
            api_key="",
            model="hermes-agent",
            timeout=1,
            payload_mode="full",
            include_recent_actions=True,
        )
        event = {"id": 12, "message": "debug", "context": {"bots": [{"name": "瓦小狸"}]}}

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client.send_event(conversation="wow-test", event=event, action_results=[{"id": 2}])

        envelope = json.loads(captured["payload"]["input"].split("\n", 1)[1])
        self.assertEqual(envelope["context_mode"], "embedded")
        self.assertIn("context", envelope["event"])
        self.assertEqual(envelope["recent_action_results"], [{"id": 2}])

    def test_recent_chat_context_keeps_same_conversation_and_visible_reply(self):
        current = {
            "id": 30,
            "channel": "whisper",
            "speaker_guid": 556,
            "speaker_name": "Wuya",
            "bot_guid": 202,
            "bot_name": "瓦小狸",
            "message": "其他几个去哪里?",
        }
        events = [
            {
                "id": 28,
                "channel": "whisper",
                "speaker_guid": 556,
                "speaker_name": "Wuya",
                "bot_guid": 202,
                "bot_name": "瓦小狸",
                "message": "坐哪艘船去荆棘谷?",
            },
            {
                "id": 27,
                "channel": "whisper",
                "speaker_guid": 556,
                "speaker_name": "Wuya",
                "bot_guid": 999,
                "bot_name": "别的机器人",
                "message": "这句不该混进来",
            },
        ]

        def fake_fetch_action_results(_db, event_id, limit=8):
            if event_id == 28:
                return [
                    {
                        "status": "done",
                        "action_type": "reply",
                        "channel": "whisper",
                        "bot_name": "瓦小狸",
                        "text": "最南边码头去藏宝海湾。",
                    }
                ]
            return []

        with (
            patch("hermes_relay.fetch_events", return_value=events),
            patch("hermes_relay.fetch_action_results", side_effect=fake_fetch_action_results),
        ):
            context = hermes_relay.recent_chat_context_for_event(object(), current, limit=4)

        self.assertEqual(len(context), 1)
        self.assertEqual(context[0]["message"], "坐哪艘船去荆棘谷?")
        self.assertEqual(context[0]["visible_reply"]["text"], "最南边码头去藏宝海湾。")

    def test_recent_chat_context_is_disabled_by_default(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(hermes_relay.recent_chat_limit(), 0)

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

    def test_conversation_for_whisper_isolated_by_player_and_bot(self):
        first = {"channel": "whisper", "speaker_guid": 10, "bot_guid": 20}
        other_player = {"channel": "whisper", "speaker_guid": 11, "bot_guid": 20}
        other_bot = {"channel": "whisper", "speaker_guid": 10, "bot_guid": 21}

        self.assertNotEqual(hermes_relay.conversation_for(first), hermes_relay.conversation_for(other_player))
        self.assertNotEqual(hermes_relay.conversation_for(first), hermes_relay.conversation_for(other_bot))

    def test_conversation_for_solo(self):
        event = {"channel": "say", "speaker_guid": 10, "bot_guid": None, "group_leader_guid": None}
        self.assertEqual(hermes_relay.conversation_for(event), "wow-player-10")

    def test_conversation_for_anchor_say_is_speaker_scoped(self):
        event = {
            "channel": "say",
            "speaker_guid": 10,
            "bot_guid": None,
            "group_leader_guid": 99,
            "message": "瓦小狸，帮我看看",
        }
        self.assertEqual(hermes_relay.conversation_for(event), "wow-anchor-10")

    def test_conversation_for_adds_epoch_when_configured(self):
        event = {"channel": "whisper", "speaker_guid": 10, "bot_guid": 20, "group_leader_guid": 99}
        with patch.dict("os.environ", {"PLAYERBOT_HERMES_CONVERSATION_EPOCH": "gjzn-20260523"}):
            self.assertEqual(hermes_relay.conversation_for(event), "wow-whisper-10-20-gjzn-20260523")

    def test_speaker_is_allowed_by_name_guid_or_account(self):
        event = {"speaker_name": "Wuya", "speaker_guid": 556, "speaker_account": 1}
        self.assertTrue(hermes_relay.speaker_is_allowed(event))
        with patch.dict(
            "os.environ",
            {
                "PLAYERBOT_HERMES_ALLOWED_PLAYER_NAMES": "",
                "PLAYERBOT_HERMES_ALLOWED_PLAYER_GUIDS": "556",
                "PLAYERBOT_HERMES_ALLOWED_ACCOUNTS": "",
                "PLAYERBOT_HERMES_ALLOW_ALL_PLAYERS": "0",
            },
        ):
            self.assertTrue(hermes_relay.speaker_is_allowed(event))
        with patch.dict(
            "os.environ",
            {
                "PLAYERBOT_HERMES_ALLOWED_PLAYER_NAMES": "",
                "PLAYERBOT_HERMES_ALLOWED_PLAYER_GUIDS": "",
                "PLAYERBOT_HERMES_ALLOWED_ACCOUNTS": "1",
                "PLAYERBOT_HERMES_ALLOW_ALL_PLAYERS": "0",
            },
        ):
            self.assertTrue(hermes_relay.speaker_is_allowed(event))
        with patch.dict(
            "os.environ",
            {
                "PLAYERBOT_HERMES_ALLOWED_PLAYER_NAMES": "Other",
                "PLAYERBOT_HERMES_ALLOWED_PLAYER_GUIDS": "",
                "PLAYERBOT_HERMES_ALLOWED_ACCOUNTS": "",
                "PLAYERBOT_HERMES_ALLOW_ALL_PLAYERS": "0",
            },
        ):
            self.assertFalse(hermes_relay.speaker_is_allowed(event))

    def test_unauthorized_event_replies_locally_without_calling_hermes(self):
        class FakeDb:
            def scalar_int(self, sql):
                return 0

        class FakeClient:
            def send_event(self, **kwargs):
                raise AssertionError("unauthorized event should not reach Hermes")

        event = {
            "id": 88,
            "channel": "whisper",
            "speaker_guid": 999,
            "speaker_account": 9,
            "speaker_name": "Stranger",
            "bot_guid": 20,
            "bot_name": "瓦小狸",
            "group_leader_guid": 999,
            "message": "瓦小狸，帮我叫队友上线",
        }

        with tempfile.TemporaryDirectory() as tmp:
            relay = hermes_relay.Relay(FakeDb(), FakeClient(), pathlib.Path(tmp) / "state.json")
            with (
                patch.dict(
                    "os.environ",
                    {
                        "PLAYERBOT_HERMES_ALLOWED_PLAYER_NAMES": "Wuya",
                        "PLAYERBOT_HERMES_ALLOWED_PLAYER_GUIDS": "",
                        "PLAYERBOT_HERMES_ALLOWED_ACCOUNTS": "",
                        "PLAYERBOT_HERMES_ALLOW_ALL_PLAYERS": "0",
                        "PLAYERBOT_HERMES_LISTEN_SCOPE": "group",
                    },
                ),
                patch("hermes_relay.log_event") as log_event,
                patch("hermes_relay.enqueue_action", return_value={"action_id": 77, "deduped": False}) as enqueue,
            ):
                relay.handle_event(event)

            enqueue.assert_called_once()
            kwargs = enqueue.call_args.kwargs
            self.assertEqual(kwargs["action_type"], "reply")
            self.assertEqual(kwargs["bot_name"], "瓦小狸")
            self.assertEqual(kwargs["channel"], "whisper")
            self.assertIn("欢迎来到树人魔兽", kwargs["text"])
            self.assertIn("开启白名单", kwargs["text"])
            self.assertEqual(log_event.call_args_list[0].args[0], "relay_event_unauthorized")

    def test_simple_social_reply_text(self):
        self.assertEqual(hermes_relay.simple_social_reply_text("晚上好"), "晚上好，我在。")
        self.assertEqual(hermes_relay.simple_social_reply_text("小狸，在吗？"), "我在。")
        self.assertEqual(hermes_relay.simple_social_reply_text("在不？"), "我在。")
        self.assertEqual(hermes_relay.simple_social_reply_text("瓦小狸谢谢"), "不客气。")
        self.assertEqual(hermes_relay.simple_social_reply_text("bye"), "拜拜。")
        self.assertEqual(hermes_relay.simple_social_reply_text("再见"), "拜拜。")
        self.assertEqual(hermes_relay.simple_social_reply_text("c"), "c")
        self.assertIsNone(hermes_relay.simple_social_reply_text("晚上好，帮我叫队友上线"))

    def test_context_control_request(self):
        self.assertEqual(hermes_relay.context_control_request("瓦小狸，新建会话"), "reset")
        self.assertEqual(hermes_relay.context_control_request("小狸，压缩上下文"), "compress")
        self.assertEqual(hermes_relay.context_control_request("帮我新建一个会话"), "reset")
        self.assertEqual(hermes_relay.context_control_request("压缩一下对话记忆"), "compress")
        self.assertEqual(hermes_relay.context_control_request("清空上下文"), "reset")
        self.assertEqual(hermes_relay.context_control_request("帮我叫队友上线"), "")

    def test_intrinsic_command_request_only_matches_short_commands(self):
        self.assertEqual(hermes_relay.intrinsic_command_request("summon"), "summon")
        self.assertEqual(hermes_relay.intrinsic_command_request("瓦小狸，follow"), "follow")
        self.assertEqual(hermes_relay.intrinsic_command_request("release!"), "release")
        self.assertEqual(hermes_relay.intrinsic_command_request("帮我再组一个法师"), "")
        self.assertEqual(hermes_relay.intrinsic_command_request("summon a mage"), "")

    def test_group_with_bot_request_only_matches_short_invites(self):
        self.assertTrue(hermes_relay.group_with_bot_request("组我"))
        self.assertTrue(hermes_relay.group_with_bot_request("瓦小狸，拉我进组"))
        self.assertTrue(hermes_relay.group_with_bot_request("小狸组一下"))
        self.assertFalse(hermes_relay.group_with_bot_request("帮我组个法师"))
        self.assertFalse(hermes_relay.group_with_bot_request("队友上线"))

    def test_group_with_bot_fast_path_invites_current_bot(self):
        class FakeDb:
            def scalar_int(self, _sql):
                return 0

        event = {
            "id": 91,
            "channel": "whisper",
            "speaker_guid": 556,
            "speaker_account": 1,
            "speaker_name": "Wuya",
            "bot_guid": 20,
            "bot_name": "瓦小狸",
            "group_leader_guid": 556,
            "message": "组我",
            "context": {"target_bot_context": {"name": "瓦小狸", "bot_kind": "anchor_world"}},
        }
        calls = []

        def fake_enqueue_action(_db, **kwargs):
            calls.append(kwargs)
            return {"action_id": 42, "deduped": False}

        with tempfile.TemporaryDirectory() as tmp:
            relay = hermes_relay.Relay(FakeDb(), client=object(), state_path=pathlib.Path(tmp) / "state.json")
            with (
                patch("hermes_relay.enqueue_action", side_effect=fake_enqueue_action),
                patch.object(relay, "wait_for_action_result", return_value={"status": "done"}),
                patch.object(relay, "enqueue_visible_reply") as reply,
                patch("hermes_relay.log_event"),
            ):
                self.assertTrue(relay.try_handle_group_with_bot_request(event))

        self.assertEqual(calls[0]["action_type"], "invite_player")
        self.assertEqual(calls[0]["payload"]["target_player"], "瓦小狸")
        self.assertTrue(calls[0]["payload"]["fast_path"])
        reply.assert_called_once()

    def test_intrinsic_command_does_not_call_hermes_or_enqueue_actions(self):
        class FakeDb:
            def scalar_int(self, sql):
                return 0

        class FakeClient:
            def send_event(self, **kwargs):
                raise AssertionError("intrinsic command should not reach Hermes")

        event = {
            "id": 89,
            "channel": "party",
            "speaker_guid": 556,
            "speaker_account": 1,
            "speaker_name": "Wuya",
            "bot_guid": 20,
            "bot_name": "瓦小狸",
            "group_leader_guid": 556,
            "message": "summon",
            "context": {"bots": [{"name": "瓦小狸", "bot_kind": "anchor_world"}]},
        }

        with tempfile.TemporaryDirectory() as tmp:
            relay = hermes_relay.Relay(FakeDb(), FakeClient(), pathlib.Path(tmp) / "state.json")
            with (
                patch.dict(
                    "os.environ",
                    {
                        "PLAYERBOT_HERMES_ALLOWED_PLAYER_NAMES": "Wuya",
                        "PLAYERBOT_HERMES_ALLOWED_PLAYER_GUIDS": "",
                        "PLAYERBOT_HERMES_ALLOWED_ACCOUNTS": "",
                        "PLAYERBOT_HERMES_ALLOW_ALL_PLAYERS": "0",
                        "PLAYERBOT_HERMES_LISTEN_SCOPE": "group",
                    },
                ),
                patch("hermes_relay.log_event") as log_event,
                patch("hermes_relay.enqueue_action") as enqueue,
            ):
                relay.handle_event(event)

            enqueue.assert_not_called()
            self.assertEqual(log_event.call_args.args[0], "fast_intrinsic_command")

    def test_context_control_rotates_conversation_epoch(self):
        class FakeDb:
            def scalar_int(self, sql):
                return 0

        event = {
            "id": 77,
            "channel": "whisper",
            "speaker_guid": 10,
            "speaker_name": "Wuya",
            "bot_guid": 20,
            "bot_name": "瓦小狸",
            "group_leader_guid": 99,
            "message": "瓦小狸，新建会话",
        }

        with tempfile.TemporaryDirectory() as tmp:
            relay = hermes_relay.Relay(FakeDb(), object(), pathlib.Path(tmp) / "state.json")
            with (
                patch.dict("os.environ", {"PLAYERBOT_HERMES_CONVERSATION_EPOCH": "gjzn-20260523"}),
                patch("hermes_relay.enqueue_action", return_value={"action_id": 7, "deduped": False}),
                patch("hermes_relay.log_event"),
            ):
                self.assertTrue(relay.try_handle_context_control(event))

            self.assertEqual(
                relay.conversation_epochs["wow-whisper-10-20"],
                "gjzn-20260523-event77",
            )
            self.assertEqual(
                hermes_relay.conversation_for(event, relay.conversation_epochs["wow-whisper-10-20"]),
                "wow-whisper-10-20-gjzn-20260523-event77",
            )

    def test_clean_playerbot_command_strips_prefix(self):
        self.assertEqual(server.clean_playerbot_command_line(".playerbots bot remove Gessa"), "remove Gessa")
        self.assertEqual(server.clean_playerbot_command_line(".bot list"), "list")

    def test_clean_playerbot_command_rejects_controls(self):
        with self.assertRaises(ValueError):
            server.clean_playerbot_command_line("list\nremove Gessa")

    def test_whisper_event_requires_whisper_reply_channel(self):
        event = {"channel": "whisper"}
        self.assertEqual(server.reply_channel_error_for_event(event, "party"), "whisper_event_requires_whisper_reply")
        self.assertEqual(server.reply_channel_error_for_event(event, "whisper"), "")
        self.assertEqual(server.reply_channel_error_for_event({"channel": "party"}, "party"), "")

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

    def test_party_state_summary_is_progressive_and_omits_deep_bot_context(self):
        event = {
            "id": 301,
            "channel": "party",
            "speaker_guid": 556,
            "speaker_name": "Wuya",
            "message": "看看队伍",
            "context": {
                "environment": {
                    "location": {
                        "map_name": "Eastern Kingdoms",
                        "zone_name": "提瑞斯法林地",
                        "area_name": "幽暗城门口",
                        "x": 1831.2,
                        "y": 241.8,
                    },
                    "nearby_hostiles": [{"name": "血色战士"}],
                },
                "speaker": {
                    "guid": 556,
                    "name": "Wuya",
                    "class": 11,
                    "level": 30,
                    "role": "player",
                    "health_pct": 82.4,
                    "mana_pct": 61,
                    "alive": True,
                    "combat": False,
                    "location": {"zone_name": "提瑞斯法林地", "area_name": "幽暗城门口"},
                },
                "group_members": [
                    {
                        "guid": 556,
                        "name": "Wuya",
                        "class": 11,
                        "level": 30,
                        "role": "player",
                        "health_pct": 82.4,
                        "mana_pct": 61,
                        "alive": True,
                        "combat": False,
                    },
                    {
                        "guid": 202,
                        "name": "瓦小狸",
                        "is_bot": True,
                        "bot_kind": "group",
                        "class": 5,
                        "level": 30,
                        "role": "healer",
                        "spec_name": "holy",
                        "active_strategy_text": "Strategies: holy heal, buff, cure, follow, loot",
                        "active_strategies": ["holy heal", "buff", "cure", "follow", "loot"],
                        "health_pct": 100,
                        "mana_pct": 94,
                        "alive": True,
                        "combat": False,
                        "selected_target": {"name": "血色战士"},
                    },
                ],
                "bots": [
                    {
                        "guid": 202,
                        "name": "瓦小狸",
                        "bot_kind": "group",
                        "class": 5,
                        "active_strategy_text": "Strategies: holy heal, buff, cure",
                    },
                    {
                        "guid": 303,
                        "name": "令狐冲",
                        "bot_kind": "owned",
                        "class": 1,
                        "level": 30,
                        "role": "tank",
                        "health_pct": 77,
                        "mana_pct": 0,
                    },
                ],
            },
        }

        summary = server.party_state_summary(event)

        self.assertEqual(summary["summary"]["member_count"], 2)
        self.assertEqual(summary["summary"]["bot_count"], 1)
        self.assertEqual(summary["members"][1]["name"], "瓦小狸")
        self.assertEqual(summary["members"][1]["class"]["zh"], "牧师")
        self.assertEqual(summary["members"][1]["health_pct"], 100)
        self.assertEqual(summary["members"][1]["mana_pct"], 94)
        self.assertEqual(summary["available_bots_not_in_party"][0]["name"], "令狐冲")
        self.assertEqual(summary["location"], {"map_name": "Eastern Kingdoms", "zone_name": "提瑞斯法林地", "area_name": "幽暗城门口"})

        members_json = json.dumps(summary["members"], ensure_ascii=False)
        self.assertNotIn("active_strategy_text", members_json)
        self.assertNotIn("active_strategies", members_json)
        self.assertNotIn("selected_target", members_json)
        self.assertNotIn("nearby_hostiles", json.dumps(summary["members"], ensure_ascii=False))
        self.assertNotIn("nearby_hostiles", json.dumps(summary.get("location", {}), ensure_ascii=False))
        self.assertIn("wow_get_bot_profile", summary["next_suggestion"])

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

    def test_gm_command_help_expands_level_recipe(self):
        class FakeWorldDb:
            def query_rows(self, sql):
                self.sql = sql
                return [
                    [
                        "levelup",
                        "2",
                        "Syntax: .levelup [$playername] [#numberoflevels]",
                    ]
                ]

        world = FakeWorldDb()
        recipes = server.matching_gm_command_recipes("我升级过头了，怎么降级")
        commands = server.search_gm_command_help(world, "我升级过头了，怎么降级", gm_level=3)

        self.assertEqual(recipes[0]["key"], "change_level")
        self.assertIn("levelup", server.gm_command_search_terms("我升级过头了，怎么降级"))
        self.assertIn("`security` <= 3", world.sql)
        self.assertIn("`name` = 'levelup'", world.sql)
        self.assertEqual(commands[0]["command"], ".levelup")
        self.assertTrue(commands[0]["can_use"])

    def test_gm_command_help_expands_spell_lookup_recipe(self):
        recipes = server.matching_gm_command_recipes("怎么给你学双天赋技能")
        terms = server.gm_command_search_terms("怎么给你学双天赋技能")

        self.assertEqual(recipes[0]["key"], "lookup_and_learn_spell")
        self.assertIn("lookup spell", terms)
        self.assertIn("learn", terms)

    def test_autonomy_commands_are_typed_command_candidates(self):
        for command in ["grind", "equip upgrade", "s gray", "s vendor", "repair", "b vendor", "mail ?", "mail take *"]:
            self.assertIn(command, server.BOT_COMMANDS)

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

    def test_location_auto_uses_target_bot_for_you_pronoun(self):
        event = {
            "id": 1397,
            "speaker_guid": 556,
            "speaker_name": "Wuya",
            "target_name": "瓦小狸",
            "message": "你在哪呢?",
            "context": {
                "speaker": {
                    "guid": 556,
                    "name": "Wuya",
                    "location": {"zone_name": "Arathi Highlands", "area_name": "Refuge Pointe"},
                },
                "environment": {
                    "location": {"zone_name": "Arathi Highlands", "area_name": "Refuge Pointe"},
                },
                "target_bot_context": {
                    "guid": 202,
                    "name": "瓦小狸",
                    "is_bot": True,
                    "bot_kind": "anchor_world",
                    "location": {"zone_name": "Stormwind City", "area_name": "Stormwind City"},
                },
            },
        }

        payload = server.location_payload_fields(event)

        self.assertEqual(payload["resolved_subject"], "bot")
        self.assertEqual(payload["location"]["area_name"], "Stormwind City")
        self.assertEqual(payload["speaker"]["location"]["area_name"], "Refuge Pointe")

    def test_location_auto_uses_speaker_for_me_pronoun(self):
        event = {
            "id": 1398,
            "speaker_guid": 556,
            "speaker_name": "Wuya",
            "target_name": "瓦小狸",
            "message": "我在哪?",
            "context": {
                "speaker": {
                    "guid": 556,
                    "name": "Wuya",
                    "location": {"zone_name": "Arathi Highlands", "area_name": "Refuge Pointe"},
                },
                "target_bot_context": {
                    "guid": 202,
                    "name": "瓦小狸",
                    "is_bot": True,
                    "bot_kind": "anchor_world",
                    "location": {"zone_name": "Stormwind City", "area_name": "Stormwind City"},
                },
            },
        }

        payload = server.location_payload_fields(event)

        self.assertEqual(payload["resolved_subject"], "speaker")
        self.assertEqual(payload["location"]["area_name"], "Refuge Pointe")
        self.assertEqual(payload["target_bot"]["location"]["area_name"], "Stormwind City")

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

    def test_relay_falls_back_when_hermes_returns_text_without_reply(self):
        event = {
            "id": 124,
            "channel": "whisper",
            "bot_name": "瓦小狸",
            "target_name": "瓦小狸",
            "context": {"bots": [{"name": "瓦小狸", "bot_kind": "anchor_world"}]},
        }
        captured = {}

        def fake_enqueue_action(db, **kwargs):
            captured.update(kwargs)
            return {"ok": True, "action_id": 457}

        relay = object.__new__(hermes_relay.Relay)
        relay.db = object()
        with (
            patch("hermes_relay.enqueue_action", side_effect=fake_enqueue_action),
            patch("hermes_relay.log_event") as log_event,
        ):
            relay.ensure_visible_reply(event, [], hermes_text="瓦小狸：我查到了，先这么办。")

        self.assertEqual(captured["action_type"], "reply")
        self.assertEqual(captured["channel"], "whisper")
        self.assertEqual(captured["bot_name"], "瓦小狸")
        self.assertIn("我查到了", captured["text"])
        self.assertEqual(log_event.call_args.args[0], "fallback_reply_enqueued")
        self.assertEqual(log_event.call_args.kwargs["reason"], "hermes_final_text_without_reply")

    def test_relay_ignores_no_action_fallback_text(self):
        event = {
            "id": 125,
            "channel": "whisper",
            "bot_name": "瓦小狸",
            "context": {"bots": [{"name": "瓦小狸", "bot_kind": "anchor_world"}]},
        }
        relay = object.__new__(hermes_relay.Relay)
        relay.db = object()
        with (
            patch("hermes_relay.enqueue_action") as enqueue,
            patch("hermes_relay.log_event") as log_event,
        ):
            relay.ensure_visible_reply(event, [], hermes_text="no_action")

        enqueue.assert_not_called()
        self.assertEqual(log_event.call_args.args[0], "fallback_reply_skipped")
        self.assertEqual(log_event.call_args.kwargs["reason"], "empty_text")

    def test_successful_visible_reply_must_match_expected_channel(self):
        results = [{"action_type": "reply", "status": "done", "channel": "party"}]
        self.assertTrue(hermes_relay.successful_visible_reply(results))
        self.assertFalse(hermes_relay.successful_visible_reply(results, channel="whisper"))
        self.assertTrue(hermes_relay.successful_visible_reply(results, channel="party"))

    def test_relay_direct_scope_accepts_whisper_and_addressed_world_chat(self):
        with patch.dict(
            "os.environ",
            {
                "PLAYERBOT_AGENT_ANCHOR_BOT_NAME": "瓦小狸",
                "PLAYERBOT_AGENT_ANCHOR_ALIASES": "小狸",
                "PLAYERBOT_HERMES_LISTEN_SCOPE": "direct",
                "PLAYERBOT_HERMES_IGNORE_UNADDRESSED_SAY": "1",
            },
            clear=False,
        ):
            self.assertEqual(
                hermes_relay.should_skip_event({"channel": "whisper", "message": "帮我看看"}),
                (False, ""),
            )
            self.assertEqual(
                hermes_relay.should_skip_event({"channel": "say", "message": "小狸，现在谁在线"}),
                (False, ""),
            )
            self.assertEqual(
                hermes_relay.should_skip_event({"channel": "yell", "message": "瓦小狸，回来"}),
                (False, ""),
            )
            self.assertEqual(
                hermes_relay.should_skip_event({"channel": "party", "message": "小狸，现在谁在线"}),
                (True, "outside_listen_scope"),
            )
            self.assertEqual(
                hermes_relay.should_skip_event({"channel": "say", "message": "天气真好"}),
                (True, "outside_listen_scope"),
            )

    def test_relay_skips_unaddressed_say_when_scope_allows_world_chat(self):
        with patch.dict(
            "os.environ",
            {
                "PLAYERBOT_AGENT_ANCHOR_BOT_NAME": "瓦小狸",
                "PLAYERBOT_AGENT_ANCHOR_ALIASES": "小狸",
                "PLAYERBOT_HERMES_LISTEN_SCOPE": "all",
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

    def test_relay_team_online_fast_path_payload_contains_command_line(self):
        class FakeDb:
            def scalar_int(self, _sql):
                return 0

        event = {
            "id": 99,
            "channel": "party",
            "message": "让小队的人上线",
            "speaker_name": "Wuya",
            "context": {
                "group_members": [
                    {"name": "Wuya", "online": True, "is_bot": False},
                    {"name": "中年狼", "online": False, "offline_in_group": True, "is_bot": True},
                ]
            },
        }
        calls = []

        def fake_enqueue_action(_db, **kwargs):
            calls.append(kwargs)
            return {"action_id": len(calls)}

        with tempfile.TemporaryDirectory() as tmp:
            relay = hermes_relay.Relay(FakeDb(), client=object(), state_path=pathlib.Path(tmp) / "state.json")
            with (
                patch("hermes_relay.enqueue_action", side_effect=fake_enqueue_action),
                patch.object(
                    relay,
                    "wait_for_action_results",
                    return_value=[{"action_type": "playerbot_command", "command": "add 中年狼", "status": "done"}],
                ),
                patch.object(relay, "enqueue_visible_reply"),
            ):
                self.assertTrue(relay.try_handle_team_online_request(event))

        playerbot_commands = [call for call in calls if call.get("action_type") == "playerbot_command"]
        self.assertEqual(len(playerbot_commands), 1)
        self.assertEqual(playerbot_commands[0]["command"], "add 中年狼")
        self.assertEqual(playerbot_commands[0]["payload"]["command_line"], "add 中年狼")

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
