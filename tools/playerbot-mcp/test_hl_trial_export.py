#!/usr/bin/env python3

import base64
import json
import pathlib
import sys
import unittest


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import export_hl_trial
import wow_common


def b64_text(value):
    return base64.b64encode(str(value).encode("utf-8")).decode("ascii")


def b64_json(value):
    return base64.b64encode(json.dumps(value, ensure_ascii=False).encode("utf-8")).decode("ascii")


def event_row(*, event_id=101, meta=None, message="加个奶"):
    return [
        str(event_id),
        "2026-05-25 19:00:00",
        "party",
        "556",
        "1",
        "Wuya",
        None,
        "",
        "202",
        "瓦小狸",
        "556",
        b64_json(meta or {}),
        b64_text(message),
    ]


class HeuristicLearningTrialExportTests(unittest.TestCase):
    def test_build_trial_summarizes_failed_action(self):
        event = wow_common.event_row_to_dict(
            event_row(
                meta={
                    "group_members": [
                        {"name": "Wuya", "online": True, "is_bot": False},
                        {"name": "瓦小狸", "online": True, "is_bot": True},
                    ]
                }
            )
        )
        actions = [
            {
                "id": 9,
                "status": "error",
                "action_type": "summon_bot",
                "bot_name": "瓦小狸",
                "command": "",
                "strategy": "",
                "channel": "",
                "payload": {"role": "healer"},
                "result": "",
                "error": "pool empty",
            }
        ]

        trial = export_hl_trial.build_trial(
            event=event,
            actions=actions,
            combat_summaries=[],
            label="healer-pool-empty",
            status="failed",
            failure_mode="adapter",
            hypothesis="MCP should reply with a pool exhaustion reason.",
            expected="Player sees a short failure reason.",
            actual="No visible reply.",
            next_patch="Add fallback reply for summon_bot pool failures.",
            created_at="2026-06-02T00:00:00+00:00",
        )

        self.assertEqual(trial["schema_version"], 1)
        self.assertEqual(trial["trial_id"], "wow-event-101")
        self.assertEqual(trial["labels"]["failure_mode"], "adapter")
        self.assertEqual(trial["event"]["message"], "加个奶")
        self.assertEqual(trial["action_summary"]["phase"], "failed")
        self.assertEqual(trial["action_summary"]["failures"][0]["error"], "pool empty")
        self.assertEqual(trial["review"]["next_patch"], "Add fallback reply for summon_bot pool failures.")
        self.assertNotIn("context", trial["event"])

    def test_build_trial_can_include_compacted_context(self):
        event = wow_common.event_row_to_dict(
            event_row(
                meta={
                    "speaker": {
                        "name": "Wuya",
                        "location": {"zone_name": "荆棘谷", "area_name": "藏宝海湾"},
                    },
                    "inventory": {"items": [{"name": "ignored"}]},
                }
            )
        )

        trial = export_hl_trial.build_trial(
            event=event,
            actions=[],
            combat_summaries=[],
            include_context=True,
            created_at="2026-06-02T00:00:00+00:00",
        )

        self.assertTrue(trial["event"]["context_is_compacted"])
        self.assertEqual(trial["event"]["context"]["speaker"]["name"], "Wuya")
        self.assertIn("inventory", trial["event"]["context"]["omitted_context_keys"])


if __name__ == "__main__":
    unittest.main()
