#!/usr/bin/env python3
"""Shared database and logging helpers for the WoW PlayerBot MCP bridge."""

from __future__ import annotations

import base64
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any


DEFAULT_DB_DSN = "127.0.0.1;3306;acore;acore;acore_playerbots"
DEFAULT_AUTH_DB_DSN = "127.0.0.1;3306;acore;acore;acore_auth"
DEFAULT_WORLD_DB_DSN = "127.0.0.1;3306;acore;acore;acore_playerbot_world"
DEFAULT_CHARACTERS_DB_DSN = "127.0.0.1;3306;acore;acore;acore_playerbot_characters"


EVENT_ZH: dict[str, str] = {
    "startup": "服务启动",
    "startup_backlog_skipped": "启动时跳过积压事件",
    "shutdown": "服务停止",
    "poll_error": "轮询异常",
    "relay_event": "转发游戏事件",
    "relay_event_skipped": "游戏事件已跳过",
    "hermes_request": "请求 Hermes",
    "hermes_response": "收到 Hermes 响应",
    "hermes_http_error": "Hermes HTTP 错误",
    "hermes_error": "Hermes 调用异常",
    "fast_consumable_request": "快捷补给请求",
    "fast_consumable_no_mage": "快捷补给无可用法师",
    "fast_consumable_reply": "快捷补给结果回复",
    "fast_intrinsic_command": "快捷跳过本能命令",
    "fast_team_online_request": "快捷叫回离线队友",
    "fast_team_online_no_offline_group_bots": "快捷叫回队友无离线机器人",
    "fallback_reply_enqueued": "兜底回复已入队",
    "fallback_reply_skipped": "兜底回复跳过",
    "action_enqueued": "动作已入队",
    "action_dedupe": "动作去重",
    "action_rejected_stale_event": "动作已拦截：旧事件",
    "tool_recent_events": "工具调用：读取最近事件",
    "tool_party_state": "工具调用：读取队伍状态",
    "tool_location": "工具调用：读取当前位置",
    "tool_action_results": "工具调用：读取动作结果",
    "tool_last_command_diagnostic": "工具调用：读取上一条指令诊断",
    "tool_session_goals": "工具调用：读取会话目标记忆",
    "tool_set_session_goal": "工具调用：写入会话目标记忆",
    "tool_player_quests": "工具调用：读取玩家任务",
    "tool_search_quests": "工具调用：搜索任务库",
    "tool_quest_details": "工具调用：读取任务详情",
    "tool_quest_guide": "工具调用：任务陪伴指南",
    "tool_resolve_place": "工具调用：解析地点",
    "tool_plan_route": "工具调用：规划路线",
    "tool_group_travel_status": "工具调用：读取队伍行进状态",
    "tool_start_bot_travel": "工具调用：开始机器人集合",
    "tool_inventory_for_trade": "工具调用：读取交易背包",
    "tool_search_auction": "工具调用：搜索拍卖行",
    "tool_estimate_item_value": "工具调用：估算物品价值",
    "tool_plan_auction_sales": "工具调用：规划拍卖出售",
    "tool_recent_combat_summaries": "工具调用：读取最近战斗摘要",
    "tool_combat_summary": "工具调用：读取战斗详情",
    "tool_playerbot_command_catalog": "工具调用：读取 Playerbot 命令目录",
    "tool_bot_profile": "工具调用：读取机器人画像",
    "tool_supported_bot_strategies": "工具调用：读取机器人策略清单",
    "tool_reply": "工具调用：机器人回复",
    "tool_summon_bot": "工具调用：召唤机器人",
    "tool_init_bot": "工具调用：初始化机器人",
    "tool_dismiss_bot": "工具调用：机器人下线",
    "tool_refresh_bot": "工具调用：刷新机器人",
    "tool_level_bot": "工具调用：升级/重随机机器人",
    "tool_init_instance_quests": "工具调用：初始化副本任务",
    "tool_list_bots": "工具调用：列出机器人",
    "tool_lookup_bot_pool": "工具调用：查询可召唤机器人池",
    "tool_run_playerbot_command": "工具调用：执行 Playerbot 命令",
    "tool_run_playerbot_command_dry_run": "工具调用：预演 Playerbot 命令",
    "tool_admin_denied": "工具调用：高权限动作已拒绝",
    "tool_invite_player": "工具调用：邀请玩家入队",
    "tool_provide_consumables": "工具调用：提供法师水和面包",
    "tool_set_bot_strategy": "工具调用：切换机器人策略",
    "tool_set_bot_role": "工具调用：切换机器人职责",
}


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
        return value[:limit] + f"...<已截断 {len(value) - limit} 个字符>"
    if isinstance(value, dict):
        if depth > 8:
            return "<已截断过深字典>"
        return {key: truncate(item, limit, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        if depth > 8:
            return "<已截断过深列表>"
        items = [truncate(item, limit, depth + 1) for item in value[:80]]
        if len(value) > 80:
            items.append(f"<已截断 {len(value) - 80} 项>")
        return items
    return value


def log_event(event: str, **fields: Any) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "event": event,
        "event_zh": EVENT_ZH.get(event, event),
        **{key: truncate(value) for key, value in fields.items()},
    }
    print(json.dumps(record, ensure_ascii=False, separators=(",", ":")), flush=True)


def decode_b64(value: str | None) -> str:
    if not value:
        return ""
    return base64.b64decode(value).decode("utf-8", errors="replace")


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


def sql_like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return sql_quote(f"%{escaped}%")


def sql_uint(value: int | None) -> str:
    if value is None:
        return "NULL"
    return str(int(value))


class MysqlCli:
    """Small mysql CLI adapter so services do not need a Python DB dependency."""

    def __init__(self, dsn: str) -> None:
        parts = dsn.split(";")
        if len(parts) != 5:
            raise ValueError("DB DSN must be host;port;user;password;database")
        self.host, self.port, self.user, self.password, self.database = parts

    @classmethod
    def from_env(cls, name: str = "PLAYERBOT_MCP_DB_DSN") -> "MysqlCli":
        return cls(os.getenv(name, os.getenv("PLAYERBOT_AGENT_DB_DSN", DEFAULT_DB_DSN)))

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


def json_from_b64(value: str | None) -> dict[str, Any]:
    text = decode_b64(value)
    if not text:
        return {}
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        repaired = repair_legacy_teamid_json(text)
        if repaired == text:
            return {}
        try:
            payload = json.loads(repaired)
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}


def repair_legacy_teamid_json(text: str) -> str:
    """Repair old event meta where C++ streamed TeamId:uint8 as raw bytes."""
    return (
        text.replace('"team":\x00', '"team":0')
        .replace('"team":\x01', '"team":1')
        .replace('"team":\x02', '"team":2')
    )


def clean_wow_text(text: str) -> str:
    replacements = {
        "$B": "\n",
        "$b": "\n",
        "$N": "{玩家}",
        "$n": "{玩家}",
        "$R": "{种族}",
        "$r": "{种族}",
        "$C": "{职业}",
        "$c": "{职业}",
    }
    result = text.replace("\r\n", "\n").replace("\r", "\n")
    for old, new in replacements.items():
        result = result.replace(old, new)
    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")
    return result.strip()


