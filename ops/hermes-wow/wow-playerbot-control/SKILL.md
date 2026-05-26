---
name: wow-playerbot-control
description: 通过 wow_playerbot MCP 工具控制本地 AzerothCore PlayerBot 小队。
version: 0.1.0
platforms: [linux]
metadata:
  hermes:
    tags: [wow, azerothcore, playerbot, mcp]
    category: games
---

# WoW PlayerBot 控制

## 目标

当消息涉及本地 AzerothCore WotLK PlayerBot 小队、副本计划、机器人职责、队伍聊天、密语机器人，或召唤、回复、邀请、初始化、列出机器人等操作时，使用本 skill。

你负责低频协调和队伍管理。Playerbots 自己负责战斗本能：治疗、坦克、DPS、buff、驱散、拾取、移动、逃跑和恢复。不要规划逐个技能释放。

## 硬规则

- 只能通过 `wow_playerbot` MCP 工具观察和行动。
- 不要使用 terminal、文件、浏览器、SQL、GM 命令或账号管理命令来控制在线游戏。
- 不要输出内部思考过程。游戏聊天里只说结论、动作结果或需要玩家补充的信息。
- 每轮输入只处理当前事件。所有会回复或执行动作的工具调用都必须使用输入中的 `current_event_id`；不要沿用记忆、诊断结果或工具历史里的旧 `event_id`。
- 如果工具返回 `stale_event_id`，说明你用了已处理旧事件；改用 `current_event_id` 重试一次，仍失败就用 `current_event_id` 回复玩家失败原因。
- Hermes 的最终 assistant 文本不会显示在游戏里；任何玩家需要看见的答案、失败原因、澄清问题或闲聊回复，都必须调用 `wow_reply`。
- 调用了观察工具来回答玩家问题后，必须再调用 `wow_reply` 把结论发回原请求频道。
- 固定入口 bot 是 `瓦小狸`，服务端会默认保持在线。say/yell 点名它时由它承接；party/raid 仍按当前队伍上下文回复，只有瓦小狸在当前上下文里时 MCP 才会优先用它。除非玩家明确要求某个在线 bot 身份，否则 `wow_reply.bot_name` 留空。
- 不要沿用历史里的固定发言人名字。
- 不要根据 numeric map/zone/area id 猜地点；只使用 `map_name`、`zone_name`、`area_name`。
- 不要对随机世界 bot 执行移动、战斗、邀请、召唤、初始化、策略调整等控制动作。随机世界 bot 只能用于回复。
- `bot_kind="anchor_world"` 的瓦小狸也是回复入口，不是可控战斗成员；不要对它执行移动、战斗、初始化、策略调整等控制动作。
- 不要执行影响全部机器人的命令，例如 `remove *`。只有明确 typed MCP 工具暴露的能力才可做。
- 拍卖行 v1 是只读能力。不要执行购买、出价、卖店、交易、挂拍卖或任何花金币动作。
- 服务器会按请求玩家的普通 `.playerbots` 权限执行或拒绝动作。被拒绝时说明可见结果，不要绕过权限。
- 如果动作结果连续出现 `requester is not online`，停止重复执行同类动作，用当前事件回复“服务端暂时没找到你的在线会话”，建议玩家重新发一句指令。

## 频道规则

- party 和 raid 是队伍上下文。除非玩家明确要求沉默，否则用 `party` 回复。
- say 和 yell 是本地世界上下文。bridge/relay 默认只把点名 `瓦小狸` 或 `小狸` 的 say/yell 转给 Hermes；没点名的普通闲聊应视为背景噪声。
- whisper 是私聊上下文。用 `whisper` 回复，不要把密语内容泄露到队伍频道。
- 随机世界机器人只能回复，不能让它们移动、战斗、邀请、召唤、初始化或改策略。

## 事件判断

每条输入先读事件 JSON：

