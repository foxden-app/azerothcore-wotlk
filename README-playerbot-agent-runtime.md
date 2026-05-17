# PlayerBot Agent 运行手册

这是 `playerbot-agent` 分支的本地运行说明。它不是架构设计文档；架构和开发方向以 [ARCHITECTURE-agent-playerbots.md](/home/wuya/git/azerothcore-wotlk-git/ARCHITECTURE-agent-playerbots.md) 为准。

## 源码

- 核心分支：`playerbot-agent`
- 核心上游：`playerbots-core/Playerbot`
- Playerbots 模块目录：`modules/mod-playerbots`
- Playerbots 模块上游：`https://github.com/mod-playerbots/mod-playerbots.git`
- Agent 桥模块目录：`modules/mod-playerbot-agent`
- Agent Python 侧车：`tools/playerbot-agent/agent_bridge.py`

`modules/mod-playerbots` 是一个本地嵌套 Git checkout，并被根仓库忽略。这符合 AzerothCore 模块的常见使用方式。

## 运行路径

- worldserver：`/home/wuya/git/azerothcore-wotlk-git/env/dist/bin/worldserver`
- worldserver 配置：`/home/wuya/git/azerothcore-wotlk-git/env/dist/etc/worldserver.conf`
- Playerbots 配置：`/home/wuya/git/azerothcore-wotlk-git/env/dist/etc/modules/playerbots.conf`
- Agent 桥配置：`/home/wuya/git/azerothcore-wotlk-git/env/dist/etc/modules/playerbot_agent.conf`
- Agent 侧车环境文件：`/home/wuya/git/azerothcore-wotlk-git/env/dist/etc/playerbot-agent.env`
- 日志目录：`/home/wuya/git/azerothcore-wotlk-git/env/dist/logs`
- systemd 服务：`azerothcore-auth.service`、`azerothcore-world.service`、`azerothcore-playerbot-agent.service`

端口：

- 共享 authserver：`3724`
- PlayerBot worldserver：`8085`
- SOAP：`7879`

数据库：

- 共享登录库：`acore_auth`
- PlayerBot world 库：`acore_playerbot_world`
- PlayerBot 角色库：`acore_playerbot_characters`
- Playerbots 模块库：`acore_playerbots`

Realm：

- `realmlist.id = 1`
- 名称：`Agent PlayerBot`
- 地址：`38.207.189.99`
- 端口：`8085`

## 当前调优

这个服现在按“可控 AddClass 小队 + 轻量随机世界 bot”来跑。AddClass bot 用于组队控制；随机世界 bot 用于让有人类玩家附近更热闹，并支持密语闲聊。

关键配置：

- `AiPlayerbot.RandomBotAutologin = 1`
- `AiPlayerbot.MinRandomBots = 30`
- `AiPlayerbot.MaxRandomBots = 50`
- `AiPlayerbot.PlayerHotspotBots = 1`
- `AiPlayerbot.PlayerHotspotMinBots = 6`
- `AiPlayerbot.PlayerHotspotMaxBots = 10`
- `AiPlayerbot.RandomBotJoinLfg = 1`
- `AiPlayerbot.RandomBotJoinBG = 1`
- `AiPlayerbot.AddClassAccountPoolSize = 50`
- `MinWorldUpdateTime = 10`
- `MapUpdateInterval = 50`
- `MapUpdate.Threads = 1`

随机 bot 会自动上线，但人类玩家只能把他们当作路人闲聊对象；LLM Adapter 对随机世界 bot 密语只开放 `reply/no_reply`。真正可控的是你召唤进队的 AddClass bot。

bot 账号前缀是 `pbagent`。初始数据库里有 55 个 bot 账号、550 个 bot 角色，其中 50 个账号被分配给 AddClass 池。

## 常用操作

查看状态：

```bash
systemctl --no-pager --full status azerothcore-auth.service azerothcore-world.service azerothcore-playerbot-agent.service
ss -ltnp | rg ':8085|:7879|:3724|:3306'
tail -n 80 env/dist/logs/Server.log
tail -n 80 env/dist/logs/Playerbots.log
python3 tools/playerbot-agent/agent_bridge.py --once --rule-only
```

跟踪 LLM 和 skill 调用：

```bash
tail -f env/dist/logs/playerbot-agent.log
tail -f env/dist/logs/Server.log | rg 'module.playerbot_agent|Playerbot Agent'

mysql -h127.0.0.1 -P3306 -uacore -pacore --default-character-set=utf8mb4 acore_playerbots \
  -e "SELECT id, created_at, channel, speaker_name, bot_name, message, LEFT(meta, 1200) AS meta FROM agent_playerbot_events ORDER BY id DESC LIMIT 5\\G"

mysql -h127.0.0.1 -P3306 -uacore -pacore --default-character-set=utf8mb4 acore_playerbots \
  -e "SELECT id, source_event_id, status, bot_name, action_type, channel, text, command, strategy, bot_state, payload_json, result, error FROM agent_playerbot_actions ORDER BY id DESC LIMIT 20\\G"

mysql -h127.0.0.1 -P3306 -uacore -pacore --default-character-set=utf8mb4 acore_playerbots \
  -e "SELECT id, created_at, group_leader_name, duration_ms, kills, deaths, summary_text, LEFT(facts_json, 1200) AS facts FROM agent_playerbot_combat_summaries ORDER BY id DESC LIMIT 5\\G"
```

