-- DB update 2026_04_24_02 -> 2026_05_29_00
-- Add a hunter-only stable whistle for opening the portable stable UI.

SET @STABLE_WHISTLE := 900002;

DELETE FROM `item_template_locale` WHERE `ID` = @STABLE_WHISTLE;
DELETE FROM `item_template` WHERE `entry` = @STABLE_WHISTLE;

DROP TEMPORARY TABLE IF EXISTS `tmp_hunter_stable_whistle`;
CREATE TEMPORARY TABLE `tmp_hunter_stable_whistle` LIKE `item_template`;
INSERT INTO `tmp_hunter_stable_whistle` SELECT * FROM `item_template` WHERE `entry` = 3456;

UPDATE `tmp_hunter_stable_whistle`
SET
  `entry` = @STABLE_WHISTLE,
  `name` = 'Stable Master''s Whistle',
  `Quality` = 3,
  `BuyPrice` = 0,
  `SellPrice` = 0,
  `AllowableClass` = 4,
  `ItemLevel` = 10,
  `RequiredLevel` = 10,
  `maxcount` = 1,
  `description` = 'Opens your hunter stable outside combat.',
  `spellid_1` = 9515,
  `spelltrigger_1` = 0,
  `spellcharges_1` = 0,
  `spellppmRate_1` = 0,
  `spellcooldown_1` = 30000,
  `spellcategory_1` = 0,
  `spellcategorycooldown_1` = -1,
  `ScriptName` = 'item_hunter_stable_whistle',
  `VerifiedBuild` = 0;

INSERT INTO `item_template` SELECT * FROM `tmp_hunter_stable_whistle`;
DROP TEMPORARY TABLE `tmp_hunter_stable_whistle`;

INSERT INTO `item_template_locale` (`ID`, `locale`, `Name`, `Description`, `VerifiedBuild`) VALUES
(@STABLE_WHISTLE, 'zhCN', '兽栏管理员的哨子', '在非战斗状态打开你的猎人兽栏。', 0),
(@STABLE_WHISTLE, 'zhTW', '獸欄管理員的哨子', '在非戰鬥狀態打開你的獵人獸欄。', 0);
