#!/usr/bin/env python3
"""Summarize WoW PlayerBot Agent Heuristic Learning trial JSONL files."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def load_trials(path: Path) -> list[dict[str, Any]]:
    trials: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"{path}:{line_no}: invalid JSONL: {exc}") from exc
            if isinstance(payload, dict):
                trials.append(payload)
    return trials


def compact_errors(actions: list[dict[str, Any]]) -> str:
    errors = []
    for action in actions:
        error = str(action.get("error") or "").strip()
        if error:
            action_type = str(action.get("action_type") or "")
            errors.append(f"{action_type}:{error}" if action_type else error)
    return " | ".join(errors[:5])


def trial_rows(trials: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for trial in trials:
        labels = trial.get("labels") if isinstance(trial.get("labels"), dict) else {}
        event = trial.get("event") if isinstance(trial.get("event"), dict) else {}
        speaker = event.get("speaker") if isinstance(event.get("speaker"), dict) else {}
        actions = trial.get("actions") if isinstance(trial.get("actions"), list) else []
        action_summary = trial.get("action_summary") if isinstance(trial.get("action_summary"), dict) else {}
        review = trial.get("review") if isinstance(trial.get("review"), dict) else {}
        rows.append(
            {
                "trial_id": str(trial.get("trial_id") or ""),
                "created_at": str(trial.get("created_at") or ""),
                "event_id": str(event.get("id") or ""),
                "status": str(labels.get("status") or "unknown"),
                "failure_mode": str(labels.get("failure_mode") or "unknown"),
                "label": str(labels.get("label") or ""),
                "channel": str(event.get("channel") or ""),
                "speaker": str(speaker.get("name") or ""),
                "message": str(event.get("message") or ""),
                "action_phase": str(action_summary.get("phase") or ""),
                "action_counts": json.dumps(action_summary.get("counts") or {}, ensure_ascii=False, separators=(",", ":")),
                "errors": compact_errors([item for item in actions if isinstance(item, dict)]),
                "next_patch": str(review.get("next_patch") or ""),
            }
        )
    return rows


def summary_counts(rows: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "total": len(rows),
        "by_status": dict(Counter(row["status"] for row in rows)),
        "by_failure_mode": dict(Counter(row["failure_mode"] for row in rows)),
        "by_action_phase": dict(Counter(row["action_phase"] for row in rows if row["action_phase"])),
        "top_labels": dict(Counter(row["label"] for row in rows if row["label"]).most_common(10)),
    }


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "trial_id",
        "created_at",
        "event_id",
        "status",
        "failure_mode",
        "label",
        "channel",
        "speaker",
        "message",
        "action_phase",
        "action_counts",
        "errors",
        "next_patch",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize PlayerBot Agent HL trial JSONL records.")
    parser.add_argument("jsonl", help="trial JSONL path, for example var/playerbot-hl-trials/trials.jsonl")
    parser.add_argument("--csv", default="", help="write per-trial CSV rows")
    parser.add_argument("--json", default="", help="write aggregate summary JSON")
    parser.add_argument("--rows-json", action="store_true", help="print per-trial rows instead of aggregate summary")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    trials = load_trials(Path(args.jsonl))
    rows = trial_rows(trials)
    summary = summary_counts(rows)
    if args.csv:
        write_csv(Path(args.csv), rows)
    if args.json:
        write_json(Path(args.json), {"summary": summary, "rows": rows})
    payload: Any = rows if args.rows_json else summary
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
