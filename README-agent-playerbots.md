# Agent PlayerBot 当前说明

架构和部署真相源见：[ARCHITECTURE-agent-playerbots.md](/home/wuya/git/azerothcore-wotlk-git/ARCHITECTURE-agent-playerbots.md)。

Harness / Planner Agent、Intent 接口和阶段路线图见：[ROADMAP-agent-playerbots.md](/home/wuya/git/azerothcore-wotlk-git/ROADMAP-agent-playerbots.md)。

基于 Heuristic Learning 的可回放迭代闭环见：[AGENT-HEURISTIC-LEARNING.md](AGENT-HEURISTIC-LEARNING.md)。

本分支当前目标：先把成熟的 `mod-playerbots` 跑成稳定的“机器人本能层”，再接入 LLM-Agent 做中文聊天理解、队伍意图识别和宏观调度。

## 当前状态

已经完成：

- 基于 `mod-playerbots/azerothcore-wotlk` 的 `Playerbot` 分支启动新核心。
- 安装 `modules/mod-playerbots`。
- 新增 `modules/mod-playerbot-agent` 游戏内薄桥。
- 新增 `tools/playerbot-agent/agent_bridge.py` Python 侧车。
- 建立独立数据库：`acore_playerbot_world`、`acore_playerbot_characters`、`acore_playerbots`。
- 复用生产 `acore_auth`，新增 Realm `Agent PlayerBot`，端口 `8085`。
- 生成 `PBAGENT*` bot 账号和 AddClass bot 池。
- 已开启轻量随机世界 bot 生态，当前目标是 30 到 50 个，并把 6 到 10 个空闲随机 bot 调度到真实玩家附近。
- 保留 AddClass 能力，作为玩家按需召唤、组队和控制 bot 的入口。
- 已实现第一批中文指挥 skill：跟随、停下、撤退、攻击、拉怪、重点治疗、拾取策略、buff 策略、治疗安全/补输出。
- 已接入环境/战斗感知：聊天 prompt 会带当前队伍状态、目标、附近敌人、最近 action 结果和最近战斗摘要。
- 随机世界 bot 被真人密语时会进入 LLM 闲聊回复，但只允许 `reply/no_reply`，不会执行跟随、治疗、拾取等控制动作。
- 已实现 v2 队伍生命周期强类型动作：`summon_bot`、`init_bot`、`dismiss_bot`、`list_bots`、`lookup_bot_pool`、`refresh_bot`、`level_bot`、`init_instance_quests`、`invite_player`。
- 无现成 bot 的队伍/附近聊天也能触发生命周期 intent，例如“加个奶”“看看机器人池子”。

还没完成：

- 还没有做长期跨天记忆、任务规划、背包/装备策略。

## 怎么玩

登录普通入口，Realm 选择：

```text
Agent PlayerBot
```

当前主要使用 Playerbots 命令：

```text
.playerbots bot lookup
.playerbots bot addclass priest female
.playerbots bot addclass warrior male
.playerbots bot addclass mage
.playerbots bot list
.playerbots bot init=auto <机器人名>
.playerbots bot remove <机器人名>
```

可用职业参数：

```text
warrior paladin hunter rogue priest shaman mage warlock druid dk
```

中文对照：

```text
warrior 战士
paladin 圣骑士
hunter  猎人
rogue   盗贼
priest  牧师/治疗
shaman  萨满/治疗
mage    法师
warlock 术士
druid   德鲁伊/治疗
dk      死亡骑士
```

当前配置同时跑两类 bot：

- AddClass bot：你用 `.playerbots bot addclass ...` 召唤出来的队友，可组队、跟随、治疗、拾取，也能被 Agent skill 调控。
- 随机世界 bot：自动上线，让世界热闹起来。服务器会把一部分空闲随机 bot 调度到真人玩家附近；你可以密语他们闲聊，但他们不是你的可控队友。

