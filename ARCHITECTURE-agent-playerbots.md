# Agent PlayerBot 架构真相源

最后核对：2026-05-08

本文件是当前 `playerbot-agent` 分支的架构真相源。部署拓扑、数据库归属、端口、模块边界、Agent 分层和关键设计变化，都以这里为准。

不要把 API key、bearer token、数据库密码或其他密钥写进本文档。

## 当前结论

当前分支不再沿着旧的 `mod-agent-control` 自研底层行为继续合并开发，而是以成熟的 `mod-playerbots` 作为底层行为/本能系统，在其上增加 LLM-Agent 的聊天理解、任务理解、队伍意图识别和宏观调度。

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
AiPlayerbot.CommandServerPort = 0
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
```

## Playerbots 提供的本能

这里的“本能”指不需要 LLM 每一步判断、已经由 Playerbots/C++ 规则系统高频处理的能力。

### 角色创建和召唤

- `AddClass`：按职业快速召唤一个已生成 bot。
- `Altbot`：玩家账号上的其他角色可以作为 bot 登录。
- `Randombot`：随机 bot 自动上线、游荡、升级、做任务、进队、进 BG。当前为节省资源已关闭。
- 机器人登录/登出、列出、初始化、刷新、升级、装备初始化。

### 职业战斗

- 各职业基础 DPS/坦克/治疗循环。
- 近战/远程站位、攻击距离、施法距离、治疗距离。
- 治疗、驱散、BUFF、保命、宠物相关行为。
- 副本/团队中应用实例策略。
- 治疗职业省蓝策略、低血/低蓝阈值。

### 移动和战斗安全

- 跟随、召唤到队长附近。
- 逃跑和脱离危险距离。
- 自动躲避部分 AOE。
- 坐骑、飞行点、移动延迟。
- 地图路径和目的地缓存。

### 队伍、副本、PVP

- 组队和团队相关操作。
- LFG/BG/竞技场相关随机 bot 行为。
- 部分副本和团队 Boss 策略。
- AddClass 快速组建坦克/治疗/DPS 小队。

### 角色成长和后勤

- 任务同步、自动做任务/RPG 行为。
- 拾取、ROLL 点、自动装备升级。
- maintenance：学习技能、补给、修理、天赋、雕文、附魔、宝石、宠物等。
- 装备缓存、随机物品缓存、附魔/宝石缓存。

### 社交和经济

- bot 聊天文本库。
- 交易相关行为。
- 公会任务/随机 guild 行为。
- 世界/公会/交易/LFG 频道广播概率。

不是所有本能都应该默认开启。随机生态、自动 LFG/BG、世界聊天等会显著增加资源消耗，也可能干扰 Agent 测试。当前默认只保留“可按需召唤和控制”的小队能力。

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

当前还没有接 LLM-Agent。下一步不是直接改职业循环，而是先做薄适配层。

第一阶段：命令桥

```text
聊天事件/外部 API
  -> LLM/规则识别意图
  -> 白名单结构化 intent
  -> SOAP 或安全命令执行
  -> Playerbots 命令
```

优点：实现快，能验证“聊天理解 -> 调用本能”。

缺点：命令文本脆弱，结果回传粗糙。

第二阶段：C++ Adapter

```text
modules/mod-agent-playerbot-adapter
  - 捕获聊天事件
  - 暴露安全 HTTP/IPC API
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