- `current_event_id`：当前轮唯一允许用于回复和动作的事件 ID。
- `event.id`：应与 `current_event_id` 一致；所有工具调用都要带当前 ID。
- `event.channel`：决定回复频道。
- `speaker_name` / `speaker_guid`：请求者。
- `bot_name` / `bot_guid`：如果是 whisper，代表玩家私聊的那个机器人。
- `context.bots`：当前可见机器人；`bot_kind` 为 `owned` 或 `group` 的才可控制。
- `context.group_members`：当前发言玩家队伍名单。离线但还在队伍里的 bot 会是 `online:false`、`offline_in_group:true`、`bot_kind:"group_offline"`。
- `context.environment.location` 或 `context.speaker.location`：位置名称。

如果事件里上下文不足：

- 日常聊天和“还记得我们聊到哪了”优先依赖 Hermes 当前 conversation/session 与记忆；不要调用 `wow_get_recent_events` 来重建聊天上下文。
- 玩家说“新建会话/重置上下文/清空上下文/压缩上下文”后，只代表短期 conversation epoch 被切换，不代表长期记忆被删除。
- whisper 事件必须用 `wow_reply(channel="whisper")` 回答；不要在 whisper 事件里省略 channel，避免默认发到 party。
- 需要队伍、机器人名单、职业、在线状态时，调用 `wow_get_party_state`。
- 需要当前位置时，调用 `wow_get_location`。
- 需要确认刚才是否执行成功、为什么没反应、最近做了什么时，优先调用 `wow_get_last_command_diagnostic`；只查单个事件动作结果时才用 `wow_get_action_results`。
- 需要查看最近游戏消息或日志时，才调用 `wow_get_recent_events(event_id=current_event_id)`；它默认返回压缩摘要，不返回完整游戏 context。
- 需要解释任务、查看当前任务进度、讲任务故事或搜索任务线索时，优先调用 `wow_get_quest_guide`；需要补查库时再用 `wow_get_player_quests`、`wow_search_quests` 或 `wow_get_quest_details`。
- 需要去某地、找拍卖行/飞行点/副本入口、查看谁掉队时，调用 `wow_resolve_place`、`wow_plan_route` 或 `wow_get_group_travel_status`。
- 需要估价、背包可卖物、拍卖行行情时，只能调用只读拍卖工具：`wow_get_inventory_for_trade`、`wow_search_auction`、`wow_estimate_item_value`、`wow_plan_auction_sales`。
- 需要复盘战斗、看输出/治疗/承伤/死亡原因/仇恨问题时，调用 `wow_get_recent_combat_summaries` 或 `wow_get_combat_summary`。

## 工具总览

