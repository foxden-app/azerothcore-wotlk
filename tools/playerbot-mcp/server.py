#!/usr/bin/env python3
"""向 Hermes 暴露安全 AzerothCore PlayerBot 工具的 MCP 服务。"""

from __future__ import annotations

import os
import math
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings

from wow_common import (
    DEFAULT_AUTH_DB_DSN,
    DEFAULT_CHARACTERS_DB_DSN,
    DEFAULT_WORLD_DB_DSN,
    MysqlCli,
    apply_quest_progress,
    auction_sale_recommendation,
    build_quest_guide_entry,
    combat_summary_has_fight,
    compact_combat_summary,
    compact_quest,
    enqueue_action,
    estimate_item_value_from_auctions,
    env_bool,
    env_int,
    fetch_auction_rows,
    fetch_action_results,
    fetch_combat_summary,
    fetch_inventory_items,
    fetch_player_quest_statuses,
    fetch_quest_details,
    fetch_recent_combat_summaries,
    fetch_recent_context_actions,
    fetch_event,
    fetch_events,
    fetch_item_templates,
    fetch_session_goals,
    latest_event,
    log_event,
    money_dict,
    quest_objective_progress_lines,
    recommend_quests_for_player,
    search_quests,
    search_items,
    sql_like,
    sql_quote,
    summarize_action_results,
    upsert_session_goal,
)


CONTROLLED_BOT_KINDS = {"owned", "group"}
REPLY_BOT_KINDS = CONTROLLED_BOT_KINDS | {"random_world", "anchor_world"}
REPLY_CHANNELS = {"party", "raid", "say", "whisper"}
ANCHOR_BOT_DEFAULT_NAME = "瓦小狸"
PLAYERBOT_COMMAND_MAX_LEN = 512
GROUP_SELECTORS = {"group", "party", "all", "*", "队伍", "全体", "所有", "所有人", "大家", "机器人"}
HEALER_SELECTORS = {"healer", "healers", "heal", "治疗", "奶", "奶妈", "治疗们"}
TANK_SELECTORS = {"tank", "tanks", "坦", "坦克", "t"}
DPS_SELECTORS = {"dps", "damage", "输出", "打手"}
ROLE_SELECTORS = {
    "healer": HEALER_SELECTORS,
    "tank": TANK_SELECTORS,
    "dps": DPS_SELECTORS,
}
CONSUMABLE_STACK_LIMIT = 5
BOT_COMMANDS = {
    "follow",
    "stay",
    "flee",
    "runaway",
    "attack",
    "pull",
    "pull back",
    "ready",
    "max dps",
    "ll normal",
    "ll gray",
    "ll all",
    "focus heal clear",
}
ADMIN_PLAYER_NAMES_DEFAULT = ""
ADMIN_GM_LEVEL_DEFAULT = 3
MIN_DUAL_SPEC_LEVEL_DEFAULT = 40
AUDITED_PLAYERBOT_COMMAND_PREFIXES = {
    "addaccount",
    "initself",
    "random",
    "refresh=raid",
    "reload",
    "self",
    "tweak",
}
CLASS_IDS = {
    "warrior": 1,
    "paladin": 2,
    "hunter": 3,
    "rogue": 4,
    "priest": 5,
    "dk": 6,
    "shaman": 7,
    "mage": 8,
    "warlock": 9,
    "druid": 11,
}
CLASS_NAMES = {value: key for key, value in CLASS_IDS.items()}
CLASS_ZH = {
    1: "战士",
    2: "圣骑士",
    3: "猎人",
    4: "潜行者",
    5: "牧师",
    6: "死亡骑士",
    7: "萨满祭司",
    8: "法师",
    9: "术士",
    11: "德鲁伊",
}
CLASS_ALIASES = {
    "warrior": {"warrior", "战士", "防战", "狂暴战", "武器战", "zs"},
    "paladin": {"paladin", "圣骑", "骑士", "奶骑", "防骑", "惩戒骑", "qs"},
    "hunter": {"hunter", "猎人", "lr"},
    "rogue": {"rogue", "盗贼", "贼", "潜行者", "dz"},
    "priest": {"priest", "牧师", "神牧", "戒律", "ms"},
    "dk": {"dk", "death_knight", "death knight", "死骑", "死亡骑士"},
    "shaman": {"shaman", "萨满", "萨满祭司", "奶萨", "sm"},
    "mage": {"mage", "法师", "冰法", "火法", "奥法", "fs"},
    "warlock": {"warlock", "术士", "ss"},
    "druid": {"druid", "德鲁伊", "小德", "奶德", "熊", "鸟德", "猫德"},
}

SPEC_ZH = {
    "arms": "武器",
    "fury": "狂怒",
    "prot": "防护",
    "holy": "神圣",
    "retrib": "惩戒",
    "beast": "野兽控制",
    "marks": "射击",
    "surv": "生存",
    "assas": "刺杀",
    "combat": "战斗",
    "subtle": "敏锐",
    "disc": "戒律",
    "shadow": "暗影",
    "blooddps": "鲜血",
    "frostdps": "冰霜",
    "unholydps": "邪恶",
    "resto": "恢复",
    "enhance": "增强",
    "elem": "元素",
    "enh": "增强",
    "ele": "元素",
    "arcane": "奥术",
    "fire": "火焰",
    "frost": "冰霜",
    "afflic": "痛苦",
    "demo": "恶魔",
    "destro": "毁灭",
    "balance": "平衡",
    "feraldps": "野性",
}

GLOBAL_SAFE_STRATEGIES = {
    "aoe",
    "boost",
    "buff",
    "cc",
    "cure",
    "dps debuff",
    "healer dps",
    "loot",
    "nc",
    "pull",
}
CLASS_STRATEGIES: dict[int, set[str]] = {
    CLASS_IDS["warrior"]: {"tank", "arms", "fury"},
    CLASS_IDS["paladin"]: {
        "tank",
        "dps",
        "heal",
        "offheal",
        "bhealth",
        "bmana",
        "bdps",
        "bstats",
        "barmor",
        "bcast",
        "bspeed",
        "baoe",
        "rfire",
        "rfrost",
        "rshadow",
        "bthreat",
    },
    CLASS_IDS["hunter"]: {"bm", "mm", "surv", "pet", "trap weave", "bdps", "bspeed", "rnature"},
    CLASS_IDS["rogue"]: {"dps", "melee", "stealth", "stealthed"},
    CLASS_IDS["priest"]: {"heal", "shadow", "dps", "holy dps", "holy heal", "shadow aoe", "shadow debuff", "rshadow"},
    CLASS_IDS["dk"]: {"tank", "blood", "frost", "unholy", "frost aoe", "unholy aoe", "bdps"},
    CLASS_IDS["shaman"]: {
        "heal",
        "resto",
        "melee",
        "enh",
        "dps",
        "caster",
        "ele",
        "searing",
        "magma",
        "flametongue",
        "wrath",
        "healing stream",
        "mana spring",
        "cleansing",
        "wrath of air",
        "windfury",
        "strength of earth",
        "stoneskin",
        "tremor",
        "earthbind",
    },
    CLASS_IDS["mage"]: {"frost", "fire", "frostfire", "arcane", "firestarter", "bmana", "bdps"},
    CLASS_IDS["warlock"]: {
        "affli",
        "demo",
        "destro",
        "tank",
        "pet",
        "meta melee",
        "imp",
        "voidwalker",
        "succubus",
        "felhunter",
        "felguard",
        "ss self",
        "ss master",
        "ss tank",
        "ss healer",
    },
    CLASS_IDS["druid"]: {
        "bear",
        "tank",
        "cat",
        "caster",
        "dps",
        "heal",
        "offheal",
        "cat aoe",
        "caster aoe",
        "caster debuff",
        "dps debuff",
        "melee",
    },
}
SUPPORTED_STRATEGIES: set[str] = GLOBAL_SAFE_STRATEGIES | set().union(*CLASS_STRATEGIES.values())
STRATEGIES = {f"{prefix}{strategy}" for strategy in SUPPORTED_STRATEGIES for prefix in ("+", "-")}

ROLE_PLANS: dict[int, dict[str, dict[str, Any]]] = {
    CLASS_IDS["warrior"]: {
        "tank": {"label": "防战/坦克", "role": "tank", "changes": ["+tank", "-arms", "-fury"]},
        "arms": {"label": "武器输出", "role": "dps", "changes": ["+arms", "-tank", "-fury"]},
        "fury": {"label": "狂怒输出", "role": "dps", "changes": ["+fury", "-tank", "-arms"]},
        "dps": {"label": "输出", "role": "dps", "changes": ["+fury", "-tank"]},
    },
    CLASS_IDS["paladin"]: {
        "tank": {"label": "防骑/坦克", "role": "tank", "changes": ["+tank", "-dps", "-heal", "-offheal"]},
        "healer": {"label": "奶骑/治疗", "role": "healer", "changes": ["+heal", "-tank", "-dps", "-offheal"]},
        "heal": {"alias": "healer"},
        "dps": {"label": "惩戒输出", "role": "dps", "changes": ["+dps", "-tank", "-heal", "-offheal"]},
        "offheal": {"label": "辅助治疗", "role": "healer", "changes": ["+offheal", "-tank", "-heal"]},
    },
    CLASS_IDS["hunter"]: {
        "bm": {"label": "兽王输出", "role": "dps", "changes": ["+bm", "-mm", "-surv"]},
        "beast": {"alias": "bm"},
        "mm": {"label": "射击输出", "role": "dps", "changes": ["+mm", "-bm", "-surv"]},
        "marks": {"alias": "mm"},
        "surv": {"label": "生存输出", "role": "dps", "changes": ["+surv", "-bm", "-mm"]},
        "dps": {"alias": "mm"},
    },
    CLASS_IDS["rogue"]: {
        "dps": {"label": "输出", "role": "dps", "changes": ["+dps", "-melee"]},
        "melee": {"label": "刺杀/近战输出", "role": "dps", "changes": ["+melee", "-dps"]},
    },
    CLASS_IDS["priest"]: {
        "healer": {"label": "治疗", "role": "healer", "changes": ["+heal", "-shadow", "-dps", "-holy dps"]},
        "heal": {"alias": "healer"},
        "holy": {"label": "神圣治疗", "role": "healer", "changes": ["+holy heal", "-shadow", "-dps", "-holy dps"]},
        "holy_heal": {"alias": "holy"},
        "shadow": {"label": "暗影输出", "role": "dps", "changes": ["+shadow", "-heal", "-holy heal", "-holy dps"]},
        "dps": {"alias": "shadow"},
        "holy_dps": {"label": "神圣输出", "role": "dps", "changes": ["+holy dps", "-heal", "-holy heal", "-shadow"]},
    },
    CLASS_IDS["dk"]: {
        "tank": {"label": "鲜血坦克", "role": "tank", "changes": ["+tank", "-frost", "-unholy"]},
        "blood": {"alias": "tank"},
        "frost": {"label": "冰霜输出", "role": "dps", "changes": ["+frost", "-tank", "-blood", "-unholy"]},
        "unholy": {"label": "邪恶输出", "role": "dps", "changes": ["+unholy", "-tank", "-blood", "-frost"]},
        "dps": {"alias": "frost"},
    },
    CLASS_IDS["shaman"]: {
        "healer": {"label": "恢复治疗", "role": "healer", "changes": ["+heal", "-melee", "-dps", "-caster", "-ele", "-enh"]},
        "heal": {"alias": "healer"},
        "resto": {"alias": "healer"},
        "enh": {"label": "增强近战", "role": "dps", "changes": ["+enh", "-heal", "-resto", "-caster", "-ele"]},
        "melee": {"alias": "enh"},
        "dps": {"alias": "enh"},
        "ele": {"label": "元素施法输出", "role": "dps", "changes": ["+ele", "-heal", "-resto", "-melee", "-enh"]},
        "caster": {"alias": "ele"},
    },
    CLASS_IDS["mage"]: {
        "frost": {"label": "冰法", "role": "dps", "changes": ["+frost", "-fire", "-frostfire", "-arcane"]},
        "fire": {"label": "火法", "role": "dps", "changes": ["+fire", "-frost", "-frostfire", "-arcane"]},
        "arcane": {"label": "奥法", "role": "dps", "changes": ["+arcane", "-frost", "-fire", "-frostfire"]},
        "frostfire": {"label": "霜火法", "role": "dps", "changes": ["+frostfire", "-frost", "-fire", "-arcane"]},
        "dps": {"alias": "frost"},
    },
    CLASS_IDS["warlock"]: {
        "affli": {"label": "痛苦输出", "role": "dps", "changes": ["+affli", "-demo", "-destro", "-tank"]},
        "affliction": {"alias": "affli"},
        "demo": {"label": "恶魔输出", "role": "dps", "changes": ["+demo", "-affli", "-destro", "-tank"]},
        "destro": {"label": "毁灭输出", "role": "dps", "changes": ["+destro", "-affli", "-demo", "-tank"]},
        "tank": {"label": "术士坦克", "role": "tank", "changes": ["+tank", "-affli", "-demo", "-destro"]},
        "dps": {"alias": "affli"},
    },
    CLASS_IDS["druid"]: {
        "tank": {"label": "熊坦", "role": "tank", "changes": ["+bear", "-cat", "-caster", "-dps", "-heal", "-offheal"]},
        "bear": {"alias": "tank"},
        "cat": {"label": "猫德输出", "role": "dps", "changes": ["+cat", "-bear", "-tank", "-caster", "-heal", "-offheal"]},
        "dps": {"alias": "cat"},
        "caster": {"label": "平衡施法输出", "role": "dps", "changes": ["+caster", "-bear", "-tank", "-cat", "-heal", "-offheal"]},
        "balance": {"alias": "caster"},
        "healer": {"label": "恢复治疗", "role": "healer", "changes": ["+heal", "-bear", "-tank", "-cat", "-caster", "-dps", "-offheal"]},
        "heal": {"alias": "healer"},
        "resto": {"alias": "healer"},
        "offheal": {"label": "辅助治疗", "role": "healer", "changes": ["+offheal", "-bear", "-tank", "-heal"]},
    },
}
RACE_IDS = {
    "human": 1,
    "orc": 2,
    "dwarf": 3,
    "night_elf": 4,
    "undead": 5,
    "tauren": 6,
    "gnome": 7,
    "troll": 8,
    "blood_elf": 10,
    "draenei": 11,
}
RACE_NAMES = {value: key for key, value in RACE_IDS.items()}
RACE_ZH = {
    1: "人类",
    2: "兽人",
    3: "矮人",
    4: "暗夜精灵",
    5: "亡灵",
    6: "牛头人",
    7: "侏儒",
    8: "巨魔",
    10: "血精灵",
    11: "德莱尼",
}
RACE_ALIASES = {
    "human": {"human", "人类"},
    "orc": {"orc", "兽人"},
    "dwarf": {"dwarf", "矮人"},
    "night_elf": {"night_elf", "night elf", "nightelf", "暗夜精灵", "夜精灵", "精灵"},
    "undead": {"undead", "亡灵", "被遗忘者"},
    "tauren": {"tauren", "牛头人", "牛头"},
    "gnome": {"gnome", "侏儒"},
    "troll": {"troll", "巨魔"},
    "blood_elf": {"blood_elf", "blood elf", "bloodelf", "血精灵", "血精"},
    "draenei": {"draenei", "德莱尼"},
}
ALLIANCE_RACES = {1, 3, 4, 7, 11}
HORDE_RACES = {2, 5, 6, 8, 10}

