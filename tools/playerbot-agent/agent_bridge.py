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

ATTENTION_SECONDS = 30
RECENT_MESSAGE_LIMIT = 80
RECENT_MESSAGE_TTL = 3600


@dataclasses.dataclass(frozen=True)
class BotInfo:
    guid: int
    name: str
    klass: int
    level: int
    role: str


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
    bot: BotInfo
    channel: str | None = None
    text: str | None = None
    command: str | None = None
    strategy: str | None = None
    bot_state: str | None = None


@dataclasses.dataclass(frozen=True)
class CombatMemory:
    id: int
    created_at: str
    leader: str
    summary: str


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def truncate(value: Any, limit: int = 4000) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"...<truncated {len(value) - limit} chars>"
    return value


def log_agent(event: str, **fields: Any) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "event": event,
        **{key: truncate(value) for key, value in fields.items()},
    }
    print(json.dumps(record, ensure_ascii=False, separators=(",", ":")), flush=True)


def action_to_dict(action: Action) -> dict[str, Any]:
    return {
        "type": action.action_type,
        "bot": action.bot.name,
        "channel": action.channel,
        "text": action.text,
        "command": action.command,
        "strategy": action.strategy,
        "bot_state": action.bot_state,
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
        self.history: deque[tuple[float, str, str, str]] = deque(maxlen=RECENT_MESSAGE_LIMIT)
        self.combat_memory: deque[CombatMemory] = deque(
            maxlen=int(os.getenv("PLAYERBOT_AGENT_COMBAT_MEMORY_LIMIT", "3"))
        )
        state = self._load_state(replay)
        self.last_id = state["last_id"]
        self.last_combat_id = state["last_combat_id"]
        self.llm = OpenAICompatibleClient.from_env()
        self.trace = env_bool("PLAYERBOT_AGENT_TRACE", False)
        self.trace_prompt = env_bool("PLAYERBOT_AGENT_TRACE_PROMPT", self.trace)
        self._load_recent_combat_memory()
        log_agent(
            "startup",
            rule_only=self.rule_only,
            llm_available=self.llm.available,
            llm_model=getattr(self.llm, "model", ""),
            llm_base_url=getattr(self.llm, "base_url", ""),
            llm_thinking=getattr(self.llm, "thinking", ""),
            last_id=self.last_id,
            last_combat_id=self.last_combat_id,
            poll_limit=self.poll_limit,
        )

    def _safe_scalar_int(self, sql: str) -> int:
        try:
            return self.db.scalar_int(sql)
        except RuntimeError:
            return 0

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
            payload = json.loads(meta) if meta else {}
            bots = [
                BotInfo(
                    guid=int(bot.get("guid", 0)),
                    name=str(bot.get("name", "")),
                    klass=int(bot.get("class", 0)),
                    level=int(bot.get("level", 0)),
                    role=str(bot.get("role", "unknown")),
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
        response = self.llm.complete(
            prompt,
            system="你是魔兽世界队伍战斗复盘器，只输出严格 JSON，不要输出解释。",
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        log_agent("combat_summary_response", id=combat_id, latency_ms=latency_ms, response=response)

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
                "`command`, `strategy`, `bot_state`, TO_BASE64(COALESCE(`result`, '')), "
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
                    "result": decode_b64(row[8]),
                    "error": decode_b64(row[9]),
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
        self.history.append((now, event.channel, event.speaker_name, event.message))
        while self.history and now - self.history[0][0] > RECENT_MESSAGE_TTL:
            self.history.popleft()

    def _is_for_bot(self, event: ChatEvent, bot: BotInfo, now: float) -> bool:
        if event.channel == "whisper" and event.bot_guid == bot.guid:
            self.attention_until[bot.guid] = now + ATTENTION_SECONDS
            return True

        if contains_alias(event.message, bot):
            self.attention_until[bot.guid] = now + ATTENTION_SECONDS
            return True

        return event.channel in {"party", "raid", "say", "yell"} and self.attention_until[bot.guid] > now

    def llm_actions(self, event: ChatEvent, bot: BotInfo) -> list[Action]:
        recent_action_results = self.fetch_recent_action_results(event, bot)
        prompt = build_prompt(event, bot, self.history, self.combat_memory, recent_action_results)
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
        response = self.llm.complete(prompt)
        latency_ms = int((time.monotonic() - started) * 1000)
        log_agent("llm_response", event_id=event.id, bot=bot.name, latency_ms=latency_ms, response=response)
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
        sql = (
            "INSERT INTO `agent_playerbot_actions` "
            "(`source_event_id`, `requester_guid`, `requester_name`, `bot_guid`, `bot_name`, "
            "`action_type`, `channel`, `text`, `command`, `strategy`, `bot_state`) VALUES "
            f"({event.id}, {event.speaker_guid}, {sql_quote(event.speaker_name)}, "
            f"{action.bot.guid}, {sql_quote(action.bot.name)}, {sql_quote(action.action_type)}, "
            f"{sql_quote(action.channel)}, {sql_quote(action.text)}, {sql_quote(action.command)}, "
            f"{sql_quote(action.strategy)}, {sql_quote(action.bot_state)})"
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


def rule_actions(event: ChatEvent, bot: BotInfo) -> list[Action]:
    text = event.message.strip().lower()
    actions: list[Action] = []

    def say(message: str) -> None:
        actions.append(Action("reply", bot=bot, channel=reply_channel(event), text=message[:80]))

    def command(command_text: str) -> None:
        actions.append(Action("command", bot=bot, command=command_text))

    def strategy(strategy_text: str, state: str) -> None:
        actions.append(Action("strategy", bot=bot, strategy=strategy_text, bot_state=state))

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
        thinking = os.getenv("PLAYERBOT_AGENT_LLM_THINKING", "").strip().lower()
        if not thinking and "deepseek" in base_url.lower() and model.startswith("deepseek-v4"):
            thinking = "disabled"
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
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            log_agent("llm_error", error=f"{type(exc).__name__}: {exc}", base_url=self.base_url, model=self.model)
            return ""
        return str(data.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()


class NullLLM:
    available = False

    def complete(self, prompt: str, system: str | None = None) -> str:
        return ""


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
    history: Iterable[tuple[float, str, str, str]],
    combat_memory: Iterable[CombatMemory] = (),
    recent_action_results: Iterable[dict[str, Any]] = (),
) -> str:
    recent = [
        {"channel": channel, "speaker": speaker, "message": message}
        for _, channel, speaker, message in history
    ]
    payload = {
        "bot": dataclasses.asdict(bot),
        "event": {
            "channel": event.channel,
            "speaker": event.speaker_name,
            "message": event.message,
        },
        "world_context": event.context,
        "recent_messages": recent[-20:],
        "recent_combat_summaries": [dataclasses.asdict(memory) for memory in combat_memory],
        "recent_action_results": list(recent_action_results),
        "available_skills": [
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
        ],
    }
    return (
        "根据输入判断是否需要这个 bot 回复或行动。回复要短，像游戏队友，不要提 AI/模型。\n"
        "只允许输出 JSON：{\"reply\":{\"channel\":\"party|whisper|say\",\"text\":\"...\"}|null,"
        "\"actions\":[{\"skill\":\"...\",\"args\":{}}]}。\n"
        f"输入：{json.dumps(payload, ensure_ascii=False)}"
    )


def actions_from_llm_payload(payload: dict[str, Any], event: ChatEvent, bot: BotInfo) -> list[Action]:
    actions: list[Action] = []
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
    parser.add_argument("--rule-only", action="store_true", help="disable LLM calls even when OPENAI_API_KEY is set")
    parser.add_argument("--poll-interval", type=float, default=float(os.getenv("PLAYERBOT_AGENT_POLL_INTERVAL", "1")))
    parser.add_argument("--state", default=os.getenv("PLAYERBOT_AGENT_STATE", "var/playerbot-agent/state.json"))
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    dsn = os.getenv("PLAYERBOT_AGENT_DB_DSN", default_dsn_from_conf())
    bridge = AgentBridge(MysqlCli(dsn), Path(args.state), replay=args.replay, rule_only=args.rule_only)

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


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