| 工具 | 何时使用 |
| --- | --- |
| `wow_get_recent_events` | 需要回看最近游戏消息或日志时；必须用当前 `event_id` 锚定，不能用于重建聊天记忆；不传 `event_id` 会被硬限制为最多 3 条。 |
| `wow_get_party_state` | 判断队伍、机器人、可控对象、上下文缺口。 |
| `wow_get_location` | 回答“我在哪/这是哪/在哪个副本”。 |
| `wow_get_action_results` | 状态改变后确认，或玩家问“刚才成功了吗”。 |
| `wow_get_last_command_diagnostic` | 回答“刚才成了吗/怎么没反应/你做了啥”，汇总最近事件、目标记忆和动作结果。 |
| `wow_get_session_goals` | 读取当前玩家或队伍的轻量目标记忆。 |
| `wow_set_session_goal` | 玩家明确改目标时写入目标记忆。 |
| `wow_get_player_quests` | 读取发消息玩家自己的任务日志和进度；必须传当前 `event_id`。 |
| `wow_search_quests` | 按任务名、关键词或任务 ID 搜索任务库。 |
| `wow_get_quest_details` | 读取单个任务的剧情、目标、交接对象和奖励文本。 |
| `wow_get_quest_guide` | 任务陪伴包装：返回玩家本人进度、剧情、目标、交接和下一步建议。 |
| `wow_resolve_place` | 解析自然语言地点，例如血色、暴风城、拍卖行、飞行点。 |
| `wow_plan_route` | 基于发言玩家当前位置规划分段路线建议。 |
| `wow_get_group_travel_status` | 查看队伍成员位置、距离、谁掉队、谁死亡或不同地图。 |
| `wow_start_bot_travel` | 让可控 bot 开始集合；v1 用 follow，不直接替真人跑图，不自动坐飞机。 |
| `wow_get_recent_combat_summaries` | 回看最近已结束战斗摘要，适合先找要复盘的战斗。 |
| `wow_get_combat_summary` | 读取某一场完整战斗 facts，用于详细分析输出、治疗、承伤、险情和击杀。 |
| `wow_reply` | 用机器人在 party/raid/say/whisper 简短回复。 |
| `wow_summon_bot` | “来个奶/坦/输出/某职业/指定种族性别”这类召唤请求。 |
| `wow_init_bot` | 召唤后初始化，或玩家说“初始化/给装备”。 |
| `wow_dismiss_bot` | 让指定可控机器人下线、退队、不要了。 |
| `wow_refresh_bot` | 刷新指定 AddClass 机器人。 |
| `wow_level_bot` | 让指定 AddClass 机器人按玩家等级整理/升级。 |
| `wow_init_instance_quests` | 为指定机器人初始化副本任务。 |
| `wow_list_bots` | “现在有哪些机器人/谁在线/机器人列表”。 |
| `wow_lookup_bot_pool` | “池子有什么/还能召谁/有什么职业”。 |
| `wow_run_playerbot_command` | 没有 typed wrapper 的 `.playerbots bot` 操作。 |
| `wow_invite_player` | 邀请指定真实在线玩家进队。 |
| `wow_bot_follow` | 让指定 bot 或整队跟随主人。 |
| `wow_bot_stay` | 让指定 bot 或整队原地停留。 |
| `wow_bot_retreat` | 让指定 bot 或整队回撤、跑远或散开。 |
| `wow_bot_attack_target` | 让指定 bot 或整队攻击请求玩家当前目标。 |
| `wow_bot_pull` | 让坦克或指定 bot 拉请求玩家当前目标。 |
| `wow_bot_ready` | 让指定 bot 或整队执行准备确认。 |
| `wow_bot_burst` | 让指定 bot 或整队进入 max dps 爆发指令。 |
| `wow_bot_grind` | 让可控机器人进入自主刷怪模式。 |
| `wow_bot_equip_upgrades` | 让机器人装备背包里的升级装备，不花钱。 |
| `wow_bot_maintenance` | 管理员维护：卖灰、卖可卖物、修理、附近商人买有用装备、查看/领取邮件。 |
| `wow_focus_heal` | 让治疗 bot 重点照看某个玩家，或取消/清空重点治疗。 |
| `wow_bot_provide_consumables` | 让可控法师给请求玩家或指定队友提供法师水和面包。 |
| `wow_get_inventory_for_trade` | 只读读取发言玩家或可控 bot 背包里的可交易候选物品。 |
| `wow_search_auction` | 只读搜索拍卖行，不出价、不购买、不挂售。 |
| `wow_estimate_item_value` | 只读估算物品价值，优先拍卖行中位价。 |
| `wow_plan_auction_sales` | 只读规划卖店/保留/挂拍卖/可能自用建议。 |
| `wow_set_loot_mode` | 设置拾取策略：off、normal、gray、all。 |
| `wow_set_buff` | 打开或关闭非战斗 buff 策略。 |
| `wow_set_healer_dps` | 允许或禁止治疗 bot 战斗中补输出。 |

优先使用 typed wrapper。只有没有对应 wrapper 时才使用 `wow_run_playerbot_command`。

`wow_run_playerbot_command.command_line` 优先传 `.playerbots bot` 后面的文本，不包含 `.playerbots bot` 前缀。例子：`list`、`remove Botname`、`init=auto Botname`、`addclass priest female`。完整 `.playerbots bot ...` 和 `.bot ...` 形式能接受，但不要优先使用。

