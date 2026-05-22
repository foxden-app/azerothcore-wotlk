/*
 * Thin in-game bridge for the Playerbot LLM-Agent sidecar.
 *
 * The bridge deliberately exposes only a tiny, whitelisted surface:
 * - queue real-player chat events with current Playerbot context
 * - execute pending reply / command / strategy actions from the sidecar
 */

#include "Chat.h"
#include "AiFactory.h"
#include "CellImpl.h"
#include "CharacterCache.h"
#include "Config.h"
#include "Creature.h"
#include "DatabaseEnv.h"
#include "DBCStores.h"
#include "GridNotifiers.h"
#include "GridNotifiersImpl.h"
#include "Group.h"
#include "Item.h"
#include "Log.h"
#include "Map.h"
#include "ObjectAccessor.h"
#include "ObjectMgr.h"
#include "Player.h"
#include "PlayerScript.h"
#include "PlayerbotAI.h"
#include "PlayerbotAIConfig.h"
#include "PlayerbotMgr.h"
#include "Playerbots.h"
#include "RandomPlayerbotMgr.h"
#include "ScriptMgr.h"
#include "GameTime.h"
#include "SharedDefines.h"
#include "UnitScript.h"
#include "World.h"
#include "WorldScript.h"
#include "WorldSession.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <deque>
#include <iomanip>
#include <list>
#include <map>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