def int_value(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def float_value(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_state(path: Path) -> dict[str, int]:
    if not path.exists():
        return {"last_id": 0}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"last_id": 0}
    return {"last_id": int(payload.get("last_id", 0))}


def save_state(path: Path, state: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def event_row_to_dict(row: list[str | None]) -> dict[str, Any]:
    meta = json_from_b64(row[11])
    return {
        "id": int(row[0] or 0),
        "created_at": str(row[1] or ""),
        "channel": str(row[2] or ""),
        "speaker_guid": int(row[3] or 0),
        "speaker_account": int(row[4] or 0),
        "speaker_name": str(row[5] or ""),
        "target_guid": int(row[6]) if row[6] else None,
        "target_name": str(row[7]) if row[7] else None,
        "bot_guid": int(row[8]) if row[8] else None,
        "bot_name": str(row[9]) if row[9] else None,
        "group_leader_guid": int(row[10]) if row[10] else None,
        "message": decode_b64(row[12]),
        "context": meta,
    }


COMPACT_EVENT_MESSAGE_LIMIT = 400
COMPACT_CONTEXT_TEXT_LIMIT = 180
COMPACT_CONTEXT_ACTOR_LIMIT = 8


def compact_text(value: Any, limit: int = COMPACT_CONTEXT_TEXT_LIMIT) -> str:
    text = "" if value is None else str(value)
    bounded = max(16, int(limit))
    if len(text) <= bounded:
        return text
    return text[:bounded] + f"...<已截断 {len(text) - bounded} 个字符>"


def compact_scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    return compact_text(value)


def compact_location(location: Any) -> dict[str, Any]:
    if not isinstance(location, dict):
        return {}
    result: dict[str, Any] = {}
    for key in ("map_name", "zone_name", "area_name", "name", "map_id", "zone_id", "area_id"):
        value = location.get(key)
        if value not in (None, ""):
            result[key] = compact_scalar(value)
    return result


def compact_actor(actor: Any) -> dict[str, Any]:
    if not isinstance(actor, dict):
        return {}
    result: dict[str, Any] = {}
    for key in (
        "guid",
        "name",
        "level",
        "class",
        "class_name",
        "race",
        "race_name",
        "team",
        "online",
        "alive",
        "combat",
        "bot_kind",
        "role",
        "spec",
        "distance",
        "map_id",
        "zone_id",
        "area_id",
    ):
        value = actor.get(key)
        if value not in (None, ""):
            result[key] = compact_scalar(value)
    location = compact_location(actor.get("location"))
    if location:
        result["location"] = location
    return result


def compact_actor_list(items: Any, limit: int = COMPACT_CONTEXT_ACTOR_LIMIT) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    actors = [compact_actor(item) for item in items if isinstance(item, dict)]
    actors = [actor for actor in actors if actor]
    bounded = max(1, int(limit))
    result = actors[:bounded]
    if len(actors) > bounded:
        result.append({"omitted": len(actors) - bounded})
    return result


def compact_context(event: dict[str, Any]) -> dict[str, Any]:
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    if not context:
        return {}
    result: dict[str, Any] = {}
    speaker = compact_actor(context.get("speaker"))
    if speaker:
        result["speaker"] = speaker
    environment = context.get("environment") if isinstance(context.get("environment"), dict) else {}
    environment_location = compact_location(environment.get("location")) if environment else {}
    if environment_location:
        result["environment"] = {"location": environment_location}
    target_bot = compact_actor(context.get("target_bot_context"))
    if target_bot:
        result["target_bot_context"] = target_bot
    bots = compact_actor_list(context.get("bots"))
    if bots:
        result["bots"] = bots
    members = compact_actor_list(context.get("group_members"))
    if members:
        result["group_members"] = members
    combat = context.get("combat") if isinstance(context.get("combat"), dict) else {}
    if combat:
        result["combat"] = {
            key: compact_scalar(combat[key])
            for key in ("in_combat", "active", "started_at", "ended_at", "duration_ms", "summary_id")
            if combat.get(key) not in (None, "")
        }
    omitted = sorted(
        key
        for key in context
        if key not in {"speaker", "environment", "target_bot_context", "bots", "group_members", "combat"}
    )
    if omitted:
        result["omitted_context_keys"] = omitted[:16]
        if len(omitted) > 16:
            result["omitted_context_keys"].append(f"<已省略 {len(omitted) - 16} 项>")
    return result


def event_context_summary(event: dict[str, Any]) -> dict[str, Any]:
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    summary: dict[str, Any] = {"context_available": bool(context)}
    if not context:
        return summary

    speaker = context.get("speaker") if isinstance(context.get("speaker"), dict) else {}
    environment = context.get("environment") if isinstance(context.get("environment"), dict) else {}
    location = compact_location(environment.get("location") if environment else {})
    if not location:
        location = compact_location(speaker.get("location") if speaker else {})
    if location:
        summary["location"] = location

    raw_members = context.get("group_members") if isinstance(context.get("group_members"), list) else []
    raw_bots = context.get("bots") if isinstance(context.get("bots"), list) else []
    bot_names: list[str] = []
    player_names: list[str] = []
    online_count = 0
    for member in raw_members:
        if not isinstance(member, dict):
            continue
        name = str(member.get("name") or "").strip()
        if member.get("online", True):
            online_count += 1
        if str(member.get("bot_kind") or ""):
            if name:
                bot_names.append(name)
        elif name:
            player_names.append(name)
    for bot in raw_bots:
        if not isinstance(bot, dict):
            continue
        name = str(bot.get("name") or "").strip()
        if name and name not in bot_names:
            bot_names.append(name)
    if raw_members or raw_bots:
        summary["party"] = {
            "members": len(raw_members),
            "online": online_count if raw_members else None,
            "bots": bot_names[:COMPACT_CONTEXT_ACTOR_LIMIT],
            "players": player_names[:COMPACT_CONTEXT_ACTOR_LIMIT],
        }

    combat = context.get("combat") if isinstance(context.get("combat"), dict) else {}
    member_in_combat = any(
        bool(item.get("combat") or item.get("in_combat"))
        for item in raw_members
        if isinstance(item, dict)
    )
    speaker_in_combat = bool(speaker.get("combat") or speaker.get("in_combat")) if speaker else False
    summary["combat"] = {
        "in_combat": bool(combat.get("in_combat") or combat.get("active") or speaker_in_combat or member_in_combat),
        "has_combat_context": bool(combat or context.get("recent_combat") or context.get("combat_summaries")),
    }
    summary["available_context_keys"] = sorted(context.keys())[:16]
    return summary


def compact_event(event: dict[str, Any], *, include_context: bool = False) -> dict[str, Any]:
    message = str(event.get("message") or "")
    result: dict[str, Any] = {
        "id": event.get("id"),
        "created_at": event.get("created_at"),
        "channel": event.get("channel"),
        "speaker": {
            "guid": event.get("speaker_guid"),
            "account": event.get("speaker_account"),
            "name": event.get("speaker_name"),
        },
        "message": compact_text(message, COMPACT_EVENT_MESSAGE_LIMIT),
        "context_available": isinstance(event.get("context"), dict) and bool(event.get("context")),
        "context_summary": event_context_summary(event),
    }
    if len(message) > COMPACT_EVENT_MESSAGE_LIMIT:
        result["message_truncated"] = True
    if event.get("target_guid") or event.get("target_name"):
        result["target"] = {"guid": event.get("target_guid"), "name": event.get("target_name")}
    if event.get("bot_guid") or event.get("bot_name"):
        result["bot"] = {"guid": event.get("bot_guid"), "name": event.get("bot_name")}
    if event.get("group_leader_guid"):
        result["group_leader_guid"] = event.get("group_leader_guid")
    if include_context:
        result["context"] = compact_context(event)
        result["context_is_compacted"] = True
    return result


def fetch_events(
    db: MysqlCli,
    *,
    after_id: int = 0,
    limit: int = 20,
    unprocessed_only: bool = False,
    max_id: int = 0,
    speaker_guid: int = 0,
    group_leader_guid: int = 0,
    channel: str = "",
    newest_first: bool = False,
) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), 100))
    filters = [f"`id` > {int(after_id)}"]
    if int(max_id) > 0:
        filters.append(f"`id` <= {int(max_id)}")
    if int(speaker_guid) > 0:
        filters.append(f"`speaker_guid` = {int(speaker_guid)}")
    if int(group_leader_guid) > 0:
        filters.append(f"`group_leader_guid` = {int(group_leader_guid)}")
    clean_channel = str(channel or "").strip()
    if clean_channel:
        filters.append(f"`channel` = {sql_quote(clean_channel)}")
    if unprocessed_only:
        filters.append("`processed_at` IS NULL")
    where = " AND ".join(filters)
    order = "DESC" if newest_first else "ASC"
    rows = db.query_rows(
        "SELECT `id`, `created_at`, `channel`, `speaker_guid`, `speaker_account`, `speaker_name`, "
        "`target_guid`, `target_name`, `bot_guid`, `bot_name`, `group_leader_guid`, "
        "TO_BASE64(COALESCE(`meta`, '')), TO_BASE64(`message`) "
        "FROM `agent_playerbot_events` "
        f"WHERE {where} ORDER BY `id` {order} LIMIT {bounded_limit}"
    )
    return [event_row_to_dict(row) for row in rows]


def fetch_event(db: MysqlCli, event_id: int) -> dict[str, Any] | None:
    rows = db.query_rows(
        "SELECT `id`, `created_at`, `channel`, `speaker_guid`, `speaker_account`, `speaker_name`, "
        "`target_guid`, `target_name`, `bot_guid`, `bot_name`, `group_leader_guid`, "
        "TO_BASE64(COALESCE(`meta`, '')), TO_BASE64(`message`) "
        "FROM `agent_playerbot_events` "
        f"WHERE `id` = {int(event_id)} LIMIT 1"
    )
    return event_row_to_dict(rows[0]) if rows else None


def latest_event(db: MysqlCli, speaker_guid: int = 0) -> dict[str, Any] | None:
    where = f"WHERE `speaker_guid` = {int(speaker_guid)}" if speaker_guid else ""
    rows = db.query_rows(
        "SELECT `id`, `created_at`, `channel`, `speaker_guid`, `speaker_account`, `speaker_name`, "
        "`target_guid`, `target_name`, `bot_guid`, `bot_name`, `group_leader_guid`, "
        "TO_BASE64(COALESCE(`meta`, '')), TO_BASE64(`message`) "
        "FROM `agent_playerbot_events` "
        f"{where} ORDER BY `id` DESC LIMIT 1"
    )
    return event_row_to_dict(rows[0]) if rows else None


