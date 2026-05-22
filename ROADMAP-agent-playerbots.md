# Agent PlayerBot 路线图

最后核对：2026-05-21

本文件记录 `playerbot-agent` 分支后续怎么把 Playerbots 的职业本能暴露给上层 Agent。架构和部署真相源仍然是 [ARCHITECTURE-agent-playerbots.md](ARCHITECTURE-agent-playerbots.md)。

本文档回答三个问题：

1. 上层 Harness / Planner Agent 怎么工作。
2. 它通过什么接口调用 Playerbots 本能。
3. Harness 是否应该做成独立、不耦合的工程。

## 总体判断

推荐路线：Harness 做成独立进程和独立工程边界，但 MVP 阶段先放在当前仓库的 `tools/` 下，等接口稳定后再拆成单独仓库。

这意味着：

- Harness 不链接 AzerothCore C++ 代码，不包含 `PlayerbotAI` 头文件，不直接调用 worldserver 内部对象。
- Harness 不直接写角色库、世界库，也不直接拼 `.playerbots` 命令。
- Harness 只读受控上下文，只输出结构化 Intent。
- Intent Adapter 做权限、冷却、参数校验和白名单转换。
- `mod-playerbot-agent` 是唯一能把外部意图落到游戏内行为的执行边界。

核心原则：

```text
LLM / Harness 不逐秒按技能。
LLM / Harness 只调度低频本能。
高频战斗、治疗、坦克、移动、躲技能仍由 mod-playerbots 小脑处理。
```

## 分层设计

```text
玩家聊天 / 世界状态 / 战斗摘要
  -> mod-playerbot-agent 采集上下文
  -> Agent Event Stream
  -> Harness / Planner Agent
  -> Intent Plan JSON
  -> Intent Adapter 校验和翻译
  -> agent_playerbot_actions
  -> mod-playerbot-agent C++ 执行
  -> mod-playerbots 小脑执行具体行为
  -> 动作结果 / 战斗复盘 / 记忆更新
```

### Harness / Planner Agent

职责：

- 理解中文聊天、密语、队伍频道和上下文。
- 判断玩家真实意图：闲聊、组队、求治疗、准备副本、战斗复盘、邀请真人。
- 做低频规划：队伍编成、职业选择、准备顺序、战斗后策略调整。
- 管理记忆：玩家偏好、最近战斗教训、常用队伍配置、失败动作结果。
- 输出 Intent Plan，不输出底层命令。

记忆分层：

- 侧车工作记忆：最近队伍聊天、附近说话、玩家密语、动作结果和战斗摘要，直接进入 prompt，窗口有限，默认只保留数小时。
- Harness 会话记忆：当前队伍目标、正在准备的副本、刚才被纠正的信息、最近几轮私聊和队伍讨论，需要跨侧车重启恢复。
- Harness 长期记忆：玩家偏好、常用队伍配置、稳定事实和失败教训，需要摘要、置信度、可删除和可覆盖。

原则：侧车可以做短期 prompt 缓存和事件表恢复，但不能把所有历史无限塞进上下文；长期归纳、检索、压缩和遗忘策略放到 Harness memory 层。

私聊记忆需要可见性边界：记录谁对谁说、来源频道和适用范围。默认只给相关玩家、相关 bot 或队伍计划使用，不能把某个玩家私聊内容随便泄露到队伍频道回复里。

不负责：

- 不选择每一秒使用哪个职业技能。
- 不直接施放具体法术。
- 不直接移动坐标级路径。
- 不直接修改 AzerothCore 数据库。
- 不直接控制随机世界 bot 的战斗行为。

### Intent Adapter

职责：

- 把 Harness 输出的 Intent Plan 转成安全动作。
- 检查权限、队伍人数、bot 是否可控、目标是否在线、阵营是否一致。
- 做冷却、频率限制、确认逻辑和错误反馈。
- 记录动作结果，给 Harness 下一轮复盘使用。

Adapter 可以先在 Python 侧车中实现；强类型动作稳定后，再把关键校验下沉到 `mod-playerbot-agent` C++ 桥。

### mod-playerbot-agent C++ 桥

职责：

