# Agent PlayerBot 架构真相源

最后核对：2026-05-08

本文件是当前 `playerbot-agent` 分支的架构真相源。部署拓扑、数据库归属、端口、模块边界、Agent 分层和关键设计变化，都以这里为准。

不要把 API key、bearer token、数据库密码或其他密钥写进本文档。

## 当前结论

当前分支不再沿着旧的 `mod-agent-control` 自研底层行为继续合并开发，而是以成熟的 `mod-playerbots` 作为底层行为/本能系统，在其上增加 LLM-Agent 的聊天理解、任务理解、队伍意图识别和宏观调度。

2026-05-08 已落地第一版试验实现：

```text
modules/mod-playerbot-agent
  - 游戏内薄桥接模块
  - 捕获真实玩家聊天
  - 把事件写入 acore_playerbots.agent_playerbot_events
  - 轮询 acore_playerbots.agent_playerbot_actions
  - 只执行 reply / command / strategy 三类白名单动作

tools/playerbot-agent/agent_bridge.py
  - Python 侧车大脑
  - 中文规则优先
  - 可选 OpenAI-compatible LLM
  - 输出 Playerbots 小脑可执行的安全动作
```

核心分层：

```text
LLM-Agent 大脑
  - 理解中文聊天和上下文
  - 判断玩家意图、队伍需求、短期目标
  - 输出结构化意图，不直接操作游戏原语

Agent 意图适配器
  - 把大脑意图翻译成 Playerbots 能理解的命令/API
  - 校验权限、目标、冷却、频率和安全边界
  - 记录执行结果，给大脑做反馈

Playerbots 本能/小脑
  - 职业战斗循环、治疗、坦克、DPS、BUFF、驱散
  - 跟随、移动、躲 AOE、逃跑、复活、召唤、组队辅助
  - 装备、天赋、技能、补给、拾取、任务同步、副本/BG 策略

AzerothCore 世界
  - 地图、角色、战斗、寻路、数据库、网络会话
```

大模型不应该每秒决定“按哪个技能”。这类高频行为属于 Playerbots 本能。大模型应该决定“现在需要一个治疗进队”“这句话是在叫我组人”“这波打完先休整”“这个副本需要坦克+治疗+3DPS”。

## 当前部署

### 共享登录

```text
authserver: 0.0.0.0:3724
auth 库:    acore_auth
公网入口:   38.207.189.99
```

### 生产服

生产服 worldserver 当前已停止；共享 authserver 仍在使用。旧生产 world 配置仍在：

```text
源码/运行目录: /home/wuya/git/azerothcore-wotlk
worldserver:  0.0.0.0:8085
world 库:     acore_world
角色库:       acore_characters
```

### PlayerBot Agent 服

```text
源码/构建目录: /home/wuya/git/azerothcore-wotlk-git
运行目录:      /home/wuya/git/azerothcore-wotlk-git/env/dist
tmux 会话:     playerbot-world
worldserver:   0.0.0.0:8086
SOAP:          0.0.0.0:7879
auth 库:       acore_auth
world 库:      acore_playerbot_world
角色库:        acore_playerbot_characters
Playerbots库:  acore_playerbots
核心分支:      playerbot-agent
核心上游:      playerbots-core/Playerbot
模块:          modules/mod-playerbots
Agent桥模块:   modules/mod-playerbot-agent
Agent侧车:     tools/playerbot-agent/agent_bridge.py
```

Realm：

```text
id=1  AzerothCore       38.207.189.99:8085
id=2  Agent PlayerBot   38.207.189.99:8086
```

当前 `8086` 是 PlayerBot Agent 服。它复用 `acore_auth`，但使用独立 world/characters/playerbots 数据库，避免污染生产数据。

## 当前运行策略

本服先按“Agent 可调度 bot 池”运行，而不是“随机机器人生态服”运行。

关键配置：

```text
AiPlayerbot.Enabled = 1
AiPlayerbot.RandomBotAutologin = 0
AiPlayerbot.MinRandomBots = 0
AiPlayerbot.MaxRandomBots = 0
AiPlayerbot.RandomBotLoginAtStartup = 0
AiPlayerbot.RandomBotJoinLfg = 0
AiPlayerbot.RandomBotJoinBG = 0
AiPlayerbot.RandomBotTalk = 0
AiPlayerbot.RandomBotSuggestDungeons = 0
AiPlayerbot.AddClassCommand = 1
AiPlayerbot.AddClassAccountPoolSize = 50
AiPlayerbot.ApplyInstanceStrategies = 1
AiPlayerbot.CombatStrategies = "-healer dps"
AiPlayerbot.CommandServerPort = 0
AgentPlayerbot.Enabled = 1
AgentPlayerbot.ActionPollIntervalMs = 500
AgentPlayerbot.MaxReplyLength = 220
```