def fetch_action_results(db: MysqlCli, event_id: int, limit: int = 8) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), 50))
    rows = db.query_rows(
        "SELECT `id`, `created_at`, `updated_at`, `status`, `action_type`, "
        "`bot_name`, `channel`, TO_BASE64(COALESCE(`text`, '')), "
        "`command`, `strategy`, `bot_state`, TO_BASE64(COALESCE(`payload_json`, '')), "
        "TO_BASE64(COALESCE(`result`, '')), TO_BASE64(COALESCE(`error`, '')) "
        "FROM `agent_playerbot_actions` "
        f"WHERE `source_event_id` = {int(event_id)} ORDER BY `id` DESC LIMIT {bounded_limit}"
    )
    return action_rows_to_dicts(rows)


def action_rows_to_dicts(rows: list[list[str | None]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for row in rows:
        payload_text = decode_b64(row[11])
        try:
            payload: Any = json.loads(payload_text) if payload_text else {}
        except json.JSONDecodeError:
            payload = payload_text
        results.append(
            {
                "id": int(row[0] or 0),
                "created_at": str(row[1] or ""),
                "updated_at": str(row[2] or ""),
                "status": str(row[3] or ""),
                "action_type": str(row[4] or ""),
                "bot_name": str(row[5] or ""),
                "channel": str(row[6] or ""),
                "text": decode_b64(row[7]),
                "command": str(row[8] or ""),
                "strategy": str(row[9] or ""),
                "bot_state": str(row[10] or ""),
                "payload": payload,
                "result": decode_b64(row[12]),
                "error": decode_b64(row[13]),
            }
        )
    return results


def fetch_recent_context_actions(db: MysqlCli, event: dict[str, Any], limit: int = 12) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), 50))
    event_id = int(event.get("id") or 0)
    speaker_guid = int(event.get("speaker_guid") or 0)
    group_leader_guid = int(event.get("group_leader_guid") or 0)
    clauses = [f"a.`source_event_id` = {event_id}"]
    if speaker_guid:
        clauses.append(f"a.`requester_guid` = {speaker_guid}")
    if group_leader_guid:
        clauses.append(f"e.`group_leader_guid` = {group_leader_guid}")
    rows = db.query_rows(
        "SELECT a.`id`, a.`created_at`, a.`updated_at`, a.`status`, a.`action_type`, "
        "a.`bot_name`, a.`channel`, TO_BASE64(COALESCE(a.`text`, '')), "
        "a.`command`, a.`strategy`, a.`bot_state`, TO_BASE64(COALESCE(a.`payload_json`, '')), "
        "TO_BASE64(COALESCE(a.`result`, '')), TO_BASE64(COALESCE(a.`error`, '')) "
        "FROM `agent_playerbot_actions` a "
        "LEFT JOIN `agent_playerbot_events` e ON e.`id` = a.`source_event_id` "
        f"WHERE {' OR '.join(clauses)} ORDER BY a.`id` DESC LIMIT {bounded_limit}"
    )
    return action_rows_to_dicts(rows)


def summarize_action_results(actions: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    failures: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    successes: list[dict[str, Any]] = []
    for action in actions:
        status = str(action.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
        brief = {
            "id": action.get("id"),
            "type": action.get("action_type"),
            "bot": action.get("bot_name"),
            "command": action.get("command") or action.get("strategy") or action.get("channel"),
            "result": action.get("result"),
            "error": action.get("error"),
        }
        if status == "error":
            failures.append(brief)
        elif status in {"pending", "running"}:
            pending.append(brief)
        elif status == "done":
            successes.append(brief)

    if failures:
        phase = "failed"
        next_suggestion = "先按失败原因处理；如果是 bot 不在线、死亡或距离过远，先集合或换一个可控 bot。"
    elif pending:
        phase = "running"
        next_suggestion = "动作已提交但还没回写结果，稍后再查一次。"
    elif successes:
        phase = "succeeded"
        next_suggestion = "可以继续下一步。"
    else:
        phase = "empty"
        next_suggestion = "没有找到最近动作结果，可能刚才只是查询或消息没有触发动作。"

    return {
        "phase": phase,
        "counts": counts,
        "successes": successes[:5],
        "failures": failures[:5],
        "pending": pending[:5],
        "next_suggestion": next_suggestion,
    }


def combat_summary_row_to_dict(row: list[str | None]) -> dict[str, Any]:
    facts_text = decode_b64(row[11] if len(row) > 11 else "")
    try:
        facts_payload: Any = json.loads(facts_text) if facts_text else {}
    except json.JSONDecodeError:
        facts_payload = {"raw": facts_text}
    facts = facts_payload if isinstance(facts_payload, dict) else {"value": facts_payload}
    return {
        "id": int_value(row[0] if len(row) > 0 else 0),
        "created_at": str(row[1] if len(row) > 1 and row[1] is not None else ""),
        "summarized_at": str(row[2] if len(row) > 2 and row[2] is not None else ""),
        "group_leader_guid": int_value(row[3] if len(row) > 3 else 0),
        "group_leader_name": str(row[4] if len(row) > 4 and row[4] is not None else ""),
        "map_id": int_value(row[5] if len(row) > 5 else 0),
        "zone_id": int_value(row[6] if len(row) > 6 else 0),
        "area_id": int_value(row[7] if len(row) > 7 else 0),
        "duration_ms": int_value(row[8] if len(row) > 8 else 0),
        "kills": int_value(row[9] if len(row) > 9 else 0),
        "deaths": int_value(row[10] if len(row) > 10 else 0),
        "facts": facts,
        "summary_text": decode_b64(row[12] if len(row) > 12 else ""),
    }


def _combat_member_brief(member: dict[str, Any]) -> dict[str, Any]:
    brief: dict[str, Any] = {
        "name": str(member.get("name") or ""),
        "role": str(member.get("role") or ""),
        "is_bot": bool(member.get("is_bot")),
        "damage_done": int_value(member.get("damage_done")),
        "damage_taken": int_value(member.get("damage_taken")),
        "healing_done": int_value(member.get("healing_done")),
        "healing_received": int_value(member.get("healing_received")),
        "deaths": int_value(member.get("deaths")),
        "hostile_hits_taken": int_value(member.get("hostile_hits_taken")),
    }
    if member.get("min_health_pct") is not None:
        brief["min_health_pct"] = round(float_value(member.get("min_health_pct")), 1)
    if member.get("min_mana_pct") is not None:
        brief["min_mana_pct"] = round(float_value(member.get("min_mana_pct")), 1)
    return brief


def _combat_enemy_brief(enemy: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(enemy.get("name") or ""),
        "level": int_value(enemy.get("level")),
        "killed": bool(enemy.get("killed")),
        "damage_done": int_value(enemy.get("damage_done")),
        "damage_taken": int_value(enemy.get("damage_taken")),
    }


def compact_combat_summary(summary: dict[str, Any], top_limit: int = 5) -> dict[str, Any]:
    facts = summary.get("facts") if isinstance(summary.get("facts"), dict) else {}
    totals = facts.get("totals") if isinstance(facts.get("totals"), dict) else {}
    members = [item for item in facts.get("members", []) if isinstance(item, dict)]
    enemies = [item for item in facts.get("enemies", []) if isinstance(item, dict)]
    timeline = [str(item) for item in facts.get("timeline", []) if item is not None]
    group_leader = facts.get("group_leader") if isinstance(facts.get("group_leader"), dict) else {}
    bounded_top = max(1, min(int(top_limit), 10))

    top_damage = sorted(members, key=lambda item: int_value(item.get("damage_done")), reverse=True)
    top_healing = sorted(members, key=lambda item: int_value(item.get("healing_done")), reverse=True)
    top_taken = sorted(members, key=lambda item: int_value(item.get("damage_taken")), reverse=True)
    danger = sorted(
        [
            item
            for item in members
            if int_value(item.get("deaths")) > 0
            or (item.get("min_health_pct") is not None and float_value(item.get("min_health_pct"), 100.0) < 60.0)
        ],
        key=lambda item: (int_value(item.get("deaths")) == 0, float_value(item.get("min_health_pct"), 100.0)),
    )

    compact: dict[str, Any] = {
        "id": int_value(summary.get("id")),
        "created_at": str(summary.get("created_at") or ""),
        "group_leader": {
            "guid": int_value(group_leader.get("guid") or summary.get("group_leader_guid")),
            "name": str(group_leader.get("name") or summary.get("group_leader_name") or ""),
        },
        "map_id": int_value(summary.get("map_id") or facts.get("map_id")),
        "zone_id": int_value(summary.get("zone_id") or facts.get("zone_id")),
        "area_id": int_value(summary.get("area_id") or facts.get("area_id")),
        "duration_ms": int_value(summary.get("duration_ms") or facts.get("duration_ms")),
        "kills": int_value(summary.get("kills") or totals.get("kills")),
        "deaths": int_value(summary.get("deaths") or totals.get("deaths")),
        "totals": {
            "damage_done": int_value(totals.get("damage_done")),
            "damage_taken": int_value(totals.get("damage_taken")),
            "healing_done": int_value(totals.get("healing_done")),
            "bot_threat_events": int_value(totals.get("bot_threat_events")),
            "healer_threat_events": int_value(totals.get("healer_threat_events")),
        },
        "members": [_combat_member_brief(item) for item in members],
        "top_damage": [
            _combat_member_brief(item) for item in top_damage[:bounded_top] if int_value(item.get("damage_done")) > 0
        ],
        "top_healing": [
            _combat_member_brief(item) for item in top_healing[:bounded_top] if int_value(item.get("healing_done")) > 0
        ],
        "top_damage_taken": [
            _combat_member_brief(item) for item in top_taken[:bounded_top] if int_value(item.get("damage_taken")) > 0
        ],
        "danger": [_combat_member_brief(item) for item in danger[:bounded_top]],
        "enemies": [_combat_enemy_brief(item) for item in enemies[:bounded_top]],
        "timeline_tail": timeline[-8:],
        "has_summary_text": bool(summary.get("summary_text")),
    }
    if summary.get("summary_text"):
        compact["summary_text"] = str(summary.get("summary_text"))
    return compact


def combat_summary_has_fight(summary: dict[str, Any]) -> bool:
    facts = summary.get("facts") if isinstance(summary.get("facts"), dict) else {}
    totals = facts.get("totals") if isinstance(facts.get("totals"), dict) else {}
    if any(
        int_value(totals.get(key)) > 0
        for key in ("damage_done", "damage_taken", "kills", "deaths", "bot_threat_events", "healer_threat_events")
    ):
        return True
    members = [item for item in facts.get("members", []) if isinstance(item, dict)]
    if any(
        int_value(item.get("damage_done")) > 0
        or int_value(item.get("damage_taken")) > 0
        or int_value(item.get("deaths")) > 0
        or int_value(item.get("hostile_hits_taken")) > 0
        for item in members
    ):
        return True
    enemies = [item for item in facts.get("enemies", []) if isinstance(item, dict)]
    return any(
        bool(item.get("killed"))
        or int_value(item.get("damage_done")) > 0
        or int_value(item.get("damage_taken")) > 0
        for item in enemies
    )


def fetch_recent_combat_summaries(
    db: MysqlCli,
    *,
    group_leader_guid: int = 0,
    limit: int = 5,
) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), 20))
    where = f"WHERE `group_leader_guid` = {int(group_leader_guid)}" if int(group_leader_guid or 0) > 0 else ""
    rows = db.query_rows(
        "SELECT `id`, `created_at`, COALESCE(`summarized_at`, ''), "
        "`group_leader_guid`, COALESCE(`group_leader_name`, ''), "
        "`map_id`, `zone_id`, `area_id`, `duration_ms`, `kills`, `deaths`, "
        "TO_BASE64(COALESCE(`facts_json`, '')), TO_BASE64(COALESCE(`summary_text`, '')) "
        "FROM `agent_playerbot_combat_summaries` "
        f"{where} ORDER BY `id` DESC LIMIT {bounded_limit}"
    )
    return [combat_summary_row_to_dict(row) for row in rows]