- 捕获游戏内真实玩家事件。
- 聚合队伍状态、bot 状态、目标、附近敌人、战斗摘要。
- 执行 `reply / command / strategy` 以及未来强类型动作。
- 只开放白名单动作。
- 把每次执行状态写回 `agent_playerbot_actions.status/result/error`。

### mod-playerbots 小脑

职责：

- 职业循环、治疗、坦克、DPS、控制、驱散、Buff。
- 跟随、移动、躲 AOE、逃跑、跑尸、复活。
- 拾取、装备、补给、任务、副本和团队策略。
- 对触发器、优先级和动作队列做高频决策。

## Harness 是否单开工程

结论分两步。

### MVP 阶段：同仓库，独立边界

先继续放在当前仓库，建议路径：

```text
tools/playerbot-agent/
  agent_bridge.py          当前侧车和规则/LLM 适配器
  test_agent_bridge.py     当前测试

tools/playerbot-harness/   后续可新增
  planner.py               Planner Agent
  memory.py                记忆层
  intents.py               Intent Schema
  adapter_client.py        调用当前 DB 队列或未来 HTTP API
  tests/
```

这样做的原因：

- 方便和当前 DB schema、C++ 桥、运行脚本一起演进。
- 开发时不需要处理跨仓库版本同步。
- 可以在同一个 CI 或本地测试里验证 Intent 到 C++ 执行结果。
- 只要约束好导入边界，代码仍然是解耦的。

MVP 阶段的硬边界：

- `tools/playerbot-harness` 不能导入 AzerothCore C++。
- 不读取 `modules/mod-playerbots` 内部实现作为运行依赖。
- 只依赖公开的 `AgentEvent`、`IntentPlan`、`ActionResult` schema。
- 只通过 `adapter_client` 写入受控动作队列。

### 稳定阶段：可拆成独立仓库

当下面条件满足时，再拆成单独工程更合适：

- Intent Schema 连续几轮没有大改。
- 需要多服复用同一个 Harness。
- 需要独立发布、部署、监控和回滚。
- 需要引入更重的 Agent 框架、向量库、任务队列或 Web UI。
- Harness 的测试、依赖和发布节奏明显不同于 AzerothCore。

拆出后建议形态：

```text
playerbot-harness-agent/
  playerbot_harness/
    planner/
    memory/
    adapters/
    schemas/
  tests/
  pyproject.toml
```

和当前仓库的关系：

```text
azerothcore-wotlk-git
  modules/mod-playerbot-agent  游戏内执行边界
  docs/schema                  Intent/事件 schema 真相源

playerbot-harness-agent
  只依赖 schema 和网络/DB API
  不依赖 AzerothCore 源码
```

## 接口路线

### 当前 v1：DB 队列桥

已经存在：

```text
agent_playerbot_events
  - 游戏内聊天事件
  - 说话者、目标 bot、队伍成员、附近敌人、当前目标等上下文

agent_playerbot_actions
  - sidecar 写入待执行动作
  - C++ 桥轮询执行
  - status/result/error 记录执行结果

agent_playerbot_combat_summaries
  - C++ 聚合战斗事实
  - Python 侧车压缩成短战斗记忆
```

当前动作类型：

```text
reply     bot 回复
command   白名单 Playerbots 聊天快捷命令
strategy  白名单策略切换
payload   生命周期、邀请和维护类强类型 payload
```

### v2：强类型 Intent Action

建议给 `agent_playerbot_actions` 增加通用 payload：

```text
payload_json TEXT NULL
```

新动作形态：

```json
{
  "action_type": "summon_bot",
  "payload": {
    "role": "healer",
    "class_hint": "priest",
    "gender": "female"
  }
}
```

保留旧字段：

```text
command
strategy
bot_state
```

这样旧的 `reply / command / strategy` 不受影响，新能力可以逐步迁移到 `payload_json`。

### Harness 输入事件

Harness 每次处理的输入应整理成稳定 JSON：