服务器性能调优：

```text
MinWorldUpdateTime = 10
MapUpdateInterval = 50
MapUpdate.Threads = 1
```

当前数据库里已经生成：

```text
PBAGENT* bot 账号: 55
bot 角色:          550
AddClass 账号池:   50
测试迁移真人角色: 9
```

`AiPlayerbot.CombatStrategies = "-healer dps"` 是当前试玩期的保守设置：治疗 bot 仍会治疗、驱散、保命、喝水和跟随，但默认不额外启用治疗输出策略，降低“治疗跟着战士日常打怪时抢仇恨”的概率。后续如果要测试更激进的效率，可以改回空字符串或由 Adapter 按场景切换策略。

已从旧测试角色库 `acore_agent_characters` 通过 AzerothCore `pdump` 迁移到当前 `acore_playerbot_characters`：

| 账号 | 角色 |
| --- | --- |
| `WUYA_TEST` | `小猎` Lv1 猎人、`中年狼` Lv1 牧师、`乌鸦` Lv2 法师、`Wuya` Lv19 战士 |
| `WUGUI` | `滑才怪` Lv19 猎人 |
| `XIAOWU` | `小宝` Lv2 术士、`小德` Lv19 德鲁伊 |
| `GM` | `小管` Lv58 盗贼 |
| `WUJI` | `赛博丶执著爱` Lv20 圣骑士 |

迁移前备份保存在：

```text
/home/wuya/backups/azerothcore-wotlk-git/playerbot-test-migration-20260508/
```

## Playerbots 提供的本能

这里的“本能”指不需要 LLM 每一步判断、已经由 Playerbots/C++ 规则系统高频处理的能力。当前清单主要来自 `PlayerbotMgr`、`RandomPlayerbotMgr`、`ChatCommandHandlerStrategy` 和 `AiFactory`。不是模块里的所有命令都应暴露给 LLM。本文档用四个等级描述可调用程度：

| 等级 | 含义 | Agent 侧处理 |
| --- | --- | --- |
| L1 | 当前已经能通过 `.playerbots` 命令桥或 SOAP 间接调用 | 可以先包装成 Intent Adapter |
| L2 | bot 上线后自动生效的策略/行为树本能 | 上层通过职业、初始化、队伍目标和策略调参间接控制 |
| L3 | 底层已有聊天快捷命令、Action 或 C++ 能力，但还没有安全强类型 API | 需要 Adapter 包装，不能让 LLM 直接拼命令 |
| L4 | 模块支持但当前配置关闭，或只适合 GM/维护 | 默认不暴露给 LLM |

### L1：当前可直接包装的命令桥本能

这些能力已经能从游戏内 `.playerbots bot ...` 使用，也可以由服务端通过 SOAP 代执行。第一版 Agent Adapter 应优先只包装这些低风险能力。

