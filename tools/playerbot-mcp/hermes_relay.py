#!/usr/bin/env python3
"""Relay AzerothCore PlayerBot events into a remote Hermes Agent conversation."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from wow_common import (
    MysqlCli,
    enqueue_action,
    env_bool,
    env_int,
    fetch_action_results,
    fetch_events,
    load_state,
    log_event,
    save_state,
)


DEFAULT_HERMES_URL = "http://192.168.1.179:8642/v1/responses"
REPLY_CHANNELS = {"party", "raid", "say", "whisper"}
LISTEN_SCOPES = {"whisper", "direct", "group", "all"}
RECENT_CHAT_DEFAULT_LIMIT = 0
RECENT_CHAT_MAX_LIMIT = 8
RECENT_CHAT_FETCH_MULTIPLIER = 4
RECENT_CHAT_TEXT_LIMIT = 260
MINIMAL_EVENT_KEYS = (
    "id",
    "created_at",
    "channel",
    "speaker_guid",
    "speaker_account",
    "speaker_name",
    "target_guid",
    "target_name",
    "bot_guid",
    "bot_name",
    "group_leader_guid",
    "message",
)
CONTROLLED_BOT_KINDS = {"owned", "group"}
REPLY_BOT_KINDS = {"owned", "group", "random_world", "anchor_world"}
ANCHOR_BOT_DEFAULT_NAME = "瓦小狸"
DEFAULT_UNAUTHORIZED_REPLY = "你好，旅行者，欢迎来到树人魔兽。如需助理服务，请联系 GM 开启白名单。"
MAGE_CLASS_ID = 8
CONSUMABLE_STACK_LIMIT = 5
CONFIRM_ACTION_TYPES = {
    "summon_bot",
    "init_bot",
    "dismiss_bot",
    "refresh_bot",
    "level_bot",
    "init_instance_quests",
    "playerbot_command",
    "invite_player",
    "provide_consumables",
    "command",
    "strategy",
}
PENDING_STATUSES = {"", "queued", "pending", "running"}
ROSTER_ONLINE_RE = re.compile(r"(?<!\S)\+([^,\s]+)")
QUEST_PROGRESS_PREFIX_RE = re.compile(
    r"(?:questie|questhelper|carbonite|pfquest|任务插件|任务进度|任务完成|任务目标|目标完成|已完成任务|"
    r"quest progress|quest complete|objective complete)",
    re.IGNORECASE,
)
QUEST_PROGRESS_COUNT_RE = re.compile(r"(?:\[[^\]]{1,80}\]|任务[^：:]{0,30})[^\n]{0,160}\b\d+\s*/\s*\d+\b")
QUEST_PROGRESS_DONE_RE = re.compile(r"(?:任务|quest)[^\n]{0,80}(?:完成|complete|completed)", re.IGNORECASE)
ZH_STACK_NUMBERS = {
    "一": 1,
    "两": 2,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
}
CONSUMABLE_REQUEST_WORDS = ("给", "做", "来", "要", "补", "发", "整", "拿", "弄", "搓", "递", "帮")
CONSUMABLE_NEGATIVE_WORDS = ("不要", "不用", "别", "无需", "不需要")
WATER_WORDS = ("水", "喝的", "饮料", "蓝水", "魔法水")
FOOD_WORDS = ("面包", "吃的", "食物", "干粮", "点心", "魔法餐", "魔法面包")
BOTH_WORDS = ("吃喝", "水和面包", "水跟面包", "水加面包", "水和吃的", "吃的喝的", "喝的吃的", "水面包")
CONSUMABLE_EXACT_REQUESTS = {"水", "面包", "吃的", "喝的", "吃喝", "食物", "饮料"}
TEAM_ONLINE_RE = re.compile(
    r"(?:队友|队伍|小队|队里|组里|他们|大家|灰名|离线).{0,12}(?:上线|上来|回来|叫回|叫回来|拉回|拉回来|归队)"
    r"|(?:上线|上来|回来|叫回|叫回来|拉回|拉回来|归队).{0,12}(?:队友|队伍|小队|队里|组里|他们|大家|灰名|离线)"
)
GROUP_WITH_BOT_MESSAGES = {
    "组我",
    "组一下",
    "组队",
    "进组",
    "加我",
    "邀请我",
    "拉我",
    "拉我进组",
    "拉我组队",
    "来组",
}
CONTEXT_RESET_PHRASES = (
    "新建会话",
    "新开会话",
    "开新会话",
    "另开会话",
    "重开会话",
    "重置会话",
    "重置上下文",
    "清空上下文",
    "清理上下文",
    "刷新上下文",
    "忘掉上下文",
    "忘记上下文",
    "切新会话",
    "切换会话",
)
CONTEXT_COMPRESS_PHRASES = (
    "压缩上下文",
    "压缩会话",
    "总结上下文",
    "整理上下文",
)
SOCIAL_PUNCT_RE = re.compile(r"[，。！？!?,.～~、：:；;]+")
GREETING_MESSAGES = {
    "hi",
    "hello",
    "hey",
    "你好",
    "您好",
    "早",
    "早上好",
    "上午好",
    "中午好",
    "下午好",
    "晚上好",
}
PING_MESSAGES = {"在吗", "你在吗", "在不", "在不在", "在么", "你在么"}
THANKS_MESSAGES = {"谢谢", "谢了", "多谢", "辛苦了", "感谢"}
BYE_MESSAGES = {"bye", "byebye", "拜拜", "再见", "88", "888", "下了", "晚安"}
ECHO_MESSAGES = {"c"}
INTRINSIC_COMMANDS = {
    "follow": "follow",
    "summon": "summon",
    "release": "release",
}

INSTRUCTIONS = """你是 WoW PlayerBot 队伍级 Agent。

处理输入中的单个 AzerothCore 游戏事件。relay 默认只给当前消息的最小事件包。

