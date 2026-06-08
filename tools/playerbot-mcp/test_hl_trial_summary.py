#!/usr/bin/env python3

import json
import pathlib
import sys
import tempfile
import unittest


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import summarize_hl_trials


class HeuristicLearningTrialSummaryTests(unittest.TestCase):
    def test_summary_counts_failure_modes_and_rows(self):
        trials = [
            {
                "trial_id": "wow-event-1",
                "created_at": "2026-06-02T00:00:00+00:00",
                "labels": {"status": "failed", "failure_mode": "adapter", "label": "summon-fallback"},
                "event": {
                    "id": 1,
                    "channel": "party",
                    "speaker": {"name": "Wuya"},
                    "message": "加个奶",
                },
                "actions": [{"action_type": "summon_bot", "error": "pool empty"}],
                "action_summary": {"phase": "failed", "counts": {"error": 1}},
                "review": {"next_patch": "Add fallback reply."},
            },
            {
                "trial_id": "wow-event-2",
                "labels": {"status": "succeeded", "failure_mode": "unknown", "label": "follow"},
                "event": {"id": 2, "channel": "whisper", "speaker": {"name": "Wuya"}, "message": "跟我"},
                "actions": [{"action_type": "command", "error": ""}],
                "action_summary": {"phase": "succeeded", "counts": {"done": 1}},
                "review": {},
            },
        ]

        rows = summarize_hl_trials.trial_rows(trials)
        summary = summarize_hl_trials.summary_counts(rows)

        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["by_status"]["failed"], 1)
        self.assertEqual(summary["by_failure_mode"]["adapter"], 1)
        self.assertEqual(rows[0]["errors"], "summon_bot:pool empty")
        self.assertEqual(rows[0]["next_patch"], "Add fallback reply.")

    def test_load_trials_and_write_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            jsonl = root / "trials.jsonl"
            jsonl.write_text(
                json.dumps({"trial_id": "wow-event-1", "labels": {}, "event": {}, "actions": []}) + "\n",
                encoding="utf-8",
            )
            rows = summarize_hl_trials.trial_rows(summarize_hl_trials.load_trials(jsonl))
            csv_path = root / "summary.csv"

            summarize_hl_trials.write_csv(csv_path, rows)

            self.assertIn("trial_id", csv_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertIn("wow-event-1", csv_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