PLACE_ALIASES: list[dict[str, Any]] = [
    {
        "key": "scarlet_monastery",
        "kind": "dungeon",
        "name": "血色修道院",
        "aliases": ["血色", "血色修道院", "scarlet", "scarlet monastery", "sm"],
        "map_id": 0,
        "map_name": "Eastern Kingdoms",
        "zone_name": "提瑞斯法林地",
        "area_name": "血色修道院",
        "x": 2870.0,
        "y": -820.0,
        "z": 160.0,
        "min_level": 26,
        "route_hint": "先到幽暗城或提瑞斯法林地，再沿东北道路到修道院入口集合。",
    },
    {
        "key": "stormwind",
        "kind": "city",
        "name": "暴风城",
        "aliases": ["暴风城", "sw", "stormwind", "stormwind city"],
        "team": "alliance",
        "map_id": 0,
        "map_name": "Eastern Kingdoms",
        "zone_name": "暴风城",
        "area_name": "贸易区",
        "x": -8833.0,
        "y": 628.0,
        "z": 94.0,
        "route_hint": "联盟主城；可从艾尔文森林步行进城，远距离优先找飞行点或传送门。",
    },
    {
        "key": "stormwind_auction_house",
        "kind": "auction_house",
        "name": "暴风城拍卖行",
        "aliases": ["暴风拍卖行", "暴风城拍卖行", "stormwind auction", "trade district auction house"],
        "team": "alliance",
        "map_id": 0,
        "map_name": "Eastern Kingdoms",
        "zone_name": "暴风城",
        "area_name": "贸易区",
        "x": -8816.0,
        "y": 666.0,
        "z": 99.0,
        "route_hint": "在暴风城贸易区，银行附近。",
    },
    {
        "key": "ironforge",
        "kind": "city",
        "name": "铁炉堡",
        "aliases": ["铁炉堡", "if", "ironforge"],
        "team": "alliance",
        "map_id": 0,
        "map_name": "Eastern Kingdoms",
        "zone_name": "铁炉堡",
        "area_name": "平民区",
        "x": -4981.0,
        "y": -881.0,
        "z": 502.0,
        "route_hint": "联盟主城；远距离优先使用飞行点、矿道地铁或法师传送门。",
    },
    {
        "key": "darnassus",
        "kind": "city",
        "name": "达纳苏斯",
        "aliases": ["达纳苏斯", "darnassus"],
        "team": "alliance",
        "map_id": 1,
        "map_name": "Kalimdor",
        "zone_name": "达纳苏斯",
        "area_name": "贸易区",
        "x": 9952.0,
        "y": 2280.0,
        "z": 1341.0,
        "route_hint": "联盟主城；通常经鲁瑟兰村传送或飞行点抵达。",
    },
    {
        "key": "exodar",
        "kind": "city",
        "name": "埃索达",
        "aliases": ["埃索达", "exodar"],
        "team": "alliance",
        "map_id": 530,
        "map_name": "Outland",
        "zone_name": "埃索达",
        "area_name": "圣光穹顶",
        "x": -3987.0,
        "y": -11846.0,
        "z": -2.0,
        "route_hint": "联盟主城；从秘蓝岛路线或主城传送门前往。",
    },
    {
        "key": "orgrimmar",
        "kind": "city",
        "name": "奥格瑞玛",
        "aliases": ["奥格", "奥格瑞玛", "og", "orgrimmar"],
        "team": "horde",
        "map_id": 1,
        "map_name": "Kalimdor",
        "zone_name": "奥格瑞玛",
        "area_name": "力量谷",
        "x": 1629.0,
        "y": -4373.0,
        "z": 31.0,
        "route_hint": "部落主城；远距离优先找飞行点、飞艇或传送门。",
    },
    {
        "key": "orgrimmar_auction_house",
        "kind": "auction_house",
        "name": "奥格瑞玛拍卖行",
        "aliases": ["奥格拍卖行", "奥格瑞玛拍卖行", "orgrimmar auction"],
        "team": "horde",
        "map_id": 1,
        "map_name": "Kalimdor",
        "zone_name": "奥格瑞玛",
        "area_name": "力量谷",
        "x": 1676.0,
        "y": -4450.0,
        "z": 20.0,
        "route_hint": "在奥格瑞玛力量谷东侧。",
    },
    {
        "key": "undercity",
        "kind": "city",
        "name": "幽暗城",
        "aliases": ["幽暗城", "undercity", "uc"],
        "team": "horde",
        "map_id": 0,
        "map_name": "Eastern Kingdoms",
        "zone_name": "幽暗城",
        "area_name": "贸易区",
        "x": 1586.0,
        "y": 239.0,
        "z": -52.0,
        "route_hint": "部落主城；可从提瑞斯法林地升降梯进入。",
    },
    {
        "key": "thunder_bluff",
        "kind": "city",
        "name": "雷霆崖",
        "aliases": ["雷霆崖", "tb", "thunder bluff"],
        "team": "horde",
        "map_id": 1,
        "map_name": "Kalimdor",
        "zone_name": "雷霆崖",
        "area_name": "中部高地",
        "x": -1280.0,
        "y": 127.0,
        "z": 131.0,
        "route_hint": "部落主城；远距离优先使用飞行点。",
    },
    {
        "key": "silvermoon",
        "kind": "city",
        "name": "银月城",
        "aliases": ["银月城", "silvermoon"],
        "team": "horde",
        "map_id": 530,
        "map_name": "Outland",
        "zone_name": "银月城",
        "area_name": "皇家贸易区",
        "x": 9487.0,
        "y": -7279.0,
        "z": 14.0,
        "route_hint": "部落主城；可经传送宝珠、飞行点或传送门抵达。",
    },
    {
        "key": "shattrath",
        "kind": "city",
        "name": "沙塔斯",
        "aliases": ["沙塔斯", "沙城", "shattrath"],
        "map_id": 530,
        "map_name": "Outland",
        "zone_name": "沙塔斯城",
        "area_name": "圣光广场",
        "x": -1848.0,
        "y": 5410.0,
        "z": -12.0,
        "route_hint": "外域主城；常见路线是主城传送门或飞行点。",
    },
    {
        "key": "dalaran",
        "kind": "city",
        "name": "达拉然",
        "aliases": ["达拉然", "dalaran"],
        "map_id": 571,
        "map_name": "Northrend",
        "zone_name": "达拉然",
        "area_name": "鲁因广场",
        "x": 5805.0,
        "y": 624.0,
        "z": 647.0,
        "route_hint": "诺森德主城；通常用传送门、炉石或飞行点。",
    },
    {
        "key": "flight_master",
        "kind": "flight_master",
        "name": "飞行管理员",
        "aliases": ["飞行点", "鸟点", "飞行管理员", "flight point", "flight master"],
        "route_hint": "先在当前营地或主城找飞行管理员；v1 只做路线建议，不自动坐飞机。",
    },
    {
        "key": "auction_house",
        "kind": "auction_house",
        "name": "拍卖行",
        "aliases": ["拍卖行", "ah", "auction house"],
        "route_hint": "先按阵营去最近主城拍卖行；v1 只读估价，不会自动买卖或挂售。",
    },
]


PLAYERBOT_COMMAND_CATALOG: list[dict[str, str]] = [
    {
        "category": "观察",
        "command_line": "list",
        "description": "列出请求玩家当前可控机器人的名单和在线/离线状态。",
        "preferred_tool": "wow_list_bots",
    },
    {
        "category": "观察",
        "command_line": "lookup",
        "description": "查看 AddClass 池里当前可召唤的机器人职业。",
        "preferred_tool": "wow_lookup_bot_pool",
    },
    {
        "category": "生命周期",
        "command_line": "addclass priest female / add 奶我一口",
        "description": "按职业召唤 AddClass 机器人。只指定职业/性别时用 addclass；指定种族时由 MCP 先选候选角色，再执行 add <Botname>。",
        "preferred_tool": "wow_summon_bot",
    },
    {
        "category": "生命周期",
        "command_line": "add Botname",
        "description": "把请求玩家自己账号或已绑定账号里的指定角色登录为机器人。",
        "preferred_tool": "wow_run_playerbot_command",
    },
    {
        "category": "生命周期",
        "command_line": "addaccount AccountOrCharacter",
        "description": "从允许的账号登录角色；仍受服务端账号绑定规则限制。",
        "preferred_tool": "wow_run_playerbot_command",
    },
    {
        "category": "生命周期",
        "command_line": "init=auto Botname",
        "description": "按请求玩家等级和装备分数自动初始化一个 AddClass 机器人。",
        "preferred_tool": "wow_init_bot",
    },
    {
        "category": "生命周期",
        "command_line": "init=epic Botname",
        "description": "在服务端权限和配置允许时，按装备品质初始化 AddClass 机器人；别名包括 white/common、green/uncommon、blue/rare、epic/purple、legendary/yellow。",
        "preferred_tool": "wow_run_playerbot_command",
    },
    {
        "category": "生命周期",
        "command_line": "init=5000 Botname",
        "description": "在服务端权限和配置允许时，按指定 gear score 初始化 AddClass 机器人。",
        "preferred_tool": "wow_run_playerbot_command",
    },
    {
        "category": "生命周期",
        "command_line": "remove Botname",
        "description": "让一个可控机器人下线。",
        "preferred_tool": "wow_dismiss_bot",
    },
    {
        "category": "维护",
        "command_line": "refresh Botname",
        "description": "按当前等级刷新一个 AddClass 机器人。",
        "preferred_tool": "wow_refresh_bot",
    },
    {
        "category": "维护",
        "command_line": "refresh=raid Botname",
        "description": "服务端接受时，解除或重置一个 AddClass 机器人的团队副本绑定状态。",
        "preferred_tool": "wow_run_playerbot_command",
    },
    {
        "category": "维护",
        "command_line": "levelup Botname",
        "description": "按等级重新随机/整理一个 AddClass 机器人。",
        "preferred_tool": "wow_level_bot",
    },
    {
        "category": "维护",
        "command_line": "quests Botname",
        "description": "为一个 AddClass 机器人初始化副本任务。",
        "preferred_tool": "wow_init_instance_quests",
    },
    {
        "category": "维护",
        "command_line": "random Botname",
        "description": "在服务端权限和配置允许时，随机化一个 AddClass 机器人。",
        "preferred_tool": "wow_run_playerbot_command",
    },
    {
        "category": "小队指挥",
        "command_line": "follow",
        "description": "让一个或一组可控机器人跟随主人；优先使用 typed tool。",
        "preferred_tool": "wow_bot_follow",
    },
    {
        "category": "小队指挥",
        "command_line": "stay",
        "description": "让一个或一组可控机器人原地停留。",
        "preferred_tool": "wow_bot_stay",
    },
    {
        "category": "小队指挥",
        "command_line": "flee",
        "description": "让一个或一组可控机器人回撤。",
        "preferred_tool": "wow_bot_retreat",
    },
    {
        "category": "小队指挥",
        "command_line": "attack",
        "description": "让一个或一组可控机器人攻击请求玩家当前目标。",
        "preferred_tool": "wow_bot_attack_target",
    },
    {
        "category": "小队指挥",
        "command_line": "pull",
        "description": "让指定机器人拉请求玩家当前目标。",
        "preferred_tool": "wow_bot_pull",
    },
    {
        "category": "小队指挥",
        "command_line": "ready",
        "description": "让一个或一组可控机器人执行准备确认。",
        "preferred_tool": "wow_bot_ready",
    },
    {
        "category": "小队指挥",
        "command_line": "focus heal +Player",
        "description": "让治疗机器人重点照看某个玩家。",
        "preferred_tool": "wow_focus_heal",
    },
    {
        "category": "补给",
        "command_line": "provide consumables Botname Player water=1 food=1",
        "description": "让可控法师给请求玩家或指定队友提供法师水和面包；默认各一组。",
        "preferred_tool": "wow_bot_provide_consumables",
    },
    {
        "category": "策略",
        "command_line": "+loot / -loot / ll normal|gray|all",
        "description": "设置拾取策略。",
        "preferred_tool": "wow_set_loot_mode",
    },
    {
        "category": "策略",
        "command_line": "+buff / -buff",
        "description": "打开或关闭非战斗 buff 策略。",
        "preferred_tool": "wow_set_buff",
    },
    {
        "category": "策略",
        "command_line": "+healer dps / -healer dps",
        "description": "允许或禁止治疗在战斗中补输出。",
        "preferred_tool": "wow_set_healer_dps",
    },
    {
        "category": "策略",
        "command_line": "profile Botname / strategy",
        "description": "读取机器人画像、真实专精、当前 AI 状态和已激活策略。回答“你是什么天赋/职责”前优先用这个。",
        "preferred_tool": "wow_get_bot_profile",
    },
    {
        "category": "策略",
        "command_line": "+heal / +shadow / +bear / +frost 等",
        "description": "开关某个安全 AI 策略；更推荐用 wow_set_bot_role 做高层职责切换。",
        "preferred_tool": "wow_set_bot_strategy",
    },
    {
        "category": "策略",
        "command_line": "set role healer/tank/dps/shadow/bear/frost",
        "description": "按职业安全切换高层职责或流派；这是 AI 策略切换，不等于重新洗真实天赋。",
        "preferred_tool": "wow_set_bot_role",
    },
    {
        "category": "高级",
        "command_line": "self",
        "description": "如果请求玩家的服务端权限允许，切换真人角色的 self-bot 模式。",
        "preferred_tool": "wow_run_playerbot_command",
    },
    {
        "category": "高级",
        "command_line": "tweak",
        "description": "如果服务端接受，循环切换 playerbot tweak 值。",
        "preferred_tool": "wow_run_playerbot_command",
    },
    {
        "category": "高级",
        "command_line": "reload",
        "description": "请求玩家有 GM 权限时，重新加载 Playerbots 配置。",
        "preferred_tool": "wow_run_playerbot_command",
    },
    {
        "category": "高级",
        "command_line": "initself=epic",
        "description": "GM 权限允许时初始化请求玩家自己的角色；变体包括 initself 和 initself=<quality|gearScore>。",
        "preferred_tool": "wow_run_playerbot_command",
    },
]


def db() -> MysqlCli:
    return MysqlCli.from_env()


def world_db() -> MysqlCli:
    return MysqlCli(os.getenv("PLAYERBOT_MCP_WORLD_DB_DSN", DEFAULT_WORLD_DB_DSN))


def characters_db() -> MysqlCli:
    return MysqlCli(os.getenv("PLAYERBOT_MCP_CHARACTERS_DB_DSN", DEFAULT_CHARACTERS_DB_DSN))


def auth_db() -> MysqlCli:
    return MysqlCli(os.getenv("PLAYERBOT_MCP_AUTH_DB_DSN", DEFAULT_AUTH_DB_DSN))


def collect_context_bots(event: dict[str, Any], allowed_kinds: set[str]) -> list[dict[str, Any]]:
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    sources: list[Any] = []
    if isinstance(context.get("bots"), list):
        sources.extend(context["bots"])
    if isinstance(context.get("target_bot_context"), dict):
        sources.append(context["target_bot_context"])
    if isinstance(context.get("group_members"), list):
        sources.extend(context["group_members"])

    bots: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in sources:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        kind = str(item.get("bot_kind") or "").strip()
        if not name or kind not in allowed_kinds:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        bots.append(item)
    return bots


def bot_candidates(event: dict[str, Any]) -> list[dict[str, Any]]:
    return collect_context_bots(event, CONTROLLED_BOT_KINDS)


def reply_bot_candidates(event: dict[str, Any]) -> list[dict[str, Any]]:
    return collect_context_bots(event, REPLY_BOT_KINDS)


def anchor_bot_name() -> str:
    return os.getenv("PLAYERBOT_AGENT_ANCHOR_BOT_NAME", ANCHOR_BOT_DEFAULT_NAME).strip()