```json
{
  "event_id": 123,
  "channel": "party",
  "speaker": {
    "guid": 1,
    "name": "Wuya",
    "class": "warrior",
    "level": 19
  },
  "message": "加个奶，准备下副本",
  "group": {
    "leader": "Wuya",
    "members": []
  },
  "bots": [
    {
      "guid": 100,
      "name": "小牧",
      "role": "healer",
      "bot_kind": "owned",
      "health_pct": 100,
      "mana_pct": 88
    }
  ],
  "environment": {
    "map_id": 0,
    "zone_id": 12,
    "selected_target": null,
    "nearby_hostiles": []
  },
  "recent_action_results": [],
  "recent_combat_summaries": []
}
```

### Harness 输出计划

Harness 输出 Intent Plan：

```json
{
  "plan_id": "evt-123",
  "summary": "队伍缺治疗，召唤牧师并初始化。",
  "intents": [
    {
      "intent": "summon_bot",
      "role": "healer",
      "class_hint": "priest",
      "gender": null,
      "reason": "玩家要求加个奶"
    },
    {
      "intent": "init_bot",
      "target": "last_summoned",
      "mode": "auto"
    },
    {
      "intent": "bot_follow",
      "target": "last_summoned"
    }
  ],
  "reply": {
    "channel": "party",
    "text": "我给你补个治疗，进队后先初始化。"
  }
}
```

Adapter 再把它变成实际动作队列。

## Intent Schema v1

第一批只做低风险、低频能力。

| Intent | 参数 | 执行边界 | 说明 |
| --- | --- | --- | --- |
| `reply` | `bot`, `channel`, `text` | C++ 桥 | bot 聊天回复。 |
| `summon_bot` | `role`, `class_hint`, `gender`, `race_hint` | MCP/C++ 强类型动作 | 从 AddClass 池召唤 bot；指定种族时 MCP 先选具体角色，再执行 `add <Botname>`。 |
| `init_bot` | `bot`, `mode=auto` | C++ 强类型动作 | 初始化 AddClass bot。普通玩家默认只允许 `auto`。 |
| `dismiss_bot` | `bot` | C++ 强类型动作 | 删除可控 bot。对 `*` 需要确认。 |
| `list_bots` | 无 | C++ 强类型动作 | 返回当前可控 bot 列表。 |
| `invite_player` | `target_player` | C++ 强类型动作 | 邀请真实玩家进队。 |
| `bot_follow` | `bot/group` | command 白名单 | 跟随主人或队伍。 |
| `bot_stay` | `bot/group` | command 白名单 | 原地停留。 |
| `bot_retreat` | `bot/group` | command 白名单 | 撤退，底层映射 `flee`。 |
| `bot_runaway` | `bot/group` | command 白名单 | 跑远或散开。 |
| `bot_attack_target` | `bot/group`, `target=selected` | command 白名单 | 攻击玩家当前目标。 |
| `bot_pull` | `bot`, `target=selected` | command 白名单 | 让指定 bot 拉当前目标。 |
| `bot_ready` | `bot/group` | command 白名单 | 准备确认。 |
| `focus_heal_add` | `bot`, `player` | command 白名单 | 治疗重点照看某玩家。 |
| `focus_heal_remove` | `bot`, `player` | command 白名单 | 取消重点治疗。 |
| `set_strategy` | `bot/group`, `add`, `remove`, `state` | strategy 白名单 | 策略切换，如 `-healer dps`。 |

Hermes MCP 已暴露对应 typed tools：

```text
wow_bot_follow
wow_bot_stay
wow_bot_retreat
wow_bot_attack_target
wow_bot_pull
wow_bot_ready
wow_bot_burst
wow_focus_heal
wow_set_loot_mode
wow_set_buff
wow_set_healer_dps
```

暂不开放：

- 任意 `cast spell`。
- 任意 `.playerbots` 原始命令。
- GM、账号、随机 bot 维护、传送、调试命令。
- 对随机世界 bot 的控制动作。
- 对角色库、世界库的直接写入。

## 阶段路线图

### v1 已完成：聊天指挥、战斗摘要和 MCP 指挥工具

状态：