| Agent intent | 当前 Playerbots 小脑语言 | 说明 |
| --- | --- | --- |
| `lookup_bot_pool` | `.playerbots bot lookup` | 查看 AddClass 池中可召唤 bot。 |
| `summon_bot` | `.playerbots bot addclass <class> [male\|female\|0\|1]` | 从 AddClass 池按职业召唤同阵营 bot。职业参数：`warrior`、`paladin`、`hunter`、`rogue`、`priest`、`shaman`、`mage`、`warlock`、`druid`、`dk`。 |
| `list_bots` | `.playerbots bot list` | 列出当前玩家可控 bot。 |
| `dismiss_bot` | `.playerbots bot remove <name>`，别名 `logout`、`rm` | 让指定 bot 下线。`*` 可作用于队伍内 bot，但 Adapter 应做权限和确认。 |
| `login_account_bot` | `.playerbots bot add <charname>` | 登录玩家自己账号或可信账号上的指定角色作为 bot。 |
| `login_account_bots` | `.playerbots bot addaccount <account-or-char>` | 登录指定账号下角色。需要账号/可信账号权限，默认不作为第一批开放能力。 |
| `init_bot` | `.playerbots bot init=auto <name>` | 给 AddClass bot 按主人等级和装备分数初始化装备。非 GM 受 `autoInitOnly` 影响时应只用 `init=auto`。 |
| `init_bot_quality` | `.playerbots bot init=green/blue/epic/legendary <name>` | 按品质初始化装备。别名包括 `white/common`、`green/uncommon`、`blue/rare`、`epic/purple`、`legendary/yellow`。建议只给 GM Adapter 暴露。 |
| `init_bot_gearscore` | `.playerbots bot init=<gearScore> <name>` | 按 gear score 上限初始化装备。建议只给 GM Adapter 暴露。 |
| `refresh_bot` | `.playerbots bot refresh <name>` | 刷新 AddClass bot 装备/状态。 |
| `refresh_bot_raid_lock` | `.playerbots bot refresh=raid <name>` | 解除副本绑定相关状态。源码里标注该能力还不完美，默认不作为常规玩家能力。 |
| `level_bot` | `.playerbots bot levelup <name>`，别名 `level` | 让 AddClass bot 跟随等级初始化。 |
| `init_instance_quests` | `.playerbots bot quests <name>` | 初始化副本任务。 |
| `reload_playerbot_config` | `.playerbots bot reload` | 重新读取 Playerbots 配置。GM/运维能力，不给普通 LLM 调用。 |
| `toggle_selfbot` | `.playerbots bot self` | 给真人角色启用/关闭 bot AI。调试能力，不作为常规 Agent 能力。 |

### L2：上线后自动运行的核心本能

这些能力不需要上层逐条调用。只要 bot 被召唤、初始化并跟随主人，Playerbots 会在战斗/非战斗/死亡状态下自动选择动作。

| 类别 | 已存在的本能 | 上层大脑如何调控 |
| --- | --- | --- |
| 职业战斗循环 | 全职业基础输出、治疗、坦克、驱散、控制、AOE、爆发、施法距离、攻击距离、站位 | 主要通过 `summon_bot` 的职业选择、初始化质量、队伍组成和后续 `set_strategy` 控制。 |
| 坦克行为 | `tank`、`tank assist`、`pull`、`pull back`、`tank face`、威胁/拉怪相关策略 | 大脑决定“需要坦克”“让坦克开怪/停手”，小脑处理具体技能和仇恨。 |
| 治疗行为 | `heal`、`holy heal`、`cure`、`save mana`、`healer dps`、低血/低蓝阈值 | 大脑决定是否召治疗、是否进入休整，小脑持续治疗、驱散、省蓝。 |
| DPS 行为 | `dps`、`dps assist`、各职业专精、`behind`、`boost`、`max dps` 相关策略 | 大脑决定集火目标、是否爆发，小脑负责技能循环。 |
| 非战斗跟随 | `follow`、`formation`、`stay`、回到主人、距离控制 | 大脑只表达“跟我/停在这/散开”，小脑处理移动。 |
| 战斗安全 | `avoid aoe`、`flee`、`runaway`、药水、食物/喝水、复活/死亡跟随 | 大脑不用逐秒躲技能，只决定是否撤退、休整、复活。 |
| Buff 和宠物 | 职业 Buff、图腾/祝福/法师护甲、猎人/术士宠物、死亡骑士/猎人等职业资源 | 大脑不调单个 Buff，最多设置队伍目标或角色定位。 |
| 拾取和物品 | `loot`、ROLL、开箱/开锁、装备/卸装、自动装备升级、包裹补给 | 大脑可以决定“允许拾取/卖垃圾/准备副本”，小脑处理细节。 |
| 任务和成长 | 接/交/共享任务、任务同步、自动任务/RPG、清理过期任务 | 大脑负责宏观任务选择，小脑执行 NPC 交互和移动。 |
| 移动和旅行 | 坐骑、飞行点、旅行目的地缓存、寻路、召唤到主人附近 | 大脑说目标，小脑处理怎么走。 |
| 副本/团队 | `ApplyInstanceStrategies`、副本和团队 Boss 专用触发器/动作、队伍职责 | 大脑负责组队和目标，小脑负责 Boss 技能应对。 |
| BG/竞技场 | 战场策略、竞技场策略、战场目标移动 | 当前随机 bot 自动进 BG 已关闭，后续只在明确测试时打开。 |

