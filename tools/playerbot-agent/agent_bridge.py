#!/usr/bin/env python3
"""Python sidecar for the Playerbot Agent bridge.

The sidecar reads chat events queued by mod-playerbot-agent, applies cheap
Chinese rules first, optionally asks an OpenAI-compatible chat-completions
model, and writes whitelisted action rows for worldserver to execute.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


CLASS_ALIASES: dict[int, list[str]] = {
    1: ["战士", "zs", "warrior", "坦"],
    2: ["骑士", "圣骑", "奶骑", "qs", "paladin"],
    3: ["猎人", "lr", "hunter"],
    4: ["盗贼", "dz", "贼", "rogue"],
    5: ["牧师", "ms", "神牧", "戒律", "priest", "治疗", "奶妈", "奶"],
    6: ["dk", "死亡骑士", "死骑"],
    7: ["萨满", "sm", "奶萨", "shaman", "治疗", "奶妈", "奶"],
    8: ["法师", "fs", "mage"],
    9: ["术士", "ss", "warlock"],
    11: ["德鲁伊", "小德", "奶德", "druid", "治疗", "奶妈", "奶"],
}

ROLE_ALIASES: dict[str, list[str]] = {
    "healer": ["治疗", "奶", "奶妈", "奶我", "healer"],
    "tank": ["坦", "坦克", "tank", "t"],
    "dps": ["输出", "dps", "打手"],
}

CONTROLLED_BOT_KINDS = {"owned", "group"}

SUMMON_CLASS_HINTS: list[tuple[str, str, list[str]]] = [
    ("healer", "paladin", ["奶骑", "圣骑", "骑士", "paladin"]),
    ("healer", "shaman", ["奶萨", "萨满", "shaman"]),
    ("healer", "druid", ["奶德", "小德", "德鲁伊", "druid"]),
    ("healer", "priest", ["牧师", "神牧", "戒律", "治疗", "奶妈", "奶", "priest", "healer"]),
    ("tank", "paladin", ["防骑", "圣骑坦", "骑士坦"]),
    ("tank", "druid", ["熊", "熊坦", "德坦"]),
    ("tank", "dk", ["dk坦", "死骑坦", "死亡骑士"]),
    ("tank", "warrior", ["战士", "防战", "坦克", "坦", "warrior", "tank"]),
    ("dps", "hunter", ["猎人", "lr", "hunter"]),
    ("dps", "rogue", ["盗贼", "dz", "贼", "rogue"]),
    ("dps", "warlock", ["术士", "ss", "warlock"]),
    ("dps", "mage", ["法师", "fs", "远程", "输出", "dps", "mage"]),
]

GENERIC_INVITE_TARGETS = {
    "个",
    "一个",
    "队",
    "队伍",
    "奶",
    "奶妈",
    "治疗",
    "坦",
    "坦克",
    "输出",
    "dps",
    "机器人",
    "bot",
}

ATTENTION_SECONDS = 30
RECENT_MESSAGE_LIMIT = 240
RECENT_MESSAGE_TTL = 14400
PROMPT_MESSAGE_LIMIT = 40


@dataclasses.dataclass(frozen=True)
class BotInfo:
    guid: int
    name: str
    klass: int
    level: int
    role: str
    bot_kind: str = "playerbot"
    race: int = 0
    team: int = 0


@dataclasses.dataclass(frozen=True)
class ChatEvent:
    id: int
    channel: str
    speaker_guid: int
    speaker_name: str
    target_guid: int | None
    target_name: str | None
    bot_guid: int | None
    bot_name: str | None
    message: str
    bots: list[BotInfo]
    context: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class Action:
    action_type: str
    bot: BotInfo | None = None
    channel: str | None = None
    text: str | None = None
    command: str | None = None
    strategy: str | None = None
    bot_state: str | None = None
    payload: dict[str, Any] | None = None


@dataclasses.dataclass(frozen=True)
class CombatMemory:
    id: int
    created_at: str
    leader: str
    summary: str


@dataclasses.dataclass(frozen=True)
class LLMCompletion:
    content: str
    reasoning: str = ""
    finish_reason: str = ""
    usage: dict[str, Any] = dataclasses.field(default_factory=dict)
    raw: dict[str, Any] | None = None


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, minimum: int = 0) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return max(minimum, int(value))
    except ValueError:
        return default


def truncate(value: Any, limit: int = 4000, depth: int = 0) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"...<truncated {len(value) - limit} chars>"
    if isinstance(value, dict):
        if depth > 8:
            return "<truncated nested dict>"
        return {key: truncate(item, limit, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        if depth > 8:
            return "<truncated nested list>"
        items = [truncate(item, limit, depth + 1) for item in value[:80]]
        if len(value) > 80:
            items.append(f"<truncated {len(value) - 80} items>")
        return items
    return value


def log_agent(event: str, **fields: Any) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "event": event,
        **{key: truncate(value) for key, value in fields.items()},
    }
    print(json.dumps(record, ensure_ascii=False, separators=(",", ":")), flush=True)


def normalize_llm_thinking(value: str, base_url: str, model: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if normalized in {"1", "true", "yes", "on", "enable", "enabled"}:
        return "enabled"
    if normalized in {"0", "false", "no", "off", "disable", "disabled"}:
        return "disabled"
    if not normalized and "deepseek" in base_url.lower() and model.startswith("deepseek-v4"):
        return "disabled"
    return normalized


def llm_completion_log_fields(
    completion: LLMCompletion,
    *,
    include_reasoning: bool = False,
    include_raw: bool = False,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "response": completion.content,
        "finish_reason": completion.finish_reason,
        "usage": completion.usage,
    }
    if include_reasoning:
        fields["reasoning"] = completion.reasoning
    elif completion.reasoning:
        fields["reasoning_chars"] = len(completion.reasoning)
    if include_raw:
        fields["raw_response"] = completion.raw
    return fields


def action_to_dict(action: Action) -> dict[str, Any]:
    return {
        "type": action.action_type,
        "bot": action.bot.name if action.bot else None,
        "channel": action.channel,
        "text": action.text,
        "command": action.command,
        "strategy": action.strategy,
        "bot_state": action.bot_state,
        "payload": action.payload,
    }


class MysqlCli:
    """Small mysql CLI adapter so the sidecar has no Python DB dependency."""

    def __init__(self, dsn: str) -> None:
        parts = dsn.split(";")
        if len(parts) != 5:
            raise ValueError("PLAYERBOT_AGENT_DB_DSN must be host;port;user;password;database")
        self.host, self.port, self.user, self.password, self.database = parts

    def _run(self, sql: str) -> str:
        env = os.environ.copy()
        env["MYSQL_PWD"] = self.password
        cmd = [
            "mysql",
            "-h",
            self.host,
            "-P",
            self.port,
            "-u",
            self.user,
            "--default-character-set=utf8mb4",
            "--batch",
            "--skip-column-names",
            self.database,
            "-e",
            sql,
        ]
        proc = subprocess.run(cmd, env=env, text=True, capture_output=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "mysql command failed")
        return proc.stdout

    def query_rows(self, sql: str) -> list[list[str | None]]:
        output = self._run(sql)
        rows: list[list[str | None]] = []
        for line in output.splitlines():
            if not line:
                continue
            rows.append([mysql_unescape(value) for value in line.split("\t")])
        return rows

    def execute(self, sql: str) -> None:
        self._run(sql)

    def scalar_int(self, sql: str) -> int:
        rows = self.query_rows(sql)
        if not rows or not rows[0] or rows[0][0] is None:
            return 0
        return int(rows[0][0])


def mysql_unescape(value: str) -> str | None:
    if value == "NULL":
        return None
    result: list[str] = []
    i = 0
    while i < len(value):
        if value[i] != "\\" or i + 1 >= len(value):
            result.append(value[i])
            i += 1
            continue
        nxt = value[i + 1]
        result.append({"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\"}.get(nxt, nxt))
        i += 2
    return "".join(result)


def sql_quote(value: str | None) -> str:
    if value is None:
        return "NULL"
    escaped = (
        value.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\0", "\\0")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return f"'{escaped}'"


def sql_uint(value: int | None) -> str:
    if value is None:
        return "NULL"
    return str(int(value))


class AgentBridge:
    def __init__(
        self,
        db: MysqlCli,
        state_path: Path,
        *,
        replay: bool = False,
        rule_only: bool = False,
        poll_limit: int = 20,
    ) -> None:
        self.db = db
        self.state_path = state_path
        self.rule_only = rule_only
        self.poll_limit = poll_limit
        self.attention_until: dict[int, float] = defaultdict(float)
        self.history_limit = env_int("PLAYERBOT_AGENT_HISTORY_LIMIT", RECENT_MESSAGE_LIMIT, 20)
        self.history_ttl = env_int("PLAYERBOT_AGENT_HISTORY_TTL_SECONDS", RECENT_MESSAGE_TTL, 60)
        self.prompt_message_limit = env_int("PLAYERBOT_AGENT_PROMPT_MESSAGE_LIMIT", PROMPT_MESSAGE_LIMIT, 5)
        self.history: deque[tuple[float, str, str, str, str]] = deque(maxlen=self.history_limit)
        self.combat_memory: deque[CombatMemory] = deque(
            maxlen=env_int("PLAYERBOT_AGENT_COMBAT_MEMORY_LIMIT", 3, 0)
        )
        state = self._load_state(replay)
        self.last_id = state["last_id"]
        self.last_combat_id = state["last_combat_id"]
        self.llm = OpenAICompatibleClient.from_env()
        self.trace = env_bool("PLAYERBOT_AGENT_TRACE", False)
        self.trace_prompt = env_bool("PLAYERBOT_AGENT_TRACE_PROMPT", self.trace)
        self.trace_reasoning = env_bool("PLAYERBOT_AGENT_TRACE_REASONING", False)
        self.trace_llm_raw = env_bool("PLAYERBOT_AGENT_TRACE_LLM_RAW", False)
        self._ensure_schema_compat()
        self._load_recent_history()
        self._load_recent_combat_memory()
        log_agent(
            "startup",
            rule_only=self.rule_only,
            llm_available=self.llm.available,
            llm_model=getattr(self.llm, "model", ""),
            llm_base_url=getattr(self.llm, "base_url", ""),
            llm_thinking=getattr(self.llm, "thinking", ""),
            trace_prompt=self.trace_prompt,
            trace_reasoning=self.trace_reasoning,
            trace_llm_raw=self.trace_llm_raw,
            last_id=self.last_id,
            last_combat_id=self.last_combat_id,
            poll_limit=self.poll_limit,
            history_items=len(self.history),
            history_limit=self.history_limit,
            history_ttl_seconds=self.history_ttl,
            prompt_message_limit=self.prompt_message_limit,
        )

    def _safe_scalar_int(self, sql: str) -> int:
        try:
            return self.db.scalar_int(sql)
        except RuntimeError:
            return 0

    def _ensure_schema_compat(self) -> None:
        try:
            has_actions = self.db.scalar_int(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'agent_playerbot_actions'"
            )
            if not has_actions:
                return

            has_payload = self.db.scalar_int(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'agent_playerbot_actions' "
                "AND COLUMN_NAME = 'payload_json'"
            )
            if not has_payload:
                self.db.execute(
                    "ALTER TABLE `agent_playerbot_actions` "
                    "ADD COLUMN `payload_json` TEXT NULL DEFAULT NULL AFTER `bot_state`"
                )
                log_agent("schema_compat", added_column="agent_playerbot_actions.payload_json")
        except RuntimeError as exc:
            log_agent("schema_compat_error", error=str(exc))

    def _load_state(self, replay: bool) -> dict[str, int]:
        if replay:
            return {"last_id": 0, "last_combat_id": 0}
        if self.state_path.exists():
            try:
                payload = json.loads(self.state_path.read_text())
                return {
                    "last_id": int(payload.get("last_id", 0)),
                    "last_combat_id": int(payload.get("last_combat_id", 0)),
                }
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        return {
            "last_id": self._safe_scalar_int("SELECT COALESCE(MAX(`id`), 0) FROM `agent_playerbot_events`"),
            "last_combat_id": self._safe_scalar_int(
                "SELECT COALESCE(MAX(`id`), 0) FROM `agent_playerbot_combat_summaries`"
            ),
        }

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps({"last_id": self.last_id, "last_combat_id": self.last_combat_id}, ensure_ascii=False),
            encoding="utf-8",
        )

    def _load_recent_history(self) -> None:
        try:
            rows = self.db.query_rows(
                "SELECT UNIX_TIMESTAMP(`created_at`), `channel`, `speaker_name`, "
                "COALESCE(`target_name`, `bot_name`, ''), TO_BASE64(`message`) "
                "FROM `agent_playerbot_events` "
                f"WHERE `created_at` >= DATE_SUB(NOW(), INTERVAL {int(self.history_ttl)} SECOND) "
                f"ORDER BY `id` DESC LIMIT {int(self.history_limit)}"
            )
        except RuntimeError as exc:
            if self.trace:
                log_agent("history_load_error", error=str(exc))
            return

        for row in reversed(rows):
            self.history.append(
                (
                    float(row[0] or 0),
                    str(row[1] or ""),
                    str(row[2] or ""),
                    str(row[3] or ""),
                    decode_b64(row[4]),
                )
            )

    def poll_once(self) -> int:
        combat_count = self.poll_combat_summaries()
        events = self.fetch_events()
        for event in events:
            self.handle_event(event)
            self.last_id = max(self.last_id, event.id)
        if events:
            self.db.execute(
                "UPDATE `agent_playerbot_events` SET `processed_at` = NOW() "
                f"WHERE `id` <= {self.last_id} AND `processed_at` IS NULL"
            )
        if events or combat_count:
            self._save_state()
        return len(events) + combat_count

    def fetch_events(self) -> list[ChatEvent]:
        sql = (
            "SELECT `id`, `channel`, `speaker_guid`, `speaker_name`, `target_guid`, `target_name`, "
            "`bot_guid`, `bot_name`, TO_BASE64(`message`), TO_BASE64(COALESCE(`meta`, '')) "
            "FROM `agent_playerbot_events` "
            f"WHERE `id` > {int(self.last_id)} ORDER BY `id` ASC LIMIT {int(self.poll_limit)}"
        )
        events: list[ChatEvent] = []
        for row in self.db.query_rows(sql):
            meta = decode_b64(row[9])
            try:
                payload = json.loads(meta) if meta else {}
            except json.JSONDecodeError as exc:
                payload = {}
                log_agent(
                    "event_meta_json_error",
                    event_id=int(row[0] or 0),
                    error=str(exc),
                    meta_preview=meta[:400],
                )
            bots = [
                BotInfo(
                    guid=int(bot.get("guid", 0)),
                    name=str(bot.get("name", "")),
                    klass=int(bot.get("class", 0)),
                    level=int(bot.get("level", 0)),
                    role=str(bot.get("role", "unknown")),
                    bot_kind=str(bot.get("bot_kind", "playerbot")),
                    race=int(bot.get("race", 0)),
                    team=int(bot.get("team", 0)),
                )
                for bot in payload.get("bots", [])
                if int(bot.get("guid", 0)) > 0 and bot.get("name")
            ]
            events.append(
                ChatEvent(
                    id=int(row[0] or 0),
                    channel=str(row[1] or ""),
                    speaker_guid=int(row[2] or 0),
                    speaker_name=str(row[3] or ""),
                    target_guid=int(row[4]) if row[4] else None,
                    target_name=str(row[5]) if row[5] else None,
                    bot_guid=int(row[6]) if row[6] else None,
                    bot_name=str(row[7]) if row[7] else None,
                    message=decode_b64(row[8]),
                    bots=bots,
                    context=payload,
                )
            )
        return events

    def _load_recent_combat_memory(self) -> None:
        try:
            rows = self.db.query_rows(
                "SELECT `id`, `created_at`, `group_leader_name`, TO_BASE64(COALESCE(`summary_text`, '')), "
                "TO_BASE64(`facts_json`) FROM `agent_playerbot_combat_summaries` "
                "ORDER BY `id` DESC LIMIT 3"
            )
        except RuntimeError:
            return

        memories: list[CombatMemory] = []
        for row in reversed(rows):
            summary = decode_b64(row[3]) or local_combat_summary(json.loads(decode_b64(row[4]) or "{}"))
            if summary:
                memories.append(
                    CombatMemory(
                        id=int(row[0] or 0),
                        created_at=str(row[1] or ""),
                        leader=str(row[2] or ""),
                        summary=summary,
                    )
                )
        self.combat_memory.extend(memories)

    def poll_combat_summaries(self) -> int:
        limit = int(os.getenv("PLAYERBOT_AGENT_COMBAT_POLL_LIMIT", "5"))
        try:
            rows = self.db.query_rows(
                "SELECT `id`, `created_at`, `group_leader_name`, TO_BASE64(`facts_json`), "
                "TO_BASE64(COALESCE(`summary_text`, '')) "
                "FROM `agent_playerbot_combat_summaries` "
                f"WHERE `id` > {int(self.last_combat_id)} ORDER BY `id` ASC LIMIT {limit}"
            )
        except RuntimeError as exc:
            if self.trace:
                log_agent("combat_summary_query_error", error=str(exc))
            return 0

        processed = 0
        for row in rows:
            combat_id = int(row[0] or 0)
            facts_text = decode_b64(row[3])
            try:
                facts = json.loads(facts_text) if facts_text else {}
            except json.JSONDecodeError:
                facts = {}

            existing_summary = decode_b64(row[4])
            summary = existing_summary or self.summarize_combat(facts, combat_id)
            if summary and not existing_summary:
                self.db.execute(
                    "UPDATE `agent_playerbot_combat_summaries` SET `summary_text` = "
                    f"{sql_quote(summary)}, `summarized_at` = NOW() WHERE `id` = {combat_id}"
                )

            memory = CombatMemory(
                id=combat_id,
                created_at=str(row[1] or ""),
                leader=str(row[2] or ""),
                summary=summary,
            )
            self.combat_memory.append(memory)
            self.last_combat_id = max(self.last_combat_id, combat_id)
            processed += 1
            log_agent("combat_summary_seen", id=combat_id, leader=memory.leader, summary=summary, facts=facts if self.trace else None)

        return processed

    def summarize_combat(self, facts: dict[str, Any], combat_id: int) -> str:
        fallback = local_combat_summary(facts)
        if self.rule_only or not self.llm.available:
            return fallback

        prompt = build_combat_summary_prompt(facts)
        if self.trace_prompt:
            log_agent("combat_summary_prompt", id=combat_id, prompt=prompt)

        started = time.monotonic()
        completion = self.llm.complete_result(
            prompt,
            system="你是魔兽世界队伍战斗复盘器，只输出严格 JSON，不要输出解释。",
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        log_agent(
            "combat_summary_response",
            id=combat_id,
            latency_ms=latency_ms,
            **llm_completion_log_fields(
                completion,
                include_reasoning=self.trace_reasoning,
                include_raw=self.trace_llm_raw,
            ),
        )
        response = completion.content

        try:
            payload = json.loads(response)
        except json.JSONDecodeError:
            return fallback

        summary = str(payload.get("summary", "")).strip()
        tips = payload.get("tips") if isinstance(payload.get("tips"), list) else []
        tip_text = "；".join(str(tip).strip() for tip in tips if str(tip).strip())
        if summary and tip_text:
            return f"{summary} 建议：{tip_text}"
        return summary or fallback

    def fetch_recent_action_results(self, event: ChatEvent, bot: BotInfo) -> list[dict[str, Any]]:
        try:
            rows = self.db.query_rows(
                "SELECT `id`, `status`, `action_type`, `channel`, TO_BASE64(COALESCE(`text`, '')), "
                "`command`, `strategy`, `bot_state`, TO_BASE64(COALESCE(`payload_json`, '')), "
                "TO_BASE64(COALESCE(`result`, '')), "
                "TO_BASE64(COALESCE(`error`, '')) FROM `agent_playerbot_actions` "
                f"WHERE `requester_guid` = {event.speaker_guid} AND `bot_guid` = {bot.guid} "
                "ORDER BY `id` DESC LIMIT 8"
            )
        except RuntimeError:
            return []

        results: list[dict[str, Any]] = []
        for row in rows:
            results.append(
                {
                    "id": int(row[0] or 0),
                    "status": row[1],
                    "type": row[2],
                    "channel": row[3],
                    "text": decode_b64(row[4]),
                    "command": row[5],
                    "strategy": row[6],
                    "bot_state": row[7],
                    "payload": decode_b64(row[8]),
                    "result": decode_b64(row[9]),
                    "error": decode_b64(row[10]),
                }
            )
        return results

    def handle_event(self, event: ChatEvent) -> None:
        now = time.time()
        self._remember(event, now)
        log_agent(
            "chat_event",
            event_id=event.id,
            channel=event.channel,
            speaker=event.speaker_name,
            target=event.target_name,
            bot_names=[bot.name for bot in event.bots],
            message=event.message,
            context=event.context if self.trace else None,
        )

        for action in lifecycle_rule_actions(event):
            log_agent("decision", event_id=event.id, bot=None, source="lifecycle_rule", actions=[action_to_dict(action)])
            self.enqueue_action(event, action)

        for bot in event.bots:
            if not self._is_for_bot(event, bot, now):
                if self.trace:
                    log_agent("attention_skip", event_id=event.id, bot=bot.name)
                continue

            actions = rule_actions(event, bot)
            source = "rule" if actions else ""
            if not actions and not self.rule_only and self.llm.available:
                actions = self.llm_actions(event, bot)
                source = "llm" if actions else "llm_empty"
            elif not actions:
                source = "no_action"

            log_agent(
                "decision",
                event_id=event.id,
                bot=bot.name,
                source=source,
                actions=[action_to_dict(action) for action in actions],
            )

            for action in actions:
                self.enqueue_action(event, action)

    def _remember(self, event: ChatEvent, now: float) -> None:
        target = event.target_name or event.bot_name or ""
        self.history.append((now, event.channel, event.speaker_name, target, event.message))
        while self.history and now - self.history[0][0] > self.history_ttl:
            self.history.popleft()

    def _is_for_bot(self, event: ChatEvent, bot: BotInfo, now: float) -> bool:
        if event.channel == "whisper" and event.bot_guid == bot.guid:
            self.attention_until[bot.guid] = now + ATTENTION_SECONDS
            return True

        if contains_alias(event.message, bot):
            self.attention_until[bot.guid] = now + ATTENTION_SECONDS
            return True

        primary = primary_responder(event)
        if (
            event.channel in {"party", "raid"}
            and primary
            and primary.guid == bot.guid
            and looks_actionable_group_chat(event.message)
        ):
            self.attention_until[bot.guid] = now + ATTENTION_SECONDS
            return True

        return event.channel in {"party", "raid", "say", "yell"} and self.attention_until[bot.guid] > now

    def llm_actions(self, event: ChatEvent, bot: BotInfo) -> list[Action]:
        recent_action_results = self.fetch_recent_action_results(event, bot)
        prompt = build_prompt(
            event,
            bot,
            self.history,
            self.combat_memory,
            recent_action_results,
            recent_message_limit=self.prompt_message_limit,
        )
        if self.combat_memory:
            log_agent(
                "combat_memory_used",
                event_id=event.id,
                bot=bot.name,
                memory=[dataclasses.asdict(memory) for memory in self.combat_memory],
            )
        if self.trace_prompt:
            log_agent("llm_prompt", event_id=event.id, bot=bot.name, prompt=prompt)

        started = time.monotonic()
        completion = self.llm.complete_result(prompt)
        latency_ms = int((time.monotonic() - started) * 1000)
        log_agent(
            "llm_response",
            event_id=event.id,
            bot=bot.name,
            latency_ms=latency_ms,
            **llm_completion_log_fields(
                completion,
                include_reasoning=self.trace_reasoning,
                include_raw=self.trace_llm_raw,
            ),
        )
        response = completion.content
        if not response:
            return []
        try:
            payload = json.loads(response)
        except json.JSONDecodeError as exc:
            log_agent("llm_json_error", event_id=event.id, bot=bot.name, error=str(exc), response=response)
            return []
        actions = actions_from_llm_payload(payload, event, bot)
        log_agent(
            "llm_skill_mapping",
            event_id=event.id,
            bot=bot.name,
            requested_skills=payload.get("actions", []),
            mapped_actions=[action_to_dict(action) for action in actions],
        )
        return actions

    def enqueue_action(self, event: ChatEvent, action: Action) -> None:
        payload_json = (
            json.dumps(action.payload, ensure_ascii=False, separators=(",", ":")) if action.payload else None
        )
        sql = (
            "INSERT INTO `agent_playerbot_actions` "
            "(`source_event_id`, `requester_guid`, `requester_name`, `bot_guid`, `bot_name`, "
            "`action_type`, `channel`, `text`, `command`, `strategy`, `bot_state`, `payload_json`) VALUES "
            f"({event.id}, {event.speaker_guid}, {sql_quote(event.speaker_name)}, "
            f"{sql_uint(action.bot.guid if action.bot else None)}, "
            f"{sql_quote(action.bot.name if action.bot else None)}, {sql_quote(action.action_type)}, "
            f"{sql_quote(action.channel)}, {sql_quote(action.text)}, {sql_quote(action.command)}, "
            f"{sql_quote(action.strategy)}, {sql_quote(action.bot_state)}, {sql_quote(payload_json)})"
        )
        self.db.execute(sql)
        log_agent("action_enqueued", event_id=event.id, action=action_to_dict(action))


def decode_b64(value: str | None) -> str:
    if not value:
        return ""
    return base64.b64decode(value).decode("utf-8", errors="replace")


def aliases_for_bot(bot: BotInfo) -> set[str]:
    aliases = {bot.name.lower()}
    aliases.update(alias.lower() for alias in CLASS_ALIASES.get(bot.klass, []))
    aliases.update(alias.lower() for alias in ROLE_ALIASES.get(bot.role, []))
    return aliases


def contains_alias(message: str, bot: BotInfo) -> bool:
    lowered = message.lower()
    return any(alias and alias in lowered for alias in aliases_for_bot(bot))


def reply_channel(event: ChatEvent) -> str:
    if event.channel == "whisper":
        return "whisper"
    if event.channel in {"party", "raid"}:
        return "party"
    return "say"


def is_world_bot(bot: BotInfo) -> bool:
    return bot.bot_kind in {"random_world", "addclass_world"}


def reply_only_event(event: ChatEvent, bot: BotInfo) -> bool:
    return event.channel == "whisper" and is_world_bot(bot)


def location_question(text: str) -> bool:
    lowered = text.strip().lower()
    return any(token in lowered for token in ["在哪", "哪里", "这是哪", "什么地方", "位置", "where"])


def context_location(context: dict[str, Any]) -> dict[str, Any]:
    environment = context.get("environment") if isinstance(context.get("environment"), dict) else {}
    location = environment.get("location") if isinstance(environment.get("location"), dict) else {}
    if location:
        return location

    speaker = context.get("speaker") if isinstance(context.get("speaker"), dict) else {}
    location = speaker.get("location") if isinstance(speaker.get("location"), dict) else {}
    if location:
        return location

    return environment or speaker


def location_reply_text(context: dict[str, Any]) -> str | None:
    location = context_location(context)
    if not location:
        return None

    area = str(location.get("area_name") or "").strip()
    zone = str(location.get("zone_name") or "").strip()
    game_map = str(location.get("map_name") or "").strip()

    if area and zone and area != zone:
        return f"这里是{area}，属于{zone}。"
    if area or zone:
        name = area or zone
        return f"我这边只能确认大区域是{name}，室内小区域没拿到。"
    if game_map:
        return f"我这边只能确认地图是{game_map}，区域名没拿到。"
    return None


def controllable_bots(event: ChatEvent) -> list[BotInfo]:
    return [bot for bot in event.bots if bot.bot_kind in CONTROLLED_BOT_KINDS]


def primary_responder(event: ChatEvent) -> BotInfo | None:
    bots = controllable_bots(event)
    return bots[0] if bots else None


def looks_actionable_group_chat(message: str) -> bool:
    text = message.strip().lower()
    if not text:
        return False
    return any(
        token in text
        for token in [
            "我想",
            "我要",
            "准备",
            "副本",
            "下本",
            "下副本",
            "任务",
            "什么",
            "谁",
            "哪",
            "哪里",
            "为何",
            "为什么",
            "背景",
            "故事",
            "吗",
            "怎么",
            "怎么样",
            "能不能",
            "可以",
            "继续",
            "走",
            "开打",
            "拉怪",
            "治疗",
            "加血",
            "奶",
            "坦",
            "输出",
            "buff",
            "拾取",
            "捡",
            "组队",
            "进队",
            "邀请",
            "?",
            "？",
        ]
    )


def has_lifecycle_context(event: ChatEvent) -> bool:
    if event.channel == "whisper":
        return bool(controllable_bots(event))
    if event.bots and not controllable_bots(event):
        return False
    return event.channel in {"party", "raid", "say", "yell"}


def lifecycle_target_bot(event: ChatEvent, text: str) -> str | None:
    bots = controllable_bots(event)
    for bot in bots:
        if bot.name and bot.name.lower() in text:
            return bot.name
    if len(bots) == 1:
        return bots[0].name
    return None


def summon_intent_from_text(text: str) -> tuple[str, str] | None:
    wants_summon = any(
        token in text
        for token in [
            "加个",
            "来个",
            "补个",
            "组个",
            "加一个",
            "来一个",
            "补一个",
            "组一个",
            "addclass",
        ]
    )
    if not wants_summon:
        return None

    for role, class_hint, aliases in SUMMON_CLASS_HINTS:
        if any(alias in text for alias in aliases):
            return role, class_hint

    if "稳" in text and ("队" in text or "副本" in text):
        return "healer", "priest"

    return None


def group_setup_actions(event: ChatEvent) -> list[Action]:
    roles = {bot.role for bot in controllable_bots(event)}
    desired = [
        ("tank", "warrior"),
        ("healer", "priest"),
        ("dps", "mage"),
    ]
    return [
        Action("summon_bot", payload={"role": role, "class_hint": class_hint})
        for role, class_hint in desired
        if role not in roles
    ]


def invite_target_from_text(text: str) -> str | None:
    match = re.search(r"(?:邀请|拉|组)\s*([a-z0-9_\-\u4e00-\u9fff]{1,16}?)(?:进队|入队|组队|$)", text)
    if not match:
        return None

    target = match.group(1).strip()
    if not target or target in GENERIC_INVITE_TARGETS or "个" in target:
        return None
    return target


def available_skills(event: ChatEvent, bot: BotInfo) -> list[str]:
    if reply_only_event(event, bot):
        return ["reply", "no_reply"]

    return [
        "reply",
        "no_reply",
        "bot_follow",
        "bot_stay",
        "bot_retreat",
        "bot_runaway",
        "bot_attack_target",
        "bot_pull",
        "bot_ready",
        "focus_heal_add",
        "focus_heal_remove",
        "focus_heal_clear",
        "loot_off",
        "loot_normal",
        "loot_gray",
        "loot_all",
        "buff_on",
        "buff_off",
        "healer_safe",
        "healer_burst",
    ]


def lifecycle_rule_actions(event: ChatEvent) -> list[Action]:
    text = event.message.strip().lower()
    if not text:
        return []

    if not has_lifecycle_context(event):
        return []

    def summon(role: str, class_hint: str) -> Action:
        return Action("summon_bot", payload={"role": role, "class_hint": class_hint})

    if any(token in text for token in ["机器人池", "bot池", "池子", "可用机器人", "lookup"]):
        return [Action("lookup_bot_pool", payload={})]

    if any(token in text for token in ["机器人列表", "bot列表", "bot list", "list bots", "我有哪些机器人", "已有机器人"]):
        return [Action("list_bots", payload={})]

    if ("下副本" in text or "下本" in text or ("副本" in text and any(token in text for token in ["想", "组", "队", "去", "打"]))) and not any(
        token in text for token in ["副本任务", "初始化任务", "quests"]
    ):
        actions = group_setup_actions(event)
        if actions:
            return actions

    summon_intent = summon_intent_from_text(text)
    if summon_intent:
        role, class_hint = summon_intent
        return [summon(role, class_hint)]

    target = lifecycle_target_bot(event, text)
    if target and any(token in text for token in ["副本任务", "初始化任务", "quests"]):
        return [Action("init_instance_quests", payload={"bot": target})]

    if target and any(token in text for token in ["初始化", "配装", "init"]):
        return [Action("init_bot", payload={"bot": target, "mode": "auto"})]

    if target and any(token in text for token in ["刷新", "refresh"]):
        return [Action("refresh_bot", payload={"bot": target})]

    if target and any(token in text for token in ["同步等级", "升级", "levelup", "level"]):
        return [Action("level_bot", payload={"bot": target})]

    if target and any(token in text for token in ["下线", "踢掉", "解散", "不要了", "移除", "dismiss", "remove"]):
        return [Action("dismiss_bot", payload={"bot": target})]

    invite_target = invite_target_from_text(text)
    if invite_target:
        return [Action("invite_player", payload={"target_player": invite_target})]

    return []


def rule_actions(event: ChatEvent, bot: BotInfo) -> list[Action]:
    text = event.message.strip().lower()
    actions: list[Action] = []
    reply_only = reply_only_event(event, bot)

    def say(message: str) -> None:
        actions.append(Action("reply", bot=bot, channel=reply_channel(event), text=message[:80]))

    def command(command_text: str) -> None:
        actions.append(Action("command", bot=bot, command=command_text))

    def strategy(strategy_text: str, state: str) -> None:
        actions.append(Action("strategy", bot=bot, strategy=strategy_text, bot_state=state))

    if location_question(text):
        location_text = location_reply_text(event.context)
        if location_text:
            say(location_text)
            return actions

    if any(token in text for token in ["晚上好", "晚安", "你好", "早上好", "早啊", "hello", "hi"]):
        if "晚安" in text:
            say("晚安，明天见")
        elif "早" in text:
            say("早，来啦")
        else:
            say("晚上好")
        return actions

    if any(token in text for token in ["在吗", "在不在", "在么"]):
        say("在呢")
        return actions

    if reply_only:
        return actions

    if any(token in text for token in ["跟我", "过来", "跟上", "follow"]):
        command("follow")
        say("跟上了")
        return actions

    if any(token in text for token in ["停一下", "停下", "原地", "别动", "stay"]):
        command("stay")
        say("我停这")
        return actions

    if any(token in text for token in ["撤", "快跑", "别打了", "脱战", "flee"]):
        command("flee")
        say("先撤")
        return actions

    if any(token in text for token in ["跑远", "散开", "离远点", "runaway"]):
        command("runaway")
        say("我拉开")
        return actions

    if any(token in text for token in ["打我的目标", "攻击我的目标", "集火", "attack"]):
        command("attack")
        say("打你的目标")
        return actions

    if any(token in text for token in ["拉怪", "开怪", "pull"]):
        command("pull")
        say("我来拉")
        return actions

    if any(token in text for token in ["准备好", "准备了吗", "ready"]):
        command("ready")
        return actions

    if any(token in text for token in ["加我", "奶我", "看我血", "盯我", "保我"]):
        if bot.role == "healer" or bot.klass in {2, 5, 7, 11}:
            command(f"focus heal +{event.speaker_name}")
            say("看你血")
            return actions

    if any(token in text for token in ["不用加我", "别盯我", "不用奶我"]):
        if bot.role == "healer" or bot.klass in {2, 5, 7, 11}:
            command(f"focus heal -{event.speaker_name}")
            say("好")
            return actions

    if any(token in text for token in ["安心奶", "别输出", "别抢仇恨", "别打怪"]):
        if bot.role == "healer" or bot.klass in {2, 5, 7, 11}:
            strategy("-healer dps", "combat")
            say("我专心奶")
            return actions

    if any(token in text for token in ["帮忙输出", "可以输出", "打快点", "爆发"]):
        if bot.role == "healer" or bot.klass in {2, 5, 7, 11}:
            strategy("+healer dps", "combat")
            say("我补点伤害")
            return actions
        command("max dps")
        say("开爆发")
        return actions

    if any(token in text for token in ["别捡", "不要捡", "停捡"]):
        strategy("-loot", "noncombat")
        say("不捡了")
        return actions

    if any(token in text for token in ["全捡", "都捡"]):
        strategy("+loot", "noncombat")
        command("ll all")
        say("全捡")
        return actions

    if any(token in text for token in ["捡垃圾", "灰色也捡"]):
        strategy("+loot", "noncombat")
        command("ll gray")
        say("灰色也捡")
        return actions

    if any(token in text for token in ["补buff", "加buff", "上buff"]):
        strategy("+buff", "noncombat")
        say("补buff")
        return actions

    if any(token in text for token in ["别补buff", "不用buff"]):
        strategy("-buff", "noncombat")
        say("先不补")
        return actions

    return []


class OpenAICompatibleClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 20.0,
        *,
        max_tokens: int = 512,
        thinking: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.thinking = thinking

    @classmethod
    def from_env(cls) -> "OpenAICompatibleClient | NullLLM":
        api_key = os.getenv("OPENAI_API_KEY", "")
        model = os.getenv("PLAYERBOT_AGENT_MODEL", "")
        if not api_key or not model:
            return NullLLM()
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        thinking = normalize_llm_thinking(os.getenv("PLAYERBOT_AGENT_LLM_THINKING", ""), base_url, model)
        return cls(
            base_url,
            api_key,
            model,
            float(os.getenv("PLAYERBOT_AGENT_LLM_TIMEOUT", "20")),
            max_tokens=int(os.getenv("PLAYERBOT_AGENT_LLM_MAX_TOKENS", "512")),
            thinking=thinking,
        )

    @property
    def available(self) -> bool:
        return True

    def request_payload(self, prompt: str, system: str | None = None) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": system or "你是魔兽世界队伍里的 Playerbot 大脑，只输出严格 JSON，不要输出解释。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        if self.max_tokens > 0:
            payload["max_tokens"] = self.max_tokens
        if self.thinking in {"disabled", "enabled"}:
            payload["thinking"] = {"type": self.thinking}
        return payload

    def complete(self, prompt: str, system: str | None = None) -> str:
        return self.complete_result(prompt, system).content

    def complete_result(self, prompt: str, system: str | None = None) -> LLMCompletion:
        payload = self.request_payload(prompt, system)
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except OSError:
                body = ""
            log_agent(
                "llm_error",
                error=f"HTTPError: {exc}",
                http_status=exc.code,
                body=body,
                base_url=self.base_url,
                model=self.model,
                thinking=self.thinking,
            )
            return LLMCompletion("")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            log_agent(
                "llm_error",
                error=f"{type(exc).__name__}: {exc}",
                base_url=self.base_url,
                model=self.model,
                thinking=self.thinking,
            )
            return LLMCompletion("")

        choice = data.get("choices", [{}])[0] if isinstance(data.get("choices"), list) else {}
        message = choice.get("message", {}) if isinstance(choice, dict) else {}
        if not isinstance(message, dict):
            message = {}
        content = str(message.get("content") or "").strip()
        reasoning = str(
            message.get("reasoning_content")
            or message.get("reasoning")
            or message.get("reasoningContent")
            or ""
        ).strip()
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        finish_reason = str(choice.get("finish_reason") or "") if isinstance(choice, dict) else ""
        if reasoning and not content:
            log_agent(
                "llm_empty_content",
                base_url=self.base_url,
                model=self.model,
                thinking=self.thinking,
                reasoning_chars=len(reasoning),
                finish_reason=finish_reason,
                usage=usage,
            )
        return LLMCompletion(content, reasoning, finish_reason, usage, data)


class NullLLM:
    available = False

    def complete(self, prompt: str, system: str | None = None) -> str:
        return ""

    def complete_result(self, prompt: str, system: str | None = None) -> LLMCompletion:
        return LLMCompletion("")


def seconds_text(duration_ms: int | float) -> str:
    seconds = max(0, int(round(float(duration_ms) / 1000.0)))
    return f"{seconds}秒"


def lowest_member(facts: dict[str, Any], key: str) -> dict[str, Any] | None:
    members = [member for member in facts.get("members", []) if isinstance(member, dict)]
    members = [member for member in members if isinstance(member.get(key), (int, float))]
    if not members:
        return None
    return min(members, key=lambda member: float(member.get(key, 101)))


def local_combat_summary(facts: dict[str, Any]) -> str:
    totals = facts.get("totals") if isinstance(facts.get("totals"), dict) else {}
    duration = seconds_text(facts.get("duration_ms", 0))
    kills = int(totals.get("kills") or facts.get("kills") or 0)
    deaths = int(totals.get("deaths") or facts.get("deaths") or 0)
    damage_done = int(totals.get("damage_done") or 0)
    damage_taken = int(totals.get("damage_taken") or 0)
    healing_done = int(totals.get("healing_done") or 0)
    healer_threat = int(totals.get("healer_threat_events") or 0)

    parts = [
        f"上一场战斗持续{duration}",
        f"击杀{kills}个目标",
        f"队伍死亡{deaths}次",
        f"造成伤害{damage_done}",
        f"承受伤害{damage_taken}",
        f"治疗{healing_done}",
    ]

    low_hp = lowest_member(facts, "min_health_pct")
    if low_hp:
        parts.append(f"{low_hp.get('name')}最低血量{float(low_hp.get('min_health_pct', 0)):.0f}%")

    low_mana = lowest_member(facts, "min_mana_pct")
    if low_mana:
        parts.append(f"{low_mana.get('name')}最低蓝量{float(low_mana.get('min_mana_pct', 0)):.0f}%")

    if healer_threat:
        parts.append(f"治疗被怪命中{healer_threat}次，注意仇恨")

    if deaths:
        parts.append("下次优先保命和撤退")
    elif healer_threat:
        parts.append("下次让战士先拉稳，治疗保持安心奶")
    else:
        parts.append("整体可继续按当前节奏推进")

    return "，".join(parts) + "。"


def build_combat_summary_prompt(facts: dict[str, Any]) -> str:
    return (
        "根据这场魔兽世界队伍战斗事实，压缩成一段中文战斗记忆。"
        "关注是否死人、治疗是否危险、是否抢仇恨、是否需要撤退或调整策略。"
        "只允许输出 JSON：{\"summary\":\"一句话复盘\",\"tips\":[\"建议1\",\"建议2\"]}。\n"
        f"战斗事实：{json.dumps(facts, ensure_ascii=False)}"
    )


def build_prompt(
    event: ChatEvent,
    bot: BotInfo,
    history: Iterable[tuple[float, str, str, str, str]],
    combat_memory: Iterable[CombatMemory] = (),
    recent_action_results: Iterable[dict[str, Any]] = (),
    *,
    recent_message_limit: int = PROMPT_MESSAGE_LIMIT,
) -> str:
    recent = [
        {
            "channel": channel,
            "speaker": speaker,
            "target": target or None,
            "message": message,
        }
        for _, channel, speaker, target, message in history
    ]
    payload = {
        "bot": dataclasses.asdict(bot),
        "event": {
            "channel": event.channel,
            "speaker": event.speaker_name,
            "message": event.message,
            "target": event.target_name,
        },
        "world_context": event.context,
        "recent_messages": recent[-max(1, recent_message_limit):],
        "recent_combat_summaries": [dataclasses.asdict(memory) for memory in combat_memory],
        "recent_action_results": list(recent_action_results),
        "reply_only": reply_only_event(event, bot),
        "available_skills": available_skills(event, bot),
    }
    return (
        "根据输入判断是否需要这个 bot 回复或行动。回复要短，像游戏队友，不要提 AI/模型。\n"
        "只能使用 available_skills 里的技能；reply_only=true 时只能闲聊回复或 no_reply，不能控制移动、战斗、拾取、治疗目标。\n"
        "位置判断必须优先使用 world_context.environment.location 或 speaker.location 里的 map_name/zone_name/area_name；"
        "不要凭 map_id/zone_id/area_id 数字猜地名。只有大区域时要说只能确认大区域。\n"
        "只允许输出 JSON：{\"reply\":{\"channel\":\"party|whisper|say\",\"text\":\"...\"}|null,"
        "\"actions\":[{\"skill\":\"...\",\"args\":{}}]}。\n"
        f"输入：{json.dumps(payload, ensure_ascii=False)}"
    )


def actions_from_llm_payload(payload: dict[str, Any], event: ChatEvent, bot: BotInfo) -> list[Action]:
    actions: list[Action] = []
    allowed = set(available_skills(event, bot))
    reply = payload.get("reply")
    if isinstance(reply, dict):
        text = str(reply.get("text", "")).strip()
        if text:
            channel = str(reply.get("channel") or reply_channel(event))
            if channel not in {"party", "whisper", "say"}:
                channel = reply_channel(event)
            actions.append(Action("reply", bot=bot, channel=channel, text=text[:120]))

    for item in payload.get("actions", []):
        if not isinstance(item, dict):
            continue
        skill = str(item.get("skill", ""))
        if skill not in allowed:
            continue
        args = item.get("args") if isinstance(item.get("args"), dict) else {}
        actions.extend(skill_to_actions(skill, args, event, bot))
    return actions


def skill_to_actions(skill: str, args: dict[str, Any], event: ChatEvent, bot: BotInfo) -> list[Action]:
    command_map = {
        "bot_follow": "follow",
        "bot_stay": "stay",
        "bot_retreat": "flee",
        "bot_runaway": "runaway",
        "bot_attack_target": "attack",
        "bot_pull": "pull",
        "bot_ready": "ready",
    }
    if skill in command_map:
        return [Action("command", bot=bot, command=command_map[skill])]
    if skill == "focus_heal_add":
        target = str(args.get("player") or event.speaker_name)
        return [Action("command", bot=bot, command=f"focus heal +{target}")]
    if skill == "focus_heal_remove":
        target = str(args.get("player") or event.speaker_name)
        return [Action("command", bot=bot, command=f"focus heal -{target}")]
    if skill == "focus_heal_clear":
        return [Action("command", bot=bot, command="focus heal clear")]
    if skill == "loot_off":
        return [Action("strategy", bot=bot, strategy="-loot", bot_state="noncombat")]
    if skill == "loot_gray":
        return [
            Action("strategy", bot=bot, strategy="+loot", bot_state="noncombat"),
            Action("command", bot=bot, command="ll gray"),
        ]
    if skill == "loot_all":
        return [
            Action("strategy", bot=bot, strategy="+loot", bot_state="noncombat"),
            Action("command", bot=bot, command="ll all"),
        ]
    if skill == "loot_normal":
        return [
            Action("strategy", bot=bot, strategy="+loot", bot_state="noncombat"),
            Action("command", bot=bot, command="ll normal"),
        ]
    if skill == "buff_on":
        return [Action("strategy", bot=bot, strategy="+buff", bot_state="noncombat")]
    if skill == "buff_off":
        return [Action("strategy", bot=bot, strategy="-buff", bot_state="noncombat")]
    if skill == "healer_safe":
        return [Action("strategy", bot=bot, strategy="-healer dps", bot_state="combat")]
    if skill == "healer_burst":
        return [Action("strategy", bot=bot, strategy="+healer dps", bot_state="combat")]
    return []


def default_dsn_from_conf() -> str:
    conf = Path("env/dist/etc/modules/playerbots.conf")
    if conf.exists():
        match = re.search(r'^\s*PlayerbotsDatabaseInfo\s*=\s*"([^"]+)"', conf.read_text(errors="ignore"), re.M)
        if match:
            return match.group(1)
    return "127.0.0.1;3306;acore;acore;acore_playerbots"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Playerbot Agent Python sidecar")
    parser.add_argument("--once", action="store_true", help="poll once and exit")
    parser.add_argument("--replay", action="store_true", help="start from event id 0 instead of skipping old events")
    parser.add_argument(
        "--rule-only",
        action="store_true",
        default=env_bool("PLAYERBOT_AGENT_RULE_ONLY", False),
        help="disable LLM calls even when OPENAI_API_KEY is set; defaults from PLAYERBOT_AGENT_RULE_ONLY",
    )
    parser.add_argument("--poll-interval", type=float, default=float(os.getenv("PLAYERBOT_AGENT_POLL_INTERVAL", "1")))
    parser.add_argument("--state", default=os.getenv("PLAYERBOT_AGENT_STATE", "var/playerbot-agent/state.json"))
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    dsn = os.getenv("PLAYERBOT_AGENT_DB_DSN", default_dsn_from_conf())
    bridge = AgentBridge(MysqlCli(dsn), Path(args.state), replay=args.replay, rule_only=args.rule_only)

    try:
        while True:
            try:
                count = bridge.poll_once()
                if count:
                    print(
                        f"processed {count} item(s), last_id={bridge.last_id}, last_combat_id={bridge.last_combat_id}",
                        flush=True,
                    )
            except Exception as exc:
                print(f"playerbot-agent error: {exc}", file=sys.stderr, flush=True)
                if args.once:
                    return 1
            if args.once:
                return 0
            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        log_agent("shutdown", reason="interrupt")
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