- 捕获真实玩家聊天。
- Python 侧车规则优先，LLM 可选。
- 支持 `reply / command / strategy`。
- 支持跟随、停下、撤退、攻击、拉怪、重点治疗、拾取、Buff、治疗输出策略。
- Hermes MCP 已为这些白名单能力提供 typed tools，Agent 不需要直接拼英文 bot 聊天快捷命令。
- 支持战斗事实聚合和短战斗记忆。
- 随机世界 bot 密语只允许 `reply/no_reply`。

验收标准：

- 队伍内 bot 能听懂常见中文短命令。
- 战斗后可以回答“刚才打得怎么样”。
- 侧车测试通过。

### v2：队伍生命周期 API

目标：玩家可以说“加个奶”“组个稳一点的队”，Agent 自动召唤、初始化、跟随。

当前状态：MVP 已落地到 `mod-playerbot-agent` 和 `tools/playerbot-agent`。已支持 `payload_json`、召唤、初始化、删除、列表、池子查询、刷新、同步等级、副本任务和邀请真实玩家；“召唤后自动初始化/跟随”的多 intent 计划仍放到 v3/Harness 层处理。

新增能力：

- `payload_json` 或等价强类型 payload。
- `summon_bot(role, class_hint, gender, race_hint)`。
- `init_bot(bot, mode=auto)`。
- `dismiss_bot(bot)`。
- `list_bots()`。
- `invite_player(target_player)`。
- 基础去重：同一个聊天事件只生成一次生命周期动作。
- 后续动作冷却：避免重复聊天触发重复召唤多个 bot。
- 结果反馈：召唤失败、池中无角色、队伍满、目标离线等原因必须写回。

MVP 用例：

```text
玩家：加个奶
Agent: summon_bot(role=healer, class_hint=priest)
C++: 从 AddClass 池召唤牧师
Agent: init_bot(mode=auto)
Agent: bot_follow
```

验收标准：

- 不需要玩家手动输入 `.playerbots bot addclass priest`。
- 失败时 bot 或系统能说出可理解原因。
- LLM 不能绕过 Adapter 执行原始命令。

### v3：Harness Planner

目标：从“中文命令映射器”升级成“队伍长”。

新增能力：

- 事件分类：闲聊、组队、战斗指挥、副本准备、复盘、邀请真人。
- 多 Intent 计划：一句话拆成多个 bot 的动作。
- 短期记忆：最近聊天、最近动作结果、当前队伍目标。
- 战斗复盘策略：根据战斗摘要调整 `healer_safe`、`focus_heal`、`stay`、`pull` 等。
- Planner 测试：给定事件和上下文，断言输出 Intent Plan。

MVP 用例：

```text
玩家：牧师保我，法师别 A，战士拉我目标
Agent:
  focus_heal_add(牧师, Wuya)
  set_strategy(法师, remove=["aoe"])
  bot_pull(战士, target=selected)
```

验收标准：

- 一句话多对象、多动作能正确拆解。
- Planner 输出只包含 Intent Schema 允许的动作。
- 每个 intent 都有 `reason`，方便审计。

### v4：偏好记忆和副本准备

目标：让 Agent 记住玩家偏好和常用流程。

新增能力：

- 玩家偏好表：默认队伍配置、默认拾取、是否允许治疗输出、常用职业偏好。
- 副本准备 checklist：队伍人数、角色职责、Buff、治疗蓝量、任务、装备初始化。
- 战斗失败经验：某副本、某 Boss、某队伍配置下的调整建议。
- 记忆写入需要规则保护，避免 LLM 把一次偶然事件写成永久偏好。

MVP 用例：

```text
玩家：老样子组队
Agent:
  读取 Wuya 偏好
  召唤牧师治疗、法师远程 DPS
  设置治疗专心奶
  开启捡灰色垃圾
```

验收标准：

- 记忆可以查看、覆盖、删除。
- 偏好不会污染其他玩家。
- Agent 能解释“为什么这样组队”。

### v5：独立 Harness 工程和多服复用

目标：把 Harness 从当前仓库拆出，成为可复用 Agent 服务。

新增能力：

- 独立 Python package。
- 版本化 schema。
- 多 realm / 多服务器配置。
- HTTP 或 gRPC API 可选，但 DB 队列仍可保留为稳定后端。
- 监控面板：事件、计划、动作、失败原因、记忆命中。

