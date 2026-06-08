#!/usr/bin/env python3
"""Export compact Heuristic Learning trial records for WoW PlayerBot Agent."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wow_common import (
    MysqlCli,
    compact_combat_summary,
    compact_event,
    fetch_action_results,
    fetch_event,
    fetch_recent_combat_summaries,
    latest_event,
    summarize_action_results,
)


SCHEMA_VERSION = 1
FAILURE_MODES = {
    "state_reader",
    "planner",
    "adapter",
    "executor",
    "instinct",
    "memory",
    "infra",
    "unknown",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_trial(
    *,
    event: dict[str, Any],
    actions: list[dict[str, Any]],
    combat_summaries: list[dict[str, Any]],
    include_context: bool = False,
    label: str = "",
    status: str = "unknown",
    failure_mode: str = "",
    hypothesis: str = "",
    expected: str = "",
    actual: str = "",
    next_patch: str = "",
    notes: str = "",
    created_at: str | None = None,
) -> dict[str, Any]:
    event_id = int(event.get("id") or 0)
    group_leader_guid = int(event.get("group_leader_guid") or 0)
    clean_failure_mode = failure_mode if failure_mode in FAILURE_MODES else "unknown"
    labels = {
        "label": label,
        "status": status or "unknown",
        "failure_mode": clean_failure_mode,
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "trial_id": f"wow-event-{event_id}",
        "created_at": created_at or utc_now(),
        "source": {
            "event_table": "acore_playerbots.agent_playerbot_events",
            "action_table": "acore_playerbots.agent_playerbot_actions",
            "combat_table": "acore_playerbots.agent_playerbot_combat_summaries",
        },
        "labels": labels,
        "event": compact_event(event, include_context=include_context),
        "actions": actions,
        "action_summary": summarize_action_results(actions),
        "recent_combat_summaries": [compact_combat_summary(item) for item in combat_summaries],
        "review": {
            "hypothesis": hypothesis,
            "expected": expected,
            "actual": actual,
            "next_patch": next_patch,
            "notes": notes,
        },
        "replay": {
            "event_id": event_id,
            "group_leader_guid": group_leader_guid,
            "commands": [
                f"python3 tools/playerbot-mcp/export_hl_trial.py --event-id {event_id} --pretty",
                "python3 -m unittest discover -s tools/playerbot-mcp -p 'test_hl_trial_*.py'",
                (
                    "test -x /home/wuya/git/azerothcore-wotlk-git/var/playerbot-mcp-venv/bin/python "
                    "&& /home/wuya/git/azerothcore-wotlk-git/var/playerbot-mcp-venv/bin/python "
                    "-m unittest tools/playerbot-mcp/test_playerbot_mcp.py "
                    "tools/playerbot-mcp/test_hl_trial_export.py "
                    "tools/playerbot-mcp/test_hl_trial_summary.py"
                ),
            ],
        },
    }


def load_event(db: MysqlCli, event_id: int, latest: bool, speaker_guid: int) -> dict[str, Any]:
    event = latest_event(db, speaker_guid=speaker_guid) if latest else fetch_event(db, event_id)
    if not event:
        if latest:
            scope = f" for speaker_guid={speaker_guid}" if speaker_guid else ""
            raise RuntimeError(f"no latest event found{scope}")
        raise RuntimeError(f"event_id={event_id} was not found")
    return event


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export one PlayerBot Agent event, its actions, and nearby combat summaries "
            "as a compact Heuristic Learning trial."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--event-id", type=int, default=0, help="agent_playerbot_events.id to export")
    source.add_argument("--latest", action="store_true", help="export the newest event, optionally scoped by speaker")
    parser.add_argument("--speaker-guid", type=int, default=0, help="scope --latest to a speaker GUID")
    parser.add_argument("--include-context", action="store_true", help="include compacted event context in the trial")
    parser.add_argument("--combat-limit", type=int, default=3, help="recent combat summaries to include")
    parser.add_argument("--label", default="", help="short human label, for example healer-follow-failed")
    parser.add_argument(
        "--status",
        choices=("unknown", "failed", "succeeded", "mixed", "needs_review"),
        default="unknown",
        help="human review status for this trial",
    )
    parser.add_argument(
        "--failure-mode",
        choices=tuple(sorted(FAILURE_MODES)),
        default="",
        help="coarse failure layer for review and later compression",
    )
    parser.add_argument("--hypothesis", default="", help="current diagnosis")
    parser.add_argument("--expected", default="", help="expected game-visible behavior")
    parser.add_argument("--actual", default="", help="actual game-visible behavior")
    parser.add_argument("--next-patch", default="", help="next code, prompt, tool, or test change")
    parser.add_argument("--notes", default="", help="free-form human notes")
    parser.add_argument("--out", default="", help="append compact JSONL to this path, usually under var/")
    parser.add_argument("--pretty", action="store_true", help="print pretty JSON to stdout")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    db = MysqlCli.from_env()
    event = load_event(db, args.event_id, bool(args.latest), args.speaker_guid)
    event_id = int(event.get("id") or 0)
    actions = fetch_action_results(db, event_id, limit=20)
    combat_summaries = fetch_recent_combat_summaries(
        db,
        group_leader_guid=int(event.get("group_leader_guid") or 0),
        limit=args.combat_limit,
    )
    trial = build_trial(
        event=event,
        actions=actions,
        combat_summaries=combat_summaries,
        include_context=bool(args.include_context),
        label=args.label,
        status=args.status,
        failure_mode=args.failure_mode,
        hypothesis=args.hypothesis,
        expected=args.expected,
        actual=args.actual,
        next_patch=args.next_patch,
        notes=args.notes,
    )

    if args.out:
        append_jsonl(Path(args.out), trial)
        print(f"wrote {trial['trial_id']} to {args.out}")
    else:
        indent = 2 if args.pretty else None
        print(json.dumps(trial, ensure_ascii=False, indent=indent, separators=None if indent else (",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