namespace
{
bool AgentEnabled = true;
bool SchemaReady = false;
uint32 ActionPollIntervalMs = 500;
uint32 ActionPollElapsedMs = 0;
uint32 MaxReplyLength = 0;
bool TraceLog = true;
float ContextRange = 45.0f;
uint32 MaxNearbyHostiles = 8;
bool CombatTelemetryEnabled = true;
uint32 CombatIdleEndMs = 5000;
uint32 CombatMinDurationMs = 3000;
uint32 CombatTimelineLimit = 80;
bool AnchorBotAutologin = true;
std::string AnchorBotName = "瓦小狸";
std::vector<std::string> AnchorBotAliases;
uint32 AnchorBotEnsureIntervalMs = 5000;
uint32 AnchorBotEnsureElapsedMs = 0;
uint32 AnchorBotGuidLow = 0;
bool AnchorBotMissingLogged = false;

bool IsPlayerbot(Player* player);

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

std::string TrimAscii(std::string value)
{
    auto isSpace = [](unsigned char c) { return std::isspace(c) != 0; };
    value.erase(value.begin(), std::find_if(value.begin(), value.end(), [&](unsigned char c) { return !isSpace(c); }));
    value.erase(std::find_if(value.rbegin(), value.rend(), [&](unsigned char c) { return !isSpace(c); }).base(), value.end());
    return value;
}

bool IsUtf8ContinuationByte(char c)
{
    return (static_cast<unsigned char>(c) & 0xC0) == 0x80;
}

size_t Utf8SafePrefixLength(std::string const& value, size_t maxBytes)
{
    size_t limit = std::min(maxBytes, value.size());
    while (limit > 0 && limit < value.size() && IsUtf8ContinuationByte(value[limit]))
        --limit;
    return limit;
}

std::string SoftLimitUtf8(std::string value, uint32 maxBytes)
{
    if (!maxBytes || value.size() <= maxBytes)
        return value;

    std::string const suffix = "...";
    if (maxBytes <= suffix.size())
        return suffix.substr(0, maxBytes);

    size_t limit = Utf8SafePrefixLength(value, maxBytes - suffix.size());

    if (!limit)
        return suffix;

    size_t preferred = value.find_last_of(" \t\r\n,.;:!?)]}", limit - 1);
    if (preferred != std::string::npos && preferred > limit / 2)
        limit = preferred + 1;

    limit = Utf8SafePrefixLength(value, limit);

    std::string clipped = TrimAscii(value.substr(0, limit));
    return clipped.empty() ? suffix : clipped + suffix;
}

std::vector<std::string> SplitUtf8ForChat(std::string value, size_t maxBytes)
{
    std::vector<std::string> chunks;
    value = TrimAscii(value);
    while (!value.empty())
    {
        if (value.size() <= maxBytes)
        {
            chunks.push_back(value);
            break;
        }

        size_t limit = Utf8SafePrefixLength(value, maxBytes);
        if (!limit)
            break;

        size_t preferred = value.find_last_of(" \t\r\n,.;:!?)]}", limit - 1);
        if (preferred != std::string::npos && preferred > maxBytes / 2)
            limit = preferred + 1;

        limit = Utf8SafePrefixLength(value, limit);
        std::string chunk = TrimAscii(value.substr(0, limit));
        if (!chunk.empty())
            chunks.push_back(chunk);

        value = TrimAscii(value.substr(limit));
    }

    return chunks;
}

bool ContainsAny(std::string const& value, std::vector<std::string> const& tokens)
{
    for (std::string const& token : tokens)
        if (!token.empty() && value.find(token) != std::string::npos)
            return true;

    return false;
}

std::vector<std::string> SplitConfigList(std::string value)
{
    std::vector<std::string> result;
    std::string current;
    for (char c : value)
    {
        if (c == ',' || c == ';')
        {
            current = TrimAscii(current);
            if (!current.empty())
                result.push_back(current);
            current.clear();
            continue;
        }

        current.push_back(c);
    }

    current = TrimAscii(current);
    if (!current.empty())
        result.push_back(current);

    return result;
}

std::vector<std::string> AnchorBotTokens()
{
    std::vector<std::string> tokens;
    if (!AnchorBotName.empty())
        tokens.push_back(AnchorBotName);
    for (std::string const& alias : AnchorBotAliases)
        if (!alias.empty() && std::find(tokens.begin(), tokens.end(), alias) == tokens.end())
            tokens.push_back(alias);
    return tokens;
}

bool MessageAddressesAnchor(std::string const& msg)
{
    return ContainsAny(msg, AnchorBotTokens());
}

ObjectGuid ResolveAnchorBotGuid()
{
    if (AnchorBotGuidLow)
        return ObjectGuid::Create<HighGuid::Player>(AnchorBotGuidLow);

    if (AnchorBotName.empty())
        return ObjectGuid::Empty;

    ObjectGuid guid = sCharacterCache->GetCharacterGuidByName(AnchorBotName);
    if (guid)
        AnchorBotGuidLow = guid.GetCounter();

    return guid;
}

bool IsAnchorBot(Player* player)
{
    if (!player)
        return false;

    ObjectGuid anchorGuid = ResolveAnchorBotGuid();
    return anchorGuid && player->GetGUID() == anchorGuid;
}

Player* GetOnlineAnchorBot()
{
    ObjectGuid anchorGuid = ResolveAnchorBotGuid();
    if (!anchorGuid)
        return nullptr;

    Player* bot = ObjectAccessor::FindConnectedPlayer(anchorGuid);
    return IsPlayerbot(bot) ? bot : nullptr;
}

void EnsureAnchorBotOnline()
{
    if (!AnchorBotAutologin || AnchorBotName.empty())
        return;

    ObjectGuid anchorGuid = ResolveAnchorBotGuid();
    if (!anchorGuid)
    {
        if (!AnchorBotMissingLogged)
        {
            LOG_WARN("module.playerbot_agent", "Anchor bot '{}' was not found in character cache", AnchorBotName);
            AnchorBotMissingLogged = true;
        }
        return;
    }

    AnchorBotMissingLogged = false;
    if (ObjectAccessor::FindConnectedPlayer(anchorGuid))
        return;

    LOG_INFO("module.playerbot_agent", "Ensuring anchor bot '{}' ({}) is online", AnchorBotName, anchorGuid.GetCounter());
    sRandomPlayerbotMgr.AddPlayerBot(anchorGuid, 0);
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

std::string JsonUnescape(std::string const& value)
{
    std::ostringstream out;
    for (std::size_t i = 0; i < value.size(); ++i)
    {
        if (value[i] != '\\' || i + 1 >= value.size())
        {
            out << value[i];
            continue;
        }

        char next = value[++i];
        switch (next)
        {
            case '"':
                out << '"';
                break;
            case '\\':
                out << '\\';
                break;
            case '/':
                out << '/';
                break;
            case 'b':
                out << '\b';
                break;
            case 'f':
                out << '\f';
                break;
            case 'n':
                out << '\n';
                break;
            case 'r':
                out << '\r';
                break;
            case 't':
                out << '\t';
                break;
            default:
                out << next;
                break;
        }
    }

    return out.str();
}

bool ExtractJsonString(std::string const& json, std::string const& key, std::string& value)
{
    value.clear();
    std::string needle = "\"" + key + "\"";
    std::size_t pos = json.find(needle);
    if (pos == std::string::npos)
        return false;

    pos = json.find(':', pos + needle.size());
    if (pos == std::string::npos)
        return false;

    ++pos;
    while (pos < json.size() && std::isspace(static_cast<unsigned char>(json[pos])))
        ++pos;

    if (json.compare(pos, 4, "null") == 0)
        return true;

    if (pos >= json.size() || json[pos] != '"')
        return false;

    ++pos;
    std::ostringstream raw;
    bool escaping = false;
    for (; pos < json.size(); ++pos)
    {
        char c = json[pos];
        if (escaping)
        {
            raw << '\\' << c;
            escaping = false;
            continue;
        }

        if (c == '\\')
        {
            escaping = true;
            continue;
        }

        if (c == '"')
        {
            value = JsonUnescape(raw.str());
            return true;
        }

        raw << c;
    }

    return false;
}

std::string JoinMessages(std::vector<std::string> const& messages)
{
    std::ostringstream out;
    for (std::size_t i = 0; i < messages.size(); ++i)
    {
        if (i)
            out << "\n";
        out << messages[i];
    }

    return out.str();
}

bool LooksLikeLifecycleRequest(std::string const& message)
{
    std::string lowered = ToLowerAscii(message);
    static std::vector<std::string> const lifecycleTokens = {
        "加个", "来个", "补个", "组个", "加一个", "来一个", "补一个",
        "机器人", "bot", "bots", "lookup", "list", "init", "refresh", "levelup",
        "初始化", "刷新", "同步等级", "升级", "副本", "下本", "下副本", "邀请", "进队", "入队"
    };

    return ContainsAny(lowered, lifecycleTokens);
}

struct BotSnapshot
{
    uint32 guid = 0;
    std::string name;
    std::string kind;
    uint8 playerClass = 0;
    uint8 race = 0;
    uint8 level = 0;
    uint32 team = 0;
    std::string role;
    std::string specName;
    int32 specTab = -1;
    std::string aiState;
    std::string strategyText;
    float healthPct = 0.0f;
    uint32 manaPct = 0;
    bool alive = false;
    bool combat = false;
    bool hasDistance = false;
    float distance = 0.0f;
    uint32 mapId = 0;
    uint32 zoneId = 0;
    uint32 areaId = 0;
    std::string mapName;
    std::string zoneName;
    std::string areaName;
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

LocaleConstant DefaultLocale()
{
    return sWorld ? sWorld->GetDefaultDbcLocale() : LOCALE_enUS;
}

std::string AreaName(uint32 areaId)
{
    AreaTableEntry const* area = sAreaTableStore.LookupEntry(areaId);
    if (!area)
        return "";

    char const* name = area->area_name[DefaultLocale()];
    if (!name || !*name)
        name = area->area_name[LOCALE_enUS];

    return name ? name : "";
}

std::string MapName(Player* player)
{
    if (!player)
        return "";

    if (Map* map = player->FindMap())
        return map->GetMapName();

    MapEntry const* entry = sMapStore.LookupEntry(player->GetMapId());
    if (!entry)
        return "";

    char const* name = entry->name[DefaultLocale()];
    if (!name || !*name)
        name = entry->name[LOCALE_enUS];

    return name ? name : "";
}

std::string LocationToJson(Player* player)
{
    if (!player)
        return "null";

    uint32 mapId = player->GetMapId();
    uint32 zoneId = player->GetZoneId();
    uint32 areaId = player->GetAreaId();

    std::ostringstream out;
    out << "{\"map_id\":" << mapId
        << ",\"map_name\":\"" << JsonEscape(MapName(player)) << "\""
        << ",\"zone_id\":" << zoneId
        << ",\"zone_name\":\"" << JsonEscape(AreaName(zoneId)) << "\""
        << ",\"area_id\":" << areaId
        << ",\"area_name\":\"" << JsonEscape(AreaName(areaId)) << "\""
        << ",\"x\":" << FloatString(player->GetPositionX())
        << ",\"y\":" << FloatString(player->GetPositionY())
        << ",\"z\":" << FloatString(player->GetPositionZ())
        << ",\"orientation\":" << FloatString(player->GetOrientation())
        << ",\"precision\":\"server_area_table\"}";
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

std::string StrategyListToJsonArray(std::string strategyText)
{
    std::string normalized = strategyText;
    std::string prefix = "Strategies:";
    if (normalized.size() >= prefix.size() && normalized.compare(0, prefix.size(), prefix) == 0)
        normalized = normalized.substr(prefix.size());

    std::ostringstream out;
    out << "[";
    bool first = true;
    std::stringstream stream(normalized);
    std::string item;
    while (std::getline(stream, item, ','))
    {
        item = TrimAscii(item);
        if (item.empty())
            continue;

        if (!first)
            out << ",";
        first = false;
        out << "\"" << JsonEscape(item) << "\"";
    }
    out << "]";
    return out.str();
}

std::string BotAiState(PlayerbotAI* botAI)
{
    return botAI ? botAI->HandleRemoteCommand("state") : "";
}

std::string BotStrategyText(PlayerbotAI* botAI)
{
    return botAI ? botAI->HandleRemoteCommand("strategy") : "";
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

bool IsPlayerbotAccount(ObjectGuid const& guid)
{
    uint32 accountId = sCharacterCache->GetCharacterAccountIdByGuid(guid);
    return accountId && sPlayerbotAIConfig.IsInRandomAccountList(accountId);
}

bool IsOwnedBot(Player* requester, Player* bot)
{
    if (!requester || !bot || !IsPlayerbot(bot))
        return false;

    if (PlayerbotMgr* mgr = GET_PLAYERBOT_MGR(requester))
        return mgr->GetPlayerBot(bot->GetGUID()) == bot;

    return false;
}

bool IsGroupedBot(Player* requester, Player* bot)
{
    return requester && bot && IsPlayerbot(bot) && requester->GetGroup() && bot->GetGroup() &&
           requester->GetGroup() == bot->GetGroup();
}

std::string ClassifyBotForSpeaker(Player* speaker, Player* bot)
{
    if (!IsPlayerbot(bot))
        return "";

    if (IsAnchorBot(bot))
        return "anchor_world";

    if (IsOwnedBot(speaker, bot))
        return "owned";

    if (IsGroupedBot(speaker, bot))
        return "group";

    if (sRandomPlayerbotMgr.IsRandomBot(bot))
        return "random_world";

    if (sRandomPlayerbotMgr.IsAddclassBot(bot))
        return "addclass_world";

    return "playerbot";
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

BotSnapshot MakeSnapshot(Player* bot, Player* reference = nullptr, std::string const& kind = "")
{
    BotSnapshot snapshot;
    snapshot.guid = bot->GetGUID().GetCounter();
    snapshot.name = bot->GetName();
    snapshot.kind = kind.empty() ? ClassifyBotForSpeaker(reference, bot) : kind;
    snapshot.playerClass = bot->getClass();
    snapshot.race = bot->getRace();
    snapshot.level = bot->GetLevel();
    snapshot.team = bot->GetTeamId();
    snapshot.role = DetectRole(bot);
    if (PlayerbotAI* botAI = GET_PLAYERBOT_AI(bot))
    {
        snapshot.specName = AiFactory::GetPlayerSpecName(bot);
        snapshot.specTab = AiFactory::GetPlayerSpecTab(bot);
        snapshot.aiState = BotAiState(botAI);
        snapshot.strategyText = BotStrategyText(botAI);
    }
    snapshot.healthPct = bot->GetHealthPct();
    snapshot.manaPct = ManaPct(bot);
    snapshot.alive = bot->IsAlive();
    snapshot.combat = bot->IsInCombat();
    snapshot.mapId = bot->GetMapId();
    snapshot.zoneId = bot->GetZoneId();
    snapshot.areaId = bot->GetAreaId();
    snapshot.mapName = MapName(bot);
    snapshot.zoneName = AreaName(snapshot.zoneId);
    snapshot.areaName = AreaName(snapshot.areaId);

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

void AddUniqueBot(std::vector<BotSnapshot>& bots, Player* bot, Player* reference = nullptr, std::string const& kind = "")
{
    if (!IsPlayerbot(bot))
        return;

    uint32 guid = bot->GetGUID().GetCounter();
    auto existing = std::find_if(bots.begin(), bots.end(), [guid](BotSnapshot const& snapshot)
    {
        return snapshot.guid == guid;
    });

    if (existing == bots.end())
        bots.push_back(MakeSnapshot(bot, reference, kind));
}

void AddAnchorBotIfOnline(std::vector<BotSnapshot>& bots, Player* reference)
{
    if (Player* anchor = GetOnlineAnchorBot())
        AddUniqueBot(bots, anchor, reference, "anchor_world");
}

std::vector<BotSnapshot> GetGroupBots(Group* group, Player* reference)
{
    std::vector<BotSnapshot> bots;
    if (!group)
        return bots;

    for (GroupReference* itr = group->GetFirstMember(); itr != nullptr; itr = itr->next())
    {
        if (Player* member = itr->GetSource())
            AddUniqueBot(bots, member, reference, ClassifyBotForSpeaker(reference, member));
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
            AddUniqueBot(bots, itr->second, master, "owned");
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

    PlayerbotAI* botAI = GET_PLAYERBOT_AI(player);
    bool isBot = botAI != nullptr;
    std::ostringstream out;
    out << "{\"guid\":" << player->GetGUID().GetCounter()
        << ",\"name\":\"" << JsonEscape(player->GetName()) << "\""
        << ",\"online\":true"
        << ",\"is_bot\":" << (isBot ? "true" : "false")
        << ",\"bot_kind\":\"" << JsonEscape(isBot ? ClassifyBotForSpeaker(reference, player) : "") << "\""
        << ",\"class\":" << static_cast<uint32>(player->getClass())
        << ",\"race\":" << static_cast<uint32>(player->getRace())
        << ",\"level\":" << static_cast<uint32>(player->GetLevel())
        << ",\"team\":" << static_cast<uint32>(player->GetTeamId())
        << ",\"role\":\"" << JsonEscape(isBot ? DetectRole(player) : "player") << "\"";

    if (isBot)
    {
        std::string strategyText = BotStrategyText(botAI);
        out << ",\"spec_name\":\"" << JsonEscape(AiFactory::GetPlayerSpecName(player)) << "\""
            << ",\"spec_tab\":" << static_cast<int32>(AiFactory::GetPlayerSpecTab(player))
            << ",\"ai_state\":\"" << JsonEscape(BotAiState(botAI)) << "\""
            << ",\"active_strategy_text\":\"" << JsonEscape(strategyText) << "\""
            << ",\"active_strategies\":" << StrategyListToJsonArray(strategyText);
    }

    out << ",\"health_pct\":" << FloatString(player->GetHealthPct())
        << ",\"mana_pct\":" << ManaPct(player)
        << ",\"alive\":" << (player->IsAlive() ? "true" : "false")
        << ",\"combat\":" << (player->IsInCombat() ? "true" : "false")
        << ",\"map_id\":" << player->GetMapId()
        << ",\"zone_id\":" << player->GetZoneId()
        << ",\"area_id\":" << player->GetAreaId()
        << ",\"location\":" << LocationToJson(player);

    if (reference && reference != player && reference->IsInMap(player))
        out << ",\"distance\":" << FloatString(reference->GetDistance(player));

    out << ",\"selected_target\":" << TargetToJson(player->GetSelectedUnit(), player)
        << ",\"combat_target\":" << TargetToJson(player->GetVictim(), player)
        << "}";
    return out.str();
}

std::string OfflineGroupMemberToJson(Group::MemberSlot const& slot)
{
    CharacterCacheEntry const* cache = sCharacterCache->GetCharacterCacheByGuid(slot.guid);
    std::string name = !slot.name.empty() ? slot.name : (cache ? cache->Name : "");
    bool isBotAccount = IsPlayerbotAccount(slot.guid);

    std::ostringstream out;
    out << "{\"guid\":" << slot.guid.GetCounter()
        << ",\"name\":\"" << JsonEscape(name) << "\""
        << ",\"online\":false"
        << ",\"offline_in_group\":true"
        << ",\"is_bot\":" << (isBotAccount ? "true" : "false")
        << ",\"bot_kind\":\"" << (isBotAccount ? "group_offline" : "") << "\""
        << ",\"group_subgroup\":" << static_cast<uint32>(slot.group)
        << ",\"group_flags\":" << static_cast<uint32>(slot.flags)
        << ",\"group_roles\":" << static_cast<uint32>(slot.roles);

    if (cache)
    {
        out << ",\"class\":" << static_cast<uint32>(cache->Class)
            << ",\"race\":" << static_cast<uint32>(cache->Race)
            << ",\"level\":" << static_cast<uint32>(cache->Level)
            << ",\"team\":" << static_cast<uint32>(Player::TeamIdForRace(cache->Race));
    }

    out << "}";
    return out.str();
}

std::string GroupMembersToJson(Player* speaker, Group* group)
{
    std::ostringstream out;
    out << "[";
    bool first = true;

    if (group)
    {
        for (Group::MemberSlot const& slot : group->GetMemberSlots())
        {
            if (!first)
                out << ",";
            first = false;

            if (Player* member = ObjectAccessor::FindConnectedPlayer(slot.guid))
                out << PlayerContextToJson(member, speaker);
            else
                out << OfflineGroupMemberToJson(slot);
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
            << ",\"bot_kind\":\"" << JsonEscape(bot.kind) << "\""
            << ",\"class\":" << static_cast<uint32>(bot.playerClass)
            << ",\"race\":" << static_cast<uint32>(bot.race)
            << ",\"level\":" << static_cast<uint32>(bot.level)
            << ",\"team\":" << bot.team
            << ",\"role\":\"" << JsonEscape(bot.role) << "\""
            << ",\"spec_name\":\"" << JsonEscape(bot.specName) << "\""
            << ",\"spec_tab\":" << bot.specTab
            << ",\"ai_state\":\"" << JsonEscape(bot.aiState) << "\""
            << ",\"active_strategy_text\":\"" << JsonEscape(bot.strategyText) << "\""
            << ",\"active_strategies\":" << StrategyListToJsonArray(bot.strategyText)
            << ",\"health_pct\":" << FloatString(bot.healthPct)
            << ",\"mana_pct\":" << bot.manaPct
            << ",\"alive\":" << (bot.alive ? "true" : "false")
            << ",\"combat\":" << (bot.combat ? "true" : "false")
            << ",\"map_id\":" << bot.mapId
            << ",\"zone_id\":" << bot.zoneId
            << ",\"area_id\":" << bot.areaId
            << ",\"map_name\":\"" << JsonEscape(bot.mapName) << "\""
            << ",\"zone_name\":\"" << JsonEscape(bot.zoneName) << "\""
            << ",\"area_name\":\"" << JsonEscape(bot.areaName) << "\"";

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

std::string EventMetaToJson(Player* speaker, Group* group, std::vector<BotSnapshot> const& bots, Player* receiver)
{
    std::ostringstream out;
    out << "{\"speaker\":" << PlayerContextToJson(speaker, speaker)
        << ",\"environment\":{"
        << "\"map_id\":" << (speaker ? speaker->GetMapId() : 0)
        << ",\"zone_id\":" << (speaker ? speaker->GetZoneId() : 0)
        << ",\"area_id\":" << (speaker ? speaker->GetAreaId() : 0)
        << ",\"location\":" << LocationToJson(speaker)
        << ",\"selected_target\":" << (speaker ? TargetToJson(speaker->GetSelectedUnit(), speaker) : "null")
        << ",\"combat_target\":" << (speaker ? TargetToJson(speaker->GetVictim(), speaker) : "null")
        << ",\"nearby_hostiles\":" << NearbyHostilesToJson(speaker)
        << "},\"group_members\":" << GroupMembersToJson(speaker, group)
        << ",\"target_bot_context\":" << (IsPlayerbot(receiver) ? PlayerContextToJson(receiver, speaker) : "null")
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
        "`payload_json` TEXT NULL DEFAULT NULL,"
        "`result` TEXT NULL DEFAULT NULL,"
        "`error` TEXT NULL DEFAULT NULL,"
        "PRIMARY KEY (`id`),"
        "KEY `idx_agent_playerbot_actions_status` (`status`, `available_at`, `id`),"
        "KEY `idx_agent_playerbot_actions_event` (`source_event_id`)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");

    QueryResult payloadColumn = PlayerbotsDatabase.Query(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'agent_playerbot_actions' "
        "AND COLUMN_NAME = 'payload_json'");
    if (!payloadColumn || payloadColumn->Fetch()[0].Get<uint64>() == 0)
    {
        PlayerbotsDatabase.DirectExecute(
            "ALTER TABLE `agent_playerbot_actions` ADD COLUMN `payload_json` TEXT NULL DEFAULT NULL AFTER `bot_state`");
    }

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
                     Player* receiver, Group* group, std::vector<BotSnapshot> bots, bool allowEmptyBots = false)
{
    if (!AgentEnabled || !SchemaReady || !IsRealPlayer(speaker) || (!allowEmptyBots && bots.empty()))
        return;

    Player* targetBot = IsPlayerbot(receiver) ? receiver : nullptr;
    uint32 targetGuid = receiver ? receiver->GetGUID().GetCounter() : 0;
    std::string targetName = receiver ? receiver->GetName() : "";
    uint32 groupLeaderGuid = group ? group->GetLeaderGUID().GetCounter() : 0;
    uint32 botGuid = targetBot ? targetBot->GetGUID().GetCounter() : 0;
    std::string botName = targetBot ? targetBot->GetName() : "";
    uint32 accountId = speaker->GetSession() ? speaker->GetSession()->GetAccountId() : 0;
    std::string meta = EventMetaToJson(speaker, group, bots, targetBot);

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

    return IsOwnedBot(requester, bot) || IsGroupedBot(requester, bot);
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

Player* FindOnlinePlayerbot(uint32 botGuid, std::string const& botName)
{
    if (botGuid)
    {
        if (Player* bot = ObjectAccessor::FindPlayer(ObjectGuid::Create<HighGuid::Player>(botGuid)))
            if (IsPlayerbot(bot))
                return bot;
    }

    std::string wanted = ToLowerAscii(botName);
    if (wanted.empty())
        return nullptr;

    PlayerBotMap bots = sRandomPlayerbotMgr.GetAllBots();
    for (PlayerBotMap::const_iterator itr = bots.begin(); itr != bots.end(); ++itr)
    {
        Player* bot = itr->second;
        if (bot && ToLowerAscii(bot->GetName()) == wanted && IsPlayerbot(bot))
            return bot;
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

bool IsSafePlayerbotCommandLine(std::string const& value)
{
    if (value.empty() || value.size() > 512)
        return false;

    for (unsigned char c : value)
    {
        if (c < 0x20 || c == 0x7f)
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
        "affli", "aoe", "arcane", "arms", "baoe", "barmor", "bear", "bcast", "bdps", "bhealth",
        "blood", "bm", "bmana", "boost", "bspeed", "bstats", "bthreat", "buff", "caster", "caster aoe",
        "caster debuff", "cat", "cat aoe", "cc", "cleansing", "cure", "demo", "destro", "dps", "dps debuff",
        "earthbind", "ele", "enh", "felguard", "felhunter", "fire", "firestarter", "flametongue", "frost",
        "frost aoe", "frostfire", "fury", "heal", "healer dps", "healing stream", "holy dps", "holy heal",
        "imp", "loot", "magma", "mana spring", "melee", "meta melee", "mm", "nc", "offheal", "pet", "pull",
        "resto", "rfire", "rfrost", "rnature", "rshadow", "shadow", "shadow aoe", "shadow debuff", "searing",
        "ss healer", "ss master", "ss self", "ss tank", "stealth", "stealthed", "stoneskin",
        "strength of earth", "succubus", "surv", "tank", "trap weave", "tremor", "unholy",
        "unholy aoe", "voidwalker", "windfury", "wrath", "wrath of air"
    };

    std::string normalized = ToLowerAscii(strategy);
    normalized = TrimAscii(normalized);
    if (normalized.empty() || (normalized[0] != '+' && normalized[0] != '-'))
        return false;

    std::string base = TrimAscii(normalized.substr(1));
    return std::find(allowedStrategies.begin(), allowedStrategies.end(), base) != allowedStrategies.end();
}

bool IsAllowedBotClass(std::string const& className)
{
    static std::vector<std::string> const classes = {
        "warrior", "paladin", "hunter", "rogue", "priest", "shaman", "mage", "warlock", "druid", "dk"
    };

    return std::find(classes.begin(), classes.end(), className) != classes.end();
}

std::string NormalizeClassHint(std::string classHint, std::string const& role)
{
    classHint = ToLowerAscii(classHint);
    if (classHint == "death_knight" || classHint == "death knight")
        classHint = "dk";

    if (IsAllowedBotClass(classHint))
        return classHint;

    std::string normalizedRole = ToLowerAscii(role);
    if (normalizedRole == "healer" || normalizedRole == "heal")
        return "priest";
    if (normalizedRole == "tank")
        return "warrior";
    if (normalizedRole == "melee_dps")
        return "rogue";

    return "mage";
}

bool IsAllowedGender(std::string const& gender)
{
    std::string normalized = ToLowerAscii(gender);
    return normalized.empty() || normalized == "male" || normalized == "female" || normalized == "0" || normalized == "1";
}

std::string PayloadValue(std::string const& payloadJson, std::string const& key)
{
    std::string value;
    ExtractJsonString(payloadJson, key, value);
    return value;
}

std::string PayloadFirstValue(std::string const& payloadJson, std::vector<std::string> const& keys)
{
    for (std::string const& key : keys)
    {
        std::string value = PayloadValue(payloadJson, key);
        if (!value.empty())
            return value;
    }

    return "";
}

uint32 PayloadUIntValue(std::string const& payloadJson, std::string const& key, uint32 defaultValue)
{
    std::string value = PayloadValue(payloadJson, key);
    if (value.empty())
        return defaultValue;

    char* end = nullptr;
    unsigned long parsed = std::strtoul(value.c_str(), &end, 10);
    if (end == value.c_str())
        return defaultValue;

    return static_cast<uint32>(parsed);
}

struct ConsumableTier
{
    uint8 requiredLevel;
    uint32 itemId;
};

uint32 SelectConsumableItem(std::vector<ConsumableTier> const& tiers, uint8 maxLevel)
{
    uint32 selected = 0;
    for (ConsumableTier const& tier : tiers)
    {
        if (tier.requiredLevel <= maxLevel)
            selected = tier.itemId;
    }

    return selected;
}

std::string ItemName(uint32 itemId)
{
    ItemTemplate const* proto = sObjectMgr->GetItemTemplate(itemId);
    if (!proto)
        return std::to_string(itemId);

    return proto->Name1;
}

bool StoreConsumable(Player* receiver, uint32 itemId, uint32 count, std::string& error)
{
    if (!receiver || !itemId || !count)
        return true;

    if (!sObjectMgr->GetItemTemplate(itemId))
    {
        error = "consumable item template not found";
        return false;
    }

    ItemPosCountVec dest;
    InventoryResult msg = receiver->CanStoreNewItem(NULL_BAG, NULL_SLOT, dest, itemId, count);
    if (msg != EQUIP_ERR_OK)
    {
        error = "target player cannot store consumables; bags may be full";
        return false;
    }

    receiver->StoreNewItem(dest, itemId, true, Item::GenerateItemRandomPropertyId(itemId));
    SQLTransaction<CharacterDatabaseConnection> trans = CharacterDatabase.BeginTransaction();
    receiver->SaveInventoryAndGoldToDB(trans);
    CharacterDatabase.CommitTransaction(trans);
    return true;
}

void CompleteAction(uint64 actionId, bool success, std::string const& result, std::string const& error);

bool ProcessProvideConsumables(uint64 actionId, Player* requester, std::string const& botName,
                               std::string const& payloadJson)
{
    if (!requester)
    {
        CompleteAction(actionId, false, "", "requester is not online");
        return true;
    }

    Player* bot = FindControlledBot(requester, 0, botName);
    if (!bot)
    {
        CompleteAction(actionId, false, "", "mage bot is not online or not controllable by requester");
        return true;
    }

    if (bot->getClass() != CLASS_MAGE)
    {
        CompleteAction(actionId, false, "", "target bot is not a mage");
        return true;
    }

    if (!bot->IsAlive())
    {
        CompleteAction(actionId, false, "", "mage bot is dead");
        return true;
    }

    if (bot->IsInCombat())
    {
        CompleteAction(actionId, false, "", "mage bot is in combat");
        return true;
    }

    std::string targetName = PayloadFirstValue(payloadJson, {"target_player", "player", "target"});
    Player* receiver = nullptr;
    if (targetName.empty() || ToLowerAscii(targetName) == ToLowerAscii(requester->GetName()))
        receiver = requester;
    else
        receiver = ObjectAccessor::FindPlayerByName(targetName, false);

    if (!receiver)
    {
        CompleteAction(actionId, false, "", "target player is not online");
        return true;
    }

    if (receiver != requester && (!requester->GetGroup() || requester->GetGroup() != receiver->GetGroup()))
    {
        CompleteAction(actionId, false, "", "target player is not in requester's group");
        return true;
    }

    if (!bot->IsInMap(receiver) || bot->GetDistance(receiver) > ContextRange)
    {
        CompleteAction(actionId, false, "", "target player is not near the mage bot");
        return true;
    }

    uint32 waterStacks = std::min<uint32>(PayloadUIntValue(payloadJson, "water_stacks", 1), 5);
    uint32 foodStacks = std::min<uint32>(PayloadUIntValue(payloadJson, "food_stacks", 1), 5);
    if (!waterStacks && !foodStacks)
    {
        CompleteAction(actionId, false, "", "empty consumable request");
        return true;
    }

    static std::vector<ConsumableTier> const waterTiers = {
        {1, 5350}, {5, 2288}, {15, 2136}, {25, 3772}, {35, 8077},
        {45, 8078}, {55, 8079}, {60, 30703}, {65, 22018}
    };
    static std::vector<ConsumableTier> const foodTiers = {
        {1, 5349}, {5, 1113}, {15, 1114}, {25, 1487}, {35, 8075},
        {45, 8076}, {55, 22895}, {65, 22019}, {74, 43518}, {80, 43523}
    };

    uint8 maxConsumableLevel = std::min<uint8>(bot->GetLevel(), receiver->GetLevel());
    uint32 waterItem = SelectConsumableItem(waterTiers, maxConsumableLevel);
    uint32 foodItem = SelectConsumableItem(foodTiers, maxConsumableLevel);

    std::string error;
    uint32 waterCount = waterStacks * 20;
    uint32 foodCount = foodStacks * 20;

    if (waterCount && !StoreConsumable(receiver, waterItem, waterCount, error))
    {
        CompleteAction(actionId, false, "", error);
        return true;
    }

    if (foodCount && !StoreConsumable(receiver, foodItem, foodCount, error))
    {
        CompleteAction(actionId, false, "", error);
        return true;
    }

    std::ostringstream result;
    result << bot->GetName() << " provided ";
    bool hasPrevious = false;
    if (waterCount)
    {
        result << waterCount << " x " << ItemName(waterItem);
        hasPrevious = true;
    }
    if (foodCount)
    {
        if (hasPrevious)
            result << ", ";
        result << foodCount << " x " << ItemName(foodItem);
    }
    result << " to " << receiver->GetName();
    CompleteAction(actionId, true, result.str(), "");
    return true;
}

bool RunPlayerbotMgrCommand(Player* requester, std::string const& args, std::string& result, std::string& error)
{
    result.clear();
    error.clear();

    if (!requester)
    {
        error = "requester is not online";
        return false;
    }

    PlayerbotMgr* mgr = GET_PLAYERBOT_MGR(requester);
    if (!mgr)
    {
        error = "requester cannot control playerbots";
        return false;
    }

    std::vector<char> buffer(args.begin(), args.end());
    buffer.push_back('\0');
    std::vector<std::string> messages = mgr->HandlePlayerbotCommand(buffer.data(), requester);
    result = JoinMessages(messages);
    if (result.empty())
        result = "ok";

    std::string normalized = ToLowerAscii(result);
    if (normalized.find("error") != std::string::npos || normalized.find("failed") != std::string::npos ||
        normalized.find("not found") != std::string::npos || normalized.find("not allowed") != std::string::npos ||
        normalized.find("permission") != std::string::npos || normalized.find("unknown command") != std::string::npos ||
        normalized.find("unknown gender") != std::string::npos || normalized.find("invalid") != std::string::npos ||
        normalized.find("disabled") != std::string::npos || normalized.find("too low") != std::string::npos ||
        normalized.find("can not") != std::string::npos || normalized.find("cannot") != std::string::npos ||
        normalized.find("already in progress") != std::string::npos)
    {
        error = result;
        return false;
    }

    return true;
}

bool IsTypedAction(std::string const& normalizedType)
{
    static std::vector<std::string> const typedActions = {
        "summon_bot", "init_bot", "dismiss_bot", "list_bots", "lookup_bot_pool",
        "refresh_bot", "level_bot", "init_instance_quests", "invite_player", "playerbot_command",
        "provide_consumables"
    };

    return std::find(typedActions.begin(), typedActions.end(), normalizedType) != typedActions.end();
}

void CompleteAction(uint64 actionId, bool success, std::string const& result, std::string const& error);

bool ProcessTypedAction(uint64 actionId, Player* requester, std::string const& normalizedType,
                        std::string const& botName, std::string const& payloadJson)
{
    std::string result;
    std::string error;

    if (normalizedType == "provide_consumables")
        return ProcessProvideConsumables(actionId, requester, botName, payloadJson);

    if (normalizedType == "playerbot_command")
    {
        std::string commandLine = TrimAscii(PayloadFirstValue(payloadJson, {"command_line", "command", "args"}));
        std::string lowered = ToLowerAscii(commandLine);
        if (StartsWith(lowered, ".playerbots"))
        {
            commandLine = TrimAscii(commandLine.substr(std::string(".playerbots").size()));
            lowered = ToLowerAscii(commandLine);
        }
        if (StartsWith(lowered, ".bot"))
        {
            commandLine = TrimAscii(commandLine.substr(std::string(".bot").size()));
            lowered = ToLowerAscii(commandLine);
        }
        if (lowered == "bot" || StartsWith(lowered, "bot "))
            commandLine = TrimAscii(commandLine.size() > 3 ? commandLine.substr(3) : "");

        if (!IsSafePlayerbotCommandLine(commandLine))
        {
            CompleteAction(actionId, false, "", "playerbot command line is invalid");
            return true;
        }

        if (!RunPlayerbotMgrCommand(requester, commandLine, result, error))
            CompleteAction(actionId, false, "", error);
        else
            CompleteAction(actionId, true, result, "");
        return true;
    }

    if (normalizedType == "summon_bot")
    {
        std::string role = PayloadValue(payloadJson, "role");
        std::string className = NormalizeClassHint(PayloadValue(payloadJson, "class_hint"), role);
        std::string gender = PayloadValue(payloadJson, "gender");
        if (!IsAllowedGender(gender))
        {
            CompleteAction(actionId, false, "", "gender is invalid");
            return true;
        }

        std::string args = "addclass " + className;
        if (!gender.empty())
            args += " " + gender;

        if (!RunPlayerbotMgrCommand(requester, args, result, error))
            CompleteAction(actionId, false, "", error);
        else
            CompleteAction(actionId, true, result, "");
        return true;
    }

    if (normalizedType == "init_bot")
    {
        std::string targetBot = PayloadFirstValue(payloadJson, {"bot", "target", "bot_name"});
        if (targetBot.empty())
            targetBot = botName;

        std::string mode = ToLowerAscii(PayloadValue(payloadJson, "mode"));
        if (mode.empty())
            mode = "auto";

        if (mode != "auto")
        {
            CompleteAction(actionId, false, "", "only init mode auto is allowed");
            return true;
        }

        if (!IsSafeCommandParam(targetBot))
        {
            CompleteAction(actionId, false, "", "bot name is invalid");
            return true;
        }

        if (!RunPlayerbotMgrCommand(requester, "init=auto " + targetBot, result, error))
            CompleteAction(actionId, false, "", error);
        else
            CompleteAction(actionId, true, result, "");
        return true;
    }

    if (normalizedType == "dismiss_bot")
    {
        std::string targetBot = PayloadFirstValue(payloadJson, {"bot", "target", "bot_name"});
        if (targetBot.empty())
            targetBot = botName;

        if (targetBot == "*")
        {
            CompleteAction(actionId, false, "", "dismiss all requires explicit confirmation and is not exposed");
            return true;
        }

        if (!IsSafeCommandParam(targetBot))
        {
            CompleteAction(actionId, false, "", "bot name is invalid");
            return true;
        }

        if (!RunPlayerbotMgrCommand(requester, "remove " + targetBot, result, error))
            CompleteAction(actionId, false, "", error);
        else
            CompleteAction(actionId, true, result, "");
        return true;
    }

    if (normalizedType == "list_bots")
    {
        if (!RunPlayerbotMgrCommand(requester, "list", result, error))
            CompleteAction(actionId, false, "", error);
        else
            CompleteAction(actionId, true, result, "");
        return true;
    }

    if (normalizedType == "lookup_bot_pool")
    {
        if (!RunPlayerbotMgrCommand(requester, "lookup", result, error))
            CompleteAction(actionId, false, "", error);
        else
            CompleteAction(actionId, true, result, "");
        return true;
    }

    if (normalizedType == "refresh_bot" || normalizedType == "level_bot" || normalizedType == "init_instance_quests")
    {
        std::string targetBot = PayloadFirstValue(payloadJson, {"bot", "target", "bot_name"});
        if (targetBot.empty())
            targetBot = botName;

        if (!IsSafeCommandParam(targetBot))
        {
            CompleteAction(actionId, false, "", "bot name is invalid");
            return true;
        }

        std::string commandName;
        if (normalizedType == "refresh_bot")
            commandName = "refresh";
        else if (normalizedType == "level_bot")
            commandName = "levelup";
        else
            commandName = "quests";

        if (!RunPlayerbotMgrCommand(requester, commandName + " " + targetBot, result, error))
            CompleteAction(actionId, false, "", error);
        else
            CompleteAction(actionId, true, result, "");
        return true;
    }

    if (normalizedType == "invite_player")
    {
        std::string targetName = PayloadFirstValue(payloadJson, {"target_player", "player", "name"});
        if (!IsSafeCommandParam(targetName))
        {
            CompleteAction(actionId, false, "", "target player name is invalid");
            return true;
        }

        if (!requester || !requester->GetSession())
        {
            CompleteAction(actionId, false, "", "requester is not online");
            return true;
        }

        Player* target = ObjectAccessor::FindPlayerByName(targetName, false);
        if (!target)
        {
            CompleteAction(actionId, false, "", "target player is not online");
            return true;
        }

        if (target == requester)
        {
            CompleteAction(actionId, false, "", "cannot invite self");
            return true;
        }

        if (target->GetGroup() || target->GetGroupInvite())
        {
            CompleteAction(actionId, false, "", "target player is already grouped or invited");
            return true;
        }

        if (!requester->IsGameMaster() && requester->GetTeamId() != target->GetTeamId())
        {
            CompleteAction(actionId, false, "", "target player is wrong faction");
            return true;
        }

        if (Group* group = requester->GetGroup())
        {
            if (!group->IsLeader(requester->GetGUID()) && !group->IsAssistant(requester->GetGUID()))
            {
                CompleteAction(actionId, false, "", "requester is not group leader or assistant");
                return true;
            }

            if (group->IsFull())
            {
                CompleteAction(actionId, false, "", "group is full");
                return true;
            }
        }

        WorldPacket packet;
        packet << target->GetName();
        packet << uint32(0);
        requester->GetSession()->HandleGroupInviteOpcode(packet);
        CompleteAction(actionId, true, "invite sent to " + target->GetName(), "");
        return true;
    }

    return false;
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

bool SendBotReplyChunk(PlayerbotAI* botAI, Player* requester, std::string const& normalized, std::string const& text)
{
    if (normalized == "party" || normalized == "raid")
    {
        Player* bot = botAI->GetBot();
        if (IsAnchorBot(bot) && (!requester->GetGroup() || !bot->GetGroup() || bot->GetGroup() != requester->GetGroup()))
            return botAI->Whisper(text, requester->GetName());
        return botAI->SayToParty(text) || botAI->TellMaster(text) || botAI->Whisper(text, requester->GetName());
    }

    if (normalized == "say")
    {
        Player* bot = botAI->GetBot();
        if (IsAnchorBot(bot) && (!bot->IsInMap(requester) || bot->GetDistance(requester) > ContextRange))
            return botAI->Whisper(text, requester->GetName());
        return botAI->Say(text);
    }

    return botAI->Whisper(text, requester->GetName());
}

bool SendBotReply(PlayerbotAI* botAI, Player* requester, std::string const& channel, std::string text)
{
    if (!botAI || !requester || text.empty())
        return false;

    text = SoftLimitUtf8(text, MaxReplyLength);

    std::string normalized = ToLowerAscii(channel);
    std::vector<std::string> chunks = SplitUtf8ForChat(text, 220);
    if (chunks.empty())
        return false;

    for (std::string const& chunk : chunks)
        if (!SendBotReplyChunk(botAI, requester, normalized, chunk))
            return false;

    return true;
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
                   std::string const& command, std::string const& strategy, std::string const& botState,
                   std::string const& payloadJson)
{
    if (TraceLog)
        LOG_INFO("module.playerbot_agent",
                 "Executing action {} type={} bot={}({}) channel={} command={} strategy={} bot_state={} payload={} text={}",
                 actionId, actionType, botName, botGuid, channel, command, strategy, botState, payloadJson, text);

    Player* requester = ObjectAccessor::FindConnectedPlayer(ObjectGuid::Create<HighGuid::Player>(requesterGuid));
    if (!requester)
    {
        CompleteAction(actionId, false, "", "requester is not online");
        return;
    }

    std::string normalizedType = ToLowerAscii(actionType);
    if (IsTypedAction(normalizedType))
    {
        if (!ProcessTypedAction(actionId, requester, normalizedType, botName, payloadJson))
            CompleteAction(actionId, false, "", "typed action failed");
        return;
    }

    Player* bot = normalizedType == "reply" ? FindOnlinePlayerbot(botGuid, botName)
                                            : FindControlledBot(requester, botGuid, botName);
    if (!bot)
    {
        CompleteAction(actionId, false, "",
                       normalizedType == "reply" ? "bot is not online" : "bot is not online or not controllable by requester");
        return;
    }

    PlayerbotAI* botAI = GET_PLAYERBOT_AI(bot);
    if (!botAI)
    {
        CompleteAction(actionId, false, "", "target player has no PlayerbotAI");
        return;
    }

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
        "`text`, `command`, `strategy`, `bot_state`, `payload_json` "
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
        std::string payloadJson = fields[10].IsNull() ? "" : fields[10].Get<std::string>();

        PlayerbotsDatabase.Execute(
            "UPDATE `agent_playerbot_actions` SET `status` = 'running', `updated_at` = NOW() "
            "WHERE `id` = {} AND `status` = 'pending'",
            actionId);

        ProcessAction(actionId, requesterGuid, botName, botGuid, actionType, channel, text, command, strategy, botState,
                      payloadJson);
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
        bool addressedAnchor = MessageAddressesAnchor(msg);
        if (addressedAnchor)
            AddAnchorBotIfOnline(bots, player);

        if (!bots.empty() || LooksLikeLifecycleRequest(msg) || addressedAnchor)
            InsertChatEvent(player, type == CHAT_MSG_YELL ? "yell" : "say", type, msg, nullptr, nullptr, bots,
                            bots.empty());
        return true;
    }

    bool OnPlayerCanUseChat(Player* player, uint32 type, uint32 language, std::string& msg, Player* receiver) override
    {
        if (language == LANG_ADDON)
            return true;

        if (type != CHAT_MSG_WHISPER || !IsPlayerbot(receiver))
            return true;

        std::vector<BotSnapshot> bots;
        AddUniqueBot(bots, receiver, player, ClassifyBotForSpeaker(player, receiver));
        Group* contextGroup = player->GetGroup() ? player->GetGroup() : (receiver ? receiver->GetGroup() : nullptr);
        InsertChatEvent(player, "whisper", type, msg, receiver, contextGroup, bots);
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

        if (!bots.empty() || LooksLikeLifecycleRequest(msg))
            InsertChatEvent(player, channel, type, msg, nullptr, group, bots, bots.empty());
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
        MaxReplyLength = sConfigMgr->GetOption<uint32>("AgentPlayerbot.MaxReplyLength", 0);
        TraceLog = sConfigMgr->GetOption<bool>("AgentPlayerbot.TraceLog", true);
        ContextRange = sConfigMgr->GetOption<float>("AgentPlayerbot.ContextRange", 45.0f);
        MaxNearbyHostiles = sConfigMgr->GetOption<uint32>("AgentPlayerbot.MaxNearbyHostiles", 8);
        CombatTelemetryEnabled = sConfigMgr->GetOption<bool>("AgentPlayerbot.CombatTelemetryEnabled", true);
        CombatIdleEndMs = sConfigMgr->GetOption<uint32>("AgentPlayerbot.CombatIdleEndMs", 5000);
        CombatMinDurationMs = sConfigMgr->GetOption<uint32>("AgentPlayerbot.CombatMinDurationMs", 3000);
        CombatTimelineLimit = sConfigMgr->GetOption<uint32>("AgentPlayerbot.CombatTimelineLimit", 80);
        AnchorBotAutologin = sConfigMgr->GetOption<bool>("AgentPlayerbot.AnchorBotAutologin", true);
        AnchorBotName = sConfigMgr->GetOption<std::string>("AgentPlayerbot.AnchorBotName", "瓦小狸");
        AnchorBotAliases = SplitConfigList(sConfigMgr->GetOption<std::string>("AgentPlayerbot.AnchorBotAliases", "小狸"));
        AnchorBotEnsureIntervalMs = std::max<uint32>(
            1000, sConfigMgr->GetOption<uint32>("AgentPlayerbot.AnchorBotEnsureIntervalMs", 5000));
        AnchorBotEnsureElapsedMs = AnchorBotEnsureIntervalMs;
        AnchorBotGuidLow = 0;
        AnchorBotMissingLogged = false;

        if (!AgentEnabled)
        {
            LOG_INFO("module.playerbot_agent", "Playerbot Agent bridge disabled");
            return;
        }

        EnsureSchema();
        LOG_INFO("module.playerbot_agent",
                 "Playerbot Agent bridge enabled; action poll interval {} ms, trace={}, context range={}, max hostiles={}, combat telemetry={}, max reply bytes={}, anchor={} autologin={}",
                 ActionPollIntervalMs, TraceLog ? "on" : "off", ContextRange, MaxNearbyHostiles,
                 CombatTelemetryEnabled ? "on" : "off", MaxReplyLength, AnchorBotName, AnchorBotAutologin ? "on" : "off");
    }

    void OnUpdate(uint32 diff) override
    {
        if (!AgentEnabled || !SchemaReady)
            return;

        if (AnchorBotAutologin)
        {
            AnchorBotEnsureElapsedMs += diff;
            if (AnchorBotEnsureElapsedMs >= AnchorBotEnsureIntervalMs)
            {
                AnchorBotEnsureElapsedMs = 0;
                EnsureAnchorBotOnline();
            }
        }

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