## 试玩建议

当前服已经设成“可玩、可召唤 bot”的保守模式：

- Realm 选 `Agent PlayerBot`。
- 旧测试服账号已经复用，角色已迁移到当前角色库。
- 随机 bot 生态已轻量开启，默认不会刷到 500 个。
- AddClass bot 池开启，普通玩家可按需召唤。
- 治疗输出策略默认关闭，治疗 bot 会更偏治疗/驱散/跟随，少做输出抢仇恨。

已迁移角色：

```text
WUYA_TEST: 小猎 Lv1 猎人, 中年狼 Lv1 牧师, 乌鸦 Lv2 法师, Wuya Lv19 战士
WUGUI:     滑才怪 Lv19 猎人
XIAOWU:    小宝 Lv2 术士, 小德 Lv19 德鲁伊
GM:        小管 Lv58 盗贼
WUJI:      赛博丶执著爱 Lv20 圣骑士
```

推荐先用 `WUYA_TEST` 的 `Wuya` 战士测试治疗跟随：

```text
.playerbots bot lookup
.playerbots bot addclass priest female
.playerbots bot list
.playerbots bot init=auto <机器人名>
```

如果只是试玩，可以按这个顺序：

```text
1. 登录 Agent PlayerBot，选择 Wuya 战士。
2. 输入：.playerbots bot addclass priest female
3. 看到牧师进队后，对她说：奶妈跟我
4. 对她说：奶妈安心奶，别输出
5. 选中一只怪自己开打，观察牧师补 buff、治疗、拾取。
6. 打完后对她说：奶妈刚才打得怎么样
```

也可以测试其他治疗：

```text
.playerbots bot addclass paladin
.playerbots bot addclass druid
.playerbots bot addclass shaman
```

如果 bot 没有按预期跟随，可以对 bot 密语英文快捷指令：

```text
follow
stay
flee
attack
pull
```

如果 worldserver 和 Python 侧车都已启动，也可以直接用中文队伍聊天/密语：

```text
牧师跟我
牧师停一下
治疗加我
加个奶
加个坦
我想下副本，组个稳一点的队
看看机器人池子
小牧初始化一下
刷新小牧
小牧同步等级
给小牧初始化副本任务
小牧下线
邀请张三进队
安心奶，别输出
捡垃圾
别捡了
补buff
奶妈刚才打得怎么样
奶妈我们现在能继续拉怪吗
```

这些话会先进入 `mod-playerbot-agent`，再由 Python 侧车转成白名单动作。没有配置大模型时，规则模式也能处理上面的常用指令。

随机世界 bot 的玩法更像路人 NPC：

```text
/w 路人bot名 晚上好
/w 路人bot名 你在干嘛
```

这类密语会带上说话玩家、目标 bot、附近敌人、地图/区域、最近战斗摘要等上下文给 LLM。因为他们是世界随机 bot，Adapter 只开放 `reply/no_reply`，所以“跟我”“加我”“全捡”不会变成控制动作。

侧车已经有独立 systemd 服务，和 auth/worldserver 平级。环境变量统一放在 `env/dist/etc/playerbot-agent.env`，首次安装服务时可从模板复制：

```bash
cd /home/wuya/git/azerothcore-wotlk-git
test -f env/dist/etc/playerbot-agent.env || install -m 0600 ops/systemd/playerbot-agent.env.dist env/dist/etc/playerbot-agent.env
sudo install -m 0644 ops/systemd/azerothcore-playerbot-agent.service /etc/systemd/system/azerothcore-playerbot-agent.service
sudo systemctl daemon-reload
sudo systemctl enable --now azerothcore-playerbot-agent.service
```

默认 env 是规则模式。接 LLM 时编辑 `env/dist/etc/playerbot-agent.env`：