硬规则：
- 只通过 wow_playerbot MCP 工具观察和行动。
- 不要调用 terminal/file/browser/任意 SQL；GM/高权限操作只能使用 MCP 明确暴露且带审计权限校验的工具，不能拼任意 GM 命令。
- 每一轮只处理输入里 current_event_id 指定的这一个事件。所有会回复或执行动作的 MCP 调用都必须传 current_event_id；不要沿用记忆、工具历史或旧诊断里的 event_id。
- 如果工具返回 stale_event_id，说明你用了旧事件；立刻改用 current_event_id 重试一次，仍失败就用 current_event_id 回复玩家失败原因。
- 需要战斗、位置、队伍、任务、背包、游戏环境或最近动作结果时，按需调用 wow_playerbot MCP 工具查询；不要假设这些上下文每轮都会随事件一起提供。
- 代词按玩家视角解析：玩家说“我/我这里/这里”是发言玩家，玩家说“你/你自己/瓦小狸”是当前应答 bot。查位置时用 wow_get_location(current_event_id, subject="speaker" 或 subject="bot")；不要把 speaker 位置当作 bot 位置。
- 如果输入含 recent_chat_context，它只是 relay 显式开启的短期故障兜底，只能用于理解“其他几个/刚才/这里/继续”等省略和代词，不是实时世界事实。正常情况下由 Hermes 当前 conversation/session 管理聊天上下文。
- 日常聊天和记忆由 Hermes 当前 conversation/session 管理；relay 不会每轮嵌入游戏环境。玩家说“新建会话/重置上下文”只表示切换短期 conversation epoch，不表示删除长期记忆。
- 不要用 wow_get_recent_events 重建聊天记忆或补齐旧上下文；玩家明确要求查看最近游戏消息/日志时才调用它，并传 current_event_id 让工具按当前玩家或队伍压缩返回。
- 玩家问“刚才成了吗/怎么没反应/最近做了什么动作”时，优先调用 wow_get_last_command_diagnostic(current_event_id)，不要扫旧 event_id。
- 不要凭 map_id/zone_id/area_id 猜地点；必须使用工具或事件中的 map_name/zone_name/area_name。
- wow_get_party_state 默认是轻量队伍摘要，只用于先看成员名、职业、血蓝、在线/战斗状态；不要把它当全量画像。只有玩家明确问某个 bot 的天赋、策略或职责细节时，再对这个 bot 单独调用 wow_get_bot_profile。
- 战斗中的高频技能、治疗、坦克和 DPS 循环交给 playerbots 本能，不要规划逐技能释放。
- party/raid/say 事件按队伍级上下文处理；whisper 事件只在私聊上下文回答，不要泄露到 party。
- whisper 事件必须只用 whisper 回复；不要在 whisper 事件里调用默认 party 回复。
- 随机世界 bot 只能回复，不能控制移动、战斗、组队或策略。
- 游戏玩家看不到你的最终 assistant 文本；凡是需要让玩家知道答案、失败原因、澄清问题或闲聊回复，都必须调用 wow_reply。
- 如果你调用了观察工具来回答玩家问题，拿到结论后必须用 wow_reply 发回原请求频道。
- 固定入口 bot 是“瓦小狸”；say/yell 点名它时由它承接。party/raid 仍按当前队伍上下文回复，只有瓦小狸在当前上下文里时 MCP 才会优先用它。
- 不要沿用历史里的固定发言人名字。
- 当玩家问“你是谁/你是什么天赋/你能不能加血/切输出/谁是坦克”等身份、职责、天赋、策略问题时，先对目标单个 bot 调用 wow_get_bot_profile 或 wow_get_supported_bot_strategies；不要凭职业名猜，也不要批量查询所有 bot 画像。
- 如果 profile 里 spec 或 active_strategies 为空，明确说“当前上下文没拿到真实天赋/策略”，不要编造技能、天赋或位置信息。
- 当玩家让 bot 切职责或流派时，优先用 wow_set_bot_role；只开关单个策略时再用 wow_set_bot_strategy 或专用工具。这是 AI 策略切换，不等于重洗真实天赋。
- 当玩家要求“第二天赋/双天赋/天赋页/重置天赋/洗天赋/切天赋”时，先调用 wow_get_bot_profile 查目标角色；如果 profile.talent_groups.known=true 且 has_second=false，不要执行替代动作，直接 wow_reply 说明没有第二套天赋，并带上 profile.talent_groups.second_unavailable_reason；如果 known=false，就说明当前拿不到天赋页数据。
- 当玩家要求真实天赋操作且工具需要 GM 权限时，只允许走 MCP 暴露的受审计工具；工具拒绝 admin_only 时，把权限不足原因回复给玩家。Wuya 这类已有 GM 权限的角色可以使用这些受控高权限工具。
- 当玩家询问 GM 命令、GM 命令组合或“怎么用命令实现某个需求”时，必须先调用 wow_search_gm_command_help(current_event_id, query=玩家需求)；严格按返回的 command.help 和 recipes 回答，不要凭记忆猜命令、参数或法术 ID。这个工具只读，不代表你可以执行任意 GM 命令。
- 当玩家说“队友上线/队伍里的人上线/当前小队里的人上线”时，先用 current_event_id 调 wow_get_party_state 和 wow_get_last_command_diagnostic；能从当前队伍、最近成功动作或玩家点名推断机器人名字时直接处理，不要改问职业配置。
- “我/你”按当前应答 bot 理解；party/raid/say 默认由瓦小狸承接，whisper 默认由被私聊 bot 承接。回答时要让玩家知道是谁在说话，但保持简短。
- 任务插件/任务进度刷屏不需要进入对话窗口；玩家问任务时直接使用任务库和角色任务进度工具查询。
- 如果动作结果连续出现 requester is not online，不要继续重复执行同类动作；用 current_event_id 回复“服务端暂时没找到你的在线会话”，并建议玩家重新发一句指令。
- 只有消息不需要任何可见回复或动作时，最终文本才写 no_action；不要用 no_action 代替游戏内回答。
- 如果需要行动，调用相应 wow_playerbot 工具；动作结果或已提交状态也要用 wow_reply 回给玩家。
- 回复要短但完整；不要为了变短而截断半句话。内容较多时先给摘要，再提示玩家继续问详情。
"""


class HermesClient:
    def __init__(
        self,
        url: str,
        api_key: str,
        model: str,
        timeout: int,
        trace_raw: bool = False,
        store: bool = False,
        truncation_auto: bool = True,
        payload_mode: str = "minimal",
        include_recent_actions: bool = False,
    ) -> None:
        self.url = url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.trace_raw = trace_raw
        self.store = store
        self.truncation_auto = truncation_auto
        self.payload_mode = normalize_payload_mode(payload_mode)
        self.include_recent_actions = include_recent_actions

    def send_event(
        self,
        *,
        conversation: str,
        event: dict[str, Any],
        action_results: list[dict[str, Any]],
        recent_chat_context: list[dict[str, Any]] | None = None,
        session: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event_payload = event_payload_for_hermes(event, self.payload_mode)
        session_payload = {"conversation": conversation, "store": self.store}
        if session:
            session_payload.update(session)
        envelope = {
            "kind": "wow_playerbot_event",
            "current_event_id": event.get("id"),
            "event": event_payload,
            "context_mode": "on_demand" if self.payload_mode == "minimal" else "embedded",
            "session": session_payload,
        }
        if recent_chat_context:
            envelope["recent_chat_context"] = recent_chat_context
        if self.include_recent_actions:
            envelope["recent_action_results"] = action_results
        payload = {
            "model": self.model,
            "conversation": conversation,
            "instructions": INSTRUCTIONS,
            "input": (
                f"处理这个 WoW 事件。current_event_id={event.get('id')}。"
                "若需要回复或调度 bot，请调用 wow_playerbot MCP 工具，所有动作和回复都传 current_event_id。\n"
                + json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
            ),
            "store": self.store,
        }
        if self.truncation_auto:
            payload["truncation"] = "auto"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        log_event(
            "hermes_request",
            event_id=event.get("id"),
            conversation=conversation,
            model=self.model,
            url=self.url,
            payload_mode=self.payload_mode,
            include_recent_actions=self.include_recent_actions,
            truncation_auto=self.truncation_auto,
            recent_chat_count=len(recent_chat_context or []),
            event_payload=event_payload if self.trace_raw else None,
            recent_action_results=action_results if self.trace_raw and self.include_recent_actions else None,
        )

        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
                parsed = json.loads(body) if body else {}
                log_event(
                    "hermes_response",
                    event_id=event.get("id"),
                    conversation=conversation,
                    status=getattr(response, "status", 0),
                    latency_ms=int((time.monotonic() - started) * 1000),
                    response_id=parsed.get("id"),
                    response_status=parsed.get("status"),
                    output_text=response_text(parsed),
                    raw=parsed if self.trace_raw else None,
                )
                return parsed
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            log_event(
                "hermes_http_error",
                event_id=event.get("id"),
                conversation=conversation,
                status=exc.code,
                body=body[:2000],
            )
            raise RuntimeError(f"Hermes HTTP {exc.code}: {body[:400]}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            log_event("hermes_error", event_id=event.get("id"), conversation=conversation, error=str(exc))
            raise RuntimeError(str(exc)) from exc


def response_text(value: Any) -> str:
    """Extract a compact text preview from OpenAI-compatible response shapes."""
    if not isinstance(value, dict):
        return ""
    direct = value.get("output_text")
    if isinstance(direct, str):
        return direct[:2000]

    chunks: list[str] = []
    for item in value.get("output", []) if isinstance(value.get("output"), list) else []:
        if not isinstance(item, dict):
            continue
        for part in item.get("content", []) if isinstance(item.get("content"), list) else []:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str):
                chunks.append(text)
    if chunks:
        return "\n".join(chunks)[:2000]

    message = value.get("message")
    if isinstance(message, str):
        return message[:2000]
    return ""


def hermes_visible_reply_text(value: str) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "no_action":
        return ""
    # Game chat is the only player-visible UI, so keep fallback text bounded.
    return text[:900]


def default_conversation_epoch() -> str:
    return os.getenv("PLAYERBOT_HERMES_CONVERSATION_EPOCH", "").strip()


def base_conversation_for(event: dict[str, Any]) -> str:
    channel = str(event.get("channel") or "").strip().lower()
    speaker_guid = int(event.get("speaker_guid") or 0)
    bot_guid = int(event.get("bot_guid") or 0)
    leader_guid = int(event.get("group_leader_guid") or 0)

    if channel == "whisper" and bot_guid:
        return f"wow-whisper-{speaker_guid}-{bot_guid}"
    if channel in {"say", "yell"} and message_addresses_anchor(str(event.get("message") or "")):
        return f"wow-anchor-{speaker_guid}"
    if leader_guid:
        return f"wow-party-{leader_guid}"
    return f"wow-player-{speaker_guid}"


def conversation_for(event: dict[str, Any], epoch: str | None = None) -> str:
    base = base_conversation_for(event)
    if epoch is None:
        epoch = default_conversation_epoch()
    if epoch:
        return f"{base}-{epoch}"
    return base


def unique_names(names: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        clean = str(name or "").strip()
        key = clean.lower()
        if clean and key not in seen:
            seen.add(key)
            result.append(clean)
    return result


def split_config_list(value: str) -> list[str]:
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


def split_config_ints(value: str) -> set[int]:
    result: set[int] = set()
    for item in split_config_list(value):
        try:
            result.add(int(item))
        except ValueError:
            continue
    return result


def normalize_payload_mode(value: str) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"full", "embedded"}:
        return "full"
    return "minimal"


def event_payload_for_hermes(event: dict[str, Any], payload_mode: str = "minimal") -> dict[str, Any]:
    if normalize_payload_mode(payload_mode) == "full":
        return event
    payload = {key: event.get(key) for key in MINIMAL_EVENT_KEYS if key in event}
    payload["context_available"] = isinstance(event.get("context"), dict) and bool(event.get("context"))
    return payload


def compact_chat_text(value: Any, limit: int = RECENT_CHAT_TEXT_LIMIT) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def recent_chat_limit() -> int:
    return min(RECENT_CHAT_MAX_LIMIT, env_int("PLAYERBOT_HERMES_RECENT_CHAT_LIMIT", RECENT_CHAT_DEFAULT_LIMIT, 0))


def recent_visible_reply_for_event(db: MysqlCli, event_id: int, channel: str) -> dict[str, str]:
    expected_channel = str(channel or "").strip().lower()
    for action in fetch_action_results(db, int(event_id), limit=8):
        if str(action.get("action_type") or "") != "reply" or str(action.get("status") or "") != "done":
            continue
        if expected_channel and str(action.get("channel") or "").strip().lower() != expected_channel:
            continue
        text = compact_chat_text(action.get("text"))
        if text:
            return {"bot": str(action.get("bot_name") or ""), "text": text}
    return {}


def recent_chat_context_for_event(db: MysqlCli, event: dict[str, Any], limit: int | None = None) -> list[dict[str, Any]]:
    bounded_limit = recent_chat_limit() if limit is None else max(0, min(int(limit), RECENT_CHAT_MAX_LIMIT))
    if bounded_limit <= 0:
        return []

    event_id = int(event.get("id") or 0)
    if event_id <= 1:
        return []

    base = base_conversation_for(event)
    channel = str(event.get("channel") or "").strip().lower()
    query_speaker_guid = 0
    query_group_leader_guid = 0
    if channel in {"party", "raid"}:
        query_group_leader_guid = int(event.get("group_leader_guid") or 0)
    else:
        query_speaker_guid = int(event.get("speaker_guid") or 0)

    raw_events = fetch_events(
        db,
        after_id=0,
        max_id=event_id - 1,
        limit=max(bounded_limit * RECENT_CHAT_FETCH_MULTIPLIER, bounded_limit),
        speaker_guid=query_speaker_guid,
        group_leader_guid=query_group_leader_guid,
        channel=channel,
        newest_first=True,
    )
    matched = [item for item in raw_events if base_conversation_for(item) == base]
    matched = list(reversed(matched[:bounded_limit]))

    context: list[dict[str, Any]] = []
    for item in matched:
        message = compact_chat_text(item.get("message"))
        if not message:
            continue
        entry: dict[str, Any] = {
            "event_id": int(item.get("id") or 0),
            "channel": str(item.get("channel") or ""),
            "speaker": str(item.get("speaker_name") or ""),
            "message": message,
        }
        reply = recent_visible_reply_for_event(db, int(item.get("id") or 0), str(item.get("channel") or ""))
        if reply:
            entry["visible_reply"] = reply
        context.append(entry)
    return context


def anchor_bot_name() -> str:
    return os.getenv("PLAYERBOT_AGENT_ANCHOR_BOT_NAME", ANCHOR_BOT_DEFAULT_NAME).strip()


def anchor_bot_aliases() -> list[str]:
    aliases = [anchor_bot_name(), *split_config_list(os.getenv("PLAYERBOT_AGENT_ANCHOR_ALIASES", "小狸"))]
    return unique_names(aliases)


def message_addresses_anchor(message: str) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    return any(alias.lower() in text for alias in anchor_bot_aliases())


def configured_listen_scope() -> str:
    scope = os.getenv("PLAYERBOT_HERMES_LISTEN_SCOPE", "direct").strip().lower()
    return scope if scope in LISTEN_SCOPES else "direct"


def event_matches_listen_scope(event: dict[str, Any]) -> bool:
    scope = configured_listen_scope()
    channel = str(event.get("channel") or "").strip().lower()
    if scope == "all":
        return True
    if scope == "whisper":
        return channel == "whisper"
    if channel == "whisper":
        return True
    if channel in {"say", "yell"} and message_addresses_anchor(str(event.get("message") or "")):
        return scope in {"direct", "group"}
    if channel in {"party", "raid"}:
        return scope == "group"
    return False


def configured_allowed_player_names() -> set[str]:
    raw = os.getenv("PLAYERBOT_HERMES_ALLOWED_PLAYER_NAMES", "Wuya")
    return {item.lower() for item in split_config_list(raw)}


def configured_allowed_player_guids() -> set[int]:
    return split_config_ints(os.getenv("PLAYERBOT_HERMES_ALLOWED_PLAYER_GUIDS", ""))


def configured_allowed_accounts() -> set[int]:
    return split_config_ints(os.getenv("PLAYERBOT_HERMES_ALLOWED_ACCOUNTS", ""))


def speaker_is_allowed(event: dict[str, Any]) -> bool:
    if env_bool("PLAYERBOT_HERMES_ALLOW_ALL_PLAYERS", False):
        return True

    names = configured_allowed_player_names()
    guids = configured_allowed_player_guids()
    accounts = configured_allowed_accounts()
    if "*" in names:
        return True

    speaker_name = str(event.get("speaker_name") or "").strip().lower()
    speaker_guid = int(event.get("speaker_guid") or 0)
    speaker_account = int(event.get("speaker_account") or 0)
    return (
        bool(speaker_name and speaker_name in names)
        or bool(speaker_guid and speaker_guid in guids)
        or bool(speaker_account and speaker_account in accounts)
    )


def unauthorized_reply_text() -> str:
    return os.getenv("PLAYERBOT_HERMES_UNAUTHORIZED_REPLY", DEFAULT_UNAUTHORIZED_REPLY).strip() or DEFAULT_UNAUTHORIZED_REPLY


def reply_channel_for_event(event: dict[str, Any]) -> str:
    channel = str(event.get("channel") or "party").strip().lower()
    if channel == "yell":
        return "say"
    if channel in REPLY_CHANNELS:
        return channel
    return "party"


def is_quest_progress_noise(message: str) -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    if message_addresses_anchor(text):
        return False
    if "?" in text or "？" in text:
        return False

    lowered = text.lower()
    if any(word in lowered for word in ("怎么", "如何", "为啥", "为什么")):
        return False

    has_count = bool(re.search(r"\d+\s*/\s*\d+", text))
    if QUEST_PROGRESS_PREFIX_RE.search(text) and (has_count or QUEST_PROGRESS_DONE_RE.search(text)):
        return True
    if QUEST_PROGRESS_COUNT_RE.search(text):
        return True
    return bool(QUEST_PROGRESS_DONE_RE.search(text) and QUEST_PROGRESS_PREFIX_RE.search(text))


def should_skip_event(event: dict[str, Any]) -> tuple[bool, str]:
    channel = str(event.get("channel") or "").strip().lower()
    message = str(event.get("message") or "")
    if env_bool("PLAYERBOT_HERMES_IGNORE_QUEST_PROGRESS_CHAT", True) and is_quest_progress_noise(message):
        return True, "quest_progress_noise"
    if not event_matches_listen_scope(event):
        return True, "outside_listen_scope"
    if (
        env_bool("PLAYERBOT_HERMES_IGNORE_UNADDRESSED_SAY", False)
        and channel in {"say", "yell"}
        and not message_addresses_anchor(message)
    ):
        return True, "unaddressed_say"
    return False, ""


def compact_message(message: str) -> str:
    return re.sub(r"\s+", "", str(message or "")).strip().lower()


def simple_social_reply_text(message: str) -> str | None:
    text = SOCIAL_PUNCT_RE.sub("", compact_message(message))
    if not text:
        return None
    for alias in sorted((alias.lower() for alias in anchor_bot_aliases()), key=len, reverse=True):
        if text.startswith(alias):
            text = text[len(alias):]
        if text.endswith(alias):
            text = text[: -len(alias)]
    if text in GREETING_MESSAGES:
        if "晚" in text:
            return "晚上好，我在。"
        if "早" in text:
            return "早上好，我在。"
        return "你好，我在。"
    if text in PING_MESSAGES:
        return "我在。"
    if text in THANKS_MESSAGES:
        return "不客气。"
    if text in BYE_MESSAGES:
        if text == "晚安":
            return "晚安。"
        return "拜拜。"
    if text in ECHO_MESSAGES:
        return text
    return None


def context_control_request(message: str) -> str:
    text = SOCIAL_PUNCT_RE.sub("", compact_message(message))
    if not text:
        return ""
    for alias in sorted((alias.lower() for alias in anchor_bot_aliases()), key=len, reverse=True):
        text = text.replace(alias, "")
    if any(phrase in text for phrase in CONTEXT_COMPRESS_PHRASES) or re.search(r"(压缩|总结|整理).{0,4}(上下文|会话|对话|记忆)", text):
        return "compress"
    if any(phrase in text for phrase in CONTEXT_RESET_PHRASES) or re.search(
        r"(新建|新开|另开|重开|重置|清空|清理|刷新|忘掉|忘记|切|切换).{0,4}(会话|上下文|对话|记忆)",
        text,
    ):
        return "reset"
    return ""


def intrinsic_command_request(message: str) -> str:
    text = SOCIAL_PUNCT_RE.sub("", compact_message(message))
    if not text:
        return ""
    for alias in sorted((alias.lower() for alias in anchor_bot_aliases()), key=len, reverse=True):
        if text.startswith(alias):
            text = text[len(alias):]
        if text.endswith(alias):
            text = text[: -len(alias)]
    return INTRINSIC_COMMANDS.get(text, "")


def group_with_bot_request(message: str) -> bool:
    text = SOCIAL_PUNCT_RE.sub("", compact_message(message))
    if not text:
        return False
    for alias in sorted((alias.lower() for alias in anchor_bot_aliases()), key=len, reverse=True):
        if text.startswith(alias):
            text = text[len(alias):]
        if text.endswith(alias):
            text = text[: -len(alias)]
    return text in GROUP_WITH_BOT_MESSAGES


def parse_stack_count(text: str) -> int:
    digit_match = re.search(r"([1-5])\s*(?:组|份|包)", text)
    if digit_match:
        return max(1, min(int(digit_match.group(1)), CONSUMABLE_STACK_LIMIT))
    for word, value in ZH_STACK_NUMBERS.items():
        if re.search(rf"{re.escape(word)}\s*(?:组|份|包)", text):
            return max(1, min(value, CONSUMABLE_STACK_LIMIT))
    return 1


def parse_consumable_request(message: str) -> dict[str, int] | None:
    text = compact_message(message)
    if not text:
        return None
    if any(word in text for word in CONSUMABLE_NEGATIVE_WORDS):
        return None

    has_both = any(word in text for word in BOTH_WORDS)
    has_water = has_both or any(word in text for word in WATER_WORDS)
    has_food = has_both or any(word in text for word in FOOD_WORDS)
    if not has_water and not has_food:
        return None

    has_stack_unit = bool(re.search(r"(?:[1-5一二两三四五]\s*(?:组|份|包))", text))
    looks_like_request = (
        any(word in text for word in CONSUMABLE_REQUEST_WORDS)
        or text in CONSUMABLE_EXACT_REQUESTS
        or has_both
        or has_stack_unit
        or "点" in text
    )
    if not looks_like_request:
        return None

    stacks = parse_stack_count(text)
    return {
        "water_stacks": stacks if has_water else 0,
        "food_stacks": stacks if has_food else 0,
    }


def event_context_sources(event: dict[str, Any]) -> list[dict[str, Any]]:
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    sources: list[Any] = []
    if isinstance(context.get("target_bot_context"), dict):
        sources.append(context["target_bot_context"])
    if isinstance(context.get("bots"), list):
        sources.extend(context["bots"])
    if isinstance(context.get("group_members"), list):
        sources.extend(context["group_members"])

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in sources:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def select_mage_bot_name(event: dict[str, Any]) -> str | None:
    for bot in event_context_sources(event):
        kind = str(bot.get("bot_kind") or "").strip()
        if kind not in CONTROLLED_BOT_KINDS:
            continue
        try:
            class_id = int(bot.get("class") or 0)
        except (TypeError, ValueError):
            class_id = 0
        if class_id != MAGE_CLASS_ID:
            continue
        if bot.get("alive") is False or bot.get("combat") is True:
            continue
        name = str(bot.get("name") or "").strip()
        if name:
            return name
    return None


def parse_team_online_request(message: str) -> bool:
    text = compact_message(message)
    if not text:
        return False
    return bool(TEAM_ONLINE_RE.search(text))


def offline_group_bot_names(event: dict[str, Any]) -> list[str]:
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    members = context.get("group_members") if isinstance(context.get("group_members"), list) else []
    names: list[str] = []
    for item in members:
        if not isinstance(item, dict):
            continue
        if item.get("online") is not False and item.get("offline_in_group") is not True:
            continue
        if item.get("is_bot") is not True and str(item.get("bot_kind") or "") != "group_offline":
            continue
        name = str(item.get("name") or "").strip()
        if name:
            names.append(name)
    return unique_names(names)


def consumable_request_label(water_stacks: int, food_stacks: int) -> str:
    if water_stacks and food_stacks:
        return "吃喝"
    if water_stacks:
        return "水"
    return "吃的"


def localized_action_error(error: str) -> str:
    mapping = {
        "mage bot is not online or not controllable by requester": "没找到你能控制的在线法师",
        "target bot is not a mage": "目标机器人不是法师",
        "mage bot is dead": "法师已经死亡",
        "mage bot is in combat": "法师还在战斗中",
        "target player is not online": "目标玩家不在线",
        "target player is not in requester's group": "目标玩家不在你的队伍里",
        "target player is not near the mage bot": "目标玩家离法师太远",
        "target player is already grouped or invited": "目标已经在队伍里或已有组队邀请",
        "target player is wrong faction": "目标阵营不匹配",
        "requester is not group leader or assistant": "你不是队长或助理，不能邀请",
        "group is full": "队伍已经满了",
        "cannot invite self": "不能邀请自己",
        "target player cannot store consumables; bags may be full": "目标玩家背包可能满了",
        "empty consumable request": "没有指定要水还是吃的",
        "requester is not online": "服务端暂时没找到你的在线会话，操作没有执行",
        "stale_event_id": "这次工具调用用了旧事件，操作已被拦截",
    }
    return mapping.get(error.strip(), error.strip() or "服务端没有返回原因")


def event_reply_candidate_names(event: dict[str, Any]) -> list[str]:
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    sources: list[Any] = []
    if isinstance(context.get("bots"), list):
        sources.extend(context["bots"])
    if isinstance(context.get("target_bot_context"), dict):
        sources.append(context["target_bot_context"])
    if isinstance(context.get("group_members"), list):
        sources.extend(context["group_members"])

    names: list[str] = []
    for item in sources:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("bot_kind") or "").strip()
        name = str(item.get("name") or "").strip()
        if kind in REPLY_BOT_KINDS and name:
            names.append(name)
    return unique_names(names)


def roster_online_names(results: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for item in results:
        result = str(item.get("result") or "")
        if "Bot roster:" not in result:
            continue
        names.extend(ROSTER_ONLINE_RE.findall(result))
    return unique_names(names)


def canonical_name(names: list[str], requested: str) -> str | None:
    wanted = requested.strip().lower()
    if not wanted:
        return None
    for name in names:
        if name.lower() == wanted:
            return name
    return None


def anchor_reply_name(names: list[str], avoid_keys: set[str]) -> str | None:
    aliases = {alias.lower() for alias in anchor_bot_aliases()}
    if not aliases:
        return None
    for name in names:
        key = name.lower()
        if key in aliases and key not in avoid_keys:
            return name
    return None


def select_reply_bot(
    event: dict[str, Any],
    results: list[dict[str, Any]],
    *,
    requested: str = "",
    channel: str = "",
    avoid: set[str] | None = None,
) -> str | None:
    avoid_keys = {item.lower() for item in (avoid or set()) if item}
    names = unique_names(event_reply_candidate_names(event) + roster_online_names(results))
    requested = requested.strip()
    normalized_channel = channel.strip().lower()

    if normalized_channel == "whisper":
        target = requested or str(event.get("bot_name") or "").strip() or str(event.get("target_name") or "").strip()
        if target:
            return canonical_name(names, target) or target

    if requested:
        canonical = canonical_name(names, requested)
        if canonical and canonical.lower() not in avoid_keys:
            return canonical

    if normalized_channel != "whisper":
        anchor = anchor_reply_name(names, avoid_keys)
        if anchor:
            return anchor

    for name in names:
        if name.lower() not in avoid_keys:
            return name

    if requested:
        return requested
    if names:
        return names[0]
    return str(event.get("bot_name") or "").strip() or None


def successful_visible_reply(results: list[dict[str, Any]], *, channel: str = "") -> bool:
    expected_channel = str(channel or "").strip().lower()
    for item in results:
        if item.get("action_type") != "reply" or item.get("status") != "done":
            continue
        if expected_channel and str(item.get("channel") or "").strip().lower() != expected_channel:
            continue
        return True
    return False


def latest_failed_reply(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in results:
        if item.get("action_type") == "reply" and item.get("status") == "error" and item.get("text"):
            return item
    return None


def needs_generic_confirmation(results: list[dict[str, Any]]) -> bool:
    return any(item.get("status") == "done" and item.get("action_type") in CONFIRM_ACTION_TYPES for item in results)


def generic_confirmation_text(results: list[dict[str, Any]]) -> str:
    commands = unique_names(
        [
            str(item.get("command") or "")
            for item in results
            if item.get("status") == "done" and item.get("action_type") == "command" and item.get("command")
        ]
    )
    if commands:
        return "已执行：" + "、".join(commands[:4]) + "。"

    action_names = unique_names(
        [
            str(item.get("action_type") or "")
            for item in results
            if item.get("status") == "done" and item.get("action_type") in CONFIRM_ACTION_TYPES
        ]
    )
    if any(name == "summon_bot" for name in action_names):
        return "已提交召唤并开始处理。"
    if any(name == "dismiss_bot" for name in action_names):
        return "已提交下线。"
    if any(name == "provide_consumables" for name in action_names):
        return "已提供法师水和面包。"
    if action_names:
        return "已执行。"
    return "已处理。"


class Relay:
    def __init__(
        self,
        db: MysqlCli,
        client: HermesClient,
        state_path: Path,
        *,
        replay: bool = False,
        poll_limit: int = 10,
        skip_backlog_on_start: bool = False,
    ) -> None:
        self.db = db
        self.client = client
        self.state_path = state_path
        self.poll_limit = poll_limit
        self.skipped_backlog = 0
        state: dict[str, Any] = load_state(state_path) if state_path.exists() else {}
        raw_epochs = state.get("conversation_epochs", {})
        self.conversation_epochs: dict[str, str] = {
            str(key): str(value)
            for key, value in raw_epochs.items()
            if str(key).strip() and str(value).strip()
        } if isinstance(raw_epochs, dict) else {}
        if replay:
            self.last_id = 0
        else:
            if state_path.exists():
                self.last_id = int(state.get("last_id") or 0)
            else:
                self.last_id = self.db.scalar_int(
                    "SELECT COALESCE(MAX(`id`), 0) FROM `agent_playerbot_events` "
                    "WHERE `processed_at` IS NOT NULL"
                )
                if not self.last_id:
                    self.last_id = self.db.scalar_int(
                        "SELECT COALESCE(MAX(`id`), 0) FROM `agent_playerbot_events`"
                    )
            if skip_backlog_on_start:
                max_id = self.db.scalar_int("SELECT COALESCE(MAX(`id`), 0) FROM `agent_playerbot_events`")
                if max_id > self.last_id:
                    self.skipped_backlog = self.db.scalar_int(
                        "SELECT COUNT(*) FROM `agent_playerbot_events` "
                        f"WHERE `id` > {int(self.last_id)} AND `id` <= {int(max_id)} "
                        "AND `processed_at` IS NULL"
                    )
                    self.db.execute(
                        "UPDATE `agent_playerbot_events` SET `processed_at` = NOW() "
                        f"WHERE `id` > {int(self.last_id)} AND `id` <= {int(max_id)} "
                        "AND `processed_at` IS NULL"
                    )
                    self.last_id = max_id

    def save(self) -> None:
        save_state(
            self.state_path,
            {
                "last_id": self.last_id,
                "conversation_epochs": self.conversation_epochs,
            },
        )

    def conversation_for_event(self, event: dict[str, Any]) -> str:
        return self.conversation_session_for_event(event)["conversation"]

    def conversation_session_for_event(self, event: dict[str, Any]) -> dict[str, Any]:
        base = base_conversation_for(event)
        epoch = self.conversation_epochs.get(base) or default_conversation_epoch()
        return {
            "base_conversation": base,
            "conversation_epoch": epoch,
            "conversation": conversation_for(event, epoch),
        }

    def rotate_conversation_epoch(self, event: dict[str, Any]) -> tuple[str, str]:
        base = base_conversation_for(event)
        prefix = default_conversation_epoch() or "wow"
        epoch = f"{prefix}-event{int(event['id'])}"
        self.conversation_epochs[base] = epoch
        self.save()
        return base, conversation_for(event, epoch)

    def poll_once(self) -> int:
        events = fetch_events(self.db, after_id=self.last_id, limit=self.poll_limit, unprocessed_only=True)
        for event in events:
            self.handle_event(event)
            self.last_id = max(self.last_id, int(event["id"]))
            self.db.execute(
                "UPDATE `agent_playerbot_events` SET `processed_at` = NOW() "
                f"WHERE `id` = {int(event['id'])} AND `processed_at` IS NULL"
            )
            self.save()
        return len(events)

    def handle_event(self, event: dict[str, Any]) -> None:
        session = self.conversation_session_for_event(event)
        conversation = session["conversation"]
        skip, skip_reason = should_skip_event(event)
        if skip:
            log_event(
                "relay_event_skipped",
                event_id=event["id"],
                conversation=conversation,
                base_conversation=session["base_conversation"],
                conversation_epoch=session["conversation_epoch"],
                channel=event["channel"],
                speaker=event["speaker_name"],
                message=event["message"],
                reason=skip_reason,
                anchor_aliases=anchor_bot_aliases(),
            )
            return
        if not speaker_is_allowed(event):
            log_event(
                "relay_event_unauthorized",
                event_id=event["id"],
                conversation=conversation,
                base_conversation=session["base_conversation"],
                conversation_epoch=session["conversation_epoch"],
                channel=event["channel"],
                speaker=event["speaker_name"],
                speaker_guid=event.get("speaker_guid"),
                speaker_account=event.get("speaker_account"),
                message=event["message"],
            )
            self.enqueue_visible_reply(
                event,
                unauthorized_reply_text(),
                channel=reply_channel_for_event(event),
                requested_bot=anchor_bot_name(),
                reason="unauthorized_whitelist",
            )
            return
        if self.try_handle_context_control(event):
            return
        if self.try_handle_simple_social_message(event):
            return
        if self.try_handle_group_with_bot_request(event):
            return
        if self.try_handle_intrinsic_command(event):
            return
        if self.try_handle_consumable_request(event):
            return
        if self.try_handle_team_online_request(event):
            return
        log_event(
            "relay_event",
            event_id=event["id"],
            conversation=conversation,
            base_conversation=session["base_conversation"],
            conversation_epoch=session["conversation_epoch"],
            channel=event["channel"],
            speaker=event["speaker_name"],
            message=event["message"],
        )
        include_recent_actions = bool(getattr(self.client, "include_recent_actions", False))
        action_results = fetch_action_results(self.db, int(event["id"]), limit=8) if include_recent_actions else []
        recent_chat_context = recent_chat_context_for_event(self.db, event)
        response = self.client.send_event(
            conversation=conversation,
            event=event,
            action_results=action_results,
            recent_chat_context=recent_chat_context,
            session=session,
        )
        settled_results = self.wait_for_action_results(int(event["id"]))
        self.ensure_visible_reply(event, settled_results, hermes_text=response_text(response))

    def try_handle_context_control(self, event: dict[str, Any]) -> bool:
        mode = context_control_request(str(event.get("message") or ""))
        if not mode:
            return False
        channel = reply_channel_for_event(event)
        base, conversation = self.rotate_conversation_epoch(event)
        reply = "已切到新会话，后续消息不会再带旧上下文。"
        if mode == "compress":
            reply = "我先切到新会话来替代压缩，旧上下文不会继续带入。"
        log_event(
            "fast_context_reset",
            event_id=event["id"],
            base_conversation=base,
            conversation=conversation,
            channel=channel,
            speaker=event.get("speaker_name"),
            message=event.get("message"),
            mode=mode,
        )
        self.enqueue_visible_reply(event, reply, channel=channel, reason="fast_context_reset")
        return True

    def try_handle_simple_social_message(self, event: dict[str, Any]) -> bool:
        reply = simple_social_reply_text(str(event.get("message") or ""))
        if not reply:
            return False
        channel = reply_channel_for_event(event)
        log_event(
            "fast_social_reply",
            event_id=event["id"],
            channel=channel,
            speaker=event.get("speaker_name"),
            message=event.get("message"),
            reply=reply,
        )
        self.enqueue_visible_reply(event, reply, channel=channel, reason="fast_social_reply")
        return True

    def try_handle_intrinsic_command(self, event: dict[str, Any]) -> bool:
        command = intrinsic_command_request(str(event.get("message") or ""))
        if not command:
            return False
        log_event(
            "fast_intrinsic_command",
            event_id=event["id"],
            channel=str(event.get("channel") or "").strip().lower(),
            speaker=event.get("speaker_name"),
            message=event.get("message"),
            command=command,
            reason="handled_by_playerbot_instinct",
        )
        return True

    def try_handle_group_with_bot_request(self, event: dict[str, Any]) -> bool:
        if not group_with_bot_request(str(event.get("message") or "")):
            return False

        event_id = int(event["id"])
        channel = reply_channel_for_event(event)
        bot_name = select_reply_bot(event, [], channel=channel)
        if not bot_name:
            log_event(
                "fast_group_with_bot_no_target",
                event_id=event_id,
                channel=channel,
                speaker=event.get("speaker_name"),
                message=event.get("message"),
            )
            self.enqueue_visible_reply(event, "我当前没看到可组队的机器人。", channel=channel, reason="fast_group_with_bot_no_target")
            return True

        enqueue_result = enqueue_action(
            self.db,
            event=event,
            action_type="invite_player",
            payload={
                "target_player": bot_name,
                "fast_path": True,
                "reason": "group_with_bot",
            },
        )
        action_id = int(enqueue_result.get("action_id") or 0)
        log_event(
            "fast_group_with_bot_request",
            event_id=event_id,
            action_id=action_id,
            bot=bot_name,
            channel=channel,
            speaker=event.get("speaker_name"),
            message=event.get("message"),
            deduped=enqueue_result.get("deduped"),
        )

        action_result = self.wait_for_action_result(event_id, action_id)
        error = str(action_result.get("error") or "").strip() if action_result else ""
        status = str(action_result.get("status") or "").strip().lower() if action_result else ""
        if status == "done" and not error:
            reply = "组队邀请已处理。"
        elif error:
            reply = f"组队没成：{localized_action_error(error)}。"
        else:
            reply = "已提交组队邀请，服务端结果还在等。"

        self.enqueue_visible_reply(event, reply, channel=channel, requested_bot=bot_name, reason="fast_group_with_bot_result")
        return True

    def try_handle_consumable_request(self, event: dict[str, Any]) -> bool:
        request = parse_consumable_request(str(event.get("message") or ""))
        if not request:
            return False

        event_id = int(event["id"])
        channel = reply_channel_for_event(event)

        water = int(request["water_stacks"])
        food = int(request["food_stacks"])
        label = consumable_request_label(water, food)
        mage_name = select_mage_bot_name(event)

        if not mage_name:
            log_event(
                "fast_consumable_no_mage",
                event_id=event_id,
                channel=channel,
                speaker=event.get("speaker_name"),
                message=event.get("message"),
                water_stacks=water,
                food_stacks=food,
            )
            self.enqueue_visible_reply(
                event,
                f"我没看到你附近有可控法师，先叫个法师再做{label}。",
                channel=channel,
                reason="fast_consumable_no_mage",
            )
            return True

        enqueue_result = enqueue_action(
            self.db,
            event=event,
            action_type="provide_consumables",
            bot_name=mage_name,
            payload={
                "target_player": str(event.get("speaker_name") or "").strip(),
                "water_stacks": str(water),
                "food_stacks": str(food),
                "fast_path": True,
            },
        )
        action_id = int(enqueue_result.get("action_id") or 0)
        log_event(
            "fast_consumable_request",
            event_id=event_id,
            action_id=action_id,
            bot=mage_name,
            channel=channel,
            speaker=event.get("speaker_name"),
            message=event.get("message"),
            water_stacks=water,
            food_stacks=food,
            deduped=enqueue_result.get("deduped"),
        )

        action_result = self.wait_for_action_result(event_id, action_id)
        error = str(action_result.get("error") or "").strip() if action_result else ""
        status = str(action_result.get("status") or "").strip().lower() if action_result else ""
        if status == "done" and not error:
            reply = f"{label}给你放包里了。"
        elif error:
            reply = f"没做成{label}：{localized_action_error(error)}。"
        else:
            reply = f"已让{mage_name}做{label}，服务端结果还在等。"

        self.enqueue_visible_reply(event, reply, channel=channel, requested_bot=mage_name, reason="fast_consumable_result")
        return True

    def try_handle_team_online_request(self, event: dict[str, Any]) -> bool:
        if not parse_team_online_request(str(event.get("message") or "")):
            return False

        event_id = int(event["id"])
        channel = reply_channel_for_event(event)

        targets = offline_group_bot_names(event)
        if not targets:
            log_event(
                "fast_team_online_no_offline_group_bots",
                event_id=event_id,
                channel=channel,
                speaker=event.get("speaker_name"),
                message=event.get("message"),
            )
            self.enqueue_visible_reply(event, "我当前队伍快照里没看到离线机器人队友。", channel=channel, reason="fast_team_online_no_targets")
            return True

        action_ids: list[int] = []
        for name in targets:
            command_line = f"add {name}"
            enqueue_result = enqueue_action(
                self.db,
                event=event,
                action_type="playerbot_command",
                command=command_line,
                payload={
                    "fast_path": True,
                    "reason": "restore_offline_group_bot",
                    "bot": name,
                    "command_line": command_line,
                },
            )
            if enqueue_result.get("action_id"):
                action_ids.append(int(enqueue_result["action_id"]))

        log_event(
            "fast_team_online_request",
            event_id=event_id,
            action_ids=action_ids,
            targets=targets,
            channel=channel,
            speaker=event.get("speaker_name"),
            message=event.get("message"),
        )

        results = self.wait_for_action_results(event_id)
        done: list[str] = []
        failed: list[str] = []
        for name in targets:
            matched = [
                item for item in results
                if item.get("action_type") == "playerbot_command" and str(item.get("command") or "") == f"add {name}"
            ]
            if matched and matched[0].get("status") == "done":
                done.append(name)
            elif matched and matched[0].get("error"):
                failed.append(f"{name}：{localized_action_error(str(matched[0].get('error') or ''))}")
            else:
                failed.append(f"{name}：服务端结果还在等")

        parts: list[str] = []
        if done:
            parts.append("已叫回：" + "、".join(done[:8]))
        if failed:
            parts.append("没叫回：" + "；".join(failed[:4]))
        self.enqueue_visible_reply(event, "。".join(parts) + "。", channel=channel, reason="fast_team_online_result")
        return True

    def wait_for_action_result(self, event_id: int, action_id: int, timeout_seconds: float = 6.0) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout_seconds
        latest: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            results = fetch_action_results(self.db, event_id, limit=20)
            for item in results:
                if int(item.get("id") or 0) == action_id:
                    latest = item
                    if str(item.get("status") or "").lower() not in PENDING_STATUSES:
                        return item
            time.sleep(0.5)
        return latest

    def enqueue_visible_reply(
        self,
        event: dict[str, Any],
        text: str,
        *,
        channel: str,
        requested_bot: str = "",
        reason: str = "fast_path_reply",
    ) -> None:
        bot_name = select_reply_bot(event, [], requested=requested_bot, channel=channel)
        if not bot_name:
            log_event(
                "fallback_reply_skipped",
                event_id=event.get("id"),
                reason="no_reply_bot",
                failed_requested_bot=requested_bot,
            )
            return
        enqueue_result = enqueue_action(
            self.db,
            event=event,
            action_type="reply",
            bot_name=bot_name,
            channel=channel,
            text=text,
            payload={
                "fallback": True,
                "fallback_reason": reason,
                "requested_bot": requested_bot,
            },
        )
        log_event(
            "fast_path_reply",
            event_id=event.get("id"),
            action_id=enqueue_result.get("action_id"),
            bot=bot_name,
            channel=channel,
            text=text,
            reason=reason,
            requested_bot=requested_bot,
            deduped=enqueue_result.get("deduped"),
        )

    def wait_for_action_results(self, event_id: int, timeout_seconds: float = 6.0) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout_seconds
        latest: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            latest = fetch_action_results(self.db, event_id, limit=20)
            if latest and not any(str(item.get("status") or "").lower() in PENDING_STATUSES for item in latest):
                return latest
            time.sleep(0.5)
        return latest

    def ensure_visible_reply(self, event: dict[str, Any], results: list[dict[str, Any]], *, hermes_text: str = "") -> None:
        channel = reply_channel_for_event(event)
        if results and successful_visible_reply(results, channel=channel):
            return

        failed_reply = latest_failed_reply(results)
        if failed_reply:
            text = str(failed_reply.get("text") or "").strip()
            requested = str(failed_reply.get("bot_name") or "").strip()
            error = str(failed_reply.get("error") or "").strip()
            if error == "requester is not online":
                log_event(
                    "fallback_reply_skipped",
                    event_id=event.get("id"),
                    reason="requester_not_online",
                    failed_requested_bot=requested,
                    error=error,
                )
                return
            avoid = {requested} if error == "bot is not online" else set()
            reason = "retry_failed_reply"
        elif needs_generic_confirmation(results):
            text = generic_confirmation_text(results)
            requested = ""
            avoid = set()
            reason = "confirm_action_without_reply"
        elif not results:
            text = hermes_visible_reply_text(hermes_text)
            requested = str(event.get("bot_name") or event.get("target_name") or "").strip()
            avoid = set()
            reason = "hermes_final_text_without_reply"
        else:
            return

        bot_name = select_reply_bot(event, results, requested=requested, channel=channel, avoid=avoid)
        if not bot_name or not text:
            log_event(
                "fallback_reply_skipped",
                event_id=event.get("id"),
                reason="no_reply_bot" if not bot_name else "empty_text",
                failed_requested_bot=requested,
            )
            return

        enqueue_result = enqueue_action(
            self.db,
            event=event,
            action_type="reply",
            bot_name=bot_name,
            channel=channel,
            text=text,
            payload={
                "fallback": True,
                "fallback_reason": reason,
                "requested_bot": requested,
            },
        )
        log_event(
            "fallback_reply_enqueued",
            event_id=event.get("id"),
            action_id=enqueue_result.get("action_id"),
            bot=bot_name,
            channel=channel,
            reason=reason,
            requested_bot=requested,
            deduped=enqueue_result.get("deduped"),
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Relay PlayerBot bridge events to Hermes")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--replay", action="store_true")
    parser.add_argument(
        "--state",
        default=os.getenv("PLAYERBOT_HERMES_RELAY_STATE", "var/playerbot-hermes-relay/state.json"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db = MysqlCli.from_env("PLAYERBOT_HERMES_RELAY_DB_DSN")
    api_key = os.getenv("PLAYERBOT_HERMES_API_KEY", "").strip()
    if not api_key and not env_bool("PLAYERBOT_HERMES_ALLOW_NO_AUTH", False):
        print("PLAYERBOT_HERMES_API_KEY must be set unless PLAYERBOT_HERMES_ALLOW_NO_AUTH=1", file=sys.stderr)
        return 2

    client = HermesClient(
        os.getenv("PLAYERBOT_HERMES_URL", DEFAULT_HERMES_URL),
        api_key,
        os.getenv("PLAYERBOT_HERMES_MODEL", "hermes-agent"),
        env_int("PLAYERBOT_HERMES_TIMEOUT", 120, 1),
        trace_raw=env_bool("PLAYERBOT_HERMES_TRACE_RAW", False),
        store=env_bool("PLAYERBOT_HERMES_STORE", False),
        truncation_auto=env_bool("PLAYERBOT_HERMES_TRUNCATION_AUTO", True),
        payload_mode=os.getenv("PLAYERBOT_HERMES_PAYLOAD_MODE", "minimal"),
        include_recent_actions=env_bool("PLAYERBOT_HERMES_INCLUDE_RECENT_ACTIONS", False),
    )
    relay = Relay(
        db,
        client,
        Path(args.state),
        replay=bool(args.replay),
        poll_limit=env_int("PLAYERBOT_HERMES_POLL_LIMIT", 10, 1),
        skip_backlog_on_start=env_bool("PLAYERBOT_HERMES_SKIP_BACKLOG_ON_START", False),
    )
    if relay.skipped_backlog:
        log_event("startup_backlog_skipped", skipped_count=relay.skipped_backlog, last_id=relay.last_id)
    log_event(
        "startup",
        last_id=relay.last_id,
        hermes_url=client.url,
        model=client.model,
        store=client.store,
        truncation_auto=client.truncation_auto,
        recent_chat_limit=recent_chat_limit(),
        state=str(args.state),
    )
    relay.save()

    interval = float(os.getenv("PLAYERBOT_HERMES_POLL_INTERVAL", "1"))
    while True:
        try:
            count = relay.poll_once()
        except KeyboardInterrupt:
            log_event("shutdown")
            return 0
        except Exception as exc:
            log_event("poll_error", error=str(exc))
            if args.once:
                return 1
            time.sleep(max(1.0, interval))
            continue

        if args.once:
            return 0
        if not count:
            time.sleep(interval)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log_event("shutdown")
        raise SystemExit(0)