职业策略已经覆盖 WotLK 十职业，常见策略名包括：

```text
priest:  dps, shadow debuff, shadow aoe, heal, holy heal, cure
mage:    arcane, fire, frost, frostfire, dps, cc, aoe, cure
warrior: tank, tank assist, pull, pull back, arms, fury, aoe
shaman:  ele, resto, enh, cure, aoe, totem/buff strategies
paladin: tank, heal, dps, cure, blessings, aoe
druid:   caster, caster aoe, caster debuff, heal, cat, bear, cure
hunter:  bm, mm, surv, cc, dps assist, aoe, pet/bdps
rogue:   melee, dps, dps assist, aoe
warlock: affli, demo, destro, curse, cc, aoe, pet
dk:      blood, frost, unholy, tank assist, pull, aoe
```

“战士玩家 + 治疗 bot 跟随打怪”的主要实现位置：

| 行为 | 代码位置 | 说明 |
| --- | --- | --- |
| 按职业/天赋挂默认战斗策略 | `modules/mod-playerbots/src/Bot/Factory/AiFactory.cpp` | 牧师非暗影挂 `heal`/`holy heal`，奶骑/奶德/奶萨挂治疗策略；治疗角色还会挂 `save mana`，当前运行配置再移除 `healer dps`。 |
| 牧师治疗触发器 | `modules/mod-playerbots/src/Ai/Class/Priest/Strategy/HealPriestStrategy.cpp` | 根据队友血量触发盾、愈合祷言、苦修、快速治疗、治疗祷言、痛苦压制等。 |
| 仇恨抑制 | `modules/mod-playerbots/src/Ai/Base/Strategy/ThreatStrategy.cpp` | `+threat` 策略被启用时，组队状态下高仇恨动作会被降权；牧师默认在中等仇恨时触发 `fade`。 |
| 跟随/停留/拉怪等指令 | `modules/mod-playerbots/src/Ai/Base/Strategy/ChatCommandHandlerStrategy.cpp` | 支持 `follow`、`stay`、`attack`、`pull`、`flee`、`ready` 等 bot 聊天快捷指令。 |

这些能力可以被包装：上层 Adapter 不需要调用“快速治疗”这种单个技能，只需要控制 `summon_bot`、`init_bot`、`bot_follow`、`bot_stay`、`bot_attack_target`、`set_strategy` 这类低频意图。

### L3：底层已有但需要 Adapter 包装的可控本能

这些动作在 Playerbots 中已经有聊天快捷命令、Action 或策略变化入口，但当前还没有我们自己的强类型、安全边界和中文理解层。它们可以作为第二批 Adapter API。

| 目标能力 | 底层入口 | 建议 Adapter 形态 |
| --- | --- | --- |
| 让 bot 跟随 | bot 聊天快捷命令 `follow` | `bot_follow(bot, target=master)` |
| 让 bot 原地停留 | `stay` | `bot_stay(bot, position=current)` |
| 让 bot 远离/散开 | `move from group`、`flee`、`runaway`、`warning`、`disperse` | `bot_spread(bot/group)`、`bot_retreat(bot/group)` |
| 攻击/协助攻击 | `attack`、`tank attack`、`pull`、`pull back`、`pull rti` | `bot_attack_target(bot, target)`、`bot_pull(bot, target)` |
| 爆发输出 | `max dps` | `set_combat_mode(bot/group, "burst")` |
| 准备确认 | `ready` | `ready_check(group)` |
| 复活/灵魂医者 | `revive` | `bot_revive(bot)` |
| 任务交互 | `accept`、`talk`、`q`、`qi`、`quests`、`reward` | `bot_accept_quest`、`bot_turnin_quest`、`bot_query_quest` |
| 物品和交易 | `t`/`nt`、`buy`、`sell`、`equip`、`unequip`、`use`、`roll` | `bot_trade`、`bot_buy`、`bot_sell`、`bot_equip`、`bot_roll` |
| 施法 | `cast`、`castnc` | `bot_cast_spell(bot, spell, target)`，必须做白名单。 |
| 宠物控制 | `pet`、`pet attack` | `bot_pet_command(bot, command, target)` |
| 天赋/雕文类维护 | `glyphs`、`glyph equip`、maintenance 相关动作 | GM 或受控维护任务，不给聊天自由调用。 |
| 策略切换 | `ChangeStrategy("+x,-y", state)` | `set_strategy(bot/group, add=[...], remove=[...], state=combat/noncombat/dead)` |
| 邀请真实玩家 | AzerothCore 组队 API，Playerbots 有组队/队伍上下文但当前没有现成 `.playerbots bot invite <player>` 命令 | `invite_player(target_player)`，需要 C++ Adapter 或安全动作层。 |