拆分条件：

- v2/v3 的 Intent Schema 已稳定。
- 当前单服验证可用。
- 需要独立部署和回滚。
- 需要接更重的 Harness 框架、向量记忆或任务队列。

## 典型用例

### 用例 1：一句话组队

```text
玩家：我想下影牙，给我组个稳一点的队。
```

Agent 判断：

- 玩家等级和职业。
- 目标副本需要坦、治疗、DPS。
- 当前队伍缺什么。
- AddClass 池是否可用。

输出：

```json
[
  {"intent": "summon_bot", "role": "tank", "class_hint": "warrior"},
  {"intent": "summon_bot", "role": "healer", "class_hint": "priest"},
  {"intent": "summon_bot", "role": "dps", "class_hint": "mage"},
  {"intent": "init_bot", "target": "all_new_bots", "mode": "auto"},
  {"intent": "set_strategy", "target": "healer", "remove": ["healer dps"], "state": "combat"}
]
```

### 用例 2：战斗后调参

战斗摘要：

```text
治疗被怪命中多次，战士最低血量 18%，牧师最低蓝量 22%。
```

Agent 输出：

```json
[
  {"intent": "set_strategy", "target": "healer", "remove": ["healer dps"], "state": "combat"},
  {"intent": "focus_heal_add", "bot": "healer", "player": "Wuya"},
  {"intent": "reply", "channel": "party", "text": "刚才治疗吃了点仇恨，下一波慢点拉，我专心保你。"}
]
```

### 用例 3：邀请真实玩家

```text
路人密语：加我，我是法师。
```

Agent 判断：

- 目标是否在线。
- 阵营是否一致。
- 队伍是否满。
- 当前队伍是否需要 DPS。

输出：

```json
{"intent": "invite_player", "target_player": "路人法师"}
```

### 用例 4：副本前准备

```text
玩家：准备一下，马上开。
```

Agent 检查：

- 有无死人。
- 治疗蓝量。
- 是否缺 Buff。
- bot 是否初始化。
- 当前目标和附近敌人。

输出：

```json
[
  {"intent": "bot_ready", "target": "group"},
  {"intent": "set_strategy", "target": "group", "add": ["buff"], "state": "noncombat"},
  {"intent": "reply", "channel": "party", "text": "等治疗蓝到 80% 再开。"}
]
```

## 安全边界

必须长期保持：

- LLM 不直接输出原始 `.playerbots` 命令。
- LLM 不直接控制随机世界 bot 的战斗、移动、拾取、治疗。
- 所有动作必须有白名单。
- 所有强类型动作必须写执行结果。
- 所有回复和动作必须绑定当前轮 `current_event_id`；已处理旧事件上的动作默认由 MCP 入队层拒绝。
- 所有高风险动作必须有权限检查和冷却。
- 普通玩家默认只能 `init=auto`。
- `dismiss_bot("*")`、账号类、GM 类、传送类、维护类动作默认不开放。

推荐审计字段：

```text
source_event_id
requester_guid / requester_name
plan_id
intent
payload_json
adapter_decision
status
result
error
created_at / updated_at
```

## 近期开发顺序

1. 写 `Intent Schema v1` 到代码和测试中。
2. 给 `agent_playerbot_actions` 增加 `payload_json`。
3. 在 C++ 桥实现 `summon_bot / init_bot / dismiss_bot / list_bots`。
4. 实现 `invite_player`，先走严格权限和队伍容量检查。
5. 在 Python Adapter 增加 `加个奶 / 加个坦 / 组队` 规则。
6. 给 LLM prompt 增加 Intent Plan 输出格式，不再只输出 skill。
7. 增加 Planner 单元测试和一组端到端手工验证脚本。
8. 再引入独立 `tools/playerbot-harness`，承接多步规划和记忆。

## 暂不做

- 不做帧级战斗 Agent。
- 不开放任意施法。
- 不做大型自主任务系统。
- 不把 Harness 直接编译进 worldserver。
- 不先拆独立仓库。
- 不先引入复杂向量库。

先把小而硬的接口闭环跑通，再让上层 Agent 逐步变聪明。