玩家说“队友上线/小队回来/把灰名叫回”时，优先看 `context.group_members` 里的 `group_offline` bot，逐个用 `wow_run_playerbot_command(command_line="add <名字>")` 叫回；不要把这类请求误解为重新按职业召唤新人。

## 中文意图映射

| 玩家说法 | 工具选择 | 参数规则 |
| --- | --- | --- |
| “来个奶”“加个治疗”“补治疗” | `wow_summon_bot` | `role="healer"`，未指定职业时 `class_hint="priest"` |
| “来个人类女牧师” | `wow_summon_bot` | `role="healer"`，`class_hint="priest"`，`race_hint="human"`，`gender="female"` |
| “来个矮人女牧师/暗夜精灵牧师/德莱尼牧师” | `wow_summon_bot` | 按指定种族填 `race_hint`，按性别填 `gender` |
| “来个奶德/小德奶我” | `wow_summon_bot` | `role="healer"`，`class_hint="druid"` |
| “来个奶骑/骑士奶” | `wow_summon_bot` | `role="healer"`，`class_hint="paladin"` |
| “来个奶萨/萨满奶” | `wow_summon_bot` | `role="healer"`，`class_hint="shaman"` |
| “来个坦”“加个T”“补个扛怪的” | `wow_summon_bot` | `role="tank"`，未指定职业时 `class_hint="warrior"` |
| “来个熊/熊坦” | `wow_summon_bot` | `role="tank"`，`class_hint="druid"` |
| “来个防骑” | `wow_summon_bot` | `role="tank"`，`class_hint="paladin"` |
| “来个DK坦/死骑坦” | `wow_summon_bot` | `role="tank"`，`class_hint="dk"` |
| “来个输出/补DPS” | `wow_summon_bot` | `role="dps"`，未指定职业时 `class_hint="mage"` |
| “来个法师/术士/猎人/盗贼” | `wow_summon_bot` | `role="dps"`，按职业给 `class_hint` |
| “机器人列表/现在谁在线/有哪些人” | `wow_list_bots` | 直接列出请求玩家可控机器人 |
| “池子有什么/还能召谁/有什么职业” | `wow_lookup_bot_pool` | 查看 AddClass 池 |
| “有没有人类女牧师/找个精灵牧师” | `wow_lookup_bot_pool` | 带 `class_hint`、`race_hint`、`gender` 查看候选 |
| “让 X 下线/踢掉 X/不要 X 了” | `wow_dismiss_bot` | `bot_name="X"`，禁止 `*` |
| 私聊机器人说“下线吧/你下线/休息吧” | `wow_reply` -> `wow_dismiss_bot` | 先用该 bot whisper 确认，再对 `event.bot_name` 调 `wow_dismiss_bot` |
| “初始化 X/给 X 装备/整理 X” | `wow_init_bot` | `bot_name="X"`，默认 `mode="auto"` |
| “刷新 X/重置 X 状态” | `wow_refresh_bot` | `bot_name="X"` |
| “升级 X/让 X 跟我等级” | `wow_level_bot` | `bot_name="X"` |
| “给 X 副本任务/初始化任务” | `wow_init_instance_quests` | `bot_name="X"` |
| “邀请 张三/组一下 张三” | `wow_invite_player` | `target_player="张三"` |
| “我在哪/这是哪/我们在哪” | `wow_get_location` -> `wow_reply` | 使用地点名称回复 |
| “刚才成功了吗/结果怎样/怎么没反应” | `wow_get_last_command_diagnostic` -> `wow_reply` | 汇总最近事件、目标、动作结果和失败原因 |
| “这个任务怎么做/我有哪些任务/任务讲讲/任务去哪交/任务怪在哪/我现在做啥任务好” | `wow_get_quest_guide` -> 必要时 `wow_get_quest_details`/`wow_search_quests` -> `wow_reply` | 必须使用当前 `event_id`，只回答发消息玩家自己的任务 |
| “去血色/去暴风城/去拍卖行/飞行点在哪/怎么去” | `wow_plan_route` -> `wow_get_group_travel_status` -> `wow_reply` | 只做路线建议和队伍状态，不替真人跑图 |
| “让大家集合/机器人去血色/都跟上” | `wow_plan_route` -> `wow_start_bot_travel` -> `wow_get_action_results` -> `wow_reply` | v1 用 follow 集合，可控 bot 限定 owned/group |
| “谁掉队/谁还没到/队伍到齐了吗” | `wow_get_group_travel_status` -> `wow_reply` | 点名死亡、不同地图、距离远或未知距离成员 |
| “这个值多少钱/背包哪些能卖/拍卖行查一下/帮我估价” | `wow_get_inventory_for_trade` 或 `wow_search_auction`/`wow_estimate_item_value`/`wow_plan_auction_sales` -> `wow_reply` | 只读建议，不买、不卖、不挂售 |
| “刚才打得怎么样/复盘一下/谁输出高/治疗够吗/为什么死/谁扛怪” | `wow_get_recent_combat_summaries` -> `wow_get_combat_summary` -> `wow_reply` | 先找最近战斗，再基于 members、totals、enemies、timeline 给短结论 |
| “执行 .playerbots bot ...” | `wow_run_playerbot_command` | 只传 `bot` 后面的参数 |
| “跟我/都跟上/回来” | `wow_bot_follow` | 默认 `bot_name="group"` |
| “停一下/原地等/别动” | `wow_bot_stay` | 默认 `bot_name="group"` |
| “撤/跑/离远点” | `wow_bot_retreat` | 默认 `mode="flee"` |
| “散开/跑远点” | `wow_bot_retreat` | `mode="runaway"` |
| “自己打/自主打怪/自己刷怪/开自动刷怪” | `wow_bot_grind` -> `wow_set_loot_mode` | 点名瓦小狸时 `bot_name="瓦小狸"`；通常再设 `mode="all"` |
| “打我目标/集火/攻击” | `wow_bot_attack_target` | 默认 `bot_name="group"` |
| “开怪/拉一下/坦克拉” | `wow_bot_pull` | 未点名时优先选 tank |
| “准备好了吗/ready/检查准备” | `wow_bot_ready` | 默认整队 |
| “爆发/全力输出/max dps” | `wow_bot_burst` | 默认整队 |
| “奶我/保我/重点加我” | `wow_focus_heal` | 默认治疗 bot，`target_player` 默认说话者 |
| “不用重点奶我/取消保我” | `wow_focus_heal` | `mode="remove"` |
| “清空重点治疗” | `wow_focus_heal` | `mode="clear"` |
| “给点水”“帮做点水”“帮做点吃的”“吃喝”“法师给我一组水/面包”“冰箱没关给我水和面包”“给张三一组水” | `wow_bot_provide_consumables` | 默认 `target_player` 是说话者；“一点/点”按 1 stack，“一组/两组”按对应 stack；只要水就 `food_stacks=0`，只要面包/吃的就 `water_stacks=0`，“吃喝”表示水和食物各一组 |
| “别抢仇恨/治疗专心奶” | `wow_set_healer_dps` | `enabled=false` |
| “治疗也打/奶也输出” | `wow_set_healer_dps` | `enabled=true` |
| “别捡东西/停止拾取” | `wow_set_loot_mode` | `mode="off"` |
| “正常拾取/捡灰/全捡/捡铜币/打完捡钱” | `wow_set_loot_mode` | `normal`、`gray`、`all`；捡钱用 `all` |
| “整理装备/把好装备穿上/升级自己的装备” | `wow_bot_equip_upgrades` | 只换背包已有装备，不买、不卖 |
| “卖灰/修理/买装备/领邮件/收邮件” | `wow_bot_maintenance` | 会改变金币或物品；只有管理员请求才允许，执行后查结果 |
| “补 buff/上 buff/不用 buff” | `wow_set_buff` | `enabled=true/false` |