`playerbot-agent.log` 是大脑侧日志，会记录 `chat_event`、`llm_prompt`、`llm_response`、`llm_skill_mapping`、`combat_summary_seen`、`combat_memory_used`、`decision`、`action_enqueued`。`Server.log` 是小脑执行日志，会记录动作是否执行成 `done`，或被权限/白名单挡成 `error`。数据库里的 `agent_playerbot_actions.status/result/error` 是动作真相源，`agent_playerbot_combat_summaries.summary_text/facts_json` 是战斗记忆真相源。

安装或刷新 systemd 服务：

```bash
sudo install -m 0644 ops/systemd/azerothcore-auth.service /etc/systemd/system/azerothcore-auth.service
sudo install -m 0644 ops/systemd/azerothcore-world.service /etc/systemd/system/azerothcore-world.service
sudo install -m 0644 ops/systemd/azerothcore-playerbot-agent.service /etc/systemd/system/azerothcore-playerbot-agent.service
test -f env/dist/etc/playerbot-agent.env || install -m 0600 ops/systemd/playerbot-agent.env.dist env/dist/etc/playerbot-agent.env
sudo systemctl daemon-reload
sudo systemctl enable azerothcore-auth.service azerothcore-world.service azerothcore-playerbot-agent.service
```

启动或重启三件套：

```bash
sudo systemctl restart azerothcore-auth.service
sudo systemctl restart azerothcore-world.service
sudo systemctl restart azerothcore-playerbot-agent.service
```

只看侧车：

```bash
systemctl --no-pager --full status azerothcore-playerbot-agent.service
journalctl -u azerothcore-playerbot-agent.service -n 120 --no-pager
tail -f env/dist/logs/playerbot-agent.log
```

停止侧车：

```bash
sudo systemctl stop azerothcore-playerbot-agent.service
```

侧车配置统一写在 `env/dist/etc/playerbot-agent.env`。默认是规则模式：

```bash
PLAYERBOT_AGENT_RULE_ONLY="1"
```

如需接大模型，编辑这个 env 文件：

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
PLAYERBOT_AGENT_HISTORY_LIMIT="240"
PLAYERBOT_AGENT_HISTORY_TTL_SECONDS="14400"
PLAYERBOT_AGENT_PROMPT_MESSAGE_LIMIT="40"
```

`PLAYERBOT_AGENT_LLM_THINKING` 可写 `enabled/enable` 或 `disabled/disable`。开启 thinking 时建议把 `PLAYERBOT_AGENT_LLM_MAX_TOKENS` 提高到 `2048`，避免推理过程吃掉输出预算后最终 `content` 为空。侧车只解析最终 `content` 里的 JSON；模型返回的 `reasoning_content` 只会按日志开关写入 `playerbot-agent.log`，不会直接进入游戏聊天或动作执行。

侧车会把最近聊天作为短期工作记忆带进 prompt，包含队伍聊天、附近说话和发给 bot 的密语目标。默认从事件表恢复最近 `240` 条、`4` 小时内的消息，每次 prompt 最多带最近 `40` 条。跨天摘要、玩家画像、队伍长期目标和计划回放应放到后续 Harness Agent 的记忆层，而不是让侧车无限扩大 prompt。

改完 env 后重启侧车：

```bash
sudo systemctl restart azerothcore-playerbot-agent.service
```

没有 LLM key 或 `PLAYERBOT_AGENT_RULE_ONLY="1"` 时，侧车会以规则模式运行，仍支持“跟我、停下、加我、安心奶、捡垃圾”等中文指令。

## Agent v1 数据流

```text
玩家聊天
  -> mod-playerbot-agent
  -> acore_playerbots.agent_playerbot_events
  -> tools/playerbot-agent/agent_bridge.py
  -> acore_playerbots.agent_playerbot_actions
  -> mod-playerbot-agent
  -> Playerbots 小脑
```

worldserver 首次启动新模块时会自动创建：

```text
agent_playerbot_events
agent_playerbot_actions
agent_playerbot_combat_summaries
```

当前执行动作：

- `reply`：bot 以队伍/密语/附近说话回复。
- `command`：白名单 Playerbots 聊天快捷命令。
- `strategy`：白名单策略开关。
- `summon_bot` / `init_bot` / `dismiss_bot` / `list_bots` / `lookup_bot_pool` / `refresh_bot` / `level_bot` / `init_instance_quests`：队伍生命周期强类型动作，经 `payload_json` 传参。
- `invite_player`：邀请真实玩家进队，执行前检查在线、阵营、队伍容量和邀请权限。
- `combat_summary`：C++ 聚合战斗事实，Python 侧车压缩成短战斗记忆，供下一次 Agent 判断使用。

常用 v2 中文触发：

```text
加个奶
加个坦
加个法师
我想下副本，组个稳一点的队
看看机器人池子
我有哪些机器人
小牧初始化一下
刷新小牧
小牧同步等级
给小牧初始化副本任务
小牧下线
邀请张三进队
```

## 主机资源

2026-05-08 已新增一个 12G swap 文件：

```text
/swap-playerbot.img
```

原来的 `/swap.img` 仍然保留，所以总 swap 约 16G。新增 swap 已写入 `/etc/fstab`，重启后会自动启用。

当前主要资源消耗通常来自：

- PlayerBot `worldserver`
- VS Code server
- MySQL

FRP、foxden 网站和 foxhole-postgres 相对很轻，不是当前资源压力的主要来源。