```bash
PLAYERBOT_AGENT_RULE_ONLY="0"
OPENAI_API_KEY="..."
OPENAI_BASE_URL="https://api.deepseek.com"
PLAYERBOT_AGENT_MODEL="deepseek-v4-flash"
PLAYERBOT_AGENT_LLM_THINKING="enabled"
PLAYERBOT_AGENT_LLM_MAX_TOKENS="2048"
PLAYERBOT_AGENT_TRACE_PROMPT="1"
PLAYERBOT_AGENT_TRACE_REASONING="1"
PLAYERBOT_AGENT_TRACE_LLM_RAW="1"
```

改完后执行 `sudo systemctl restart azerothcore-playerbot-agent.service`。key 只放在本机 env 文件里，不写入仓库。`PLAYERBOT_AGENT_LLM_THINKING` 可写 `enabled/enable` 或 `disabled/disable`；开启 thinking 时，侧车只把最终 `content` 当作 JSON 决策输入，`reasoning_content` 只进日志，不会直接发到游戏频道。规则能命中的中文指令优先走规则，其他被点名/密语的闲聊和复杂表达再交给 LLM 输出 JSON，再由 Adapter 翻译成白名单本能动作。

调试时看三处：

```bash
tail -f env/dist/logs/playerbot-agent.log
tail -f env/dist/logs/Server.log | rg 'module.playerbot_agent|Playerbot Agent'
mysql -h127.0.0.1 -P3306 -uacore -pacore --default-character-set=utf8mb4 acore_playerbots \
  -e "SELECT id, source_event_id, status, bot_name, action_type, channel, text, command, strategy, bot_state, payload_json, result, error FROM agent_playerbot_actions ORDER BY id DESC LIMIT 20\\G"

mysql -h127.0.0.1 -P3306 -uacore -pacore --default-character-set=utf8mb4 acore_playerbots \
  -e "SELECT id, created_at, group_leader_name, duration_ms, kills, deaths, summary_text, LEFT(facts_json, 1000) AS facts FROM agent_playerbot_combat_summaries ORDER BY id DESC LIMIT 5\\G"
```

`playerbot-agent.log` 能看到大模型 prompt、响应、skill 映射、战斗摘要和入队动作；`Server.log` 能看到 C++ 桥是否真正执行；`agent_playerbot_actions` 的 `status/result/error` 是动作真相源；`agent_playerbot_combat_summaries` 是战斗记忆真相源。

## 当前可调用本能速查

完整能力矩阵见：[ARCHITECTURE-agent-playerbots.md](/home/wuya/git/azerothcore-wotlk-git/ARCHITECTURE-agent-playerbots.md) 的“Playerbots 提供的本能”。

第一批适合包装给 LLM-Agent 的本能：

| Agent intent | 当前命令桥 |
| --- | --- |
| `summon_bot` | `.playerbots bot addclass <class> [male\|female]` |
| `dismiss_bot` | `.playerbots bot remove <机器人名>` |
| `list_bots` | `.playerbots bot list` |
| `lookup_bot_pool` | `.playerbots bot lookup` |
| `init_bot` | `.playerbots bot init=auto <机器人名>` |
| `refresh_bot` | `.playerbots bot refresh <机器人名>` |
| `level_bot` | `.playerbots bot levelup <机器人名>` |
| `init_instance_quests` | `.playerbots bot quests <机器人名>` |

已经存在但需要 Adapter 再包装的本能：`follow`、`stay`、`flee/runaway`、`attack`、`pull`、`max dps`、`ready`、`revive`、任务交互、交易/买卖/装备、施法、宠物控制、`set_strategy`、邀请真实玩家。

当前 v1 已经包装的本能：