def split_config_list(value: str) -> list[str]:
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


def configured_admin_player_names() -> set[str]:
    configured = os.getenv("PLAYERBOT_MCP_ADMIN_PLAYER_NAMES", ADMIN_PLAYER_NAMES_DEFAULT)
    return {item.lower() for item in split_config_list(configured) if item}


def required_admin_gm_level() -> int:
    return env_int("PLAYERBOT_MCP_ADMIN_GM_LEVEL", ADMIN_GM_LEVEL_DEFAULT, 0)


def min_dual_spec_level() -> int:
    return env_int("PLAYERBOT_MCP_MIN_DUAL_SPEC_LEVEL", MIN_DUAL_SPEC_LEVEL_DEFAULT, 1)


def event_speaker_name(event: dict[str, Any] | None) -> str:
    if not event:
        return ""
    return str(event.get("speaker_name") or "").strip()


def event_speaker_account(event: dict[str, Any] | None) -> int:
    if not event:
        return 0
    return int(event.get("speaker_account") or 0)


def account_gm_level(account_id: int) -> int:
    if int(account_id or 0) <= 0:
        return 0
    try:
        return auth_db().scalar_int(
            "SELECT COALESCE(MAX(`gmlevel`), 0) FROM `account_access` "
            f"WHERE `id` = {int(account_id)} AND `RealmID` IN (-1, 0, 1)"
        )
    except RuntimeError as exc:
        log_event("tool_admin_denied", account_id=int(account_id), reason="gm_lookup_failed", error=str(exc))
        return 0


def event_speaker_is_admin(event: dict[str, Any] | None) -> bool:
    gm_level = account_gm_level(event_speaker_account(event))
    name_allowlist = configured_admin_player_names()
    if name_allowlist and event_speaker_name(event).lower() not in name_allowlist:
        return False
    return gm_level >= required_admin_gm_level()


def command_requires_admin_audit(command: str) -> bool:
    normalized = str(command or "").strip().lower()
    return any(
        normalized == prefix or normalized.startswith(prefix + " ") or normalized.startswith(prefix + "=")
        for prefix in AUDITED_PLAYERBOT_COMMAND_PREFIXES
    )


def admin_denied_payload(event: dict[str, Any], ability: str, requested: str) -> dict[str, Any]:
    log_event(
        "tool_admin_denied",
        source_event_id=int(event.get("id") or 0),
        speaker=event_speaker_name(event),
        speaker_account=event_speaker_account(event),
        gm_level=account_gm_level(event_speaker_account(event)),
        required_gm_level=required_admin_gm_level(),
        ability=ability,
        requested=requested,
        allowed_speakers=sorted(configured_admin_player_names()),
    )
    return {
        "ok": False,
        "error": "admin_only",
        "ability": ability,
        "requested": requested,
        "speaker": event_speaker_name(event),
        "speaker_account": event_speaker_account(event),
        "gm_level": account_gm_level(event_speaker_account(event)),
        "required_gm_level": required_admin_gm_level(),
        "allowed_speakers": sorted(configured_admin_player_names()),
    }


def anchor_bot_aliases() -> list[str]:
    names = [anchor_bot_name(), *split_config_list(os.getenv("PLAYERBOT_AGENT_ANCHOR_ALIASES", ""))]
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.lower()
        if key and key not in seen:
            seen.add(key)
            result.append(name)
    return result


def anchor_reply_bot_name(bots: list[dict[str, Any]]) -> str | None:
    aliases = {name.lower() for name in anchor_bot_aliases()}
    if not aliases:
        return None
    for bot in bots:
        name = str(bot.get("name") or "").strip()
        if name and name.lower() in aliases:
            return name
    return None


def default_bot_name(event: dict[str, Any]) -> str | None:
    if event.get("bot_name"):
        return str(event["bot_name"])
    bots = reply_bot_candidates(event)
    if bots:
        return str(bots[0].get("name") or "") or None
    return None


def canonical_bot_name(bots: list[dict[str, Any]], requested: str) -> str | None:
    wanted = requested.strip().lower()
    if not wanted:
        return None
    for bot in bots:
        name = str(bot.get("name") or "").strip()
        if name and name.lower() == wanted:
            return name
    return None


def resolve_reply_bot_name(event: dict[str, Any], channel: str, requested_bot_name: str = "") -> tuple[str | None, str]:
    """Pick an online-looking speaker for a visible reply.

    Hermes may remember an old bot name such as Gessa after that bot has gone
    offline. For party/say replies, prefer the current event's online bot
    context over stale requested names. For newly summoned bots not yet present
    in the event snapshot, keep the requested name and let the bridge try it.
    """
    requested = str(requested_bot_name or "").strip()
    normalized_channel = str(channel or "").strip().lower()
    bots = reply_bot_candidates(event)

    if normalized_channel == "whisper":
        target = requested or str(event.get("bot_name") or "").strip() or str(event.get("target_name") or "").strip()
        if target:
            return canonical_bot_name(bots, target) or target, "whisper_target"
        if bots:
            return str(bots[0].get("name") or "") or None, "whisper_fallback"
        return None, "no_reply_bot"

    if requested:
        canonical = canonical_bot_name(bots, requested)
        if canonical:
            return canonical, "requested_online"
        anchor = anchor_reply_bot_name(bots)
        if anchor:
            return anchor, "anchor_requested_offline_fallback"
        if bots:
            return str(bots[0].get("name") or "") or None, "requested_offline_fallback"
        return requested, "requested_unverified"

    anchor = anchor_reply_bot_name(bots)
    if anchor:
        return anchor, "anchor_default"

    if event.get("bot_name"):
        canonical = canonical_bot_name(bots, str(event["bot_name"]))
        return canonical or str(event["bot_name"]), "event_bot"

    if bots:
        return str(bots[0].get("name") or "") or None, "default_online"
    return None, "no_reply_bot"