重点：LLM 可以识别“跟我”“停一下”“开怪”“爆发”“加我”“加个奶”，但它输出的应该是结构化 intent。Adapter 再翻译成上表里的小脑语言。不要让 LLM 直接拼英文聊天快捷命令，也不要直接执行数据库写入。

### L4：当前默认关闭或不建议暴露的能力

这些能力在模块里存在，但和当前“Agent 可调度 bot 池”目标不一致，或者资源/安全风险较高。

| 能力 | 当前状态 | 原因 |
| --- | --- | --- |
| 随机 bot 自动上线 | `AiPlayerbot.RandomBotAutologin = 0`，`MinRandomBots = 0`，`MaxRandomBots = 0` | 空服跑随机生态会显著增加 CPU/内存和数据库负载。 |
| 随机 bot 自动进 LFG/BG/竞技场 | `RandomBotJoinLfg = 0`，`RandomBotJoinBG = 0` | 会制造大量后台活动，先不用于 Agent 小队测试。 |
| 随机 bot 世界/公会/交易频道聊天 | `RandomBotTalk = 0`，相关广播概率不作为入口 | 容易干扰真实玩家和 Agent 聊天测试。 |
| `.playerbots rndbot ...` | GM/控制台能力：`stats`、`reload`、`update`、`reset`、`init`、`clear`、`level`、`refresh`、`teleport`、`revive`、`grind`、`change_strategy` | 这是随机生态维护接口，不是普通 Agent 本能。需要测试随机生态时单独打开。 |
| `.playerbots gtask ...` | GM 公会任务维护 | 不属于小队本能。 |
| `.playerbots pmon/debug ...` | 性能监控/调试 | 运维工具，不给 LLM。 |
| `.playerbots account ...` | 玩家账号绑定/解绑 | 涉及账号权限，不能由 LLM 自由调用。 |
| Playerbots CommandServer | `AiPlayerbot.CommandServerPort = 0` | 当前关闭，后续若启用也必须加认证、限流和审计。 |

### 第一批建议暴露给 LLM-Agent 的本能 API

第一阶段 v1 已实现的是“聊天 + 指挥队伍内 AddClass bot”，不包含召唤、删除、初始化 bot。原因是这些 `.playerbots bot ...` 命令需要玩家会话上下文，不能安全地让 Python 侧车通过 SOAP 控制；v1 先把已经在线/已入队的 bot 变成可聊天、可指挥的小队成员。

| Skill | 底层小脑语言 | 说明 |
| --- | --- | --- |
| `reply` / `no_reply` | bot 以 `party`/`whisper`/`say` 发言 | 人设聊天和简短确认。 |
| `bot_follow` | `follow` | 跟随主人/队伍。 |
| `bot_stay` | `stay` | 原地停留。 |
| `bot_retreat` | `flee` | 跟随撤退，偏保守。 |
| `bot_runaway` | `runaway` | 跑远/散开。 |
| `bot_attack_target` | `attack` | 攻击玩家当前目标。 |
| `bot_pull` | `pull` | 让 bot 拉玩家当前目标。 |
| `bot_ready` | `ready` | 准备确认。 |
| `focus_heal_add` | `focus heal +<玩家名>` | 治疗 bot 重点照看某个队友，例如“加我/奶我”。 |
| `focus_heal_remove` | `focus heal -<玩家名>` | 取消重点治疗。 |
| `focus_heal_clear` | `focus heal clear` | 清空重点治疗列表。 |
| `loot_off` | `-loot` | 关闭非战斗拾取策略。 |
| `loot_normal` | `+loot` + `ll normal` | 恢复正常拾取策略。 |
| `loot_gray` | `+loot` + `ll gray` | 允许捡灰色垃圾。 |
| `loot_all` | `+loot` + `ll all` | 全捡。 |
| `buff_on` / `buff_off` | `+buff` / `-buff` | 打开/关闭非战斗 buff 策略。 |
| `healer_safe` | `-healer dps` | 治疗专心奶，降低抢仇恨风险。 |
| `healer_burst` | `+healer dps` | 允许治疗补输出。 |