def fetch_combat_summary(db: MysqlCli, summary_id: int) -> dict[str, Any] | None:
    rows = db.query_rows(
        "SELECT `id`, `created_at`, COALESCE(`summarized_at`, ''), "
        "`group_leader_guid`, COALESCE(`group_leader_name`, ''), "
        "`map_id`, `zone_id`, `area_id`, `duration_ms`, `kills`, `deaths`, "
        "TO_BASE64(COALESCE(`facts_json`, '')), TO_BASE64(COALESCE(`summary_text`, '')) "
        "FROM `agent_playerbot_combat_summaries` "
        f"WHERE `id` = {int(summary_id)} LIMIT 1"
    )
    return combat_summary_row_to_dict(rows[0]) if rows else None


QUEST_STATUS_LABELS = {
    0: "未接受",
    1: "可交付",
    2: "不可用",
    3: "进行中",
    4: "可用",
    5: "失败",
    6: "已奖励",
}


def quest_status_label(status: int) -> str:
    return QUEST_STATUS_LABELS.get(int(status), f"未知状态 {status}")


def _quest_select_sql(locale: str) -> str:
    locale_sql = sql_quote(locale or "zhCN")
    text_exprs = [
        "COALESCE(NULLIF(l.`Title`, ''), q.`LogTitle`, '')",
        "COALESCE(NULLIF(l.`Details`, ''), q.`QuestDescription`, '')",
        "COALESCE(NULLIF(l.`Objectives`, ''), q.`LogDescription`, '')",
        "COALESCE(q.`AreaDescription`, '')",
        "COALESCE(q.`QuestCompletionLog`, '')",
        "COALESCE(NULLIF(l.`EndText`, ''), '')",
        "COALESCE(NULLIF(l.`CompletedText`, ''), '')",
        "COALESCE(NULLIF(req_l.`CompletionText`, ''), req.`CompletionText`, '')",
        "COALESCE(NULLIF(rew_l.`RewardText`, ''), rew.`RewardText`, '')",
        "COALESCE(NULLIF(l.`ObjectiveText1`, ''), q.`ObjectiveText1`, '')",
        "COALESCE(NULLIF(l.`ObjectiveText2`, ''), q.`ObjectiveText2`, '')",
        "COALESCE(NULLIF(l.`ObjectiveText3`, ''), q.`ObjectiveText3`, '')",
        "COALESCE(NULLIF(l.`ObjectiveText4`, ''), q.`ObjectiveText4`, '')",
    ]
    encoded = ", ".join(f"TO_BASE64({expr})" for expr in text_exprs)
    return (
        "SELECT q.`ID`, "
        f"{encoded}, "
        "q.`QuestLevel`, q.`MinLevel`, q.`QuestSortID`, q.`QuestInfoID`, q.`SuggestedGroupNum`, "
        "q.`RequiredNpcOrGo1`, q.`RequiredNpcOrGoCount1`, "
        "q.`RequiredNpcOrGo2`, q.`RequiredNpcOrGoCount2`, "
        "q.`RequiredNpcOrGo3`, q.`RequiredNpcOrGoCount3`, "
        "q.`RequiredNpcOrGo4`, q.`RequiredNpcOrGoCount4`, "
        "q.`RequiredItemId1`, q.`RequiredItemCount1`, "
        "q.`RequiredItemId2`, q.`RequiredItemCount2`, "
        "q.`RequiredItemId3`, q.`RequiredItemCount3`, "
        "q.`RequiredItemId4`, q.`RequiredItemCount4`, "
        "q.`RequiredItemId5`, q.`RequiredItemCount5`, "
        "q.`RequiredItemId6`, q.`RequiredItemCount6`, "
        "q.`ItemDrop1`, q.`ItemDropQuantity1`, "
        "q.`ItemDrop2`, q.`ItemDropQuantity2`, "
        "q.`ItemDrop3`, q.`ItemDropQuantity3`, "
        "q.`ItemDrop4`, q.`ItemDropQuantity4`, "
        "q.`StartItem`, q.`RewardNextQuest` "
        "FROM `quest_template` q "
        f"LEFT JOIN `quest_template_locale` l ON l.`ID` = q.`ID` AND l.`locale` = {locale_sql} "
        "LEFT JOIN `quest_request_items` req ON req.`ID` = q.`ID` "
        f"LEFT JOIN `quest_request_items_locale` req_l ON req_l.`ID` = q.`ID` AND req_l.`locale` = {locale_sql} "
        "LEFT JOIN `quest_offer_reward` rew ON rew.`ID` = q.`ID` "
        f"LEFT JOIN `quest_offer_reward_locale` rew_l ON rew_l.`ID` = q.`ID` AND rew_l.`locale` = {locale_sql} "
    )


def _fetch_name_map(db: MysqlCli, table: str, key_column: str, name_column: str, ids: set[int]) -> dict[int, str]:
    wanted = sorted({int(item) for item in ids if int(item) > 0})
    if not wanted:
        return {}
    rows = db.query_rows(
        f"SELECT `{key_column}`, `{name_column}` FROM `{table}` "
        f"WHERE `{key_column}` IN ({','.join(str(item) for item in wanted)})"
    )
    return {int(row[0] or 0): str(row[1] or "") for row in rows}


def _fetch_localized_name_map(
    db: MysqlCli,
    *,
    base_table: str,
    locale_table: str,
    base_key_column: str,
    locale_key_column: str,
    base_name_column: str,
    locale_name_column: str,
    ids: set[int],
    locale: str,
) -> dict[int, str]:
    wanted = sorted({int(item) for item in ids if int(item) > 0})
    if not wanted:
        return {}
    rows = db.query_rows(
        f"SELECT b.`{base_key_column}`, COALESCE(NULLIF(l.`{locale_name_column}`, ''), b.`{base_name_column}`, '') "
        f"FROM `{base_table}` b "
        f"LEFT JOIN `{locale_table}` l ON l.`{locale_key_column}` = b.`{base_key_column}` "
        f"AND l.`locale` = {sql_quote(locale or 'zhCN')} "
        f"WHERE b.`{base_key_column}` IN ({','.join(str(item) for item in wanted)})"
    )
    return {int(row[0] or 0): str(row[1] or "") for row in rows}


