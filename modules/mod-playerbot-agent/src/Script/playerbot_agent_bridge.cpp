/*
 * Thin in-game bridge for the Playerbot LLM-Agent sidecar.
 *
 * The bridge deliberately exposes only a tiny, whitelisted surface:
 * - queue real-player chat events with current Playerbot context
 * - execute pending reply / command / strategy actions from the sidecar
 */

#include "Chat.h"
#include "Config.h"
#include "DatabaseEnv.h"
#include "Group.h"
#include "Log.h"
#include "ObjectAccessor.h"
#include "Player.h"
#include "PlayerScript.h"
#include "PlayerbotAI.h"
#include "PlayerbotMgr.h"
#include "Playerbots.h"
#include "ScriptMgr.h"
#include "WorldScript.h"
#include "WorldSession.h"

#include <algorithm>
#include <cctype>
#include <iomanip>
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
};

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

BotSnapshot MakeSnapshot(Player* bot)
{
    BotSnapshot snapshot;
    snapshot.guid = bot->GetGUID().GetCounter();
    snapshot.name = bot->GetName();
    snapshot.playerClass = bot->getClass();
    snapshot.level = bot->GetLevel();
    snapshot.role = DetectRole(bot);
    return snapshot;
}

void AddUniqueBot(std::vector<BotSnapshot>& bots, Player* bot)
{
    if (!IsPlayerbot(bot))
        return;

    uint32 guid = bot->GetGUID().GetCounter();
    auto existing = std::find_if(bots.begin(), bots.end(), [guid](BotSnapshot const& snapshot)
    {
        return snapshot.guid == guid;
    });

    if (existing == bots.end())
        bots.push_back(MakeSnapshot(bot));
}

std::vector<BotSnapshot> GetGroupBots(Group* group)
{
    std::vector<BotSnapshot> bots;
    if (!group)
        return bots;

    for (GroupReference* itr = group->GetFirstMember(); itr != nullptr; itr = itr->next())
    {
        if (Player* member = itr->GetSource())
            AddUniqueBot(bots, member);
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
            AddUniqueBot(bots, itr->second);
    }

    return bots;
}

std::string BotsToJson(std::vector<BotSnapshot> const& bots)
{
    std::ostringstream out;
    out << "{\"bots\":[";

    for (std::size_t i = 0; i < bots.size(); ++i)
    {
        BotSnapshot const& bot = bots[i];
        if (i)
            out << ",";

        out << "{\"guid\":" << bot.guid
            << ",\"name\":\"" << JsonEscape(bot.name) << "\""
            << ",\"class\":" << static_cast<uint32>(bot.playerClass)
            << ",\"level\":" << static_cast<uint32>(bot.level)
            << ",\"role\":\"" << JsonEscape(bot.role) << "\"}";
    }

    out << "]}";
    return out.str();
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
    std::string meta = BotsToJson(bots);

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
}

void ProcessAction(uint64 actionId, uint32 requesterGuid, std::string const& botName, uint32 botGuid,
                   std::string const& actionType, std::string const& channel, std::string const& text,
                   std::string const& command, std::string const& strategy, std::string const& botState)
{
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
            PLAYERHOOK_CAN_PLAYER_USE_GROUP_CHAT
        })
    {
    }

    bool OnPlayerCanUseChat(Player* player, uint32 type, uint32 /*language*/, std::string& msg) override
    {
        if (type != CHAT_MSG_SAY && type != CHAT_MSG_YELL)
            return true;

        std::vector<BotSnapshot> bots = GetOwnedBots(player);
        InsertChatEvent(player, type == CHAT_MSG_YELL ? "yell" : "say", type, msg, nullptr, nullptr, bots);
        return true;
    }

    bool OnPlayerCanUseChat(Player* player, uint32 type, uint32 /*language*/, std::string& msg, Player* receiver) override
    {
        if (type != CHAT_MSG_WHISPER || !IsPlayerbot(receiver))
            return true;

        std::vector<BotSnapshot> bots;
        AddUniqueBot(bots, receiver);
        InsertChatEvent(player, "whisper", type, msg, receiver, receiver ? receiver->GetGroup() : nullptr, bots);
        return true;
    }

    bool OnPlayerCanUseChat(Player* player, uint32 type, uint32 /*language*/, std::string& msg, Group* group) override
    {
        if (type != CHAT_MSG_PARTY && type != CHAT_MSG_PARTY_LEADER && type != CHAT_MSG_RAID &&
            type != CHAT_MSG_RAID_LEADER && type != CHAT_MSG_RAID_WARNING)
            return true;

        std::vector<BotSnapshot> bots = GetGroupBots(group);
        std::string channel = (type == CHAT_MSG_RAID || type == CHAT_MSG_RAID_LEADER || type == CHAT_MSG_RAID_WARNING)
            ? "raid"
            : "party";

        InsertChatEvent(player, channel, type, msg, nullptr, group, bots);
        return true;
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

        if (!AgentEnabled)
        {
            LOG_INFO("module.playerbot_agent", "Playerbot Agent bridge disabled");
            return;
        }

        EnsureSchema();
        LOG_INFO("module.playerbot_agent", "Playerbot Agent bridge enabled; action poll interval {} ms",
                 ActionPollIntervalMs);
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
    }
};
}

void AddSC_playerbot_agent_bridge()
{
    new PlayerbotAgentPlayerScript();
    new PlayerbotAgentWorldScript();
}
