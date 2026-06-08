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

#include "Chat.h"
#include "CommandScript.h"
#include "Language.h"
#include "Log.h"
#include "ObjectMgr.h"
#include "Opcodes.h"
#include "Pet.h"
#include "Player.h"
#include "SpellInfo.h"
#include "SpellMgr.h"
#include "WorldPacket.h"
#include "WorldSession.h"

using namespace Acore::ChatCommands;

class pet_commandscript : public CommandScript
{
public:
    pet_commandscript() : CommandScript("pet_commandscript") { }

    ChatCommandTable GetCommands() const override
    {
        static ChatCommandTable petStableCommandTable =
        {
            { "call",  HandlePetStableCallCommand,  SEC_PLAYER, Console::No },
            { "list",  HandlePetStableListCommand,  SEC_PLAYER, Console::No },
            { "store", HandlePetStableStoreCommand, SEC_PLAYER, Console::No },
            { "",      HandlePetStableOpenCommand,  SEC_PLAYER, Console::No }
        };

        static ChatCommandTable petCommandTable =
        {
            { "create",  HandlePetCreateCommand,  SEC_GAMEMASTER, Console::No },
            { "learn",   HandlePetLearnCommand,   SEC_GAMEMASTER, Console::No },
            { "stable",  petStableCommandTable },
            { "unlearn", HandlePetUnlearnCommand, SEC_GAMEMASTER, Console::No }
        };

        static ChatCommandTable commandTable =
        {
            { "pet", petCommandTable }
        };

        return commandTable;
    }

    static bool CanUsePortableStable(ChatHandler* handler, Player* player)
    {
        if (!player)
            return false;

        if (player->getClass() != CLASS_HUNTER)
        {
            handler->SendErrorMessage("Portable stable is only available to hunters.");
            return false;
        }

        if (!player->IsAlive())
        {
            handler->SendErrorMessage("You must be alive to use the portable stable.");
            return false;
        }

        if (player->IsInCombat())
        {
            handler->SendErrorMessage("You cannot use the portable stable while in combat.");
            return false;
        }

        if (player->InBattleground() || player->InArena())
        {
            handler->SendErrorMessage("Portable stable is disabled in battlegrounds and arenas.");
            return false;
        }

        return true;
    }

    static bool HandlePetStableOpenCommand(ChatHandler* handler)
    {
        Player* player = handler->GetSession()->GetPlayer();
        if (!CanUsePortableStable(handler, player))
            return false;

        player->Dismount();
        player->RemoveAurasByType(SPELL_AURA_MOUNTED);
        player->GetOrInitPetStable();
        handler->GetSession()->SendStablePet(player->GetGUID());
        return true;
    }

    static bool HandlePetStableListCommand(ChatHandler* handler)
    {
        Player* player = handler->GetSession()->GetPlayer();
        if (!CanUsePortableStable(handler, player))
            return false;

        PetStable& stable = player->GetOrInitPetStable();
        uint32 usedSlots = 0;
        for (Optional<PetStable::PetInfo> const& stabledPet : stable.StabledPets)
            if (stabledPet)
                ++usedSlots;

        handler->PSendSysMessage("Hunter stable: {} used / {} slots.", usedSlots, stable.MaxStabledPets);

        if (stable.CurrentPet)
        {
            PetStable::PetInfo const& pet = *stable.CurrentPet;
            handler->PSendSysMessage("Active: #{} {} entry {} level {}", pet.PetNumber, pet.Name, pet.CreatureId, uint32(pet.Level));
        }
        else if (PetStable::PetInfo const* pet = stable.GetUnslottedHunterPet())
            handler->PSendSysMessage("Active unsummoned: #{} {} entry {} level {}", pet->PetNumber, pet->Name, pet->CreatureId, uint32(pet->Level));
        else
            handler->PSendSysMessage("Active: none");

        for (uint32 slot = 0; slot < stable.MaxStabledPets; ++slot)
        {
            Optional<PetStable::PetInfo> const& stabledPet = stable.StabledPets[slot];
            if (stabledPet)
                handler->PSendSysMessage("Slot {}: #{} {} entry {} level {}", slot + 1, stabledPet->PetNumber, stabledPet->Name, stabledPet->CreatureId, uint32(stabledPet->Level));
            else
                handler->PSendSysMessage("Slot {}: empty", slot + 1);
        }

        return true;
    }