| 中文玩法 | Agent skill | Playerbots 小脑 |
| --- | --- | --- |
| “跟我/过来/跟上” | `bot_follow` | `follow` |
| “停下/原地/别动” | `bot_stay` | `stay` |
| “撤/别打了/脱战” | `bot_retreat` | `flee` |
| “跑远/散开” | `bot_runaway` | `runaway` |
| “打我的目标/集火” | `bot_attack_target` | `attack` |
| “拉怪/开怪” | `bot_pull` | `pull` |
| “加我/奶我/保我” | `focus_heal_add` | `focus heal +玩家名` |
| “不用加我/别盯我” | `focus_heal_remove` | `focus heal -玩家名` |
| “安心奶/别输出/别抢仇恨” | `healer_safe` | `-healer dps` |
| “帮忙输出/爆发” | `healer_burst` 或 `max dps` | `+healer dps` / `max dps` |
| “捡垃圾/全捡/别捡” | `loot_gray` / `loot_all` / `loot_off` | `ll gray/all` 或 `-loot` |
| “补buff/别补buff” | `buff_on` / `buff_off` | `+buff` / `-buff` |

随机世界 bot 生态现在用于“热闹”和闲聊，但不作为 LLM-Agent 可自由控制的本能。自动 LFG/BG、`rndbot`、`gtask`、`pmon/debug`、账号绑定等仍是运维/GM 能力，不给 LLM 自由调用。

## 为什么把 Playerbots 当本能

旧方案里我们自己实现了跟随、治疗、协助、跑尸、聊天桥接等能力，但这些低层行为会持续膨胀：职业循环、躲技能、喝水、BUFF、宠物、装备、天赋、副本策略，每个都很复杂。

`mod-playerbots` 已经实现了大量小脑能力：

- 职业战斗循环。
- 治疗/坦克/DPS 角色行为。
- 跟随、召唤、移动、躲 AOE、逃跑。
- 副本/团队策略。
- AddClass 快速组队。
- 装备、技能、天赋、雕文、附魔、补给、修理。
- 拾取、ROLL 点、任务同步、随机 bot 生态。

这些都适合作为“本能”。LLM-Agent 不应该重新判断每个技能怎么放，而应该调度这些本能。

## 大脑如何调用本能

目标不是让大模型输出：

```text
.playerbots bot addclass priest female
```

而是让大模型输出结构化意图：

```json
{
  "intent": "summon_bot",
  "role": "healer",
  "class_hint": "priest"
}
```

Agent 适配器再把它翻译成 Playerbots 命令或 C++ API：

```text
.playerbots bot addclass priest
```

这样可以做权限检查、队伍人数检查、冷却、频率限制和错误反馈。

## “加我”如何处理

玩家说“加我”时，LLM-Agent 应该先理解上下文：

- 是密语里的“加我进队”？
- 是队伍里的“加我一个奶/加个治疗”？
- 说话者是谁？
- 目标是否已在队伍里？
- 队伍是否满员？
- 当前 Agent 是否有权限执行？

然后转成结构化意图。

邀请真实玩家：

```json
{
  "intent": "invite_player",
  "target_player": "说话者"
}
```

召唤一个治疗 bot：

```json
{
  "intent": "summon_bot",
  "role": "healer",
  "class_hint": "priest"
}
```

当前 `summon_bot/addclass` 能力已经具备；`invite_player` 需要下一步在适配器里补上安全动作。

## 下一步开发

当前已完成最薄的 Agent Intent Adapter 和 v2 生命周期动作。下一步优先做：

1. 把“召唤后初始化/跟随”升级成显式多 intent 计划，而不是只召唤。
2. 增加动作冷却和更细的去重，避免重复聊天触发重复召唤。
3. 增加 Planner/Harness 层，让 LLM 输出 `Intent Plan` 而不是单个 skill。
4. 接入长期跨天记忆、任务规划、背包/装备策略。

阶段目标：

```text
玩家说：“加个奶”
  -> Agent 识别缺治疗
  -> 适配器调用 AddClass priest/paladin
  -> bot 入队并初始化
  -> Playerbots 小脑自己治疗、跟随、躲技能
```

这就是“上层大脑调用下层本能”的最小闭环。