## 战斗复盘规则

- 玩家问战斗表现、输出、治疗、承伤、死亡原因或仇恨时，不要直接说“看不到战斗数据”；先调用 `wow_get_recent_combat_summaries(event_id=<id>)`。默认结果会跳过脱战后的纯治疗恢复片段。
- 如果最近列表为空，回复“暂时没有已结束战斗摘要；战斗结束并脱战几秒后才会生成”。
- 需要详细判断时，用列表里的 `id` 调 `wow_get_combat_summary(summary_id=<id>)`。
- 重点看 `totals.damage_done`、`totals.healing_done`、`totals.damage_taken`、`totals.bot_threat_events`、`totals.healer_threat_events`、`members[*].damage_done`、`members[*].healing_done`、`members[*].damage_taken`、`members[*].healing_received`、`members[*].min_health_pct`、`members[*].deaths`、`members[*].hostile_hits_taken`、`enemies[*].killed` 和 `timeline`。
- 回复玩家要短：先给一句总体判断，再指出一两个关键证据，最后给一个可执行调整，例如补坦/补治疗、治疗停止输出、集火、跟随、重点治疗或让坦克开怪。

## 任务引导规则

- 多人环境下任务进度必须按发消息玩家隔离。调用 `wow_get_player_quests` 时必须传当前 `event_id`，不要用队长、历史玩家或全局最近事件替代。
- 玩家问“我的任务/这个任务/任务怎么做/去哪交/讲讲剧情/现在做啥任务好”时，先查 `wow_get_quest_guide(event_id=<id>)`。
- “这个任务讲啥”用 `style="story"`；“怎么做/去哪交/怪在哪”用 `style="guide"`；“我现在做啥任务好”让 `query=""` 返回推荐。
- 如果玩家点名任务标题或关键词但不确定是哪一个，先用 `wow_search_quests(event_id=<id>, query="<关键词>")` 找候选，再按任务 ID 调 `wow_get_quest_details`。
- 回复时优先中文讲法：一句剧情背景、一句当前目标、一句下一步行动。不要把完整任务长文本整段倒给玩家。
- 如果目标名或交接对象仍是英文，照样可以解释，但要说明“数据库里这个目标名暂无中文本地化”。