    static bool HandlePetStableStoreCommand(ChatHandler* handler)
    {
        Player* player = handler->GetSession()->GetPlayer();
        if (!CanUsePortableStable(handler, player))
            return false;

        player->GetOrInitPetStable();

        WorldPacket packet(CMSG_STABLE_PET, 8);
        packet << player->GetGUID();
        handler->GetSession()->HandleStablePet(packet);
        return true;
    }

    static bool HandlePetStableCallCommand(ChatHandler* handler, uint8 slot)
    {
        Player* player = handler->GetSession()->GetPlayer();
        if (!CanUsePortableStable(handler, player))
            return false;

        PetStable& stable = player->GetOrInitPetStable();
        if (!slot || slot > stable.MaxStabledPets)
        {
            handler->SendErrorMessage("Usage: .pet stable call <slot>");
            return false;
        }

        Optional<PetStable::PetInfo> const& stabledPet = stable.StabledPets[slot - 1];
        if (!stabledPet)
        {
            handler->SendErrorMessage("That stable slot is empty.");
            return false;
        }

        WorldPacket packet(CMSG_UNSTABLE_PET, 12);
        packet << player->GetGUID();
        packet << uint32(stabledPet->PetNumber);
        handler->GetSession()->HandleUnstablePet(packet);
        return true;
    }

    static bool HandlePetCreateCommand(ChatHandler* handler)
    {
        Player* player = handler->GetSession()->GetPlayer();
        Creature* creatureTarget = handler->getSelectedCreature();

        if (!creatureTarget || creatureTarget->IsPet() || creatureTarget->IsPlayer())
        {
            handler->SendErrorMessage(LANG_SELECT_CREATURE);
            return false;
        }

        CreatureTemplate const* creatrueTemplate = sObjectMgr->GetCreatureTemplate(creatureTarget->GetEntry());
        // Creatures with family 0 crashes the server
        if (!creatrueTemplate->family)
        {
            handler->SendErrorMessage(LANG_CREATURE_NON_TAMEABLE, creatrueTemplate->Entry);
            return false;
        }

        if (player->IsExistPet())
        {
            handler->SendErrorMessage(LANG_YOU_ALREADY_HAVE_PET);
            return false;
        }

        if (!player->CreatePet(creatureTarget))
        {
            handler->SendErrorMessage(LANG_CREATURE_NON_TAMEABLE, creatrueTemplate->Entry);
            return false;
        }

        return true;
    }

    static bool HandlePetLearnCommand(ChatHandler* handler, SpellInfo const* spell)
    {
        if (!spell)
        {
            handler->SendErrorMessage(LANG_COMMAND_NOSPELLFOUND);
            return false;
        }

        if (!SpellMgr::IsSpellValid(spell))
        {
            handler->SendErrorMessage(LANG_COMMAND_SPELL_BROKEN, spell->Id);
            return false;
        }

        Pet* pet = handler->GetSession()->GetPlayer()->GetPet();
        if (!pet)
        {
            handler->SendErrorMessage("You have no pet");
            return false;
        }

        SpellScriptsBounds bounds = sObjectMgr->GetSpellScriptsBounds(spell->Id);
        uint32 spellDifficultyId = sSpellMgr->GetSpellDifficultyId(spell->Id);
        if (bounds.first != bounds.second || spellDifficultyId)
        {
            handler->SendErrorMessage("Spell {} cannot be learnt using a command!", spell->Id);
            return false;
        }

        // Check if pet already has it
        if (pet->HasSpell(spell->Id))
        {
            handler->SendErrorMessage("Pet already has spell: {}", spell->Id);
            return false;
        }

        pet->learnSpell(spell->Id);
        handler->PSendSysMessage("Pet has learned spell {}", spell->Id);

        return true;
    }

    static bool HandlePetUnlearnCommand(ChatHandler* handler, SpellInfo const* spell)
    {
        if (!spell)
        {
            handler->SendErrorMessage(LANG_COMMAND_NOSPELLFOUND);
            return false;
        }

        if (!SpellMgr::IsSpellValid(spell))
        {
            handler->SendErrorMessage(LANG_COMMAND_SPELL_BROKEN, spell->Id);
            return false;
        }

        Pet* pet = handler->GetSession()->GetPlayer()->GetPet();
        if (!pet)
        {
            handler->SendErrorMessage("You have no pet");
            return false;
        }

        if (pet->HasSpell(spell->Id))
        {
            pet->removeSpell(spell->Id, false);
        }
        else
        {
            handler->PSendSysMessage("Pet doesn't have that spell");
        }

        return true;
    }
};

void AddSC_pet_commandscript()
{
    new pet_commandscript();
}