def quest_row_to_dict(row: list[str | None]) -> dict[str, Any]:
    texts = [clean_wow_text(decode_b64(row[index])) for index in range(1, 14)]
    cursor = 19
    creature_or_gameobject: list[dict[str, Any]] = []
    for slot in range(1, 5):
        entry = int_value(row[cursor])
        count = int_value(row[cursor + 1])
        cursor += 2
        if entry and count:
            creature_or_gameobject.append(
                {
                    "slot": slot,
                    "entry": abs(entry),
                    "kind": "gameobject" if entry < 0 else "creature",
                    "required": count,
                }
            )
    items: list[dict[str, Any]] = []
    for slot in range(1, 7):
        item_id = int_value(row[cursor])
        count = int_value(row[cursor + 1])
        cursor += 2
        if item_id and count:
            items.append({"slot": slot, "item_id": item_id, "required": count})
    drops: list[dict[str, Any]] = []
    for slot in range(1, 5):
        item_id = int_value(row[cursor])
        count = int_value(row[cursor + 1])
        cursor += 2
        if item_id and count:
            drops.append({"slot": slot, "item_id": item_id, "quantity": count})
    return {
        "id": int_value(row[0]),
        "title": texts[0],
        "details": texts[1],
        "objectives_text": texts[2],
        "area_description": texts[3],
        "completion_log": texts[4],
        "end_text": texts[5],
        "completed_text": texts[6],
        "request_items_text": texts[7],
        "reward_text": texts[8],
        "objective_texts": [text for text in texts[9:13] if text],
        "quest_level": int_value(row[14]),
        "min_level": int_value(row[15]),
        "sort_id": int_value(row[16]),
        "info_id": int_value(row[17]),
        "suggested_group": int_value(row[18]),
        "objectives": {
            "creatures_or_gameobjects": creature_or_gameobject,
            "items": items,
            "drops": drops,
        },
        "start_item": int_value(row[cursor]),
        "next_quest": int_value(row[cursor + 1]),
    }


def enrich_quest_objective_names(db: MysqlCli, quest: dict[str, Any], locale: str = "zhCN") -> dict[str, Any]:
    objectives = quest.get("objectives") if isinstance(quest.get("objectives"), dict) else {}
    units = objectives.get("creatures_or_gameobjects") if isinstance(objectives.get("creatures_or_gameobjects"), list) else []
    items = objectives.get("items") if isinstance(objectives.get("items"), list) else []
    drops = objectives.get("drops") if isinstance(objectives.get("drops"), list) else []

    creature_ids = {int(item["entry"]) for item in units if item.get("kind") == "creature"}
    gameobject_ids = {int(item["entry"]) for item in units if item.get("kind") == "gameobject"}
    item_ids = {int(item["item_id"]) for item in items + drops if int(item.get("item_id") or 0) > 0}
    creature_names = _fetch_localized_name_map(
        db,
        base_table="creature_template",
        locale_table="creature_template_locale",
        base_key_column="entry",
        locale_key_column="entry",
        base_name_column="name",
        locale_name_column="Name",
        ids=creature_ids,
        locale=locale,
    )
    gameobject_names = _fetch_localized_name_map(
        db,
        base_table="gameobject_template",
        locale_table="gameobject_template_locale",
        base_key_column="entry",
        locale_key_column="entry",
        base_name_column="name",
        locale_name_column="name",
        ids=gameobject_ids,
        locale=locale,
    )
    item_names = _fetch_localized_name_map(
        db,
        base_table="item_template",
        locale_table="item_template_locale",
        base_key_column="entry",
        locale_key_column="ID",
        base_name_column="name",
        locale_name_column="Name",
        ids=item_ids,
        locale=locale,
    )

    for item in units:
        entry = int(item.get("entry") or 0)
        item["name"] = creature_names.get(entry, "") if item.get("kind") == "creature" else gameobject_names.get(entry, "")
    for item in items:
        item["name"] = item_names.get(int(item.get("item_id") or 0), "")
    for item in drops:
        item["name"] = item_names.get(int(item.get("item_id") or 0), "")
    return quest


def fetch_quest_details(db: MysqlCli, quest_id: int, locale: str = "zhCN") -> dict[str, Any] | None:
    rows = db.query_rows(_quest_select_sql(locale) + f"WHERE q.`ID` = {int(quest_id)} LIMIT 1")
    if not rows:
        return None
    quest = enrich_quest_objective_names(db, quest_row_to_dict(rows[0]), locale=locale)
    quest["starters"] = fetch_quest_relations(db, int(quest_id), starter=True, locale=locale)
    quest["enders"] = fetch_quest_relations(db, int(quest_id), starter=False, locale=locale)
    return quest


def fetch_quest_relations(db: MysqlCli, quest_id: int, starter: bool, locale: str = "zhCN") -> list[dict[str, Any]]:
    creature_table = "creature_queststarter" if starter else "creature_questender"
    gameobject_table = "gameobject_queststarter" if starter else "gameobject_questender"
    rows = db.query_rows(
        "SELECT 'creature', r.`id`, COALESCE(NULLIF(l.`Name`, ''), t.`name`, '') "
        f"FROM `{creature_table}` r JOIN `creature_template` t ON t.`entry` = r.`id` "
        f"LEFT JOIN `creature_template_locale` l ON l.`entry` = t.`entry` AND l.`locale` = {sql_quote(locale or 'zhCN')} "
        f"WHERE r.`quest` = {int(quest_id)} "
        "UNION ALL "
        "SELECT 'gameobject', r.`id`, COALESCE(NULLIF(l.`name`, ''), t.`name`, '') "
        f"FROM `{gameobject_table}` r JOIN `gameobject_template` t ON t.`entry` = r.`id` "
        f"LEFT JOIN `gameobject_template_locale` l ON l.`entry` = t.`entry` AND l.`locale` = {sql_quote(locale or 'zhCN')} "
        f"WHERE r.`quest` = {int(quest_id)} "
        "LIMIT 20"
    )
    return [{"kind": str(row[0] or ""), "entry": int_value(row[1]), "name": str(row[2] or "")} for row in rows]


def compact_quest(quest: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": quest["id"],
        "title": quest["title"],
        "quest_level": quest["quest_level"],
        "min_level": quest["min_level"],
        "suggested_group": quest["suggested_group"],
        "objectives_text": quest["objectives_text"],
        "turn_in_hint": quest["completed_text"] or quest["completion_log"],
        "objectives": quest["objectives"],
        "starters": quest.get("starters", [])[:5],
        "enders": quest.get("enders", [])[:5],
    }


def search_quests(db: MysqlCli, query: str, *, locale: str = "zhCN", limit: int = 10, player_level: int = 0) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), 30))
    query = str(query or "").strip()
    if not query:
        return []
    locale_sql = sql_quote(locale or "zhCN")
    like = sql_like(query)
    id_clause = f"q.`ID` = {int(query)} OR " if query.isdigit() else ""
    rows = db.query_rows(
        "SELECT q.`ID`, TO_BASE64(COALESCE(NULLIF(l.`Title`, ''), q.`LogTitle`, '')), "
        "q.`QuestLevel`, q.`MinLevel`, TO_BASE64(COALESCE(NULLIF(l.`Objectives`, ''), q.`LogDescription`, '')) "
        "FROM `quest_template` q "
        f"LEFT JOIN `quest_template_locale` l ON l.`ID` = q.`ID` AND l.`locale` = {locale_sql} "
        f"WHERE {id_clause}COALESCE(NULLIF(l.`Title`, ''), q.`LogTitle`, '') LIKE {like} ESCAPE '\\\\' "
        f"OR COALESCE(NULLIF(l.`Objectives`, ''), q.`LogDescription`, '') LIKE {like} ESCAPE '\\\\' "
        f"OR COALESCE(NULLIF(l.`Details`, ''), q.`QuestDescription`, '') LIKE {like} ESCAPE '\\\\' "
        f"ORDER BY {f'ABS(q.`QuestLevel` - {int(player_level)}) ASC,' if player_level else ''} q.`ID` ASC "
        f"LIMIT {bounded_limit}"
    )
    return [
        {
            "id": int_value(row[0]),
            "title": clean_wow_text(decode_b64(row[1])),
            "quest_level": int_value(row[2]),
            "min_level": int_value(row[3]),
            "objectives_text": clean_wow_text(decode_b64(row[4])),
        }
        for row in rows
    ]


def fetch_player_quest_statuses(db: MysqlCli, player_guid: int, limit: int = 25) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), 50))
    rows = db.query_rows(
        "SELECT `quest`, `status`, `explored`, `timer`, "
        "`mobcount1`, `mobcount2`, `mobcount3`, `mobcount4`, "
        "`itemcount1`, `itemcount2`, `itemcount3`, `itemcount4`, `itemcount5`, `itemcount6`, `playercount` "
        "FROM `character_queststatus` "
        f"WHERE `guid` = {int(player_guid)} ORDER BY `quest` LIMIT {bounded_limit}"
    )
    return [
        {
            "quest_id": int_value(row[0]),
            "status": int_value(row[1]),
            "status_label": quest_status_label(int_value(row[1])),
            "explored": bool(int_value(row[2])),
            "timer": int_value(row[3]),
            "mob_counts": [int_value(value) for value in row[4:8]],
            "item_counts": [int_value(value) for value in row[8:14]],
            "player_count": int_value(row[14]),
        }
        for row in rows
    ]