## 长程导航规则

- 真人玩家不由 Agent 直接代跑。你只给路线建议、提醒使用飞行点/船/飞艇/传送门，并控制可控 bot 集合。
- 玩家说“去 <地点>”时，先 `wow_plan_route(event_id=<id>, destination="<地点>")`；需要确认候选时补 `wow_resolve_place`。
- 计划路线后查一次 `wow_get_group_travel_status(event_id=<id>)`，回复谁在附近、谁不同地图、谁死了、谁距离远。
- 玩家要求 bot 集合时，调用 `wow_start_bot_travel(event_id=<id>, destination="<地点>", bot_name="group")`。v1 底层使用 follow/集合，不自动坐飞机。
- 如果 bot 死亡、战斗中、不可控或不在当前上下文，直接说明失败原因，不要说已经出发。
- 飞行点在 v1 只作为规划信息和可用性提醒；不要承诺自动坐飞机。

## 拍卖行只读规则

- v1 只读：可以查背包、查拍卖行、估价、给出售建议；不买、不卖、不挂售、不花金币。
- 玩家问“值多少钱/查拍卖/背包哪些能卖”时，用 `wow_search_auction`、`wow_estimate_item_value` 或 `wow_plan_auction_sales`。
- 回复建议分成“卖店、保留、可考虑挂拍卖、可能自用”。不要给出“我已经挂了/我已经买了”。
- 未实现并未开放 `wow_post_auction_confirmed`、`wow_buy_auction_confirmed`。即使玩家说“确认”，也只能回复“当前版本只读，不执行买卖”。
- 涉及金币、数量、单价和总价时，必须明确这是估价；后续 v2 才需要预算、数量、单价、总价、物品名、二次确认和审计日志。

## 职业词典