def normalize_alias(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def normalize_class_hint(class_hint: str, role: str = "") -> str:
    normalized = normalize_alias(class_hint)
    for class_name, aliases in CLASS_ALIASES.items():
        if normalized in aliases:
            return class_name

    normalized_role = normalize_alias(role)
    if normalized_role in {"healer", "heal"}:
        return "priest"
    if normalized_role == "tank":
        return "warrior"
    if normalized_role in {"melee_dps", "melee"}:
        return "rogue"
    return "mage"


def normalize_gender(gender: str) -> str:
    normalized = normalize_alias(gender)
    if not normalized:
        return ""
    if normalized in {"male", "0", "男", "男性"}:
        return "male"
    if normalized in {"female", "1", "女", "女性"}:
        return "female"
    return ""


def gender_id(gender: str) -> int | None:
    normalized = normalize_gender(gender)
    if normalized == "male":
        return 0
    if normalized == "female":
        return 1
    return None


def normalize_race_hint(race_hint: str) -> int | None:
    normalized = normalize_alias(race_hint)
    if not normalized:
        return None
    for race_name, aliases in RACE_ALIASES.items():
        if normalized in aliases:
            return RACE_IDS[race_name]
    if normalized.isdigit():
        race_id = int(normalized)
        if race_id in RACE_NAMES:
            return race_id
    return None


def race_team(race_id: int) -> int | None:
    if race_id in ALLIANCE_RACES:
        return 0
    if race_id in HORDE_RACES:
        return 1
    return None


def event_team(event: dict[str, Any]) -> int | None:
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    speaker = context.get("speaker") if isinstance(context.get("speaker"), dict) else {}
    if speaker.get("team") is None:
        return None
    try:
        return int(speaker.get("team"))
    except (TypeError, ValueError):
        return None


def addclass_candidate_dict(row: list[str | None]) -> dict[str, Any]:
    race_id = int(row[2] or 0)
    class_id = int(row[3] or 0)
    candidate_gender = int(row[4] or 0)
    return {
        "guid": int(row[0] or 0),
        "name": str(row[1] or ""),
        "race": race_id,
        "race_name": RACE_NAMES.get(race_id, str(race_id)),
        "race_zh": RACE_ZH.get(race_id, str(race_id)),
        "class": class_id,
        "class_name": CLASS_NAMES.get(class_id, str(class_id)),
        "gender": candidate_gender,
        "gender_name": "female" if candidate_gender == 1 else "male",
        "gender_zh": "女" if candidate_gender == 1 else "男",
        "level": int(row[5] or 0),
    }


def find_addclass_candidates(
    conn: MysqlCli,
    event: dict[str, Any],
    *,
    class_id: int | None = None,
    race_id: int | None = None,
    gender: int | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    where = ["pat.account_type = 2", "c.online = 0"]
    if class_id:
        where.append(f"c.class = {int(class_id)}")
    if race_id:
        where.append(f"c.race = {int(race_id)}")
    if gender is not None:
        where.append(f"c.gender = {int(gender)}")

    team = event_team(event)
    if team == 0:
        where.append("c.race IN (1,3,4,7,11)")
    elif team == 1:
        where.append("c.race IN (2,5,6,8,10)")

    bounded_limit = max(1, min(int(limit), 100))
    rows = conn.query_rows(
        "SELECT c.guid, c.name, c.race, c.class, c.gender, c.level "
        "FROM acore_playerbot_characters.characters c "
        "JOIN acore_playerbots.playerbots_account_type pat ON pat.account_id = c.account "
        "WHERE "
        + " AND ".join(where)
        + " ORDER BY c.level DESC, c.name ASC LIMIT "
        + str(bounded_limit)
    )
    return [addclass_candidate_dict(row) for row in rows]


def bot_name_from_candidate(bot: dict[str, Any]) -> str:
    return str(bot.get("name") or "").strip()


def bot_role(bot: dict[str, Any]) -> str:
    return str(bot.get("role") or "").strip().lower()


def normalize_strategy_name(strategy: str) -> str:
    normalized = str(strategy or "").strip().lower().replace("_", " ")
    while "  " in normalized:
        normalized = normalized.replace("  ", " ")
    if normalized[:1] in {"+", "-", "~", "?"}:
        normalized = normalized[1:].strip()
    return normalized


def normalize_strategy_change(strategy: str, op: str = "add") -> str:
    text = str(strategy or "").strip().lower().replace("_", " ")
    if text[:1] in {"+", "-"}:
        sign = text[0]
        name = normalize_strategy_name(text)
    else:
        normalized_op = str(op or "add").strip().lower()
        sign = "-" if normalized_op in {"remove", "del", "delete", "off", "-", "disable", "disabled"} else "+"
        name = normalize_strategy_name(text)
    return f"{sign}{name}" if name else ""


def parse_active_strategies(value: Any) -> list[str]:
    if isinstance(value, list):
        return [normalize_strategy_name(str(item)) for item in value if normalize_strategy_name(str(item))]
    text = str(value or "").strip()
    if not text:
        return []
    if text.lower().startswith("strategies:"):
        text = text.split(":", 1)[1]
    return [normalize_strategy_name(item) for item in text.split(",") if normalize_strategy_name(item)]


def supported_strategy_names(class_id: int = 0) -> list[str]:
    strategies = set(GLOBAL_SAFE_STRATEGIES)
    if int(class_id or 0) in CLASS_STRATEGIES:
        strategies |= CLASS_STRATEGIES[int(class_id or 0)]
    return sorted(strategies)


def strategy_supported_for_class(class_id: int, strategy: str) -> bool:
    name = normalize_strategy_name(strategy)
    if not name:
        return False
    return name in supported_strategy_names(int(class_id or 0))


def class_display(class_id: int) -> dict[str, Any]:
    return {
        "id": int(class_id or 0),
        "name": CLASS_NAMES.get(int(class_id or 0), ""),
        "zh": CLASS_ZH.get(int(class_id or 0), ""),
    }


def spec_display(spec_name: str) -> dict[str, str]:
    normalized = str(spec_name or "").strip().lower()
    return {"name": normalized, "zh": SPEC_ZH.get(normalized, "")}


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
        name = bot_name_from_candidate(item)
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def find_context_bot(event: dict[str, Any], bot_name: str = "") -> dict[str, Any] | None:
    selector = str(bot_name or "").strip().lower()
    bots = event_context_sources(event)
    if selector:
        for bot in bots:
            if bot_name_from_candidate(bot).lower() == selector:
                return bot
        return None

    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    target = context.get("target_bot_context") if isinstance(context.get("target_bot_context"), dict) else {}
    if target and bot_name_from_candidate(target):
        return target
    return bots[0] if bots else None


def fetch_character_identity(bot_name: str = "", guid: int = 0) -> dict[str, Any] | None:
    clauses: list[str] = []
    if int(guid or 0):
        clauses.append(f"`guid` = {int(guid)}")
    if str(bot_name or "").strip():
        clauses.append(f"`name` = {sql_quote(str(bot_name).strip())}")
    if not clauses:
        return None
    rows = characters_db().query_rows(
        "SELECT `guid`, `name`, `race`, `class`, `level`, `online`, `activeTalentGroup`, `talentGroupsCount` "
        "FROM `characters` WHERE "
        + " OR ".join(clauses)
        + " ORDER BY `online` DESC LIMIT 1"
    )
    if not rows:
        return None
    row = rows[0]
    return {
        "guid": int(row[0] or 0),
        "name": str(row[1] or ""),
        "race": int(row[2] or 0),
        "class": int(row[3] or 0),
        "level": int(row[4] or 0),
        "online": bool(int(row[5] or 0)),
        "active_talent_group": int(row[6] or 0),
        "talent_groups_count": int(row[7] or 0),
    }


def int_value(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def talent_group_snapshot(name: str = "", guid: int = 0) -> dict[str, int]:
    try:
        row = fetch_character_identity(name, guid)
    except (OSError, RuntimeError) as exc:
        log_event("tool_bot_profile_talent_snapshot_failed", name=name, guid=int(guid or 0), error=str(exc))
        return {}
    if not row:
        return {}
    return {
        "active_talent_group": int(row.get("active_talent_group") or 0),
        "talent_groups_count": int(row.get("talent_groups_count") or 0),
    }


def talent_group_profile(level: int, active_talent_group: int, talent_groups_count: int) -> dict[str, Any]:
    count = max(0, int(talent_groups_count or 0))
    active_index = max(0, int(active_talent_group or 0))
    min_level = min_dual_spec_level()
    known = count > 0
    has_second = count >= 2
    reason = ""
    if not known:
        reason = "当前没有拿到天赋页数量"
    elif not has_second:
        reason = f"当前只有{count}套天赋"
        if int(level or 0) < min_level:
            reason += f"；服务器双天赋解锁等级是{min_level}级，当前{int(level or 0)}级"
        else:
            reason += "；还没有开启第二套天赋"
    return {
        "active_index": active_index,
        "active_label": active_index + 1 if count else 0,
        "count": count,
        "known": known,
        "has_second": has_second,
        "min_dual_spec_level": min_level,
        "second_unavailable_reason": reason,
    }


def bot_profile_from_context(bot: dict[str, Any]) -> dict[str, Any]:
    class_id = int(bot.get("class") or 0)
    spec_name = str(bot.get("spec_name") or bot.get("spec") or "").strip().lower()
    strategy_text = bot.get("active_strategy_text") or bot.get("strategy_text") or bot.get("strategies") or ""
    active_strategies = parse_active_strategies(bot.get("active_strategies") if bot.get("active_strategies") else strategy_text)
    guid = int_value(bot.get("guid"))
    name = bot_name_from_candidate(bot)
    active_talent_group = int_value(bot.get("active_talent_group"), int_value(bot.get("activeTalentGroup")))
    talent_groups_count = int_value(bot.get("talent_groups_count"), int_value(bot.get("talentGroupsCount")))
    if talent_groups_count <= 0 and (guid or name):
        snapshot = talent_group_snapshot(name, guid)
        active_talent_group = int(snapshot.get("active_talent_group", active_talent_group))
        talent_groups_count = int(snapshot.get("talent_groups_count", talent_groups_count))
    level = int(bot.get("level") or 0)
    return {
        "guid": guid,
        "name": name,
        "bot_kind": str(bot.get("bot_kind") or ""),
        "class": class_display(class_id),
        "race": {"id": int(bot.get("race") or 0), "zh": RACE_ZH.get(int(bot.get("race") or 0), "")},
        "level": level,
        "role": bot_role(bot) or "unknown",
        "spec": spec_display(spec_name),
        "active_talent_group": active_talent_group,
        "talent_groups_count": talent_groups_count,
        "talent_groups": talent_group_profile(level, active_talent_group, talent_groups_count),
        "ai_state": str(bot.get("ai_state") or "").strip(),
        "active_strategies": active_strategies,
        "supported_strategies": supported_strategy_names(class_id),
        "supported_roles": supported_role_names(class_id),
        "health_pct": bot.get("health_pct"),
        "mana_pct": bot.get("mana_pct"),
        "alive": bot.get("alive"),
        "combat": bot.get("combat"),
        "location": bot.get("location") if isinstance(bot.get("location"), dict) else {
            "map_id": bot.get("map_id"),
            "zone_id": bot.get("zone_id"),
            "area_id": bot.get("area_id"),
            "map_name": bot.get("map_name"),
            "zone_name": bot.get("zone_name"),
            "area_name": bot.get("area_name"),
        },
        "selected_target": bot.get("selected_target") if isinstance(bot.get("selected_target"), dict) else None,
        "combat_target": bot.get("combat_target") if isinstance(bot.get("combat_target"), dict) else None,
        "truth": {
            "source": "event_context",
            "spec_available": bool(spec_name),
            "strategies_available": bool(active_strategies),
            "note": "spec/active_strategies 只有 bridge 已升级并且 bot 在线时才是实时事实。",
        },
    }


def bot_profile_from_character(row: dict[str, Any]) -> dict[str, Any]:
    class_id = int(row.get("class") or 0)
    level = int(row.get("level") or 0)
    active_talent_group = int(row.get("active_talent_group") or 0)
    talent_groups_count = int(row.get("talent_groups_count") or 0)
    return {
        "guid": int(row.get("guid") or 0),
        "name": str(row.get("name") or ""),
        "bot_kind": "database_character",
        "class": class_display(class_id),
        "race": {"id": int(row.get("race") or 0), "zh": RACE_ZH.get(int(row.get("race") or 0), "")},
        "level": level,
        "role": "unknown",
        "spec": {"name": "", "zh": ""},
        "ai_state": "",
        "active_strategies": [],
        "supported_strategies": supported_strategy_names(class_id),
        "supported_roles": supported_role_names(class_id),
        "online": bool(row.get("online")),
        "active_talent_group": active_talent_group,
        "talent_groups_count": talent_groups_count,
        "talent_groups": talent_group_profile(level, active_talent_group, talent_groups_count),
        "truth": {
            "source": "characters_db",
            "spec_available": False,
            "strategies_available": False,
            "note": "数据库只确认角色身份；真实 spec 和当前 AI 策略需要在线 bridge 上下文。",
        },
    }


def supported_role_names(class_id: int) -> list[str]:
    plans = ROLE_PLANS.get(int(class_id or 0), {})
    return sorted(name for name, plan in plans.items() if not plan.get("alias"))


def normalize_role_request(role: str) -> str:
    normalized = normalize_alias(role).replace(" ", "_")
    aliases = {
        "奶": "healer",
        "奶妈": "healer",
        "治疗": "healer",
        "纯治疗": "healer",
        "坦": "tank",
        "坦克": "tank",
        "输出": "dps",
        "暗牧": "shadow",
        "神牧": "holy",
        "戒律": "healer",
        "熊": "tank",
        "熊坦": "tank",
        "猫": "cat",
        "猫德": "cat",
        "鸟德": "caster",
        "平衡": "caster",
        "奶德": "healer",
        "防骑": "tank",
        "奶骑": "healer",
        "惩戒": "dps",
        "增强": "enh",
        "元素": "ele",
        "恢复": "healer",
        "冰法": "frost",
        "火法": "fire",
        "奥法": "arcane",
        "痛苦": "affli",
        "恶魔": "demo",
        "毁灭": "destro",
        "兽王": "bm",
        "射击": "mm",
        "生存": "surv",
        "鲜血": "blood",
        "冰霜": "frost",
        "邪恶": "unholy",
    }
    return aliases.get(normalized, normalized)


def role_plan_for_class(class_id: int, role: str) -> dict[str, Any] | None:
    plans = ROLE_PLANS.get(int(class_id or 0), {})
    key = normalize_role_request(role)
    seen: set[str] = set()
    while key in plans and plans[key].get("alias"):
        if key in seen:
            return None
        seen.add(key)
        key = str(plans[key]["alias"])
    plan = plans.get(key)
    if not plan:
        return None
    return {**plan, "key": key}


def unique_bot_names(bots: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for bot in bots:
        name = bot_name_from_candidate(bot)
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            names.append(name)
    return names


def is_mage_bot(bot: dict[str, Any]) -> bool:
    return int(bot.get("class") or 0) == CLASS_IDS["mage"]


def select_mage_bot_name(event: dict[str, Any], bot_name: str = "") -> str:
    selector = str(bot_name or "").strip()
    bots = bot_candidates(event)
    if selector:
        wanted = selector.lower()
        for bot in bots:
            name = bot_name_from_candidate(bot)
            if name and name.lower() == wanted:
                return name
        return selector

    for bot in bots:
        if is_mage_bot(bot):
            return bot_name_from_candidate(bot)
    return ""


def normalize_consumable_stacks(value: int, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0, min(parsed, CONSUMABLE_STACK_LIMIT))


def is_safe_player_name(value: str) -> bool:
    return bool(value) and len(value) <= 64 and not any(ord(ch) < 0x20 or ch in {"'", '"', "`", ";", "\\"} for ch in value)


def select_bot_names(
    event: dict[str, Any],
    bot_name: str = "",
    *,
    default_selector: str = "group",
    role_filter: str = "",
    fallback_first: bool = False,
) -> list[str]:
    bots = bot_candidates(event)
    selector = str(bot_name or "").strip()
    if not selector:
        selector = default_selector
    normalized = selector.lower()

    if role_filter:
        bots = [bot for bot in bots if bot_role(bot) == role_filter]

    if not selector and event.get("bot_name"):
        return [str(event["bot_name"])]

    if normalized in GROUP_SELECTORS:
        return unique_bot_names(bots)

    for role, aliases in ROLE_SELECTORS.items():
        if normalized in aliases:
            names = unique_bot_names([bot for bot in bots if bot_role(bot) == role])
            if names or not fallback_first:
                return names
            return unique_bot_names(bots[:1])

    if normalized in {"", "default"}:
        if event.get("bot_name"):
            return [str(event["bot_name"])]
        if len(bots) == 1 or fallback_first:
            return unique_bot_names(bots[:1])
        return []

    return [selector]


def enqueue_bot_command(
    event_id: int,
    *,
    bot_name: str = "",
    command: str,
    default_selector: str = "group",
    role_filter: str = "",
    fallback_first: bool = False,
) -> dict[str, Any]:
    command_text = command.strip()
    normalized = command_text.lower()
    if normalized not in BOT_COMMANDS and not normalized.startswith("focus heal +") and not normalized.startswith("focus heal -"):
        return {"ok": False, "error": "command_not_exposed"}

    conn = db()
    event = fetch_event(conn, int(event_id))
    if not event:
        return {"ok": False, "error": "event_not_found"}

    targets = select_bot_names(
        event,
        bot_name,
        default_selector=default_selector,
        role_filter=role_filter,
        fallback_first=fallback_first,
    )
    if not targets:
        return {"ok": False, "error": "no_controllable_bot"}

    action_ids: list[int] = []
    for target in targets:
        result = enqueue_action(
            conn,
            event=event,
            action_type="command",
            bot_name=target,
            command=command_text,
            payload={"command": command_text},
        )
        if result.get("action_id"):
            action_ids.append(int(result["action_id"]))

    return payload_result(
        "tool_bot_command",
        source_event_id=int(event_id),
        command=command_text,
        targets=targets,
        action_ids=action_ids,
    )


def enqueue_bot_strategy(
    event_id: int,
    *,
    bot_name: str = "",
    strategy: str,
    bot_state: str = "all",
    default_selector: str = "group",
    role_filter: str = "",
) -> dict[str, Any]:
    normalized = strategy.strip().lower()
    if normalized not in STRATEGIES:
        return {"ok": False, "error": "strategy_not_exposed"}

    state = bot_state.strip().lower().replace("_", "-") or "all"
    if state == "non-combat":
        state = "noncombat"
    if state not in {"all", "combat", "noncombat", "dead"}:
        return {"ok": False, "error": "invalid_bot_state", "allowed": ["all", "combat", "noncombat", "dead"]}

    conn = db()
    event = fetch_event(conn, int(event_id))
    if not event:
        return {"ok": False, "error": "event_not_found"}

    targets = select_bot_names(event, bot_name, default_selector=default_selector, role_filter=role_filter)
    if not targets:
        return {"ok": False, "error": "no_controllable_bot"}

    action_ids: list[int] = []
    for target in targets:
        result = enqueue_action(
            conn,
            event=event,
            action_type="strategy",
            bot_name=target,
            strategy=normalized,
            bot_state=state,
            payload={"strategy": normalized, "bot_state": state},
        )
        if result.get("action_id"):
            action_ids.append(int(result["action_id"]))

    return payload_result(
        "tool_bot_strategy",
        source_event_id=int(event_id),
        strategy=normalized,
        bot_state=state,
        targets=targets,
        action_ids=action_ids,
    )


def normalize_bot_state(bot_state: str) -> str:
    state = str(bot_state or "all").strip().lower().replace("_", "-") or "all"
    if state == "non-combat":
        state = "noncombat"
    return state


def enqueue_strategy_changes(
    conn: MysqlCli,
    event: dict[str, Any],
    *,
    targets: list[str],
    changes: list[str],
    bot_state: str,
    require_supported: bool = True,
) -> dict[str, Any]:
    state = normalize_bot_state(bot_state)
    if state not in {"all", "combat", "noncombat", "dead"}:
        return {"ok": False, "error": "invalid_bot_state", "allowed": ["all", "combat", "noncombat", "dead"]}

    context_by_name = {bot_name_from_candidate(bot).lower(): bot for bot in event_context_sources(event)}
    action_ids: list[int] = []
    blocked: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    normalized_changes = [normalize_strategy_change(change) for change in changes if normalize_strategy_change(change)]
    if not normalized_changes:
        return {"ok": False, "error": "empty_strategy"}

    for target in targets:
        bot = context_by_name.get(target.lower())
        class_id = int(bot.get("class") or 0) if bot else 0
        for change in normalized_changes:
            if require_supported and not strategy_supported_for_class(class_id, change):
                blocked.append(
                    {
                        "bot": target,
                        "strategy": change,
                        "reason": "strategy_not_supported_for_bot_class",
                        "class": class_display(class_id),
                    }
                )
                continue
            result = enqueue_action(
                conn,
                event=event,
                action_type="strategy",
                bot_name=target,
                strategy=change,
                bot_state=state,
                payload={
                    "strategy": change,
                    "bot_state": state,
                    "class_id": class_id,
                    "audit": "strategy_change",
                },
            )
            if result.get("action_id"):
                action_ids.append(int(result["action_id"]))
            accepted.append({"bot": target, "strategy": change, "action_id": result.get("action_id")})

    return {
        "ok": bool(action_ids) or bool(accepted),
        "bot_state": state,
        "targets": targets,
        "changes": normalized_changes,
        "accepted": accepted,
        "blocked": blocked,
        "action_ids": action_ids,
    }


def get_event_or_latest(conn: MysqlCli, event_id: int = 0, speaker_guid: int = 0) -> dict[str, Any] | None:
    if event_id:
        return fetch_event(conn, int(event_id))
    return latest_event(conn, int(speaker_guid or 0))


def combat_leader_guid(event: dict[str, Any] | None) -> int:
    if not event:
        return 0
    return int(event.get("group_leader_guid") or event.get("speaker_guid") or 0)


def event_speaker_level(event: dict[str, Any] | None) -> int:
    if not event:
        return 0
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    speaker = context.get("speaker") if isinstance(context.get("speaker"), dict) else {}
    return int(speaker.get("level") or 0)


def payload_result(event: str, **fields: Any) -> dict[str, Any]:
    log_event(event, **fields)
    return {"ok": True, **fields}


def clean_playerbot_command_line(command_line: str) -> str:
    command = str(command_line or "").strip()
    lowered = command.lower()
    if lowered.startswith(".playerbots"):
        command = command[len(".playerbots") :].strip()
        lowered = command.lower()
    if lowered.startswith(".bot"):
        command = command[len(".bot") :].strip()
        lowered = command.lower()
    if lowered == "bot" or lowered.startswith("bot "):
        command = command[3:].strip()
    if not command:
        raise ValueError("empty_playerbot_command")
    if len(command) > PLAYERBOT_COMMAND_MAX_LEN:
        raise ValueError("playerbot_command_too_long")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in command):
        raise ValueError("playerbot_command_has_control_character")
    return command


def enqueue_named_bot_action(event_id: int, action_type: str, bot_name: str, payload_key: str = "bot") -> dict[str, Any]:
    conn = db()
    event = fetch_event(conn, int(event_id))
    if not event:
        return {"ok": False, "error": "event_not_found"}
    target = bot_name.strip()
    if not target:
        return {"ok": False, "error": "empty_bot_name"}
    result = enqueue_action(conn, event=event, action_type=action_type, bot_name=target, payload={payload_key: target})
    return payload_result(
        f"tool_{action_type}",
        source_event_id=int(event_id),
        bot=target,
        payload={payload_key: target},
        **result,
    )


def normalize_place_text(value: str) -> str:
    return str(value or "").strip().lower().replace("_", " ")


def place_matches(place: dict[str, Any], query: str) -> int:
    needle = normalize_place_text(query)
    if not needle:
        return 0
    names = [str(place.get("name") or ""), str(place.get("key") or ""), *[str(item) for item in place.get("aliases", [])]]
    score = 0
    for name in names:
        normalized = normalize_place_text(name)
        if not normalized:
            continue
        if normalized == needle:
            score = max(score, 100)
        elif needle in normalized or normalized in needle:
            score = max(score, 70)
    return score


def place_team_matches(place: dict[str, Any], team: int | None) -> bool:
    place_team = str(place.get("team") or "").strip().lower()
    if not place_team or team is None:
        return True
    return (team == 0 and place_team == "alliance") or (team == 1 and place_team == "horde")


def public_place(place: dict[str, Any], *, score: int = 0, source: str = "static") -> dict[str, Any]:
    keys = [
        "key",
        "kind",
        "name",
        "team",
        "map_id",
        "map_name",
        "zone_name",
        "area_name",
        "x",
        "y",
        "z",
        "min_level",
        "route_hint",
    ]
    result = {key: place[key] for key in keys if key in place}
    if score:
        result["score"] = score
    result["source"] = source
    return result


def static_place_candidates(query: str, *, team: int | None = None, limit: int = 8) -> list[dict[str, Any]]:
    scored: list[tuple[int, dict[str, Any]]] = []
    for place in PLACE_ALIASES:
        if not place_team_matches(place, team):
            continue
        score = place_matches(place, query)
        if score:
            scored.append((score, place))

    # Generic service queries should also surface faction-specific capital services.
    needle = normalize_place_text(query)
    if needle in {"拍卖行", "ah", "auction house"}:
        for place in PLACE_ALIASES:
            if place.get("kind") == "auction_house" and place.get("key") != "auction_house" and place_team_matches(place, team):
                scored.append((65, place))

    scored.sort(key=lambda item: (-item[0], str(item[1].get("name") or "")))
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for score, place in scored:
        key = str(place.get("key") or place.get("name") or "")
        if key in seen:
            continue
        seen.add(key)
        result.append(public_place(place, score=score))
        if len(result) >= limit:
            break
    return result


def db_place_candidates(world: MysqlCli, query: str, *, locale: str = "zhCN", limit: int = 8) -> list[dict[str, Any]]:
    needle = str(query or "").strip()
    if not needle:
        return []
    bounded_limit = max(1, min(int(limit), 20))
    like = sql_like(needle)
    locale = locale if locale in {"zhCN", "zhTW", "enUS", "enGB", "deDE", "frFR", "esES", "esMX", "ruRU", "koKR"} else "zhCN"
    area_col = f"AreaName_Lang_{locale}"
    map_col = f"MapName_Lang_{locale}"
    name_col = f"Name_Lang_{locale}"

    results: list[dict[str, Any]] = []
    try:
        rows = world.query_rows(
            f"SELECT `ID`, `ContinentID`, COALESCE(NULLIF(`{area_col}`, ''), `AreaName_Lang_enUS`, '') "
            "FROM `areatable_dbc` "
            f"WHERE COALESCE(NULLIF(`{area_col}`, ''), `AreaName_Lang_enUS`, '') LIKE {like} ESCAPE '\\\\' "
            f"ORDER BY `ID` ASC LIMIT {bounded_limit}"
        )
        for row in rows:
            results.append(
                {
                    "key": f"area:{int(row[0] or 0)}",
                    "kind": "area",
                    "name": str(row[2] or ""),
                    "map_id": int(row[1] or 0),
                    "area_id": int(row[0] or 0),
                    "route_hint": "数据库区域候选；结合当前地图和任务文本确认具体入口。",
                    "source": "areatable_dbc",
                }
            )
    except RuntimeError:
        pass

    try:
        rows = world.query_rows(
            f"SELECT `ID`, COALESCE(NULLIF(`{map_col}`, ''), `MapName_Lang_enUS`, '') "
            "FROM `map_dbc` "
            f"WHERE COALESCE(NULLIF(`{map_col}`, ''), `MapName_Lang_enUS`, '') LIKE {like} ESCAPE '\\\\' "
            f"ORDER BY `ID` ASC LIMIT {bounded_limit}"
        )
        for row in rows:
            results.append(
                {
                    "key": f"map:{int(row[0] or 0)}",
                    "kind": "map",
                    "name": str(row[1] or ""),
                    "map_id": int(row[0] or 0),
                    "route_hint": "数据库地图候选；副本或大陆需要按入口路线前往。",
                    "source": "map_dbc",
                }
            )
    except RuntimeError:
        pass

    try:
        rows = world.query_rows(
            f"SELECT `ID`, `ContinentID`, `X`, `Y`, `Z`, COALESCE(NULLIF(`{name_col}`, ''), `Name_Lang_enUS`, '') "
            "FROM `taxinodes_dbc` "
            f"WHERE COALESCE(NULLIF(`{name_col}`, ''), `Name_Lang_enUS`, '') LIKE {like} ESCAPE '\\\\' "
            f"ORDER BY `ID` ASC LIMIT {bounded_limit}"
        )
        for row in rows:
            results.append(
                {
                    "key": f"taxi:{int(row[0] or 0)}",
                    "kind": "flight_point",
                    "name": str(row[5] or ""),
                    "map_id": int(row[1] or 0),
                    "x": float(row[2] or 0),
                    "y": float(row[3] or 0),
                    "z": float(row[4] or 0),
                    "route_hint": "飞行点候选；v1 只检查和建议，不自动乘坐。",
                    "source": "taxinodes_dbc",
                }
            )
    except RuntimeError:
        pass

    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in results:
        key = str(item.get("key") or item.get("name") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= bounded_limit:
            break
    return deduped


def resolve_place_candidates(query: str, *, team: int | None = None, locale: str = "zhCN", limit: int = 8) -> list[dict[str, Any]]:
    candidates = static_place_candidates(query, team=team, limit=limit)
    if len(candidates) < limit:
        try:
            for item in db_place_candidates(world_db(), query, locale=locale, limit=limit - len(candidates)):
                candidates.append(item)
        except (ValueError, RuntimeError):
            pass
    return candidates[: max(1, min(int(limit), 20))]


def event_location(event: dict[str, Any]) -> dict[str, Any]:
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    speaker = context.get("speaker") if isinstance(context.get("speaker"), dict) else {}
    environment = context.get("environment") if isinstance(context.get("environment"), dict) else {}
    location = environment.get("location") if isinstance(environment.get("location"), dict) else {}
    if not location:
        location = speaker.get("location") if isinstance(speaker.get("location"), dict) else {}
    result = dict(location)
    for key in ("map_id", "zone_id", "area_id"):
        if result.get(key) is None and speaker.get(key) is not None:
            result[key] = speaker.get(key)
    return result


def route_distance(origin: dict[str, Any], destination: dict[str, Any]) -> float | None:
    if int(origin.get("map_id") or -1) != int(destination.get("map_id") or -2):
        return None
    if not all(key in origin and key in destination for key in ("x", "y")):
        return None
    try:
        return math.sqrt((float(origin["x"]) - float(destination["x"])) ** 2 + (float(origin["y"]) - float(destination["y"])) ** 2)
    except (TypeError, ValueError):
        return None


def build_route_plan(event: dict[str, Any], destination_query: str, *, locale: str = "zhCN") -> dict[str, Any]:
    team = event_team(event)
    origin = event_location(event)
    candidates = resolve_place_candidates(destination_query, team=team, locale=locale, limit=5)
    if not candidates:
        return {
            "destination_query": destination_query,
            "origin": origin,
            "candidates": [],
            "stages": [],
            "status": "unresolved",
            "next_suggestion": "地点没解析出来，请换一个更完整的地名。",
        }

    destination = candidates[0]
    distance = route_distance(origin, destination)
    stages: list[dict[str, Any]] = []
    if destination.get("kind") in {"auction_house", "flight_master"} and destination.get("key") in {"auction_house", "flight_master"}:
        stages.append({"phase": "resolve_service", "text": "这是通用服务点，先按当前阵营选择最近主城或营地里的服务 NPC。"})
    if distance is not None:
        stages.append({"phase": "local_move", "text": "同地图移动，沿道路接近目标，途中让 bot 保持 follow。", "distance_yards": round(distance, 1)})
    else:
        stages.append({"phase": "long_route", "text": "跨地图路线，先到最近飞行点、主城传送门、船或飞艇，再到目标区域集合。"})
    if destination.get("route_hint"):
        stages.append({"phase": "hint", "text": str(destination["route_hint"])})
    stages.append({"phase": "group_check", "text": "到达后用队伍行进状态检查谁还掉队。"})

    return {
        "destination_query": destination_query,
        "origin": origin,
        "destination": destination,
        "candidates": candidates,
        "same_map": distance is not None,
        "distance_yards": round(distance, 1) if distance is not None else None,
        "stages": stages,
        "status": "planned",
        "next_suggestion": "真人玩家按路线移动；可控 bot 先用 follow/集合，不直接代跑真人角色。",
    }


def group_travel_status_from_event(event: dict[str, Any]) -> dict[str, Any]:
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    speaker = context.get("speaker") if isinstance(context.get("speaker"), dict) else {}
    origin = event_location(event)
    members = context.get("group_members") if isinstance(context.get("group_members"), list) else []
    if not members:
        members = [speaker] if speaker else []

    statuses: list[dict[str, Any]] = []
    for member in members:
        if not isinstance(member, dict):
            continue
        name = str(member.get("name") or "")
        if not name:
            continue
        raw_member_map = member.get("map_id")
        if raw_member_map is None and isinstance(member.get("location"), dict):
            raw_member_map = member.get("location", {}).get("map_id")
        raw_speaker_map = origin.get("map_id")
        member_map = int(raw_member_map) if raw_member_map is not None else 0
        speaker_map = int(raw_speaker_map) if raw_speaker_map is not None else 0
        distance = member.get("distance")
        if distance is None and int(member.get("guid") or 0) == int(speaker.get("guid") or 0):
            distance = 0.0
        same_map = raw_member_map is not None and raw_speaker_map is not None and member_map == speaker_map
        alive = bool(member.get("alive", True))
        combat = bool(member.get("combat", False))
        if not alive:
            state = "dead"
        elif not same_map:
            state = "different_map"
        elif distance is None:
            state = "unknown_distance"
        elif float(distance) <= 45.0:
            state = "arrived"
        elif float(distance) <= 120.0:
            state = "nearby"
        else:
            state = "lagging"
        statuses.append(
            {
                "guid": int(member.get("guid") or 0),
                "name": name,
                "is_bot": bool(member.get("is_bot")),
                "bot_kind": str(member.get("bot_kind") or ""),
                "role": str(member.get("role") or ""),
                "map_id": member_map,
                "location": member.get("location") if isinstance(member.get("location"), dict) else {},
                "distance_to_speaker": round(float(distance), 1) if distance is not None else None,
                "alive": alive,
                "combat": combat,
                "state": state,
            }
        )

    dropped = [item for item in statuses if item["state"] in {"dead", "different_map", "lagging", "unknown_distance"}]
    return {
        "origin": origin,
        "speaker": {"guid": event.get("speaker_guid"), "name": event.get("speaker_name")},
        "count": len(statuses),
        "arrived_count": len([item for item in statuses if item["state"] == "arrived"]),
        "dropped_count": len(dropped),
        "members": statuses,
        "dropped": dropped,
        "phase": "all_arrived" if statuses and not dropped else "needs_regroup",
        "next_suggestion": "让掉队成员或 bot 先 follow 集合；死亡成员先复活。" if dropped else "队伍已经在附近，可以继续下一步。",
    }


def visible_inventory_owner(event: dict[str, Any], bot_name: str = "") -> tuple[int, str, str]:
    requested = str(bot_name or "").strip()
    if not requested:
        return int(event.get("speaker_guid") or 0), str(event.get("speaker_name") or ""), "speaker"
    bots = bot_candidates(event)
    canonical = canonical_bot_name(bots, requested)
    if not canonical:
        return 0, requested, "not_controllable"
    for bot in bots:
        if str(bot.get("name") or "").lower() == canonical.lower():
            return int(bot.get("guid") or 0), canonical, "bot"
    return 0, requested, "not_controllable"


class BearerAndHealthMiddleware:
    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        if path in {"/health", "/healthz"}:
            body = b'{"ok":true}\n'
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        if self.token:
            headers = {
                key.decode("latin1").lower(): value.decode("latin1")
                for key, value in scope.get("headers", [])
            }
            if headers.get("authorization") != f"Bearer {self.token}":
                body = b'{"ok":false,"error":"unauthorized"}\n'
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return

        await self.app(scope, receive, send)


def build_mcp() -> FastMCP:
    mcp = FastMCP(
        "wow_playerbot",
        instructions=(
            "本地 AzerothCore PlayerBot 测试服的安全工具。"
            "只能用这些工具观察游戏事件，并把受控机器人动作写入队列。"
        ),
        host=os.getenv("PLAYERBOT_MCP_HOST", "0.0.0.0"),
        port=env_int("PLAYERBOT_MCP_PORT", 18765, 1),
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        log_level=os.getenv("PLAYERBOT_MCP_LOG_LEVEL", "INFO"),
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @mcp.tool()
    def wow_get_recent_events(limit: int = 10, after_id: int = 0) -> dict[str, Any]:
        """读取游戏事件队列里的最近 PlayerBot 桥接事件。"""
        conn = db()
        events = fetch_events(conn, after_id=int(after_id), limit=int(limit))
        return payload_result("tool_recent_events", count=len(events), events=events)

    @mcp.tool()
    def wow_get_party_state(event_id: int = 0, speaker_guid: int = 0) -> dict[str, Any]:
        """读取某个事件或玩家最近事件里的队伍、机器人和世界上下文。"""
        conn = db()
        event = get_event_or_latest(conn, event_id=int(event_id), speaker_guid=int(speaker_guid))
        if not event:
            return {"ok": False, "error": "event_not_found"}
        context = event.get("context") if isinstance(event.get("context"), dict) else {}
        return payload_result(
            "tool_party_state",
            event_id=event["id"],
            channel=event["channel"],
            speaker={"guid": event["speaker_guid"], "name": event["speaker_name"]},
            message=event["message"],
            context=context,
        )

    @mcp.tool()
    def wow_get_location(event_id: int = 0, speaker_guid: int = 0) -> dict[str, Any]:
        """读取服务端已知的位置名称，包括 map_name、zone_name 和 area_name。"""
        conn = db()
        event = get_event_or_latest(conn, event_id=int(event_id), speaker_guid=int(speaker_guid))
        if not event:
            return {"ok": False, "error": "event_not_found"}
        context = event.get("context") if isinstance(event.get("context"), dict) else {}
        environment = context.get("environment") if isinstance(context.get("environment"), dict) else {}
        speaker = context.get("speaker") if isinstance(context.get("speaker"), dict) else {}
        location = environment.get("location") or speaker.get("location") or {}
        return payload_result("tool_location", event_id=event["id"], location=location)

    @mcp.tool()
    def wow_resolve_place(query: str, event_id: int = 0, locale: str = "zhCN", limit: int = 8) -> dict[str, Any]:
        """把“血色、暴风城、拍卖行、飞行点”等自然语言地点解析为候选。"""
        team = None
        source_event_id = int(event_id or 0)
        if source_event_id:
            event = fetch_event(db(), source_event_id)
            if not event:
                return {"ok": False, "error": "event_not_found"}
            team = event_team(event)
        candidates = resolve_place_candidates(query, team=team, locale=locale, limit=int(limit))
        return payload_result(
            "tool_resolve_place",
            source_event_id=source_event_id,
            query=query,
            count=len(candidates),
            candidates=candidates,
            feedback={
                "phase": "resolved" if candidates else "unresolved",
                "next_suggestion": "用第一个候选规划路线；如果候选不对，让玩家补更具体地名。",
            },
        )

    @mcp.tool()
    def wow_plan_route(event_id: int, destination: str, locale: str = "zhCN") -> dict[str, Any]:
        """根据发言玩家当前位置，为目的地生成分段路线建议；不直接代跑真人角色。"""
        conn = db()
        event = fetch_event(conn, int(event_id))
        if not event:
            return {"ok": False, "error": "event_not_found"}
        plan = build_route_plan(event, destination, locale=locale)
        if plan.get("destination"):
            upsert_session_goal(
                conn,
                event,
                goal_type="travel",
                goal_text=str(plan["destination"].get("name") or destination),
                status="planning",
                payload={"destination": plan.get("destination"), "query": destination},
            )
        return payload_result(
            "tool_plan_route",
            source_event_id=int(event_id),
            plan=plan,
            feedback={
                "phase": plan.get("status", "planned"),
                "next_suggestion": plan.get("next_suggestion", ""),
            },
        )

    @mcp.tool()
    def wow_get_group_travel_status(event_id: int) -> dict[str, Any]:
        """查看队伍成员位置、距离和掉队情况。"""
        conn = db()
        event = fetch_event(conn, int(event_id))
        if not event:
            return {"ok": False, "error": "event_not_found"}
        status = group_travel_status_from_event(event)
        return payload_result(
            "tool_group_travel_status",
            source_event_id=int(event_id),
            status=status,
            feedback={
                "phase": status["phase"],
                "next_suggestion": status["next_suggestion"],
            },
        )

    @mcp.tool()
    def wow_start_bot_travel(event_id: int, destination: str, bot_name: str = "group") -> dict[str, Any]:
        """让可控 bot 开始向发言玩家集合。v1 使用 follow/集合，不自动代跑真人角色或自动坐飞机。"""
        conn = db()
        event = fetch_event(conn, int(event_id))
        if not event:
            return {"ok": False, "error": "event_not_found"}
        plan = build_route_plan(event, destination)
        targets = select_bot_names(event, bot_name, default_selector="group")
        if not targets:
            return {"ok": False, "error": "no_controllable_bot", "plan": plan}

        bots = {bot_name_from_candidate(bot).lower(): bot for bot in bot_candidates(event)}
        action_ids: list[int] = []
        blocked: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        for target in targets:
            bot = bots.get(target.lower(), {})
            if bot and not bool(bot.get("alive", True)):
                blocked.append({"bot": target, "reason": "bot_dead"})
                continue
            if bot and bool(bot.get("combat", False)):
                blocked.append({"bot": target, "reason": "bot_in_combat"})
                continue
            if bot and bot.get("distance_to_speaker") is not None and float(bot.get("distance_to_speaker") or 0) > 1000:
                warnings.append({"bot": target, "reason": "very_far", "distance": bot.get("distance_to_speaker")})
            result = enqueue_action(
                conn,
                event=event,
                action_type="command",
                bot_name=target,
                command="follow",
                payload={"command": "follow", "destination": destination, "route_plan": plan},
            )
            if result.get("action_id"):
                action_ids.append(int(result["action_id"]))

        phase = "started" if action_ids else "blocked"
        upsert_session_goal(
            conn,
            event,
            goal_type="travel",
            goal_text=str(plan.get("destination", {}).get("name") or destination),
            status=phase,
            payload={"destination": destination, "targets": targets, "action_ids": action_ids, "blocked": blocked},
        )
        return payload_result(
            "tool_start_bot_travel",
            source_event_id=int(event_id),
            destination=destination,
            targets=targets,
            action_ids=action_ids,
            blocked=blocked,
            warnings=warnings,
            plan=plan,
            feedback={
                "phase": phase,
                "next_suggestion": "查 wow_get_action_results 确认可控 bot 是否接受 follow；真人玩家仍需自己移动。",
            },
        )

    @mcp.tool()
    def wow_get_action_results(event_id: int, limit: int = 8) -> dict[str, Any]:
        """读取某个来源游戏事件最近产生的动作执行结果。"""
        conn = db()
        results = fetch_action_results(conn, int(event_id), limit=int(limit))
        return payload_result("tool_action_results", event_id=int(event_id), count=len(results), results=results)

    @mcp.tool()
    def wow_get_last_command_diagnostic(event_id: int, limit: int = 12) -> dict[str, Any]:
        """汇总最近事件和动作结果，用于回答“刚才成了吗/怎么没反应”。"""
        conn = db()
        event = fetch_event(conn, int(event_id))
        if not event:
            return {"ok": False, "error": "event_not_found"}
        actions = fetch_recent_context_actions(conn, event, limit=int(limit))
        goals = fetch_session_goals(conn, event)
        summary = summarize_action_results(actions)
        return payload_result(
            "tool_last_command_diagnostic",
            source_event_id=int(event_id),
            current_event={
                "id": event.get("id"),
                "channel": event.get("channel"),
                "speaker": event.get("speaker_name"),
                "message": event.get("message"),
            },
            action_count=len(actions),
            summary=summary,
            actions=actions,
            goals=goals,
            feedback={
                "phase": summary["phase"],
                "next_suggestion": summary["next_suggestion"],
            },
        )

    @mcp.tool()
    def wow_get_session_goals(event_id: int) -> dict[str, Any]:
        """读取当前玩家或队伍的轻量目标记忆。"""
        conn = db()
        event = fetch_event(conn, int(event_id))
        if not event:
            return {"ok": False, "error": "event_not_found"}
        goals = fetch_session_goals(conn, event)
        return payload_result("tool_session_goals", source_event_id=int(event_id), count=len(goals), goals=goals)

    @mcp.tool()
    def wow_set_session_goal(
        event_id: int,
        goal_type: str,
        goal_text: str,
        status: str = "active",
    ) -> dict[str, Any]:
        """写入当前玩家或队伍的轻量目标记忆。"""
        conn = db()
        event = fetch_event(conn, int(event_id))
        if not event:
            return {"ok": False, "error": "event_not_found"}
        normalized_type = str(goal_type or "").strip() or "general"
        normalized_status = str(status or "").strip() or "active"
        upsert_session_goal(
            conn,
            event,
            goal_type=normalized_type,
            goal_text=str(goal_text or "").strip(),
            status=normalized_status,
            payload={"manual": True},
        )
        return payload_result(
            "tool_set_session_goal",
            source_event_id=int(event_id),
            goal_type=normalized_type,
            goal_text=str(goal_text or "").strip(),
            status=normalized_status,
            feedback={"phase": "updated", "next_suggestion": "后续查询会按这个目标继续上下文。"},
        )

    @mcp.tool()
    def wow_get_player_quests(
        event_id: int = 0,
        speaker_guid: int = 0,
        quest_id: int = 0,
        include_details: bool = True,
        limit: int = 25,
        locale: str = "zhCN",
    ) -> dict[str, Any]:
        """读取请求玩家当前任务日志和进度，默认返回中文任务文本。"""
        conn = db()
        event = None
        if int(event_id or 0) > 0:
            event = fetch_event(conn, int(event_id))
            if not event:
                return {"ok": False, "error": "event_not_found"}
            player_guid = int(event.get("speaker_guid") or 0)
        elif int(speaker_guid or 0) > 0:
            player_guid = int(speaker_guid)
            event = latest_event(conn, player_guid)
        else:
            return {
                "ok": False,
                "error": "missing_player_context",
                "note": "读取玩家任务必须传 event_id 或 speaker_guid，避免多人环境下串读任务进度。",
            }

        statuses = fetch_player_quest_statuses(characters_db(), player_guid, limit=int(limit))
        if int(quest_id or 0) > 0:
            statuses = [status for status in statuses if int(status.get("quest_id") or 0) == int(quest_id)]
        quests: list[dict[str, Any]] = []
        world = world_db()
        for status in statuses:
            quest = fetch_quest_details(world, int(status["quest_id"]), locale=locale)
            if not quest:
                continue
            quest = apply_quest_progress(quest, status)
            quests.append(quest if include_details else compact_quest(quest))

        return payload_result(
            "tool_player_quests",
            source_event_id=int(event.get("id") or 0) if event else 0,
            player_guid=player_guid,
            count=len(quests),
            locale=locale,
            quests=quests,
        )

    @mcp.tool()
    def wow_search_quests(
        query: str,
        event_id: int = 0,
        speaker_guid: int = 0,
        limit: int = 10,
        locale: str = "zhCN",
    ) -> dict[str, Any]:
        """按任务 ID、中文标题、目标文本或剧情文本搜索任务库。"""
        conn = db()
        event = get_event_or_latest(conn, event_id=int(event_id), speaker_guid=int(speaker_guid)) if event_id or speaker_guid else None
        results = search_quests(world_db(), query, locale=locale, limit=int(limit), player_level=event_speaker_level(event))
        return payload_result(
            "tool_search_quests",
            source_event_id=int(event.get("id") or 0) if event else 0,
            query=query,
            count=len(results),
            locale=locale,
            results=results,
        )

    @mcp.tool()
    def wow_get_quest_details(quest_id: int, locale: str = "zhCN", event_id: int = 0) -> dict[str, Any]:
        """读取单个任务的中文剧情、目标、交接 NPC/物体和奖励文本。"""
        quest = fetch_quest_details(world_db(), int(quest_id), locale=locale)
        if not quest:
            return {"ok": False, "error": "quest_not_found"}
        return payload_result(
            "tool_quest_details",
            source_event_id=int(event_id or 0),
            quest_id=int(quest_id),
            locale=locale,
            quest=quest,
        )

    @mcp.tool()
    def wow_get_quest_guide(
        event_id: int,
        query: str = "",
        style: str = "guide",
        locale: str = "zhCN",
        limit: int = 5,
    ) -> dict[str, Any]:
        """任务陪伴包装：按发言玩家读取任务进度，并返回剧情、目标、交接和下一步建议。"""
        normalized_style = str(style or "guide").strip().lower()
        if normalized_style not in {"guide", "story", "brief"}:
            return {"ok": False, "error": "invalid_style", "allowed": ["guide", "story", "brief"]}
        conn = db()
        event = fetch_event(conn, int(event_id))
        if not event:
            return {"ok": False, "error": "event_not_found"}
        player_guid = int(event.get("speaker_guid") or 0)
        statuses = fetch_player_quest_statuses(characters_db(), player_guid, limit=50)
        status_by_id = {int(status.get("quest_id") or 0): status for status in statuses}
        world = world_db()
        quests: list[dict[str, Any]] = []
        for status in statuses:
            quest = fetch_quest_details(world, int(status["quest_id"]), locale=locale)
            if quest:
                quests.append(apply_quest_progress(quest, status))

        needle = str(query or "").strip()
        selected: list[dict[str, Any]] = []
        if needle:
            for quest in quests:
                if needle.isdigit() and int(quest.get("id") or 0) == int(needle):
                    selected.append(quest)
                elif needle.lower() in str(quest.get("title") or "").lower() or needle in str(quest.get("objectives_text") or ""):
                    selected.append(quest)
            if not selected:
                candidates = search_quests(world, needle, locale=locale, limit=int(limit), player_level=event_speaker_level(event))
                for candidate in candidates:
                    quest = fetch_quest_details(world, int(candidate["id"]), locale=locale)
                    if quest:
                        status = status_by_id.get(int(candidate["id"]), {"quest_id": int(candidate["id"]), "status": 0, "status_label": "未接受"})
                        selected.append(apply_quest_progress(quest, status))
        else:
            selected = quests

        bounded_limit = max(1, min(int(limit), 10))
        guides = [build_quest_guide_entry(quest, normalized_style) for quest in selected[:bounded_limit]]
        recommendations = recommend_quests_for_player(quests, player_level=event_speaker_level(event), limit=bounded_limit)
        if guides:
            upsert_session_goal(
                conn,
                event,
                goal_type="quest",
                goal_text=str(guides[0].get("title") or ""),
                status="active",
                payload={"quest_id": guides[0].get("quest_id"), "query": needle, "style": normalized_style},
            )
        note = ""
        if not guides and needle:
            note = "没有在当前任务日志或任务库里找到匹配任务。"
        elif not guides:
            note = "当前玩家任务日志为空。"
        return payload_result(
            "tool_quest_guide",
            source_event_id=int(event_id),
            player_guid=player_guid,
            player_name=event.get("speaker_name"),
            query=needle,
            style=normalized_style,
            locale=locale,
            count=len(guides),
            guides=guides,
            recommendations=recommendations,
            note=note,
            feedback={
                "phase": "ready" if guides or recommendations else "empty",
                "next_suggestion": "按 next_step 回复玩家；多人环境下只使用这个 event_id 的发言玩家任务进度。",
            },
        )

    @mcp.tool()
    def wow_get_recent_combat_summaries(
        event_id: int = 0,
        speaker_guid: int = 0,
        limit: int = 5,
        include_facts: bool = False,
        include_recovery: bool = False,
    ) -> dict[str, Any]:
        """读取最近已结束战斗摘要。默认跳过脱战后的纯治疗恢复片段。"""
        conn = db()
        event = (
            get_event_or_latest(conn, event_id=int(event_id), speaker_guid=int(speaker_guid))
            if event_id or speaker_guid
            else None
        )
        if (event_id or speaker_guid) and not event:
            return {"ok": False, "error": "event_not_found"}
        leader_guid = combat_leader_guid(event)
        bounded_limit = max(1, min(int(limit), 20))
        raw_limit = bounded_limit if include_recovery else min(20, max(bounded_limit * 4, bounded_limit))
        summaries = fetch_recent_combat_summaries(conn, group_leader_guid=leader_guid, limit=raw_limit)
        if not include_recovery:
            summaries = [item for item in summaries if combat_summary_has_fight(item)]
        summaries = summaries[:bounded_limit]
        payload_summaries = summaries if include_facts else [compact_combat_summary(item) for item in summaries]
        note = ""
        if not payload_summaries:
            note = "没有已结束的战斗摘要；战斗结束并空闲几秒后才会写入。"
        return payload_result(
            "tool_recent_combat_summaries",
            source_event_id=int(event.get("id") or 0) if event else 0,
            group_leader_guid=leader_guid,
            count=len(payload_summaries),
            include_facts=bool(include_facts),
            include_recovery=bool(include_recovery),
            note=note,
            summaries=payload_summaries,
        )

    @mcp.tool()
    def wow_get_combat_summary(summary_id: int = 0, event_id: int = 0, speaker_guid: int = 0) -> dict[str, Any]:
        """读取某一场完整战斗 facts。未传 summary_id 时读取该玩家队伍最近一场战斗。"""
        conn = db()
        event = None
        if int(summary_id or 0) > 0:
            summary = fetch_combat_summary(conn, int(summary_id))
        else:
            event = get_event_or_latest(conn, event_id=int(event_id), speaker_guid=int(speaker_guid))
            if not event:
                return {"ok": False, "error": "event_not_found"}
            recent = fetch_recent_combat_summaries(conn, group_leader_guid=combat_leader_guid(event), limit=20)
            fights = [item for item in recent if combat_summary_has_fight(item)]
            summary = fights[0] if fights else None
        if not summary:
            return {
                "ok": False,
                "error": "combat_summary_not_found",
                "note": "没有找到已结束的战斗摘要；战斗结束并空闲几秒后才会写入。",
            }
        return payload_result(
            "tool_combat_summary",
            source_event_id=int(event.get("id") or 0) if event else 0,
            summary_id=int(summary.get("id") or 0),
            compact=compact_combat_summary(summary),
            summary=summary,
        )

    @mcp.tool()
    def wow_get_playerbot_command_catalog(query: str = "", limit: int = 20) -> dict[str, Any]:
        """读取已知 `.playerbots bot` 命令样例和优先使用的 MCP 包装工具。"""
        needle = query.strip().lower()
        bounded_limit = max(1, min(int(limit), 100))
        commands = PLAYERBOT_COMMAND_CATALOG
        if needle:
            commands = [
                item
                for item in commands
                if needle in item["category"].lower()
                or needle in item["command_line"].lower()
                or needle in item["description"].lower()
                or needle in item["preferred_tool"].lower()
            ]
        return payload_result(
            "tool_playerbot_command_catalog",
            query=query,
            count=min(len(commands), bounded_limit),
            commands=commands[:bounded_limit],
            generic_tool="wow_run_playerbot_command",
            command_line_rule="优先只传 `.playerbots bot` 后面的参数，不要包含 `.playerbots bot` 前缀。",
        )

    @mcp.tool()
    def wow_get_bot_profile(event_id: int, bot_name: str = "") -> dict[str, Any]:
        """读取角色画像：职业、职责、专精、当前 AI 策略、位置、可切换职责和天赋页数量。回答天赋/职责问题前优先调用。"""
        conn = db()
        event = fetch_event(conn, int(event_id))
        if not event:
            return {"ok": False, "error": "event_not_found"}
        bot = find_context_bot(event, bot_name)
        if bot:
            profile = bot_profile_from_context(bot)
            return payload_result(
                "tool_bot_profile",
                source_event_id=int(event_id),
                requested_bot=bot_name,
                profile=profile,
                feedback={
                    "phase": "ready",
                    "next_suggestion": "回答玩家时只使用 profile 中明确给出的事实；需要第二天赋但 talent_groups.has_second=false 时，说明没有第二套天赋和原因。",
                },
            )

        row = fetch_character_identity(bot_name)
        if not row:
            return {"ok": False, "error": "bot_not_found_in_context_or_db", "requested_bot": bot_name}
        profile = bot_profile_from_character(row)
        return payload_result(
            "tool_bot_profile",
            source_event_id=int(event_id),
            requested_bot=bot_name,
            profile=profile,
            feedback={
                "phase": "partial",
                "next_suggestion": "只找到了角色数据库身份；可回答天赋页数量，但当前策略需要该角色在线并重新触发事件。",
            },
        )

    @mcp.tool()
    def wow_get_supported_bot_strategies(
        event_id: int,
        bot_name: str = "",
        class_hint: str = "",
    ) -> dict[str, Any]:
        """读取某个 bot 或职业可安全切换的 AI 策略和高层职责名。"""
        conn = db()
        event = fetch_event(conn, int(event_id))
        if not event:
            return {"ok": False, "error": "event_not_found"}

        bot = find_context_bot(event, bot_name)
        class_id = int(bot.get("class") or 0) if bot else 0
        if not class_id and class_hint.strip():
            class_id = CLASS_IDS[normalize_class_hint(class_hint)]
        if not class_id and bot_name.strip():
            row = fetch_character_identity(bot_name)
            class_id = int(row.get("class") or 0) if row else 0
        if not class_id:
            return {"ok": False, "error": "class_unknown", "requested_bot": bot_name, "class_hint": class_hint}

        return payload_result(
            "tool_supported_bot_strategies",
            source_event_id=int(event_id),
            bot=bot_name_from_candidate(bot) if bot else bot_name,
            class_info=class_display(class_id),
            strategies=supported_strategy_names(class_id),
            roles=supported_role_names(class_id),
            role_plans={
                name: {
                    "label": plan.get("label", ""),
                    "role": plan.get("role", ""),
                    "changes": plan.get("changes", []),
                }
                for name, plan in ROLE_PLANS.get(class_id, {}).items()
                if not plan.get("alias")
            },
            feedback={
                "phase": "ready",
                "next_suggestion": "需要改变职责时优先用 wow_set_bot_role；只想开关单个策略时用 wow_set_bot_strategy。",
            },
        )

    @mcp.tool()
    def wow_reply(event_id: int, text: str, channel: str = "party", bot_name: str = "") -> dict[str, Any]:
        """让一个可用机器人在 party、say 或 whisper 频道回复。"""
        if channel not in REPLY_CHANNELS:
            return {"ok": False, "error": "invalid_channel", "allowed": sorted(REPLY_CHANNELS)}
        conn = db()
        event = fetch_event(conn, int(event_id))
        if not event:
            return {"ok": False, "error": "event_not_found"}
        requested_bot = bot_name.strip()
        speaker, speaker_reason = resolve_reply_bot_name(event, channel, requested_bot)
        if not speaker:
            return {"ok": False, "error": "no_controllable_bot"}
        result = enqueue_action(
            conn,
            event=event,
            action_type="reply",
            bot_name=speaker,
            channel=channel,
            text=text,
            payload={
                "requested_bot": requested_bot,
                "resolved_bot": speaker,
                "resolve_reason": speaker_reason,
            },
        )
        return payload_result(
            "tool_reply",
            source_event_id=int(event_id),
            bot=speaker,
            requested_bot=requested_bot,
            resolve_reason=speaker_reason,
            channel=channel,
            **result,
        )

    @mcp.tool()
    def wow_summon_bot(
        event_id: int,
        role: str,
        class_hint: str = "",
        gender: str = "",
        race_hint: str = "",
    ) -> dict[str, Any]:
        """按职责、职业、性别和可选种族提示召唤一个可控 AddClass 机器人。

        Playerbots 原生命令只支持 addclass <class> [gender]，不支持种族。
        如果传入 race_hint，MCP 会先从 AddClass 池里选择匹配角色，再执行 add <Botname>。
        """
        role = role.strip().lower()
        if role not in {"tank", "healer", "dps"}:
            return {"ok": False, "error": "invalid_role", "allowed": ["tank", "healer", "dps"]}
        conn = db()
        event = fetch_event(conn, int(event_id))
        if not event:
            return {"ok": False, "error": "event_not_found"}

        class_name = normalize_class_hint(class_hint, role)
        normalized_gender = normalize_gender(gender)
        if gender.strip() and not normalized_gender:
            return {"ok": False, "error": "invalid_gender", "allowed": ["male", "female", "0", "1", "男", "女"]}

        race_id = normalize_race_hint(race_hint)
        if race_hint.strip() and race_id is None:
            return {
                "ok": False,
                "error": "invalid_race_hint",
                "allowed": sorted(RACE_IDS.keys()) + ["人类", "矮人", "暗夜精灵", "侏儒", "德莱尼", "血精灵"],
            }

        payload = {"role": role, "class_hint": class_name}
        if normalized_gender:
            payload["gender"] = normalized_gender

        if race_id is not None:
            requester_team = event_team(event)
            target_team = race_team(race_id)
            if requester_team is not None and target_team is not None and requester_team != target_team:
                return {
                    "ok": False,
                    "error": "race_wrong_faction",
                    "race": race_id,
                    "race_name": RACE_NAMES.get(race_id),
                    "race_zh": RACE_ZH.get(race_id),
                }

            payload["race_hint"] = RACE_NAMES.get(race_id, str(race_id))
            candidates = find_addclass_candidates(
                conn,
                event,
                class_id=CLASS_IDS[class_name],
                race_id=race_id,
                gender=gender_id(normalized_gender),
                limit=10,
            )
            if not candidates:
                alternatives = find_addclass_candidates(
                    conn,
                    event,
                    class_id=CLASS_IDS[class_name],
                    gender=gender_id(normalized_gender),
                    limit=10,
                )
                return {
                    "ok": False,
                    "error": "no_matching_addclass_candidate",
                    "filters": {
                        "class_hint": class_name,
                        "race_hint": RACE_NAMES.get(race_id, str(race_id)),
                        "gender": normalized_gender,
                    },
                    "alternatives": alternatives,
                }

            selected = candidates[0]
            command = f"add {selected['name']}"
            payload["selected_bot"] = selected["name"]
            payload["command_line"] = command
            result = enqueue_action(
                conn,
                event=event,
                action_type="playerbot_command",
                command=command,
                payload=payload,
            )
            return payload_result(
                "tool_summon_bot",
                source_event_id=int(event_id),
                payload=payload,
                selected_candidate=selected,
                **result,
            )

        result = enqueue_action(conn, event=event, action_type="summon_bot", payload=payload)
        return payload_result("tool_summon_bot", source_event_id=int(event_id), payload=payload, **result)

    @mcp.tool()
    def wow_init_bot(event_id: int, bot_name: str, mode: str = "auto") -> dict[str, Any]:
        """初始化一个可控 AddClass 机器人。"""
        conn = db()
        event = fetch_event(conn, int(event_id))
        if not event:
            return {"ok": False, "error": "event_not_found"}
        result = enqueue_action(
            conn,
            event=event,
            action_type="init_bot",
            payload={"bot": bot_name.strip(), "mode": mode.strip() or "auto"},
        )
        return payload_result("tool_init_bot", source_event_id=int(event_id), bot=bot_name, **result)

    @mcp.tool()
    def wow_dismiss_bot(event_id: int, bot_name: str) -> dict[str, Any]:
        """通过 `.playerbots bot remove` 让一个可控机器人下线。"""
        if bot_name.strip() == "*":
            return {"ok": False, "error": "dismiss_all_not_exposed"}
        return enqueue_named_bot_action(int(event_id), "dismiss_bot", bot_name)

    @mcp.tool()
    def wow_refresh_bot(event_id: int, bot_name: str) -> dict[str, Any]:
        """通过 `.playerbots bot refresh` 刷新一个可控 AddClass 机器人。"""
        return enqueue_named_bot_action(int(event_id), "refresh_bot", bot_name)

    @mcp.tool()
    def wow_level_bot(event_id: int, bot_name: str) -> dict[str, Any]:
        """通过 `.playerbots bot levelup` 按等级重新整理一个可控 AddClass 机器人。"""
        return enqueue_named_bot_action(int(event_id), "level_bot", bot_name)

    @mcp.tool()
    def wow_init_instance_quests(event_id: int, bot_name: str) -> dict[str, Any]:
        """通过 `.playerbots bot quests` 为一个可控 AddClass 机器人初始化副本任务。"""
        return enqueue_named_bot_action(int(event_id), "init_instance_quests", bot_name)

    @mcp.tool()
    def wow_list_bots(event_id: int) -> dict[str, Any]:
        """让桥接层列出请求玩家当前可控的机器人。"""
        conn = db()
        event = fetch_event(conn, int(event_id))
        if not event:
            return {"ok": False, "error": "event_not_found"}
        result = enqueue_action(conn, event=event, action_type="list_bots", payload={})
        return payload_result("tool_list_bots", source_event_id=int(event_id), **result)

    @mcp.tool()
    def wow_lookup_bot_pool(
        event_id: int,
        class_hint: str = "",
        race_hint: str = "",
        gender: str = "",
        limit: int = 20,
    ) -> dict[str, Any]:
        """概览 AddClass 池，并可按职业、种族、性别列出可召唤候选角色。"""
        conn = db()
        event = fetch_event(conn, int(event_id))
        if not event:
            return {"ok": False, "error": "event_not_found"}

        normalized_gender = normalize_gender(gender)
        if gender.strip() and not normalized_gender:
            return {"ok": False, "error": "invalid_gender", "allowed": ["male", "female", "0", "1", "男", "女"]}

        race_id = normalize_race_hint(race_hint)
        if race_hint.strip() and race_id is None:
            return {"ok": False, "error": "invalid_race_hint"}

        class_id = CLASS_IDS[normalize_class_hint(class_hint, "")] if class_hint.strip() else None
        candidates = find_addclass_candidates(
            conn,
            event,
            class_id=class_id,
            race_id=race_id,
            gender=gender_id(normalized_gender),
            limit=limit,
        )
        result = enqueue_action(conn, event=event, action_type="lookup_bot_pool", payload={})
        return payload_result(
            "tool_lookup_bot_pool",
            source_event_id=int(event_id),
            filters={
                "class_hint": CLASS_NAMES.get(class_id, "") if class_id else "",
                "race_hint": RACE_NAMES.get(race_id, "") if race_id else "",
                "gender": normalized_gender,
            },
            candidate_count=len(candidates),
            candidates=candidates,
            **result,
        )

    @mcp.tool()
    def wow_run_playerbot_command(event_id: int, command_line: str, dry_run: bool = False) -> dict[str, Any]:
        """以请求玩家身份执行服务端支持的 `.playerbots bot` 命令。

        command_line 优先传 `.playerbots bot` 后面的参数，例如：
        "list"、"remove Botname"、"init=auto Botname"、"addclass priest female"。
        传完整 ".playerbots bot ..." 或 ".bot ..." 也会被归一化。
        最终仍由游戏服务器按请求玩家的 playerbot 权限执行和拒绝。
        """
        try:
            command = clean_playerbot_command_line(command_line)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        if dry_run:
            return payload_result(
                "tool_run_playerbot_command_dry_run",
                source_event_id=int(event_id),
                command_line=command,
                dry_run=True,
                requires_admin=command_requires_admin_audit(command),
            )

        conn = db()
        event = fetch_event(conn, int(event_id))
        if not event:
            return {"ok": False, "error": "event_not_found"}
        if command_requires_admin_audit(command) and not event_speaker_is_admin(event):
            return admin_denied_payload(event, "playerbot_command", command)
        result = enqueue_action(
            conn,
            event=event,
            action_type="playerbot_command",
            command=command,
            payload={
                "command_line": command,
                "audit": "admin_only" if command_requires_admin_audit(command) else "normal_playerbot_command",
                "speaker": event_speaker_name(event),
            },
        )
        return payload_result(
            "tool_run_playerbot_command",
            source_event_id=int(event_id),
            command_line=command,
            requires_admin=command_requires_admin_audit(command),
            speaker=event_speaker_name(event),
            **result,
        )

    @mcp.tool()
    def wow_invite_player(event_id: int, target_player: str) -> dict[str, Any]:
        """邀请一个真实在线玩家加入请求玩家的小队。"""
        conn = db()
        event = fetch_event(conn, int(event_id))
        if not event:
            return {"ok": False, "error": "event_not_found"}
        target = target_player.strip()
        if not target:
            return {"ok": False, "error": "empty_target_player"}
        result = enqueue_action(
            conn,
            event=event,
            action_type="invite_player",
            payload={"target_player": target},
        )
        return payload_result("tool_invite_player", source_event_id=int(event_id), target_player=target, **result)

    @mcp.tool()
    def wow_bot_follow(event_id: int, bot_name: str = "group") -> dict[str, Any]:
        """让一个或一组可控机器人跟随主人；bot_name 可写具体名字、group、healers、tanks。"""
        return enqueue_bot_command(int(event_id), bot_name=bot_name, command="follow", default_selector="group")

    @mcp.tool()
    def wow_bot_stay(event_id: int, bot_name: str = "group") -> dict[str, Any]:
        """让一个或一组可控机器人原地停留；bot_name 可写具体名字或 group。"""
        return enqueue_bot_command(int(event_id), bot_name=bot_name, command="stay", default_selector="group")

    @mcp.tool()
    def wow_bot_retreat(event_id: int, bot_name: str = "group", mode: str = "flee") -> dict[str, Any]:
        """让可控机器人撤退；mode=flee 表示回撤，mode=runaway 表示跑远/散开。"""
        normalized = mode.strip().lower()
        if normalized not in {"flee", "runaway"}:
            return {"ok": False, "error": "invalid_retreat_mode", "allowed": ["flee", "runaway"]}
        return enqueue_bot_command(int(event_id), bot_name=bot_name, command=normalized, default_selector="group")

    @mcp.tool()
    def wow_bot_attack_target(event_id: int, bot_name: str = "group") -> dict[str, Any]:
        """让可控机器人攻击请求玩家当前目标。"""
        return enqueue_bot_command(int(event_id), bot_name=bot_name, command="attack", default_selector="group")

    @mcp.tool()
    def wow_bot_pull(event_id: int, bot_name: str = "", pull_back: bool = False) -> dict[str, Any]:
        """让指定机器人拉请求玩家当前目标；bot_name 为空时优先选择坦克，没有坦克则选第一个可控机器人。"""
        command = "pull back" if bool(pull_back) else "pull"
        return enqueue_bot_command(
            int(event_id),
            bot_name=bot_name,
            command=command,
            default_selector="tanks",
            fallback_first=True,
        )

    @mcp.tool()
    def wow_bot_ready(event_id: int, bot_name: str = "group") -> dict[str, Any]:
        """让可控机器人执行 ready 准备确认。"""
        return enqueue_bot_command(int(event_id), bot_name=bot_name, command="ready", default_selector="group")

    @mcp.tool()
    def wow_bot_burst(event_id: int, bot_name: str = "group") -> dict[str, Any]:
        """让可控机器人执行 max dps 爆发输出指令。"""
        return enqueue_bot_command(int(event_id), bot_name=bot_name, command="max dps", default_selector="group")

    @mcp.tool()
    def wow_focus_heal(
        event_id: int,
        bot_name: str = "healers",
        target_player: str = "",
        mode: str = "add",
    ) -> dict[str, Any]:
        """设置或取消治疗机器人重点照看某个玩家；mode 可为 add、remove、clear。"""
        conn = db()
        event = fetch_event(conn, int(event_id))
        if not event:
            return {"ok": False, "error": "event_not_found"}

        normalized_mode = mode.strip().lower()
        if normalized_mode in {"clear", "none", "unset"}:
            command = "focus heal clear"
        else:
            target = target_player.strip() or str(event.get("speaker_name") or "").strip()
            if not target:
                return {"ok": False, "error": "empty_target_player"}
            if any(ord(ch) < 0x20 or ch in {"'", '"', "`", ";", "\\"} for ch in target) or len(target) > 64:
                return {"ok": False, "error": "target_player_is_invalid"}
            if normalized_mode in {"add", "+", "on"}:
                command = f"focus heal +{target}"
            elif normalized_mode in {"remove", "-", "off"}:
                command = f"focus heal -{target}"
            else:
                return {"ok": False, "error": "invalid_focus_heal_mode", "allowed": ["add", "remove", "clear"]}

        return enqueue_bot_command(
            int(event_id),
            bot_name=bot_name,
            command=command,
            default_selector="healers",
            role_filter="healer",
        )

    @mcp.tool()
    def wow_bot_provide_consumables(
        event_id: int,
        bot_name: str = "",
        target_player: str = "",
        water_stacks: int = 1,
        food_stacks: int = 1,
    ) -> dict[str, Any]:
        """让可控法师给请求玩家或指定队友提供法师水和面包；默认各一组。"""
        conn = db()
        event = fetch_event(conn, int(event_id))
        if not event:
            return {"ok": False, "error": "event_not_found"}

        water = normalize_consumable_stacks(water_stacks, 1)
        food = normalize_consumable_stacks(food_stacks, 1)
        if water == 0 and food == 0:
            return {"ok": False, "error": "empty_consumable_request"}

        target = str(target_player or "").strip() or str(event.get("speaker_name") or "").strip()
        if not is_safe_player_name(target):
            return {"ok": False, "error": "target_player_is_invalid"}

        mage_name = select_mage_bot_name(event, bot_name)
        if not mage_name:
            return {"ok": False, "error": "no_controllable_mage"}

        payload = {
            "target_player": target,
            "water_stacks": str(water),
            "food_stacks": str(food),
        }
        result = enqueue_action(
            conn,
            event=event,
            action_type="provide_consumables",
            bot_name=mage_name,
            payload=payload,
        )
        return payload_result(
            "tool_provide_consumables",
            source_event_id=int(event_id),
            bot=mage_name,
            target_player=target,
            water_stacks=water,
            food_stacks=food,
            **result,
        )

    @mcp.tool()
    def wow_get_inventory_for_trade(
        event_id: int,
        bot_name: str = "",
        include_equipped: bool = False,
        limit: int = 80,
        locale: str = "zhCN",
    ) -> dict[str, Any]:
        """只读读取发言玩家或可控 bot 背包里的可交易候选物品；不会卖店、交易或挂拍卖。"""
        conn = db()
        event = fetch_event(conn, int(event_id))
        if not event:
            return {"ok": False, "error": "event_not_found"}
        owner_guid, owner_name, owner_kind = visible_inventory_owner(event, bot_name)
        if not owner_guid:
            return {"ok": False, "error": "inventory_owner_not_controllable", "owner": owner_name, "kind": owner_kind}
        items = fetch_inventory_items(
            characters_db(),
            world_db(),
            owner_guid,
            locale=locale,
            include_equipped=bool(include_equipped),
            limit=int(limit),
        )
        return payload_result(
            "tool_inventory_for_trade",
            source_event_id=int(event_id),
            owner={"guid": owner_guid, "name": owner_name, "kind": owner_kind},
            include_equipped=bool(include_equipped),
            count=len(items),
            items=items,
            read_only=True,
            feedback={
                "phase": "ready",
                "next_suggestion": "这些只是估价输入；不要执行卖店、交易或挂拍卖动作。",
            },
        )

    @mcp.tool()
    def wow_search_auction(
        query: str = "",
        item_id: int = 0,
        limit: int = 20,
        locale: str = "zhCN",
    ) -> dict[str, Any]:
        """只读搜索拍卖行；不会出价、购买或挂售。"""
        bounded_limit = max(1, min(int(limit), 100))
        world = world_db()
        item_ids: set[int] = set()
        item_candidates: list[dict[str, Any]] = []
        if int(item_id or 0) > 0:
            item_ids.add(int(item_id))
            item_candidates = list(fetch_item_templates(world, item_ids, locale=locale).values())
        elif str(query or "").strip():
            item_candidates = search_items(world, query, locale=locale, limit=50)
            item_ids = {int(item.get("item_id") or 0) for item in item_candidates}
        else:
            return {"ok": False, "error": "missing_query_or_item_id"}

        auctions = fetch_auction_rows(
            characters_db(),
            world,
            item_ids=item_ids,
            limit=bounded_limit,
            locale=locale,
        )
        return payload_result(
            "tool_search_auction",
            query=query,
            item_id=int(item_id or 0),
            candidate_count=len(item_candidates),
            item_candidates=item_candidates[:20],
            auction_count=len(auctions),
            auctions=auctions,
            read_only=True,
            feedback={
                "phase": "ready",
                "next_suggestion": "只读结果，可用于估价；购买必须等后续确认型 v2 工具。",
            },
        )

    @mcp.tool()
    def wow_estimate_item_value(item_id: int, locale: str = "zhCN", sample_limit: int = 100) -> dict[str, Any]:
        """只读估算物品单价：优先拍卖行中位 buyout，其次当前出价，最后卖店价。"""
        item_id = int(item_id)
        item = fetch_item_templates(world_db(), {item_id}, locale=locale).get(item_id)
        if not item:
            return {"ok": False, "error": "item_not_found"}
        auctions = fetch_auction_rows(
            characters_db(),
            world_db(),
            item_ids={item_id},
            limit=max(1, min(int(sample_limit), 200)),
            locale=locale,
        )
        estimate = estimate_item_value_from_auctions(item, auctions)
        return payload_result(
            "tool_estimate_item_value",
            item=item,
            auction_count=len(auctions),
            estimate=estimate,
            sample_auctions=auctions[:10],
            read_only=True,
            feedback={
                "phase": "ready",
                "next_suggestion": "这是估算，不会执行买卖；高价值物品挂售前需要人工确认。",
            },
        )

    @mcp.tool()
    def wow_plan_auction_sales(
        event_id: int,
        bot_name: str = "",
        policy: str = "safe",
        limit: int = 40,
        locale: str = "zhCN",
    ) -> dict[str, Any]:
        """只读规划背包物品处理建议：卖店、保留、挂拍卖或可能自用；不会实际挂售。"""
        conn = db()
        event = fetch_event(conn, int(event_id))
        if not event:
            return {"ok": False, "error": "event_not_found"}
        owner_guid, owner_name, owner_kind = visible_inventory_owner(event, bot_name)
        if not owner_guid:
            return {"ok": False, "error": "inventory_owner_not_controllable", "owner": owner_name, "kind": owner_kind}
        items = fetch_inventory_items(
            characters_db(),
            world_db(),
            owner_guid,
            locale=locale,
            include_equipped=False,
            limit=max(1, min(int(limit), 100)),
        )
        item_ids = {int(item.get("item_id") or 0) for item in items}
        auctions = fetch_auction_rows(characters_db(), world_db(), item_ids=item_ids, limit=200, locale=locale)
        auctions_by_item: dict[int, list[dict[str, Any]]] = {}
        for auction in auctions:
            item = auction.get("item") if isinstance(auction.get("item"), dict) else {}
            auctions_by_item.setdefault(int(item.get("item_id") or 0), []).append(auction)

        recommendations: list[dict[str, Any]] = []
        totals = {"auction": 0, "vendor": 0, "keep": 0, "use_or_auction": 0}
        for item in items:
            item_auctions = auctions_by_item.get(int(item.get("item_id") or 0), [])
            estimate = estimate_item_value_from_auctions(item, item_auctions)
            recommendation = auction_sale_recommendation(item, estimate, policy=policy)
            totals[recommendation["action"]] = totals.get(recommendation["action"], 0) + 1
            recommendations.append({"item": item, "estimate": estimate, "recommendation": recommendation})

        upsert_session_goal(
            conn,
            event,
            goal_type="auction_sales",
            goal_text=f"{owner_name} 背包估价",
            status="advice_only",
            payload={"owner": owner_name, "policy": policy, "totals": totals},
        )
        return payload_result(
            "tool_plan_auction_sales",
            source_event_id=int(event_id),
            owner={"guid": owner_guid, "name": owner_name, "kind": owner_kind},
            policy=policy,
            count=len(recommendations),
            totals=totals,
            recommendations=recommendations,
            read_only=True,
            feedback={
                "phase": "advice_only",
                "next_suggestion": "只给建议，不执行花金币、卖店或挂拍卖；v2 前必须人工二次确认。",
            },
        )

    @mcp.tool()
    def wow_set_bot_strategy(
        event_id: int,
        strategy: str,
        op: str = "add",
        bot_name: str = "group",
        bot_state: str = "combat",
    ) -> dict[str, Any]:
        """给可控 bot 开关一个安全 AI 策略。strategy 可写 heal、shadow、bear、frost、healer dps 等；op=add/remove。"""
        conn = db()
        event = fetch_event(conn, int(event_id))
        if not event:
            return {"ok": False, "error": "event_not_found"}

        change = normalize_strategy_change(strategy, op)
        if not change:
            return {"ok": False, "error": "empty_strategy"}

        targets = select_bot_names(event, bot_name, default_selector="group")
        if not targets:
            return {"ok": False, "error": "no_controllable_bot"}

        result = enqueue_strategy_changes(
            conn,
            event,
            targets=targets,
            changes=[change],
            bot_state=bot_state,
            require_supported=True,
        )
        if not result.get("ok"):
            return result
        return payload_result(
            "tool_set_bot_strategy",
            source_event_id=int(event_id),
            requested_strategy=strategy,
            op=op,
            **result,
            feedback={
                "phase": "queued",
                "next_suggestion": "稍后用 wow_get_action_results 确认 bridge 是否执行成功，再用 wow_get_bot_profile 复查当前策略。",
            },
        )

    @mcp.tool()
    def wow_set_bot_role(event_id: int, role: str, bot_name: str = "") -> dict[str, Any]:
        """把可控 bot 切到高层职责/流派，例如治疗、坦克、暗影、熊坦、猫德、冰法、火法。"""
        conn = db()
        event = fetch_event(conn, int(event_id))
        if not event:
            return {"ok": False, "error": "event_not_found"}

        targets = select_bot_names(event, bot_name, default_selector="default", fallback_first=True)
        if not targets:
            return {"ok": False, "error": "no_controllable_bot"}

        context_by_name = {bot_name_from_candidate(bot).lower(): bot for bot in event_context_sources(event)}
        action_ids: list[int] = []
        planned: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        for target in targets:
            bot = context_by_name.get(target.lower())
            class_id = int(bot.get("class") or 0) if bot else 0
            if not class_id:
                row = fetch_character_identity(target)
                class_id = int(row.get("class") or 0) if row else 0
            plan = role_plan_for_class(class_id, role)
            if not plan:
                blocked.append(
                    {
                        "bot": target,
                        "class": class_display(class_id),
                        "requested_role": role,
                        "supported_roles": supported_role_names(class_id),
                        "reason": "role_not_supported_for_bot_class",
                    }
                )
                continue
            change_result = enqueue_strategy_changes(
                conn,
                event,
                targets=[target],
                changes=list(plan.get("changes", [])),
                bot_state="combat",
                require_supported=True,
            )
            action_ids.extend(int(item) for item in change_result.get("action_ids", []))
            planned.append(
                {
                    "bot": target,
                    "class": class_display(class_id),
                    "requested_role": role,
                    "normalized_role": plan["key"],
                    "label": plan.get("label", ""),
                    "result_role": plan.get("role", ""),
                    "changes": plan.get("changes", []),
                    "accepted": change_result.get("accepted", []),
                    "blocked": change_result.get("blocked", []),
                }
            )

        if not planned:
            return {
                "ok": False,
                "error": "no_role_change_planned",
                "requested_role": role,
                "blocked": blocked,
            }
        return payload_result(
            "tool_set_bot_role",
            source_event_id=int(event_id),
            requested_role=role,
            targets=targets,
            planned=planned,
            blocked=blocked,
            action_ids=action_ids,
            feedback={
                "phase": "queued",
                "next_suggestion": "这是 AI 策略切换，不等于重新洗真实天赋；需要用动作结果和 profile 复查。",
            },
        )

    @mcp.tool()
    def wow_set_loot_mode(event_id: int, mode: str, bot_name: str = "group") -> dict[str, Any]:
        """设置可控机器人的非战斗拾取策略；mode 可为 off、normal、gray、all。"""
        normalized = mode.strip().lower()
        if normalized == "off":
            return enqueue_bot_strategy(
                int(event_id),
                bot_name=bot_name,
                strategy="-loot",
                bot_state="noncombat",
                default_selector="group",
            )
        if normalized not in {"normal", "gray", "all"}:
            return {"ok": False, "error": "invalid_loot_mode", "allowed": ["off", "normal", "gray", "all"]}

        strategy_result = enqueue_bot_strategy(
            int(event_id),
            bot_name=bot_name,
            strategy="+loot",
            bot_state="noncombat",
            default_selector="group",
        )
        if not strategy_result.get("ok"):
            return strategy_result
        command_result = enqueue_bot_command(
            int(event_id),
            bot_name=bot_name,
            command=f"ll {normalized}",
            default_selector="group",
        )
        return payload_result(
            "tool_set_loot_mode",
            source_event_id=int(event_id),
            mode=normalized,
            strategy_result=strategy_result,
            command_result=command_result,
        )

    @mcp.tool()
    def wow_set_buff(event_id: int, enabled: bool = True, bot_name: str = "group") -> dict[str, Any]:
        """打开或关闭可控机器人的非战斗 buff 策略。"""
        return enqueue_bot_strategy(
            int(event_id),
            bot_name=bot_name,
            strategy="+buff" if bool(enabled) else "-buff",
            bot_state="noncombat",
            default_selector="group",
        )

    @mcp.tool()
    def wow_set_healer_dps(event_id: int, enabled: bool = False, bot_name: str = "healers") -> dict[str, Any]:
        """允许或禁止治疗机器人在战斗中补输出；默认关闭 healer dps 让治疗专心加血。"""
        return enqueue_bot_strategy(
            int(event_id),
            bot_name=bot_name,
            strategy="+healer dps" if bool(enabled) else "-healer dps",
            bot_state="combat",
            default_selector="healers",
            role_filter="healer",
        )

    return mcp


def main() -> None:
    host = os.getenv("PLAYERBOT_MCP_HOST", "0.0.0.0")
    port = env_int("PLAYERBOT_MCP_PORT", 18765, 1)
    token = os.getenv("PLAYERBOT_MCP_BEARER_TOKEN", "").strip()
    if not token and not env_bool("PLAYERBOT_MCP_ALLOW_NO_AUTH", False):
        raise SystemExit("PLAYERBOT_MCP_BEARER_TOKEN must be set unless PLAYERBOT_MCP_ALLOW_NO_AUTH=1")

    mcp = build_mcp()
    app = BearerAndHealthMiddleware(mcp.streamable_http_app(), token)
    log_event("startup", host=host, port=port, auth=bool(token), path="/mcp")
    uvicorn.run(app, host=host, port=port, log_level=os.getenv("PLAYERBOT_MCP_UVICORN_LOG_LEVEL", "info"))


if __name__ == "__main__":
    main()
