/*
 * Thin in-game bridge for the Playerbot LLM-Agent sidecar.
 *
 * The bridge deliberately exposes only a tiny, whitelisted surface:
 * - queue real-player chat events with current Playerbot context
 * - execute pending reply / command / strategy actions from the sidecar
 */

#include "Chat.h"
#include "CellImpl.h"
#include "Config.h"
#include "Creature.h"
#include "DatabaseEnv.h"
#include "GridNotifiers.h"
#include "GridNotifiersImpl.h"
#include "Group.h"
#include "Log.h"
#include "ObjectAccessor.h"
#include "Player.h"
#include "PlayerScript.h"
#include "PlayerbotAI.h"
#include "PlayerbotMgr.h"
#include "Playerbots.h"
#include "ScriptMgr.h"
#include "GameTime.h"
#include "UnitScript.h"
#include "WorldScript.h"
#include "WorldSession.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <deque>
#include <iomanip>
#include <list>
#include <map>
#include <sstream>
#include <string>
#include <vector>

namespace
{
bool AgentEnabled = true;
bool SchemaReady = false;
uint32 ActionPollIntervalMs = 500;
uint32 ActionPollElapsedMs = 0;
uint32 MaxReplyLength = 220;
bool TraceLog = true;
float ContextRange = 45.0f;
uint32 MaxNearbyHostiles = 8;
bool CombatTelemetryEnabled = true;
uint32 CombatIdleEndMs = 5000;
uint32 CombatMinDurationMs = 3000;
uint32 CombatTimelineLimit = 80;

std::string ToLowerAscii(std::string value)
{
    std::transform(value.begin(), value.end(), value.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return value;
}

bool StartsWith(std::string const& value, std::string const& prefix)
{
    return value.size() >= prefix.size() && value.compare(0, prefix.size(), prefix) == 0;
}

std::string SqlQuote(std::string value)
{
    PlayerbotsDatabase.EscapeString(value);
    return "'" + value + "'";
}

std::string SqlNullableQuote(std::string const& value)
{
    if (value.empty())
        return "NULL";

    return SqlQuote(value);
}

std::string SqlNullableUInt(uint32 value)
{
    if (!value)
        return "NULL";

    return std::to_string(value);
}

std::string JsonEscape(std::string const& value)
{
    std::ostringstream out;
    for (unsigned char c : value)
    {
        switch (c)
        {
            case '\\':
                out << "\\\\";
                break;
            case '"':
                out << "\\\"";
                break;
            case '\b':
                out << "\\b";
                break;
            case '\f':
                out << "\\f";
                break;
            case '\n':
                out << "\\n";
                break;
            case '\r':
                out << "\\r";
                break;
            case '\t':
                out << "\\t";
                break;
            default:
                if (c < 0x20)
                    out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(c);
                else
                    out << static_cast<char>(c);
                break;
        }
    }

    return out.str();
}

struct BotSnapshot
{
    uint32 guid = 0;
    std::string name;
    uint8 playerClass = 0;
    uint8 level = 0;
    std::string role;
    float healthPct = 0.0f;
    uint32 manaPct = 0;
    bool alive = false;
    bool combat = false;
    bool hasDistance = false;
    float distance = 0.0f;
    uint32 mapId = 0;
    uint32 zoneId = 0;
    uint32 areaId = 0;
    std::string targetName;
    bool targetHostile = false;
    bool hasTargetDistance = false;
    float targetDistance = 0.0f;
};

std::string FloatString(float value)
{
    std::ostringstream out;
    out << std::fixed << std::setprecision(1) << value;
    return out.str();
}

uint32 ManaPct(Unit* unit)
{
    if (!unit)
        return 0;

    uint32 maxMana = unit->GetMaxPower(POWER_MANA);
    if (!maxMana)
        return 0;

    return static_cast<uint32>(std::round(100.0f * static_cast<float>(unit->GetPower(POWER_MANA)) / static_cast<float>(maxMana)));
}

uint64 NowMs()
{
    return static_cast<uint64>(GameTime::GetGameTimeMS().count());
}

struct CombatMemberStats
{
    uint32 guid = 0;
    std::string name;
    uint8 playerClass = 0;
    std::string role;
    bool isBot = false;
    uint64 damageDone = 0;
    uint64 damageTaken = 0;
    uint64 healingDone = 0;
    uint64 healingReceived = 0;
    float minHealthPct = 100.0f;
    uint32 minManaPct = 101;
    uint32 deaths = 0;
    uint32 hostileHitsTaken = 0;
};

struct CombatEnemyStats
{
    uint32 guid = 0;
    uint32 entry = 0;
    std::string name;
    uint8 level = 0;
    uint64 damageDone = 0;
    uint64 damageTaken = 0;
    bool killed = false;
};

struct CombatSession
{
    uint32 key = 0;
    uint32 leaderGuid = 0;
    std::string leaderName;
    uint32 mapId = 0;
    uint32 zoneId = 0;
    uint32 areaId = 0;
    uint64 startedMs = 0;
    uint64 lastActivityMs = 0;
    uint64 damageDone = 0;
    uint64 damageTaken = 0;
    uint64 healingDone = 0;
    uint32 kills = 0;
    uint32 deaths = 0;
    uint32 healerThreatEvents = 0;
    uint32 botThreatEvents = 0;
    std::map<uint32, CombatMemberStats> members;
    std::map<uint32, CombatEnemyStats> enemies;
    std::deque<std::string> timeline;
};

std::map<uint32, CombatSession> CombatSessions;

bool IsRealPlayer(Player* player)
{
    return player && player->GetSession() && !player->GetSession()->IsBot();
}

bool IsPlayerbot(Player* player)
{
    return player && GET_PLAYERBOT_AI(player) != nullptr;
}

std::string DetectRole(Player* bot)
{
    if (!bot)
        return "unknown";

    if (PlayerbotAI::IsTank(bot))
        return "tank";

    if (PlayerbotAI::IsHeal(bot))
        return "healer";

    return "dps";
}

Player* UnitOwnerPlayer(Unit* unit)
{
    return unit ? unit->GetCharmerOrOwnerPlayerOrPlayerItself() : nullptr;
}

bool GroupHasRealPlayerAndBot(Group* group)
{
    if (!group)
        return false;

    bool hasReal = false;
    bool hasBot = false;
    for (GroupReference* itr = group->GetFirstMember(); itr != nullptr; itr = itr->next())
    {
        Player* member = itr->GetSource();
        hasReal = hasReal || IsRealPlayer(member);
        hasBot = hasBot || IsPlayerbot(member);
        if (hasReal && hasBot)
            return true;
    }

    return false;
}

bool HasOwnedPlayerbot(Player* player)
{
    if (!player || !IsRealPlayer(player))
        return false;

    if (PlayerbotMgr* mgr = GET_PLAYERBOT_MGR(player))
        return mgr->GetPlayerBotsBegin() != mgr->GetPlayerBotsEnd();

    return false;
}

bool GetAgentParty(Player* player, uint32& key, uint32& leaderGuid, std::string& leaderName, Group*& group)
{
    key = 0;
    leaderGuid = 0;
    leaderName.clear();
    group = nullptr;

    if (!player)
        return false;

    group = player->GetGroup();
    if (group)
    {
        if (!GroupHasRealPlayerAndBot(group))
            return false;

        leaderGuid = group->GetLeaderGUID().GetCounter();
        key = leaderGuid ? leaderGuid : player->GetGUID().GetCounter();
        if (Player* leader = ObjectAccessor::FindPlayer(group->GetLeaderGUID()))
            leaderName = leader->GetName();
        else
            leaderName = player->GetName();
        return key != 0;
    }

    if (!HasOwnedPlayerbot(player))
        return false;

    key = player->GetGUID().GetCounter();
    leaderGuid = key;
    leaderName = player->GetName();
    return true;
}

CombatMemberStats& TouchMember(CombatSession& session, Player* player)
{
    uint32 guid = player->GetGUID().GetCounter();
    CombatMemberStats& stats = session.members[guid];
    stats.guid = guid;
    stats.name = player->GetName();
    stats.playerClass = player->getClass();
    stats.role = IsPlayerbot(player) ? DetectRole(player) : "player";
    stats.isBot = IsPlayerbot(player);
    stats.minHealthPct = std::min(stats.minHealthPct, player->GetHealthPct());
    uint32 manaPct = ManaPct(player);
    if (manaPct > 0)
        stats.minManaPct = std::min(stats.minManaPct, manaPct);
    return stats;
}

CombatEnemyStats& TouchEnemy(CombatSession& session, Unit* unit)
{
    uint32 guid = unit->GetGUID().GetCounter();
    CombatEnemyStats& stats = session.enemies[guid];
    stats.guid = guid;
    stats.entry = unit->GetEntry();
    stats.name = unit->GetName();
    stats.level = unit->GetLevel();
    return stats;
}

void AddTimeline(CombatSession& session, std::string const& text)
{
    if (!CombatTimelineLimit)
        return;

    if (session.timeline.size() >= CombatTimelineLimit)
        session.timeline.pop_front();

    session.timeline.push_back(text);
}

void RefreshSessionMembers(CombatSession& session, Group* group, Player* fallback)
{
    if (group)
    {
        for (GroupReference* itr = group->GetFirstMember(); itr != nullptr; itr = itr->next())
            if (Player* member = itr->GetSource())
                if (IsRealPlayer(member) || IsPlayerbot(member))
                    TouchMember(session, member);
        return;
    }

    if (fallback)
        TouchMember(session, fallback);
}

CombatSession* TouchCombatSession(Player* relatedPlayer, uint64 now)
{
    if (!AgentEnabled || !SchemaReady || !CombatTelemetryEnabled || !relatedPlayer)
        return nullptr;

    uint32 key = 0;
    uint32 leaderGuid = 0;
    std::string leaderName;
    Group* group = nullptr;
    if (!GetAgentParty(relatedPlayer, key, leaderGuid, leaderName, group))
        return nullptr;

    CombatSession& session = CombatSessions[key];
    if (!session.key)
    {
        session.key = key;
        session.startedMs = now;
        session.mapId = relatedPlayer->GetMapId();
        session.zoneId = relatedPlayer->GetZoneId();
        session.areaId = relatedPlayer->GetAreaId();
        AddTimeline(session, "combat started near " + relatedPlayer->GetName());
    }

    session.leaderGuid = leaderGuid;
    session.leaderName = leaderName;
    session.lastActivityMs = now;
    RefreshSessionMembers(session, group, relatedPlayer);
    return &session;
}

BotSnapshot MakeSnapshot(Player* bot, Player* reference = nullptr)
{
    BotSnapshot snapshot;
    snapshot.guid = bot->GetGUID().GetCounter();
    snapshot.name = bot->GetName();
    snapshot.playerClass = bot->getClass();
    snapshot.level = bot->GetLevel();
    snapshot.role = DetectRole(bot);
    snapshot.healthPct = bot->GetHealthPct();
    snapshot.manaPct = ManaPct(bot);
    snapshot.alive = bot->IsAlive();
    snapshot.combat = bot->IsInCombat();
    snapshot.mapId = bot->GetMapId();
    snapshot.zoneId = bot->GetZoneId();
    snapshot.areaId = bot->GetAreaId();

    if (reference && reference->IsInMap(bot))
    {
        snapshot.hasDistance = true;
        snapshot.distance = reference->GetDistance(bot);
    }

    if (Unit* target = bot->GetVictim())
    {
        snapshot.targetName = target->GetName();
        snapshot.targetHostile = target->IsHostileTo(bot);
        if (bot->IsInMap(target))
        {
            snapshot.hasTargetDistance = true;
            snapshot.targetDistance = bot->GetDistance(target);
        }
    }

    return snapshot;
}

void AddUniqueBot(std::vector<BotSnapshot>& bots, Player* bot, Player* reference = nullptr)
{
    if (!IsPlayerbot(bot))
        return;

    uint32 guid = bot->GetGUID().GetCounter();
    auto existing = std::find_if(bots.begin(), bots.end(), [guid](BotSnapshot const& snapshot)
    {
        return snapshot.guid == guid;
    });

    if (existing == bots.end())
        bots.push_back(MakeSnapshot(bot, reference));
}

std::vector<BotSnapshot> GetGroupBots(Group* group, Player* reference)
{
    std::vector<BotSnapshot> bots;
    if (!group)
        return bots;

    for (GroupReference* itr = group->GetFirstMember(); itr != nullptr; itr = itr->next())
    {
        if (Player* member = itr->GetSource())
            AddUniqueBot(bots, member, reference);
    }

    return bots;
}

std::vector<BotSnapshot> GetOwnedBots(Player* master)
{
    std::vector<BotSnapshot> bots;
    if (!master)
        return bots;

    if (PlayerbotMgr* mgr = GET_PLAYERBOT_MGR(master))
    {
        for (PlayerBotMap::const_iterator itr = mgr->GetPlayerBotsBegin(); itr != mgr->GetPlayerBotsEnd(); ++itr)
            AddUniqueBot(bots, itr->second, master);
    }

    return bots;
}

std::string TargetToJson(Unit* target, Unit* reference)
{
    if (!target)
        return "null";

    std::ostringstream out;
    out << "{\"guid\":" << target->GetGUID().GetCounter()
        << ",\"name\":\"" << JsonEscape(target->GetName()) << "\""
        << ",\"type\":\"" << (target->IsPlayer() ? "player" : (target->IsCreature() ? "creature" : "unit")) << "\""
        << ",\"entry\":" << target->GetEntry()
        << ",\"level\":" << static_cast<uint32>(target->GetLevel())
        << ",\"health_pct\":" << FloatString(target->GetHealthPct())
        << ",\"alive\":" << (target->IsAlive() ? "true" : "false")
        << ",\"combat\":" << (target->IsInCombat() ? "true" : "false");

    if (reference && reference->IsInMap(target))
        out << ",\"distance\":" << FloatString(reference->GetDistance(target))
            << ",\"hostile_to_reference\":" << (target->IsHostileTo(reference) ? "true" : "false");

    out << "}";
    return out.str();
}

std::string PlayerContextToJson(Player* player, Player* reference)
{
    if (!player)
        return "null";

    std::ostringstream out;
    out << "{\"guid\":" << player->GetGUID().GetCounter()
        << ",\"name\":\"" << JsonEscape(player->GetName()) << "\""
        << ",\"is_bot\":" << (IsPlayerbot(player) ? "true" : "false")
        << ",\"class\":" << static_cast<uint32>(player->getClass())
        << ",\"level\":" << static_cast<uint32>(player->GetLevel())
        << ",\"role\":\"" << JsonEscape(IsPlayerbot(player) ? DetectRole(player) : "player") << "\""
        << ",\"health_pct\":" << FloatString(player->GetHealthPct())
        << ",\"mana_pct\":" << ManaPct(player)
        << ",\"alive\":" << (player->IsAlive() ? "true" : "false")
        << ",\"combat\":" << (player->IsInCombat() ? "true" : "false")
        << ",\"map_id\":" << player->GetMapId()
        << ",\"zone_id\":" << player->GetZoneId()
        << ",\"area_id\":" << player->GetAreaId();

    if (reference && reference != player && reference->IsInMap(player))
        out << ",\"distance\":" << FloatString(reference->GetDistance(player));

    out << ",\"selected_target\":" << TargetToJson(player->GetSelectedUnit(), player)
        << ",\"combat_target\":" << TargetToJson(player->GetVictim(), player)
        << "}";
    return out.str();
}

std::string GroupMembersToJson(Player* speaker, Group* group)
{
    std::ostringstream out;
    out << "[";
    bool first = true;

    if (group)
    {
        for (GroupReference* itr = group->GetFirstMember(); itr != nullptr; itr = itr->next())
        {
            Player* member = itr->GetSource();
            if (!member)
                continue;

            if (!first)
                out << ",";
            first = false;
            out << PlayerContextToJson(member, speaker);
        }
    }
    else if (speaker)
    {
        out << PlayerContextToJson(speaker, speaker);
    }

    out << "]";
    return out.str();
}

std::string NearbyHostilesToJson(Player* speaker)
{
    std::ostringstream out;
    out << "[";
    if (!speaker || ContextRange <= 0.0f || !MaxNearbyHostiles)
    {
        out << "]";
        return out.str();
    }

    std::list<Unit*> targets;
    Acore::AnyUnfriendlyUnitInObjectRangeCheck check(speaker, speaker, ContextRange);
    Acore::UnitListSearcher<Acore::AnyUnfriendlyUnitInObjectRangeCheck> searcher(speaker, targets, check);
    Cell::VisitObjects(speaker, searcher, ContextRange);

    targets.remove_if([speaker](Unit* unit)
    {
        return !unit || !unit->IsInWorld() || unit->IsDuringRemoveFromWorld() || !unit->IsAlive() ||
               unit->IsPlayer() || !speaker->IsInMap(unit) || !unit->IsHostileTo(speaker);
    });

    targets.sort([speaker](Unit* left, Unit* right)
    {
        return speaker->GetDistance(left) < speaker->GetDistance(right);
    });

    uint32 count = 0;
    for (Unit* target : targets)
    {
        if (count >= MaxNearbyHostiles)
            break;

        if (count)
            out << ",";

        out << TargetToJson(target, speaker);
        ++count;
    }

    out << "]";
    return out.str();
}

std::string BotSnapshotsToJson(std::vector<BotSnapshot> const& bots)
{
    std::ostringstream out;
    out << "[";

    for (std::size_t i = 0; i < bots.size(); ++i)
    {
        BotSnapshot const& bot = bots[i];
        if (i)
            out << ",";

        out << "{\"guid\":" << bot.guid
            << ",\"name\":\"" << JsonEscape(bot.name) << "\""
            << ",\"class\":" << static_cast<uint32>(bot.playerClass)
            << ",\"level\":" << static_cast<uint32>(bot.level)
            << ",\"role\":\"" << JsonEscape(bot.role) << "\""
            << ",\"health_pct\":" << FloatString(bot.healthPct)
            << ",\"mana_pct\":" << bot.manaPct
            << ",\"alive\":" << (bot.alive ? "true" : "false")
            << ",\"combat\":" << (bot.combat ? "true" : "false")
            << ",\"map_id\":" << bot.mapId
            << ",\"zone_id\":" << bot.zoneId
            << ",\"area_id\":" << bot.areaId;

        if (bot.hasDistance)
            out << ",\"distance_to_speaker\":" << FloatString(bot.distance);

        if (!bot.targetName.empty())
        {
            out << ",\"combat_target\":{\"name\":\"" << JsonEscape(bot.targetName) << "\""
                << ",\"hostile\":" << (bot.targetHostile ? "true" : "false");
            if (bot.hasTargetDistance)
                out << ",\"distance\":" << FloatString(bot.targetDistance);
            out << "}";
        }

        out << "}";
    }

    out << "]";
    return out.str();
}

std::string EventMetaToJson(Player* speaker, Group* group, std::vector<BotSnapshot> const& bots)
{
    std::ostringstream out;
    out << "{\"speaker\":" << PlayerContextToJson(speaker, speaker)
        << ",\"environment\":{"
        << "\"map_id\":" << (speaker ? speaker->GetMapId() : 0)
        << ",\"zone_id\":" << (speaker ? speaker->GetZoneId() : 0)
        << ",\"area_id\":" << (speaker ? speaker->GetAreaId() : 0)
        << ",\"selected_target\":" << (speaker ? TargetToJson(speaker->GetSelectedUnit(), speaker) : "null")
        << ",\"combat_target\":" << (speaker ? TargetToJson(speaker->GetVictim(), speaker) : "null")
        << ",\"nearby_hostiles\":" << NearbyHostilesToJson(speaker)
        << "},\"group_members\":" << GroupMembersToJson(speaker, group)
        << ",\"bots\":" << BotSnapshotsToJson(bots)
        << "}";
    return out.str();
}

std::string CombatFactsToJson(CombatSession const& session, uint64 endedMs)
{
    uint64 durationMs = endedMs > session.startedMs ? endedMs - session.startedMs : 0;

    std::ostringstream out;
    out << "{\"group_leader\":{\"guid\":" << session.leaderGuid
        << ",\"name\":\"" << JsonEscape(session.leaderName) << "\"}"
        << ",\"map_id\":" << session.mapId
        << ",\"zone_id\":" << session.zoneId
        << ",\"area_id\":" << session.areaId
        << ",\"duration_ms\":" << durationMs
        << ",\"totals\":{"
        << "\"damage_done\":" << session.damageDone
        << ",\"damage_taken\":" << session.damageTaken
        << ",\"healing_done\":" << session.healingDone
        << ",\"kills\":" << session.kills
        << ",\"deaths\":" << session.deaths
        << ",\"bot_threat_events\":" << session.botThreatEvents
        << ",\"healer_threat_events\":" << session.healerThreatEvents
        << "},\"members\":[";

    bool first = true;
    for (auto const& pair : session.members)
    {
        CombatMemberStats const& member = pair.second;
        if (!first)
            out << ",";
        first = false;

        out << "{\"guid\":" << member.guid
            << ",\"name\":\"" << JsonEscape(member.name) << "\""
            << ",\"class\":" << static_cast<uint32>(member.playerClass)
            << ",\"role\":\"" << JsonEscape(member.role) << "\""
            << ",\"is_bot\":" << (member.isBot ? "true" : "false")
            << ",\"damage_done\":" << member.damageDone
            << ",\"damage_taken\":" << member.damageTaken
            << ",\"healing_done\":" << member.healingDone
            << ",\"healing_received\":" << member.healingReceived
            << ",\"min_health_pct\":" << FloatString(member.minHealthPct)
            << ",\"min_mana_pct\":";
        if (member.minManaPct <= 100)
            out << member.minManaPct;
        else
            out << "null";
        out << ",\"deaths\":" << member.deaths
            << ",\"hostile_hits_taken\":" << member.hostileHitsTaken
            << "}";
    }

    out << "],\"enemies\":[";
    first = true;
    for (auto const& pair : session.enemies)
    {
        CombatEnemyStats const& enemy = pair.second;
        if (!first)
            out << ",";
        first = false;

        out << "{\"guid\":" << enemy.guid
            << ",\"entry\":" << enemy.entry
            << ",\"name\":\"" << JsonEscape(enemy.name) << "\""
            << ",\"level\":" << static_cast<uint32>(enemy.level)
            << ",\"damage_done\":" << enemy.damageDone
            << ",\"damage_taken\":" << enemy.damageTaken
            << ",\"killed\":" << (enemy.killed ? "true" : "false")
            << "}";
    }

    out << "],\"timeline\":[";
    for (std::size_t i = 0; i < session.timeline.size(); ++i)
    {
        if (i)
            out << ",";
        out << "\"" << JsonEscape(session.timeline[i]) << "\"";
    }

    out << "]}";
    return out.str();
}

void InsertCombatSummary(CombatSession const& session, uint64 endedMs)
{
    uint64 durationMs = endedMs > session.startedMs ? endedMs - session.startedMs : 0;
    if (durationMs < CombatMinDurationMs && !session.kills && !session.deaths && session.damageDone < 100)
        return;

    std::string facts = CombatFactsToJson(session, endedMs);
    PlayerbotsDatabase.Execute(
        "INSERT INTO `agent_playerbot_combat_summaries` "
        "(`group_leader_guid`, `group_leader_name`, `map_id`, `zone_id`, `area_id`, `duration_ms`, "
        "`kills`, `deaths`, `facts_json`) VALUES ({}, {}, {}, {}, {}, {}, {}, {}, {})",
        session.leaderGuid, SqlQuote(session.leaderName), session.mapId, session.zoneId, session.areaId,
        durationMs, session.kills, session.deaths, SqlQuote(facts));

    if (TraceLog)
        LOG_INFO("module.playerbot_agent", "Combat summary queued leader={} duration_ms={} kills={} deaths={}",
                 session.leaderName, durationMs, session.kills, session.deaths);
}

void RecordDamage(Unit* attacker, Unit* victim, uint32 damage)
{
    if (!attacker || !victim || !damage)
        return;

    Player* attackerPlayer = UnitOwnerPlayer(attacker);
    Player* victimPlayer = UnitOwnerPlayer(victim);
    Player* relatedPlayer = attackerPlayer ? attackerPlayer : victimPlayer;
    uint64 now = NowMs();
    CombatSession* session = TouchCombatSession(relatedPlayer, now);
    if (!session)
        return;

    bool attackerMember = attackerPlayer && session->members.find(attackerPlayer->GetGUID().GetCounter()) != session->members.end();
    bool victimMember = victimPlayer && session->members.find(victimPlayer->GetGUID().GetCounter()) != session->members.end();

    if (attackerMember && !victimMember)
    {
        CombatMemberStats& member = TouchMember(*session, attackerPlayer);
        member.damageDone += damage;
        session->damageDone += damage;

        if (!victim->IsPlayer())
        {
            CombatEnemyStats& enemy = TouchEnemy(*session, victim);
            enemy.damageTaken += damage;
        }
    }

    if (victimMember && !attackerMember)
    {
        CombatMemberStats& member = TouchMember(*session, victimPlayer);
        member.damageTaken += damage;
        member.hostileHitsTaken += 1;
        session->damageTaken += damage;

        if (member.isBot)
        {
            session->botThreatEvents += 1;
            if (member.role == "healer")
                session->healerThreatEvents += 1;
        }

        if (!attacker->IsPlayer())
        {
            CombatEnemyStats& enemy = TouchEnemy(*session, attacker);
            enemy.damageDone += damage;
        }
    }
}

void RecordHeal(Unit* healer, Unit* receiver, uint32 gain)
{
    if (!healer || !receiver || !gain)
        return;

    Player* healerPlayer = UnitOwnerPlayer(healer);
    Player* receiverPlayer = UnitOwnerPlayer(receiver);
    Player* relatedPlayer = healerPlayer ? healerPlayer : receiverPlayer;
    uint64 now = NowMs();
    CombatSession* session = TouchCombatSession(relatedPlayer, now);
    if (!session)
        return;

    bool healerMember = healerPlayer && session->members.find(healerPlayer->GetGUID().GetCounter()) != session->members.end();
    bool receiverMember = receiverPlayer && session->members.find(receiverPlayer->GetGUID().GetCounter()) != session->members.end();
    if (!healerMember || !receiverMember)
        return;

    CombatMemberStats& healerStats = TouchMember(*session, healerPlayer);
    CombatMemberStats& receiverStats = TouchMember(*session, receiverPlayer);
    healerStats.healingDone += gain;
    receiverStats.healingReceived += gain;
    session->healingDone += gain;
}

void RecordDeath(Unit* unit, Unit* killer)
{
    if (!unit)
        return;

    Player* deadPlayer = UnitOwnerPlayer(unit);
    Player* killerPlayer = UnitOwnerPlayer(killer);
    Player* relatedPlayer = deadPlayer ? deadPlayer : killerPlayer;
    uint64 now = NowMs();
    CombatSession* session = TouchCombatSession(relatedPlayer, now);
    if (!session)
        return;

    if (deadPlayer && session->members.find(deadPlayer->GetGUID().GetCounter()) != session->members.end())
    {
        CombatMemberStats& member = TouchMember(*session, deadPlayer);
        member.deaths += 1;
        session->deaths += 1;
        AddTimeline(*session, deadPlayer->GetName() + " died");
        return;
    }

    if (killerPlayer && !unit->IsPlayer() && session->members.find(killerPlayer->GetGUID().GetCounter()) != session->members.end())
    {
        CombatEnemyStats& enemy = TouchEnemy(*session, unit);
        if (!enemy.killed)
        {
            enemy.killed = true;
            session->kills += 1;
            AddTimeline(*session, unit->GetName() + " killed by " + killerPlayer->GetName());
        }
    }
}

void RecordCombatEnter(Unit* unit, Unit* victim)
{
    Player* player = UnitOwnerPlayer(unit);
    if (!player)
        player = UnitOwnerPlayer(victim);

    CombatSession* session = TouchCombatSession(player, NowMs());
    if (!session)
        return;

    if (unit && !unit->IsPlayer())
        TouchEnemy(*session, unit);
    if (victim && !victim->IsPlayer())
        TouchEnemy(*session, victim);
}

void ProcessCombatSessions()
{
    if (!CombatTelemetryEnabled || CombatSessions.empty())
        return;

    uint64 now = NowMs();
    for (auto itr = CombatSessions.begin(); itr != CombatSessions.end();)
    {
        CombatSession const& session = itr->second;
        if (now >= session.lastActivityMs && now - session.lastActivityMs >= CombatIdleEndMs)
        {
            InsertCombatSummary(session, now);
            itr = CombatSessions.erase(itr);
            continue;
        }

        ++itr;
    }
}

void EnsureSchema()
{
    PlayerbotsDatabase.DirectExecute(
        "CREATE TABLE IF NOT EXISTS `agent_playerbot_events` ("
        "`id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,"
        "`created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "`processed_at` TIMESTAMP NULL DEFAULT NULL,"
        "`event_type` VARCHAR(32) NOT NULL DEFAULT 'chat',"
        "`channel` VARCHAR(16) NOT NULL,"
        "`chat_type` INT UNSIGNED NOT NULL DEFAULT 0,"
        "`speaker_guid` INT UNSIGNED NOT NULL DEFAULT 0,"
        "`speaker_account` INT UNSIGNED NOT NULL DEFAULT 0,"
        "`speaker_name` VARCHAR(64) NOT NULL DEFAULT '',"
        "`target_guid` INT UNSIGNED NULL DEFAULT NULL,"
        "`target_name` VARCHAR(64) NULL DEFAULT NULL,"
        "`group_leader_guid` INT UNSIGNED NULL DEFAULT NULL,"
        "`bot_guid` INT UNSIGNED NULL DEFAULT NULL,"
        "`bot_name` VARCHAR(64) NULL DEFAULT NULL,"
        "`message` TEXT NOT NULL,"
        "`meta` TEXT NULL DEFAULT NULL,"
        "PRIMARY KEY (`id`),"
        "KEY `idx_agent_playerbot_events_created` (`created_at`),"
        "KEY `idx_agent_playerbot_events_speaker` (`speaker_guid`)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");

    PlayerbotsDatabase.DirectExecute(
        "CREATE TABLE IF NOT EXISTS `agent_playerbot_actions` ("
        "`id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,"
        "`created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "`available_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "`updated_at` TIMESTAMP NULL DEFAULT NULL,"
        "`status` VARCHAR(16) NOT NULL DEFAULT 'pending',"
        "`source_event_id` BIGINT UNSIGNED NULL DEFAULT NULL,"
        "`requester_guid` INT UNSIGNED NOT NULL DEFAULT 0,"
        "`requester_name` VARCHAR(64) NOT NULL DEFAULT '',"
        "`bot_guid` INT UNSIGNED NULL DEFAULT NULL,"
        "`bot_name` VARCHAR(64) NULL DEFAULT NULL,"
        "`action_type` VARCHAR(32) NOT NULL,"
        "`channel` VARCHAR(16) NULL DEFAULT NULL,"
        "`text` TEXT NULL DEFAULT NULL,"
        "`command` VARCHAR(255) NULL DEFAULT NULL,"
        "`strategy` VARCHAR(255) NULL DEFAULT NULL,"
        "`bot_state` VARCHAR(16) NULL DEFAULT NULL,"
        "`result` TEXT NULL DEFAULT NULL,"
        "`error` TEXT NULL DEFAULT NULL,"
        "PRIMARY KEY (`id`),"
        "KEY `idx_agent_playerbot_actions_status` (`status`, `available_at`, `id`),"
        "KEY `idx_agent_playerbot_actions_event` (`source_event_id`)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");

    PlayerbotsDatabase.DirectExecute(
        "CREATE TABLE IF NOT EXISTS `agent_playerbot_combat_summaries` ("
        "`id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,"
        "`created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "`summarized_at` TIMESTAMP NULL DEFAULT NULL,"
        "`group_leader_guid` INT UNSIGNED NOT NULL DEFAULT 0,"
        "`group_leader_name` VARCHAR(64) NOT NULL DEFAULT '',"
        "`map_id` INT UNSIGNED NOT NULL DEFAULT 0,"
        "`zone_id` INT UNSIGNED NOT NULL DEFAULT 0,"
        "`area_id` INT UNSIGNED NOT NULL DEFAULT 0,"
        "`duration_ms` BIGINT UNSIGNED NOT NULL DEFAULT 0,"
        "`kills` INT UNSIGNED NOT NULL DEFAULT 0,"
        "`deaths` INT UNSIGNED NOT NULL DEFAULT 0,"
        "`facts_json` MEDIUMTEXT NOT NULL,"
        "`summary_text` TEXT NULL DEFAULT NULL,"
        "PRIMARY KEY (`id`),"
        "KEY `idx_agent_playerbot_combat_summarized` (`summarized_at`, `id`),"
        "KEY `idx_agent_playerbot_combat_created` (`created_at`)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");

    SchemaReady = true;
}

void InsertChatEvent(Player* speaker, std::string const& channel, uint32 chatType, std::string const& message,
                     Player* receiver, Group* group, std::vector<BotSnapshot> bots)
{
    if (!AgentEnabled || !SchemaReady || !IsRealPlayer(speaker) || bots.empty())
        return;

    Player* targetBot = IsPlayerbot(receiver) ? receiver : nullptr;
    uint32 targetGuid = receiver ? receiver->GetGUID().GetCounter() : 0;
    std::string targetName = receiver ? receiver->GetName() : "";
    uint32 groupLeaderGuid = group ? group->GetLeaderGUID().GetCounter() : 0;
    uint32 botGuid = targetBot ? targetBot->GetGUID().GetCounter() : 0;
    std::string botName = targetBot ? targetBot->GetName() : "";
    uint32 accountId = speaker->GetSession() ? speaker->GetSession()->GetAccountId() : 0;
    std::string meta = EventMetaToJson(speaker, group, bots);

    if (TraceLog)
        LOG_INFO("module.playerbot_agent", "Queued chat event channel={} speaker={} target={} bots={} message={}",
                 channel, speaker->GetName(), targetName, bots.size(), message);

    PlayerbotsDatabase.Execute(
        "INSERT INTO `agent_playerbot_events` "
        "(`event_type`, `channel`, `chat_type`, `speaker_guid`, `speaker_account`, `speaker_name`, "
        "`target_guid`, `target_name`, `group_leader_guid`, `bot_guid`, `bot_name`, `message`, `meta`) "
        "VALUES ({}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {})",
        SqlQuote("chat"), SqlQuote(channel), chatType, speaker->GetGUID().GetCounter(), accountId,
        SqlQuote(speaker->GetName()), SqlNullableUInt(targetGuid), SqlNullableQuote(targetName),
        SqlNullableUInt(groupLeaderGuid), SqlNullableUInt(botGuid), SqlNullableQuote(botName),
        SqlQuote(message), SqlQuote(meta));
}

bool IsControlledByRequester(Player* requester, Player* bot)
{
    if (!requester || !bot || !IsPlayerbot(bot))
        return false;

    if (PlayerbotMgr* mgr = GET_PLAYERBOT_MGR(requester))
        if (mgr->GetPlayerBot(bot->GetGUID()) == bot)
            return true;

    return requester->GetGroup() && bot->GetGroup() && requester->GetGroup() == bot->GetGroup();
}

Player* FindControlledBot(Player* requester, uint32 botGuid, std::string const& botName)
{
    if (!requester)
        return nullptr;

    if (botGuid)
    {
        if (Player* bot = ObjectAccessor::FindPlayer(ObjectGuid::Create<HighGuid::Player>(botGuid)))
            if (IsControlledByRequester(requester, bot))
                return bot;
    }

    std::string wanted = ToLowerAscii(botName);
    if (wanted.empty())
        return nullptr;

    if (PlayerbotMgr* mgr = GET_PLAYERBOT_MGR(requester))
    {
        for (PlayerBotMap::const_iterator itr = mgr->GetPlayerBotsBegin(); itr != mgr->GetPlayerBotsEnd(); ++itr)
        {
            Player* bot = itr->second;
            if (bot && ToLowerAscii(bot->GetName()) == wanted && IsControlledByRequester(requester, bot))
                return bot;
        }
    }

    if (Group* group = requester->GetGroup())
    {
        for (GroupReference* itr = group->GetFirstMember(); itr != nullptr; itr = itr->next())
        {
            Player* member = itr->GetSource();
            if (member && ToLowerAscii(member->GetName()) == wanted && IsControlledByRequester(requester, member))
                return member;
        }
    }

    return nullptr;
}

bool IsSafeCommandParam(std::string const& value)
{
    if (value.empty() || value.size() > 64)
        return false;

    for (unsigned char c : value)
    {
        if (c < 0x20 || c == '\'' || c == '"' || c == '`' || c == ';' || c == '\\')
            return false;
    }

    return true;
}

bool IsAllowedBotCommand(std::string const& command)
{
    static std::vector<std::string> const exactCommands = {
        "follow", "stay", "flee", "runaway", "attack", "pull", "pull back", "ready", "max dps"
    };

    std::string normalized = ToLowerAscii(command);
    if (std::find(exactCommands.begin(), exactCommands.end(), normalized) != exactCommands.end())
        return true;

    if (StartsWith(normalized, "ll "))
    {
        std::string arg = normalized.substr(3);
        return arg == "normal" || arg == "gray" || arg == "g" || arg == "all" || arg == "*" ||
               arg == "disenchant" || arg == "d" || arg == "e" || arg == "enchant";
    }

    if (StartsWith(normalized, "focus heal "))
    {
        std::string arg = command.substr(11);
        std::string loweredArg = ToLowerAscii(arg);
        if (loweredArg == "clear" || loweredArg == "none" || loweredArg == "unset" || loweredArg == "?")
            return true;

        return (StartsWith(arg, "+") || StartsWith(arg, "-")) && IsSafeCommandParam(arg.substr(1));
    }

    return false;
}

bool IsAllowedStrategy(std::string const& strategy)
{
    static std::vector<std::string> const allowedStrategies = {
        "+buff", "-buff", "+loot", "-loot", "+healer dps", "-healer dps"
    };

    std::string normalized = ToLowerAscii(strategy);
    return std::find(allowedStrategies.begin(), allowedStrategies.end(), normalized) != allowedStrategies.end();
}

bool ApplyStrategy(PlayerbotAI* botAI, std::string const& strategy, std::string const& stateName)
{
    std::string normalizedState = ToLowerAscii(stateName);

    if (normalizedState == "all")
    {
        botAI->ChangeStrategy(strategy, BOT_STATE_COMBAT);
        botAI->ChangeStrategy(strategy, BOT_STATE_NON_COMBAT);
        return true;
    }

    if (normalizedState == "combat")
    {
        botAI->ChangeStrategy(strategy, BOT_STATE_COMBAT);
        return true;
    }

    if (normalizedState == "noncombat" || normalizedState == "non_combat")
    {
        botAI->ChangeStrategy(strategy, BOT_STATE_NON_COMBAT);
        return true;
    }

    if (normalizedState == "dead")
    {
        botAI->ChangeStrategy(strategy, BOT_STATE_DEAD);
        return true;
    }

    return false;
}

bool SendBotReply(PlayerbotAI* botAI, Player* requester, std::string const& channel, std::string text)
{
    if (!botAI || !requester || text.empty())
        return false;

    if (text.size() > MaxReplyLength)
        text = text.substr(0, MaxReplyLength);

    std::string normalized = ToLowerAscii(channel);
    if (normalized == "party")
        return botAI->SayToParty(text) || botAI->TellMaster(text);

    if (normalized == "say")
        return botAI->Say(text);

    return botAI->Whisper(text, requester->GetName());
}

void CompleteAction(uint64 actionId, bool success, std::string const& result, std::string const& error)
{
    PlayerbotsDatabase.Execute(
        "UPDATE `agent_playerbot_actions` SET `status` = {}, `updated_at` = NOW(), `result` = {}, `error` = {} "
        "WHERE `id` = {}",
        SqlQuote(success ? "done" : "error"), SqlNullableQuote(result), SqlNullableQuote(error), actionId);

    if (TraceLog)
        LOG_INFO("module.playerbot_agent", "Action {} {} result={} error={}", actionId,
                 success ? "done" : "error", result, error);
}

void ProcessAction(uint64 actionId, uint32 requesterGuid, std::string const& botName, uint32 botGuid,
                   std::string const& actionType, std::string const& channel, std::string const& text,
                   std::string const& command, std::string const& strategy, std::string const& botState)
{
    if (TraceLog)
        LOG_INFO("module.playerbot_agent",
                 "Executing action {} type={} bot={}({}) channel={} command={} strategy={} bot_state={} text={}",
                 actionId, actionType, botName, botGuid, channel, command, strategy, botState, text);

    Player* requester = ObjectAccessor::FindPlayer(ObjectGuid::Create<HighGuid::Player>(requesterGuid));
    if (!requester)
    {
        CompleteAction(actionId, false, "", "requester is not online");
        return;
    }

    Player* bot = FindControlledBot(requester, botGuid, botName);
    if (!bot)
    {
        CompleteAction(actionId, false, "", "bot is not online or not controllable by requester");
        return;
    }

    PlayerbotAI* botAI = GET_PLAYERBOT_AI(bot);
    if (!botAI)
    {
        CompleteAction(actionId, false, "", "target player has no PlayerbotAI");
        return;
    }

    std::string normalizedType = ToLowerAscii(actionType);
    if (normalizedType == "reply")
    {
        if (!SendBotReply(botAI, requester, channel.empty() ? "whisper" : channel, text))
        {
            CompleteAction(actionId, false, "", "failed to send bot reply");
            return;
        }

        CompleteAction(actionId, true, "reply sent", "");
        return;
    }

    if (normalizedType == "command")
    {
        if (!IsAllowedBotCommand(command))
        {
            CompleteAction(actionId, false, "", "command is not whitelisted");
            return;
        }

        botAI->HandleCommand(CHAT_MSG_WHISPER, command, requester);
        CompleteAction(actionId, true, "command executed", "");
        return;
    }

    if (normalizedType == "strategy")
    {
        if (!IsAllowedStrategy(strategy))
        {
            CompleteAction(actionId, false, "", "strategy is not whitelisted");
            return;
        }

        if (!ApplyStrategy(botAI, strategy, botState.empty() ? "all" : botState))
        {
            CompleteAction(actionId, false, "", "bot_state is invalid");
            return;
        }

        CompleteAction(actionId, true, "strategy changed", "");
        return;
    }

    CompleteAction(actionId, false, "", "action_type is not supported");
}

void ProcessPendingActions()
{
    if (!AgentEnabled || !SchemaReady)
        return;

    QueryResult result = PlayerbotsDatabase.Query(
        "SELECT `id`, `requester_guid`, `bot_guid`, `bot_name`, `action_type`, `channel`, "
        "`text`, `command`, `strategy`, `bot_state` "
        "FROM `agent_playerbot_actions` "
        "WHERE `status` = 'pending' AND `available_at` <= NOW() "
        "ORDER BY `id` ASC LIMIT 20");

    if (!result)
        return;

    do
    {
        Field* fields = result->Fetch();
        uint64 actionId = fields[0].Get<uint64>();
        uint32 requesterGuid = fields[1].Get<uint32>();
        uint32 botGuid = fields[2].IsNull() ? 0 : fields[2].Get<uint32>();
        std::string botName = fields[3].IsNull() ? "" : fields[3].Get<std::string>();
        std::string actionType = fields[4].Get<std::string>();
        std::string channel = fields[5].IsNull() ? "" : fields[5].Get<std::string>();
        std::string text = fields[6].IsNull() ? "" : fields[6].Get<std::string>();
        std::string command = fields[7].IsNull() ? "" : fields[7].Get<std::string>();
        std::string strategy = fields[8].IsNull() ? "" : fields[8].Get<std::string>();
        std::string botState = fields[9].IsNull() ? "" : fields[9].Get<std::string>();

        PlayerbotsDatabase.Execute(
            "UPDATE `agent_playerbot_actions` SET `status` = 'running', `updated_at` = NOW() "
            "WHERE `id` = {} AND `status` = 'pending'",
            actionId);

        ProcessAction(actionId, requesterGuid, botName, botGuid, actionType, channel, text, command, strategy, botState);
    } while (result->NextRow());
}

class PlayerbotAgentPlayerScript : public PlayerScript
{
public:
    PlayerbotAgentPlayerScript() : PlayerScript("PlayerbotAgentPlayerScript",
        {
            PLAYERHOOK_CAN_PLAYER_USE_CHAT,
            PLAYERHOOK_CAN_PLAYER_USE_PRIVATE_CHAT,
            PLAYERHOOK_CAN_PLAYER_USE_GROUP_CHAT,
            PLAYERHOOK_ON_PLAYER_ENTER_COMBAT,
            PLAYERHOOK_ON_PLAYER_LEAVE_COMBAT
        })
    {
    }

    bool OnPlayerCanUseChat(Player* player, uint32 type, uint32 language, std::string& msg) override
    {
        if (language == LANG_ADDON)
            return true;

        if (type != CHAT_MSG_SAY && type != CHAT_MSG_YELL)
            return true;

        std::vector<BotSnapshot> bots = GetOwnedBots(player);
        InsertChatEvent(player, type == CHAT_MSG_YELL ? "yell" : "say", type, msg, nullptr, nullptr, bots);
        return true;
    }

    bool OnPlayerCanUseChat(Player* player, uint32 type, uint32 language, std::string& msg, Player* receiver) override
    {
        if (language == LANG_ADDON)
            return true;

        if (type != CHAT_MSG_WHISPER || !IsPlayerbot(receiver))
            return true;

        std::vector<BotSnapshot> bots;
        AddUniqueBot(bots, receiver, player);
        InsertChatEvent(player, "whisper", type, msg, receiver, receiver ? receiver->GetGroup() : nullptr, bots);
        return true;
    }

    bool OnPlayerCanUseChat(Player* player, uint32 type, uint32 language, std::string& msg, Group* group) override
    {
        if (language == LANG_ADDON)
            return true;

        if (type != CHAT_MSG_PARTY && type != CHAT_MSG_PARTY_LEADER && type != CHAT_MSG_RAID &&
            type != CHAT_MSG_RAID_LEADER && type != CHAT_MSG_RAID_WARNING)
            return true;

        std::vector<BotSnapshot> bots = GetGroupBots(group, player);
        std::string channel = (type == CHAT_MSG_RAID || type == CHAT_MSG_RAID_LEADER || type == CHAT_MSG_RAID_WARNING)
            ? "raid"
            : "party";

        InsertChatEvent(player, channel, type, msg, nullptr, group, bots);
        return true;
    }

    void OnPlayerEnterCombat(Player* player, Unit* enemy) override
    {
        RecordCombatEnter(player, enemy);
    }

    void OnPlayerLeaveCombat(Player* player) override
    {
        if (CombatSession* session = TouchCombatSession(player, NowMs()))
            AddTimeline(*session, player->GetName() + " left combat");
    }
};

class PlayerbotAgentUnitScript : public UnitScript
{
public:
    PlayerbotAgentUnitScript() : UnitScript("PlayerbotAgentUnitScript", true,
        {
            UNITHOOK_ON_HEAL,
            UNITHOOK_ON_DAMAGE,
            UNITHOOK_ON_UNIT_ENTER_COMBAT,
            UNITHOOK_ON_UNIT_DEATH
        })
    {
    }

    void OnHeal(Unit* healer, Unit* receiver, uint32& gain) override
    {
        RecordHeal(healer, receiver, gain);
    }

    void OnDamage(Unit* attacker, Unit* victim, uint32& damage) override
    {
        RecordDamage(attacker, victim, damage);
    }

    void OnUnitEnterCombat(Unit* unit, Unit* victim) override
    {
        RecordCombatEnter(unit, victim);
    }

    void OnUnitDeath(Unit* unit, Unit* killer) override
    {
        RecordDeath(unit, killer);
    }
};

class PlayerbotAgentWorldScript : public WorldScript
{
public:
    PlayerbotAgentWorldScript() : WorldScript("PlayerbotAgentWorldScript",
        {
            WORLDHOOK_ON_BEFORE_WORLD_INITIALIZED,
            WORLDHOOK_ON_UPDATE
        })
    {
    }

    void OnBeforeWorldInitialized() override
    {
        AgentEnabled = sConfigMgr->GetOption<bool>("AgentPlayerbot.Enabled", true);
        ActionPollIntervalMs = sConfigMgr->GetOption<uint32>("AgentPlayerbot.ActionPollIntervalMs", 500);
        MaxReplyLength = sConfigMgr->GetOption<uint32>("AgentPlayerbot.MaxReplyLength", 220);
        TraceLog = sConfigMgr->GetOption<bool>("AgentPlayerbot.TraceLog", true);
        ContextRange = sConfigMgr->GetOption<float>("AgentPlayerbot.ContextRange", 45.0f);
        MaxNearbyHostiles = sConfigMgr->GetOption<uint32>("AgentPlayerbot.MaxNearbyHostiles", 8);
        CombatTelemetryEnabled = sConfigMgr->GetOption<bool>("AgentPlayerbot.CombatTelemetryEnabled", true);
        CombatIdleEndMs = sConfigMgr->GetOption<uint32>("AgentPlayerbot.CombatIdleEndMs", 5000);
        CombatMinDurationMs = sConfigMgr->GetOption<uint32>("AgentPlayerbot.CombatMinDurationMs", 3000);
        CombatTimelineLimit = sConfigMgr->GetOption<uint32>("AgentPlayerbot.CombatTimelineLimit", 80);

        if (!AgentEnabled)
        {
            LOG_INFO("module.playerbot_agent", "Playerbot Agent bridge disabled");
            return;
        }

        EnsureSchema();
        LOG_INFO("module.playerbot_agent",
                 "Playerbot Agent bridge enabled; action poll interval {} ms, trace={}, context range={}, max hostiles={}, combat telemetry={}",
                 ActionPollIntervalMs, TraceLog ? "on" : "off", ContextRange, MaxNearbyHostiles,
                 CombatTelemetryEnabled ? "on" : "off");
    }

    void OnUpdate(uint32 diff) override
    {
        if (!AgentEnabled || !SchemaReady)
            return;

        ActionPollElapsedMs += diff;
        if (ActionPollElapsedMs < ActionPollIntervalMs)
            return;

        ActionPollElapsedMs = 0;
        ProcessPendingActions();
        ProcessCombatSessions();
    }
};
}

void AddSC_playerbot_agent_bridge()
{
    new PlayerbotAgentPlayerScript();
    new PlayerbotAgentUnitScript();
    new PlayerbotAgentWorldScript();
}