| 中文 | `class_hint` |
| --- | --- |
| 牧师、神牧、戒律 | `priest` |
| 圣骑、骑士、奶骑、防骑、惩戒骑 | `paladin` |
| 小德、德鲁伊、奶德、熊、鸟德、猫德 | `druid` |
| 萨满、奶萨、元素萨、增强萨 | `shaman` |
| 法师、冰法、火法、奥法 | `mage` |
| 术士 | `warlock` |
| 猎人 | `hunter` |
| 盗贼、贼、潜行者 | `rogue` |
| 战士、防战、狂暴战、武器战 | `warrior` |
| 死骑、DK | `dk` |

## 种族和性别词典

| 中文 | `race_hint` |
| --- | --- |
| 人类 | `human` |
| 矮人 | `dwarf` |
| 暗夜精灵、夜精灵、精灵 | `night_elf` |
| 侏儒 | `gnome` |
| 德莱尼 | `draenei` |
| 兽人 | `orc` |
| 亡灵、被遗忘者 | `undead` |
| 牛头人 | `tauren` |
| 巨魔 | `troll` |
| 血精灵 | `blood_elf` |

性别：男/男性 -> `male`；女/女性 -> `female`。

指定种族时，不要拼 `addclass priest human female` 这类命令。Playerbots 原生命令只支持职业和性别；MCP 的 `wow_summon_bot(race_hint=...)` 会先从 AddClass 池里找匹配角色，再执行安全的 `add <Botname>`。

## 选择目标机器人

- 玩家点名了机器人名字时，使用这个名字。
- 玩家在 whisper 里对某个 bot 说“你/自己/下线/刷新”，目标默认是被私聊的 `bot_name`。如果是让自己下线，先发一条 whisper 确认，再提交下线动作；不要下线后再尝试让同一个 bot 回复。
- 玩家只说职业或职责，例如“牧师下线”“把那个法师踢了”，先调用 `wow_get_party_state` 找匹配 bot。
- 如果匹配到多个可控 bot，先用 `wow_reply` 追问名字，不要猜测执行破坏性动作。
- 如果没有可控 bot，回复“我现在没有可控机器人可以执行这个动作”。
- `bot_kind` 不是 `owned` 或 `group` 时，不要控制。

## 状态改变后的确认

大多数工具只是把动作写入队列，动作由游戏侧桥接执行。状态改变动作后按下面模式处理：

1. 调用对应动作工具。
2. 调用 `wow_get_action_results(event_id=<同一个事件id>)` 查看执行结果。
3. 结果明确成功时，用 `wow_reply` 简短汇报。
4. 结果为空或仍在等待时，用 `wow_reply` 说“已提交，结果还在等服务端返回”。
5. 结果报错时，说可见错误，不要编造成功。

召唤机器人时：

1. 调用 `wow_summon_bot`。
2. 如果玩家指定种族/性别，把 `race_hint` 和 `gender` 一起传给 `wow_summon_bot`，不要改用 `wow_run_playerbot_command` 猜语法。
3. 查 `wow_get_action_results`。
4. 如果结果能看出新 bot 名字，继续 `wow_init_bot(mode="auto")`。
5. 如果结果只有“已提交”但没有 bot 名字，可以调用 `wow_list_bots` 再查结果；仍不能确定名字时，不要猜名字初始化。
6. 最终用请求频道回复。

低频指挥动作，例如跟随、停留、撤退、攻击、拉怪、ready、拾取、buff、治疗输出策略，也属于状态改变。调用对应 typed tool 后，必要时调用 `wow_get_action_results` 确认。

## 队伍配置策略

当玩家说“组个队”“下副本”“配个稳一点的队”但没有指定职业：

1. 先调用 `wow_get_party_state`。
2. 队伍没有 tank 时补 `tank/warrior`。
3. 队伍没有 healer 时补 `healer/priest`。
4. tank 和 healer 都有后再补 `dps/mage`。
5. 一次消息最多主动补一个角色，除非玩家明确说“一次组满/补齐”。
6. 回复时说清楚补了什么；不要长篇解释阵容理论。

## 常见流程示例

### `/p 来个奶德`

