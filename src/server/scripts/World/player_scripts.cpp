/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
 * more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program. If not, see <http://www.gnu.org/licenses/>.
 */

#include "Player.h"
#include "PlayerScript.h"

enum HunterStableWhistle
{
    ITEM_LEGACY_HUNTER_STABLE_WHISTLE = 900001,
    ITEM_HUNTER_STABLE_WHISTLE = 900002,
    HUNTER_STABLE_WHISTLE_REQUIRED_LEVEL = 10
};

enum ApprenticeAnglerQuestEnum
{
    QUEST_APPRENTICE_ANGLER = 8194
};

class HunterStableWhistlePlayerScript : public PlayerScript
{
public:
    HunterStableWhistlePlayerScript() : PlayerScript("HunterStableWhistlePlayerScript", { PLAYERHOOK_ON_LOGIN, PLAYERHOOK_ON_LEVEL_CHANGED })
    {
    }

    void OnPlayerLogin(Player* player) override
    {
        GiveWhistleIfNeeded(player);
    }

    void OnPlayerLevelChanged(Player* player, uint8 /*oldlevel*/) override
    {
        GiveWhistleIfNeeded(player);
    }

private:
    static void GiveWhistleIfNeeded(Player* player)
    {
        if (player->getClass() != CLASS_HUNTER || player->GetLevel() < HUNTER_STABLE_WHISTLE_REQUIRED_LEVEL)
            return;

        uint32 legacyCount = player->GetItemCount(ITEM_LEGACY_HUNTER_STABLE_WHISTLE, true);
        if (legacyCount)
            player->DestroyItemCount(ITEM_LEGACY_HUNTER_STABLE_WHISTLE, legacyCount, true);

        if (player->HasItemCount(ITEM_HUNTER_STABLE_WHISTLE, 1, true))
            return;

        ItemPosCountVec dest;
        InventoryResult msg = player->CanStoreNewItem(NULL_BAG, NULL_SLOT, dest, ITEM_HUNTER_STABLE_WHISTLE, 1);
        if (msg != EQUIP_ERR_OK)
        {
            player->SendSystemMessage("Your bags are full. Make room to receive the Stable Master's Whistle.");
            return;
        }

        player->StoreNewItem(dest, ITEM_HUNTER_STABLE_WHISTLE, true);
        player->SendSystemMessage("Stable Master's Whistle added to your bags.");
    }
};

class QuestApprenticeAnglerPlayerScript : public PlayerScript
{
public:
    QuestApprenticeAnglerPlayerScript() : PlayerScript("QuestApprenticeAnglerPlayerScript", {PLAYERHOOK_ON_PLAYER_COMPLETE_QUEST})
    {
    }

    void OnPlayerCompleteQuest(Player* player, Quest const* quest) override
    {
        if (quest->GetQuestId() == QUEST_APPRENTICE_ANGLER)
        {
            uint32 level = player->GetLevel();
            int32 moneyRew = 0;
            if (level <= 10)
                moneyRew = 85;
            else if (level <= 60)
                moneyRew = 2300;
            else if (level <= 69)
                moneyRew = 9000;
            else if (level <= 70)
                moneyRew = 11200;
            else if (level <= 79)
                moneyRew = 12000;
            else
                moneyRew = 19000;

            player->ModifyMoney(moneyRew);
            player->UpdateAchievementCriteria(ACHIEVEMENT_CRITERIA_TYPE_MONEY_FROM_QUEST_REWARD, uint32(moneyRew));
            player->SaveToDB(false, false);

            // Send packet with money
            WorldPacket data(SMSG_QUESTGIVER_QUEST_COMPLETE, (4 + 4 + 4 + 4 + 4));
            data << uint32(quest->GetQuestId());
            data << uint32(0);
            data << uint32(moneyRew);
            data << uint32(0);
            data << uint32(0);
            data << uint32(0);
            player->SendDirectMessage(&data);
        }
    }
};

void AddSC_player_scripts()
{
    new HunterStableWhistlePlayerScript();
    new QuestApprenticeAnglerPlayerScript();
}