def apply_quest_progress(quest: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    objectives = quest.get("objectives") if isinstance(quest.get("objectives"), dict) else {}
    for item in objectives.get("creatures_or_gameobjects", []):
        if isinstance(item, dict):
            index = int_value(item.get("slot")) - 1
            counts = status.get("mob_counts") if isinstance(status.get("mob_counts"), list) else []
            progress = int_value(counts[index] if 0 <= index < len(counts) else 0)
            item["progress"] = progress
            item["complete"] = progress >= int_value(item.get("required"))
    for item in objectives.get("items", []):
        if isinstance(item, dict):
            index = int_value(item.get("slot")) - 1
            counts = status.get("item_counts") if isinstance(status.get("item_counts"), list) else []
            progress = int_value(counts[index] if 0 <= index < len(counts) else 0)
            item["progress"] = progress
            item["complete"] = progress >= int_value(item.get("required"))
    quest["player_status"] = status
    return quest


def quest_objective_progress_lines(quest: dict[str, Any]) -> list[str]:
    objectives = quest.get("objectives") if isinstance(quest.get("objectives"), dict) else {}
    lines: list[str] = []
    for item in objectives.get("creatures_or_gameobjects", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("entry") or "")
        progress = int_value(item.get("progress"))
        required = int_value(item.get("required"))
        label = "完成" if item.get("complete") else "进行中"
        lines.append(f"{name}: {progress}/{required} {label}")
    for item in objectives.get("items", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("item_id") or "")
        progress = int_value(item.get("progress"))
        required = int_value(item.get("required"))
        label = "完成" if item.get("complete") else "进行中"
        lines.append(f"{name}: {progress}/{required} {label}")
    if not lines and quest.get("objectives_text"):
        lines.append(str(quest["objectives_text"]))
    return lines


def quest_is_complete_for_player(quest: dict[str, Any]) -> bool:
    status = quest.get("player_status") if isinstance(quest.get("player_status"), dict) else {}
    if int_value(status.get("status")) == 1:
        return True
    objectives = quest.get("objectives") if isinstance(quest.get("objectives"), dict) else {}
    tracked = [
        item
        for item in objectives.get("creatures_or_gameobjects", []) + objectives.get("items", [])
        if isinstance(item, dict)
    ]
    return bool(tracked) and all(bool(item.get("complete")) for item in tracked)


def quest_turn_in_hint(quest: dict[str, Any]) -> str:
    enders = quest.get("enders") if isinstance(quest.get("enders"), list) else []
    if enders:
        names = [str(item.get("name") or item.get("entry") or "") for item in enders[:3] if isinstance(item, dict)]
        names = [name for name in names if name]
        if names:
            return "交给 " + " / ".join(names)
    return str(quest.get("completed_text") or quest.get("completion_log") or quest.get("request_items_text") or "")


def build_quest_guide_entry(quest: dict[str, Any], style: str = "guide") -> dict[str, Any]:
    progress_lines = quest_objective_progress_lines(quest)
    complete = quest_is_complete_for_player(quest)
    turn_in = quest_turn_in_hint(quest)
    if complete and turn_in:
        next_step = turn_in
    elif progress_lines:
        next_step = "先完成未完成目标：" + "；".join([line for line in progress_lines if "完成" not in line][:2] or progress_lines[:2])
    else:
        next_step = str(quest.get("completion_log") or quest.get("objectives_text") or "查看任务文本里的地点线索。")

    entry: dict[str, Any] = {
        "quest_id": int_value(quest.get("id")),
        "title": str(quest.get("title") or ""),
        "quest_level": int_value(quest.get("quest_level")),
        "status": quest.get("player_status", {}),
        "background": str(quest.get("details") or "")[:900] if style == "story" else str(quest.get("details") or "")[:240],
        "current_objective": str(quest.get("objectives_text") or ""),
        "progress": progress_lines,
        "turn_in": turn_in,
        "next_step": next_step,
        "starters": quest.get("starters", [])[:5],
        "enders": quest.get("enders", [])[:5],
        "complete": complete,
    }
    if style == "brief":
        entry.pop("background", None)
        entry["current_objective"] = entry["current_objective"][:180]
    elif style == "story":
        entry["reward_text"] = str(quest.get("reward_text") or "")[:700]
        entry["request_items_text"] = str(quest.get("request_items_text") or "")[:500]
    return entry


def recommend_quests_for_player(quests: list[dict[str, Any]], player_level: int = 0, limit: int = 5) -> list[dict[str, Any]]:
    def score(quest: dict[str, Any]) -> tuple[int, int, int]:
        complete = 0 if quest_is_complete_for_player(quest) else 1
        quest_level = int_value(quest.get("quest_level"))
        distance = abs(quest_level - int(player_level or quest_level or 0))
        group_penalty = int_value(quest.get("suggested_group")) > 1
        return (complete, distance, int(group_penalty))

    ordered = sorted(quests, key=score)
    return [build_quest_guide_entry(item, "brief") for item in ordered[: max(1, min(int(limit), 10))]]


QUALITY_LABELS = {
    0: "粗糙",
    1: "普通",
    2: "优秀",
    3: "精良",
    4: "史诗",
    5: "传说",
    6: "神器",
    7: "传家宝",
}

ITEM_CLASS_LABELS = {
    0: "消耗品",
    1: "容器",
    2: "武器",
    3: "宝石",
    4: "护甲",
    5: "材料",
    6: "弹药",
    7: "商品材料",
    9: "配方",
    11: "箭袋",
    12: "任务",
    13: "钥匙",
    15: "杂项",
    16: "雕文",
}


def money_dict(copper: int | str | None) -> dict[str, Any]:
    value = max(0, int_value(copper))
    gold = value // 10000
    silver = (value % 10000) // 100
    copper_only = value % 100
    parts: list[str] = []
    if gold:
        parts.append(f"{gold}金")
    if silver:
        parts.append(f"{silver}银")
    if copper_only or not parts:
        parts.append(f"{copper_only}铜")
    return {"copper": value, "text": "".join(parts), "gold": gold, "silver": silver, "copper_only": copper_only}


def fetch_item_templates(db: MysqlCli, item_ids: set[int], locale: str = "zhCN") -> dict[int, dict[str, Any]]:
    wanted = sorted({int(item) for item in item_ids if int(item) > 0})
    if not wanted:
        return {}
    rows = db.query_rows(
        "SELECT t.`entry`, COALESCE(NULLIF(l.`Name`, ''), t.`name`, ''), "
        "t.`class`, t.`subclass`, t.`Quality`, t.`ItemLevel`, t.`RequiredLevel`, "
        "t.`BuyPrice`, t.`SellPrice`, t.`stackable`, t.`bonding`, t.`InventoryType` "
        "FROM `item_template` t "
        f"LEFT JOIN `item_template_locale` l ON l.`ID` = t.`entry` AND l.`locale` = {sql_quote(locale or 'zhCN')} "
        f"WHERE t.`entry` IN ({','.join(str(item) for item in wanted)})"
    )
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        item_id = int_value(row[0])
        quality = int_value(row[4])
        item_class = int_value(row[2])
        result[item_id] = {
            "item_id": item_id,
            "name": str(row[1] or ""),
            "class": item_class,
            "class_label": ITEM_CLASS_LABELS.get(item_class, str(item_class)),
            "subclass": int_value(row[3]),
            "quality": quality,
            "quality_label": QUALITY_LABELS.get(quality, str(quality)),
            "item_level": int_value(row[5]),
            "required_level": int_value(row[6]),
            "buy_price": money_dict(row[7]),
            "sell_price": money_dict(row[8]),
            "stackable": int_value(row[9], 1),
            "bonding": int_value(row[10]),
            "inventory_type": int_value(row[11]),
        }
    return result


def search_items(db: MysqlCli, query: str, *, locale: str = "zhCN", limit: int = 30) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), 100))
    needle = str(query or "").strip()
    if not needle:
        return []
    like = sql_like(needle)
    id_clause = f"t.`entry` = {int(needle)} OR " if needle.isdigit() else ""
    rows = db.query_rows(
        "SELECT t.`entry`, COALESCE(NULLIF(l.`Name`, ''), t.`name`, ''), "
        "t.`Quality`, t.`ItemLevel`, t.`RequiredLevel`, t.`SellPrice` "
        "FROM `item_template` t "
        f"LEFT JOIN `item_template_locale` l ON l.`ID` = t.`entry` AND l.`locale` = {sql_quote(locale or 'zhCN')} "
        f"WHERE {id_clause}COALESCE(NULLIF(l.`Name`, ''), t.`name`, '') LIKE {like} ESCAPE '\\\\' "
        f"OR t.`name` LIKE {like} ESCAPE '\\\\' "
        f"ORDER BY t.`Quality` DESC, t.`ItemLevel` DESC, t.`entry` ASC LIMIT {bounded_limit}"
    )
    return [
        {
            "item_id": int_value(row[0]),
            "name": str(row[1] or ""),
            "quality": int_value(row[2]),
            "quality_label": QUALITY_LABELS.get(int_value(row[2]), str(row[2] or "")),
            "item_level": int_value(row[3]),
            "required_level": int_value(row[4]),
            "sell_price": money_dict(row[5]),
        }
        for row in rows
    ]