第二阶段再补召唤和队伍生命周期：

```text
summon_bot(role, class_hint, gender?)
dismiss_bot(bot_name)
list_bots()
lookup_bot_pool()
init_bot(bot_name, mode="auto")
refresh_bot(bot_name)
level_bot(bot_name)
invite_player(player_name)
```

第三阶段再考虑任务、交易、补给、旅行、随机生态和副本专用调度。

## 大脑能否调控本能

能，但要通过一层受控的“意图适配器”。不要让 LLM 直接拼 `.playerbots` 命令，更不要直接碰数据库。

推荐接口形态：

```json
{
  "intent": "summon_bot",
  "role": "healer",
  "class_hint": "priest",
  "gender": "female",
  "reason": "队伍缺治疗"
}
```

适配器负责翻译成 Playerbots 小脑语言：

```text
.playerbots bot addclass priest female
.playerbots bot init=auto <botName>
```

或未来直接调用 C++ 内部 API：

```text
SummonAddClassBot(master, class, gender)
InitializeBot(bot, mode=auto)
SetBotStrategy(bot, strategy)
InvitePlayer(target)
DismissBot(bot)
```

### “加我”示例

玩家说“加我”时，大脑不要机械执行命令。它要先判断语境：

```text
1. 谁说的？
2. 他是否已经在队伍里？
3. 他是在请求被邀请进队，还是在请求加一个 bot？
4. 当前说话频道是密语、附近、世界，还是队伍？
5. 我是否有权限邀请他，队伍是否满员？
```

然后输出结构化意图：

```json
{
  "intent": "invite_player",
  "target_player": "说话者",
  "source": "whisper",
  "confidence": 0.91
}
```

适配器把它转成小脑动作：

```text
invite_player(target_player)
```

如果语境是队伍里有人说“加个奶”或“加我个奶”，大脑应输出：

```json
{
  "intent": "summon_bot",
  "role": "healer",
  "class_hint": "priest"
}
```

适配器再执行：

```text
.playerbots bot addclass priest
```

现状：`addclass` 这类 Playerbots 本能已经可用；“邀请某个真实玩家进队”还需要在 Agent 适配器或 C++ 安全动作层里补一个 `invite_player` 能力，不能只靠当前 `addclass` 命令解决。

## Agent 控制接口路线

当前已经接入第一版薄适配层。

第一阶段 v1：DB 队列桥

```text
真实玩家聊天
  -> mod-playerbot-agent 写 agent_playerbot_events
  -> Python sidecar 规则/LLM 识别意图
  -> 写 agent_playerbot_actions
  -> mod-playerbot-agent 执行白名单动作
  -> Playerbots 小脑处理具体行为
```

优点：不需要让 Python 伪造玩家会话；bot 可以以自己的身份说话；Playerbots 仍然负责具体战斗和移动。

限制：v1 只对在线队伍 bot 生效；不让 LLM 自由拼英文命令；召唤/删除/初始化仍由玩家手动 `.playerbots bot ...` 操作。

第二阶段：更完整 C++ Adapter

```text
modules/mod-playerbot-agent
  - 增加更强类型的 bot 生命周期 API
  - 提供 summon_bot / invite_player / dismiss_bot / set_strategy 等强类型动作
  - 内部调用 Playerbots 管理器或排队操作
```

优点：可靠、可观测、权限清晰。

第三阶段：Agent 记忆和复盘

```text
队伍聊天 / 战斗日志 / 死亡 / 蓝量 / 位置 / 命令结果
  -> 过程摘要
  -> 经验库
  -> 下次副本/任务前检索
  -> 大脑调整策略
```

## 旧文档状态

旧 Agent 原型已保存到：

```text
branch: archive/agent-control-v1-20260508
commit: 29b386eb4 archive agent control prototype
```

旧文档位置：

```text
/home/wuya/git/azerothcore-wotlk/ARCHITECTURE-agent-playerbots.md
/home/wuya/git/azerothcore-wotlk/README-agent-playerbots.md
```

旧文档中关于“分层 Agent、聊天理解、人设、安全边界、LLM 不做帧级操作”的原则继续有效；关于 `mod-agent-control`、`agent API 8790`、`acore_agent_world`、旧测试运行目录等部署细节已经过时。

以后当前分支以本文件为准。