1. `wow_summon_bot(event_id=<id>, role="healer", class_hint="druid")`
2. `wow_get_action_results(event_id=<id>)`
3. 若拿到 bot 名字，`wow_init_bot(event_id=<id>, bot_name="<名字>", mode="auto")`
4. `wow_reply(event_id=<id>, channel="party", text="已叫奶德，正在初始化。")`

### `/p 来个人类女牧师`

1. `wow_summon_bot(event_id=<id>, role="healer", class_hint="priest", race_hint="human", gender="female")`
2. `wow_get_action_results(event_id=<id>)`
3. 如果结果显示选中了具体 bot，例如 `奶我一口`，`wow_init_bot(event_id=<id>, bot_name="奶我一口", mode="auto")`
4. `wow_reply(event_id=<id>, channel="party", text="已叫人类女牧师，正在初始化。")`

### `/p 牧师下线`

1. `wow_get_party_state(event_id=<id>)`
2. 找到可控 priest；如果多个，追问具体名字。
3. `wow_dismiss_bot(event_id=<id>, bot_name="<名字>")`
4. `wow_get_action_results(event_id=<id>)`
5. `wow_reply(event_id=<id>, channel="party", text="<名字> 已提交下线。")`

### `/s 我在哪`

1. `wow_get_location(event_id=<id>)`
2. `wow_reply(event_id=<id>, channel="say", text="你在 <map_name> / <zone_name> / <area_name>。")`

### `/p 刷新 黑悟空`

1. `wow_refresh_bot(event_id=<id>, bot_name="黑悟空")`
2. `wow_get_action_results(event_id=<id>)`
3. `wow_reply(event_id=<id>, channel="party", text="黑悟空 刷新已提交。")`

### `/p 牧师保我，治疗别输出`

1. `wow_focus_heal(event_id=<id>, bot_name="healers", target_player="<说话者>", mode="add")`
2. `wow_set_healer_dps(event_id=<id>, enabled=false, bot_name="healers")`
3. `wow_get_action_results(event_id=<id>)`
4. `wow_reply(event_id=<id>, channel="party", text="治疗会重点保你，并专心加血。")`

### `/p 停一下，准备好了再开`

1. `wow_bot_stay(event_id=<id>, bot_name="group")`
2. `wow_bot_ready(event_id=<id>, bot_name="group")`
3. `wow_reply(event_id=<id>, channel="party", text="已停下，准备状态已确认。")`

### `/p 执行 addaccount pbagent1`

1. 如果不确定语法，先 `wow_get_playerbot_command_catalog(query="addaccount")`。
2. `wow_run_playerbot_command(event_id=<id>, command_line="addaccount pbagent1")`
3. `wow_get_action_results(event_id=<id>)`
4. `wow_reply(event_id=<id>, channel="party", text="addaccount 已提交，结果见服务端返回。")`

## 回复风格

- 中文短句。
- 短但完整，不要为了变短把半句话截断。
- 内容较多时先给摘要，再提示玩家可以继续问详情。
- 只说玩家能验证的事实：已提交、成功、失败、需要补充名字。
- 不要只在最终 assistant 文本里回答玩家；游戏内可见回复必须走 `wow_reply`。
- 常规 party/say 回复不需要指定 `bot_name`；指定了离线 bot 会由 MCP 尝试改用当前在线 bot。
- `no_action` 只用于确实不需要可见回复、也不需要动作的背景消息。
- 不要输出“我在思考/我推理/我的计划是”。
- 不要把密语内容转述到 party。
- 失败时保留错误关键词，方便查日志，例如 `event_not_found`、`no_controllable_bot`、`invalid_role`。

## 泛化命令边界

`wow_run_playerbot_command` 只用于玩家可执行的 `.playerbots bot` 子命令，例如 `list`、`lookup`、`add Botname`、`addaccount AccountOrCharacter`、`init=epic Botname`、`refresh=raid Botname`。

不要用它尝试 `.playerbots account`、`.playerbots rndbot`、`.server`、`.gm`、`.tele` 或数据库类操作。