def fetch_inventory_items(
    character_db: MysqlCli,
    world_db: MysqlCli,
    owner_guid: int,
    *,
    locale: str = "zhCN",
    include_equipped: bool = False,
    limit: int = 80,
) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), 200))
    equipment_filter = "" if include_equipped else "AND NOT (ci.`bag` = 0 AND ci.`slot` < 19) "
    rows = character_db.query_rows(
        "SELECT ci.`bag`, ci.`slot`, ci.`item`, ii.`itemEntry`, ii.`count`, "
        "ii.`randomPropertyId`, ii.`durability` "
        "FROM `character_inventory` ci "
        "JOIN `item_instance` ii ON ii.`guid` = ci.`item` "
        f"WHERE ci.`guid` = {int(owner_guid)} {equipment_filter}"
        "ORDER BY ci.`bag` ASC, ci.`slot` ASC "
        f"LIMIT {bounded_limit}"
    )
    template_ids = {int_value(row[3]) for row in rows}
    templates = fetch_item_templates(world_db, template_ids, locale=locale)
    items: list[dict[str, Any]] = []
    for row in rows:
        item_id = int_value(row[3])
        template = templates.get(item_id, {"item_id": item_id, "name": str(item_id)})
        count = max(1, int_value(row[4], 1))
        sell_each = int_value(template.get("sell_price", {}).get("copper") if isinstance(template.get("sell_price"), dict) else 0)
        item = {
            **template,
            "bag": int_value(row[0]),
            "slot": int_value(row[1]),
            "item_guid": int_value(row[2]),
            "count": count,
            "random_property_id": int_value(row[5]),
            "durability": int_value(row[6]),
            "sell_total": money_dict(sell_each * count),
            "equipped": int_value(row[0]) == 0 and int_value(row[1]) < 19,
        }
        items.append(item)
    return items


def fetch_auction_rows(
    character_db: MysqlCli,
    world_db: MysqlCli,
    *,
    item_ids: set[int] | None = None,
    limit: int = 20,
    locale: str = "zhCN",
) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), 200))
    where = ""
    if item_ids is not None:
        wanted = sorted({int(item) for item in item_ids if int(item) > 0})
        if not wanted:
            return []
        where = f"WHERE ii.`itemEntry` IN ({','.join(str(item) for item in wanted)}) "
    rows = character_db.query_rows(
        "SELECT ah.`id`, ah.`houseid`, ah.`itemguid`, ah.`itemowner`, ah.`buyoutprice`, "
        "ah.`time`, ah.`buyguid`, ah.`lastbid`, ah.`startbid`, ah.`deposit`, "
        "ii.`itemEntry`, ii.`count` "
        "FROM `auctionhouse` ah "
        "JOIN `item_instance` ii ON ii.`guid` = ah.`itemguid` "
        f"{where}"
        "ORDER BY CASE WHEN ah.`buyoutprice` > 0 THEN ah.`buyoutprice` / GREATEST(ii.`count`, 1) "
        "ELSE GREATEST(ah.`lastbid`, ah.`startbid`) / GREATEST(ii.`count`, 1) END ASC "
        f"LIMIT {bounded_limit}"
    )
    templates = fetch_item_templates(world_db, {int_value(row[10]) for row in rows}, locale=locale)
    auctions: list[dict[str, Any]] = []
    for row in rows:
        count = max(1, int_value(row[11], 1))
        buyout = int_value(row[4])
        current_bid = max(int_value(row[7]), int_value(row[8]))
        item_id = int_value(row[10])
        template = templates.get(item_id, {"item_id": item_id, "name": str(item_id)})
        auctions.append(
            {
                "auction_id": int_value(row[0]),
                "house_id": int_value(row[1]),
                "item_guid": int_value(row[2]),
                "owner_guid": int_value(row[3]),
                "item": template,
                "count": count,
                "buyout": money_dict(buyout),
                "unit_buyout": money_dict(buyout // count if buyout else 0),
                "current_bid": money_dict(current_bid),
                "unit_bid": money_dict(current_bid // count if current_bid else 0),
                "expires_at_unix": int_value(row[5]),
                "bidder_guid": int_value(row[6]),
                "deposit": money_dict(row[9]),
            }
        )
    return auctions


def estimate_item_value_from_auctions(item: dict[str, Any], auctions: list[dict[str, Any]]) -> dict[str, Any]:
    buyouts = [
        int_value(auction.get("unit_buyout", {}).get("copper"))
        for auction in auctions
        if isinstance(auction.get("unit_buyout"), dict) and int_value(auction.get("unit_buyout", {}).get("copper")) > 0
    ]
    bids = [
        int_value(auction.get("unit_bid", {}).get("copper"))
        for auction in auctions
        if isinstance(auction.get("unit_bid"), dict) and int_value(auction.get("unit_bid", {}).get("copper")) > 0
    ]
    vendor = int_value(item.get("sell_price", {}).get("copper") if isinstance(item.get("sell_price"), dict) else 0)
    if buyouts:
        unit = int(median(buyouts))
        source = "auction_median_buyout"
    elif bids:
        unit = int(median(bids))
        source = "auction_median_bid"
    elif vendor:
        unit = vendor
        source = "vendor_sell_price"
    else:
        unit = 0
        source = "unknown"
    return {
        "item_id": int_value(item.get("item_id")),
        "name": str(item.get("name") or ""),
        "unit_value": money_dict(unit),
        "vendor_unit": money_dict(vendor),
        "auction_seen": len(auctions),
        "min_unit_buyout": money_dict(min(buyouts) if buyouts else 0),
        "median_unit_buyout": money_dict(int(median(buyouts)) if buyouts else 0),
        "source": source,
    }


def auction_sale_recommendation(item: dict[str, Any], estimate: dict[str, Any], policy: str = "safe") -> dict[str, Any]:
    policy = str(policy or "safe").strip().lower()
    quality = int_value(item.get("quality"))
    item_class = int_value(item.get("class"))
    bonding = int_value(item.get("bonding"))
    count = max(1, int_value(item.get("count"), 1))
    vendor_unit = int_value(estimate.get("vendor_unit", {}).get("copper") if isinstance(estimate.get("vendor_unit"), dict) else 0)
    market_unit = int_value(estimate.get("unit_value", {}).get("copper") if isinstance(estimate.get("unit_value"), dict) else 0)
    total_market = market_unit * count
    total_vendor = vendor_unit * count

    if bonding in {1, 4, 5} or item_class == 12:
        action = "keep"
        reason = "绑定或任务物品，不建议交易。"
    elif market_unit and vendor_unit and market_unit >= int(vendor_unit * (1.5 if policy == "safe" else 1.2)):
        action = "auction"
        reason = "拍卖行估值明显高于卖店价。"
    elif item_class == 7 and market_unit:
        action = "auction"
        reason = "商品材料通常适合挂拍卖。"
    elif quality == 0 and vendor_unit:
        action = "vendor"
        reason = "灰色物品优先卖店。"
    elif quality >= 2 and market_unit:
        action = "use_or_auction"
        reason = "有装备或自用价值，确认不自用后再挂拍卖。"
    elif vendor_unit:
        action = "vendor"
        reason = "未看到明显拍卖溢价，安全策略建议卖店。"
    else:
        action = "keep"
        reason = "缺少稳定价格，先保留。"

    return {
        "item_id": int_value(item.get("item_id")),
        "name": str(item.get("name") or ""),
        "count": count,
        "action": action,
        "reason": reason,
        "unit_value": money_dict(market_unit),
        "total_value": money_dict(total_market),
        "vendor_total": money_dict(total_vendor),
        "policy": policy,
    }


def ensure_session_goal_table(db: MysqlCli) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS `agent_playerbot_session_goals` ("
        "`scope_type` VARCHAR(16) NOT NULL,"
        "`scope_key` BIGINT UNSIGNED NOT NULL,"
        "`goal_type` VARCHAR(32) NOT NULL,"
        "`player_guid` INT UNSIGNED NOT NULL DEFAULT 0,"
        "`group_leader_guid` INT UNSIGNED NOT NULL DEFAULT 0,"
        "`goal_text` VARCHAR(255) NOT NULL DEFAULT '',"
        "`status` VARCHAR(32) NOT NULL DEFAULT 'active',"
        "`payload_json` TEXT NULL DEFAULT NULL,"
        "`source_event_id` BIGINT UNSIGNED NULL DEFAULT NULL,"
        "`updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
        "PRIMARY KEY (`scope_type`, `scope_key`, `goal_type`),"
        "KEY `idx_agent_playerbot_session_goals_updated` (`updated_at`)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    )


def goal_scope_from_event(event: dict[str, Any]) -> tuple[str, int]:
    group_leader = int(event.get("group_leader_guid") or 0)
    if group_leader:
        return "group", group_leader
    return "player", int(event.get("speaker_guid") or 0)


def upsert_session_goal(
    db: MysqlCli,
    event: dict[str, Any],
    *,
    goal_type: str,
    goal_text: str,
    status: str = "active",
    payload: dict[str, Any] | None = None,
) -> None:
    ensure_session_goal_table(db)
    scope_type, scope_key = goal_scope_from_event(event)
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) if payload else None
    db.execute(
        "REPLACE INTO `agent_playerbot_session_goals` "
        "(`scope_type`, `scope_key`, `goal_type`, `player_guid`, `group_leader_guid`, "
        "`goal_text`, `status`, `payload_json`, `source_event_id`) VALUES "
        f"({sql_quote(scope_type)}, {int(scope_key)}, {sql_quote(goal_type[:32])}, "
        f"{int(event.get('speaker_guid') or 0)}, {int(event.get('group_leader_guid') or 0)}, "
        f"{sql_quote(goal_text[:255])}, {sql_quote(status[:32])}, {sql_quote(payload_json)}, {int(event.get('id') or 0)})"
    )


def fetch_session_goals(db: MysqlCli, event: dict[str, Any]) -> list[dict[str, Any]]:
    ensure_session_goal_table(db)
    scope_type, scope_key = goal_scope_from_event(event)
    rows = db.query_rows(
        "SELECT `scope_type`, `scope_key`, `goal_type`, `player_guid`, `group_leader_guid`, "
        "`goal_text`, `status`, TO_BASE64(COALESCE(`payload_json`, '')), "
        "`source_event_id`, `updated_at` "
        "FROM `agent_playerbot_session_goals` "
        f"WHERE `scope_type` = {sql_quote(scope_type)} AND `scope_key` = {int(scope_key)} "
        "ORDER BY `updated_at` DESC LIMIT 20"
    )
    goals: list[dict[str, Any]] = []
    for row in rows:
        payload_text = decode_b64(row[7])
        try:
            payload: Any = json.loads(payload_text) if payload_text else {}
        except json.JSONDecodeError:
            payload = payload_text
        goals.append(
            {
                "scope_type": str(row[0] or ""),
                "scope_key": int_value(row[1]),
                "goal_type": str(row[2] or ""),
                "player_guid": int_value(row[3]),
                "group_leader_guid": int_value(row[4]),
                "goal_text": str(row[5] or ""),
                "status": str(row[6] or ""),
                "payload": payload,
                "source_event_id": int_value(row[8]),
                "updated_at": str(row[9] or ""),
            }
        )
    return goals


def bots_from_event(event: dict[str, Any]) -> list[dict[str, Any]]:
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    bots = context.get("bots") if isinstance(context.get("bots"), list) else []
    return [bot for bot in bots if isinstance(bot, dict)]


def resolve_bot(event: dict[str, Any], bot_name: str | None) -> tuple[int | None, str | None]:
    if not bot_name:
        return None, None
    wanted = bot_name.strip().lower()
    for bot in bots_from_event(event):
        name = str(bot.get("name") or "")
        if name and name.lower() == wanted:
            return int(bot.get("guid") or 0) or None, name
    return None, bot_name.strip()


def requester_from_event(event: dict[str, Any] | None) -> tuple[int, str]:
    if not event:
        return 0, "Hermes"
    return int(event.get("speaker_guid") or 0), str(event.get("speaker_name") or "Hermes")


def existing_action_id(
    db: MysqlCli,
    *,
    event_id: int,
    action_type: str,
    bot_name: str | None,
    channel: str | None,
    text: str | None,
    command: str | None,
    strategy: str | None,
    bot_state: str | None,
    payload_json: str | None,
) -> int | None:
    if event_id <= 0:
        return None
    rows = db.query_rows(
        "SELECT `id` FROM `agent_playerbot_actions` "
        f"WHERE `source_event_id` = {int(event_id)} "
        f"AND `action_type` = {sql_quote(action_type)} "
        f"AND COALESCE(`bot_name`, '') = {sql_quote(bot_name or '')} "
        f"AND COALESCE(`channel`, '') = {sql_quote(channel or '')} "
        f"AND COALESCE(`text`, '') = {sql_quote(text or '')} "
        f"AND COALESCE(`command`, '') = {sql_quote(command or '')} "
        f"AND COALESCE(`strategy`, '') = {sql_quote(strategy or '')} "
        f"AND COALESCE(`bot_state`, '') = {sql_quote(bot_state or '')} "
        f"AND COALESCE(`payload_json`, '') = {sql_quote(payload_json or '')} "
        "ORDER BY `id` DESC LIMIT 1"
    )
    return int(rows[0][0] or 0) if rows else None


def latest_matching_action_id(
    db: MysqlCli,
    *,
    event_id: int,
    action_type: str,
    bot_name: str | None,
    channel: str | None,
    text: str | None,
    command: str | None,
    strategy: str | None,
    bot_state: str | None,
    payload_json: str | None,
) -> int:
    rows = db.query_rows(
        "SELECT `id` FROM `agent_playerbot_actions` "
        f"WHERE COALESCE(`source_event_id`, 0) = {int(event_id)} "
        f"AND `action_type` = {sql_quote(action_type)} "
        f"AND COALESCE(`bot_name`, '') = {sql_quote(bot_name or '')} "
        f"AND COALESCE(`channel`, '') = {sql_quote(channel or '')} "
        f"AND COALESCE(`text`, '') = {sql_quote(text or '')} "
        f"AND COALESCE(`command`, '') = {sql_quote(command or '')} "
        f"AND COALESCE(`strategy`, '') = {sql_quote(strategy or '')} "
        f"AND COALESCE(`bot_state`, '') = {sql_quote(bot_state or '')} "
        f"AND COALESCE(`payload_json`, '') = {sql_quote(payload_json or '')} "
        "ORDER BY `id` DESC LIMIT 1"
    )
    return int(rows[0][0] or 0) if rows else 0


def source_event_already_processed(db: MysqlCli, event_id: int) -> bool:
    if int(event_id or 0) <= 0:
        return False
    return bool(
        db.scalar_int(
            "SELECT CASE WHEN `processed_at` IS NULL THEN 0 ELSE 1 END "
            "FROM `agent_playerbot_events` "
            f"WHERE `id` = {int(event_id)} LIMIT 1"
        )
    )


def enqueue_action(
    db: MysqlCli,
    *,
    event: dict[str, Any] | None,
    action_type: str,
    bot_name: str | None = None,
    channel: str | None = None,
    text: str | None = None,
    command: str | None = None,
    strategy: str | None = None,
    bot_state: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event_id = int(event.get("id") or 0) if event else 0
    requester_guid, requester_name = requester_from_event(event)
    bot_guid, resolved_bot_name = resolve_bot(event or {}, bot_name)
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) if payload else None

    if (
        event_id
        and env_bool("PLAYERBOT_MCP_REJECT_PROCESSED_EVENT_ACTIONS", True)
        and source_event_already_processed(db, event_id)
    ):
        log_event(
            "action_rejected_stale_event",
            source_event_id=event_id,
            action_type=action_type,
            requester=requester_name,
            requester_guid=requester_guid,
            bot=resolved_bot_name,
            channel=channel,
            command=command,
            strategy=strategy,
            payload=payload,
        )
        return {
            "ok": False,
            "error": "stale_event_id",
            "source_event_id": event_id,
            "feedback": {
                "phase": "blocked",
                "next_suggestion": "这是已处理过的旧事件。请用当前输入里的 current_event_id 重新调用工具。",
            },
        }

    existing = existing_action_id(
        db,
        event_id=event_id,
        action_type=action_type,
        bot_name=resolved_bot_name,
        channel=channel,
        text=text,
        command=command,
        strategy=strategy,
        bot_state=bot_state,
        payload_json=payload_json,
    )
    if existing:
        log_event("action_dedupe", action_id=existing, source_event_id=event_id, action_type=action_type)
        return {"ok": True, "deduped": True, "action_id": existing}

    db.execute(
        "INSERT INTO `agent_playerbot_actions` "
        "(`source_event_id`, `requester_guid`, `requester_name`, `bot_guid`, `bot_name`, "
        "`action_type`, `channel`, `text`, `command`, `strategy`, `bot_state`, `payload_json`) VALUES "
        f"({sql_uint(event_id if event_id else None)}, {requester_guid}, {sql_quote(requester_name)}, "
        f"{sql_uint(bot_guid)}, {sql_quote(resolved_bot_name)}, {sql_quote(action_type)}, "
        f"{sql_quote(channel)}, {sql_quote(text)}, {sql_quote(command)}, "
        f"{sql_quote(strategy)}, {sql_quote(bot_state)}, {sql_quote(payload_json)})"
    )
    action_id = latest_matching_action_id(
        db,
        event_id=event_id,
        action_type=action_type,
        bot_name=resolved_bot_name,
        channel=channel,
        text=text,
        command=command,
        strategy=strategy,
        bot_state=bot_state,
        payload_json=payload_json,
    )
    log_event(
        "action_enqueued",
        action_id=action_id,
        source_event_id=event_id,
        action_type=action_type,
        bot=resolved_bot_name,
        channel=channel,
        payload=payload,
    )
    return {"ok": True, "deduped": False, "action_id": action_id}
