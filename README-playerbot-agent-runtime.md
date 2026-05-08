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
- 日志目录：`/home/wuya/git/azerothcore-wotlk-git/env/dist/logs`
- tmux 会话：`playerbot-world`

端口：

- 共享 authserver：`3724`
- PlayerBot worldserver：`8086`
- SOAP：`7879`

数据库：

- 共享登录库：`acore_auth`
- PlayerBot world 库：`acore_playerbot_world`
- PlayerBot 角色库：`acore_playerbot_characters`
- Playerbots 模块库：`acore_playerbots`

Realm：

- `realmlist.id = 2`
- 名称：`Agent PlayerBot`
- 地址：`38.207.189.99`
- 端口：`8086`

## 当前调优

这个服现在按“Agent 控制的 bot 池”来跑，不按“随机机器人生态服”来跑。

关键配置：

- `AiPlayerbot.RandomBotAutologin = 0`
- `AiPlayerbot.MinRandomBots = 0`
- `AiPlayerbot.MaxRandomBots = 0`
- `AiPlayerbot.RandomBotLoginAtStartup = 0`
- `AiPlayerbot.RandomBotJoinLfg = 0`
- `AiPlayerbot.RandomBotJoinBG = 0`
- `AiPlayerbot.AddClassAccountPoolSize = 50`
- `MinWorldUpdateTime = 10`
- `MapUpdateInterval = 50`
- `MapUpdate.Threads = 1`

也就是说，随机 bot 不会自动上线。当前保留的是 AddClass bot 池，后续由 Agent 按需召唤和控制。

bot 账号前缀是 `pbagent`。初始数据库里有 55 个 bot 账号、550 个 bot 角色，其中 50 个账号被分配给 AddClass 池。

## 常用操作

查看状态：

```bash
tmux ls
ss -ltnp | rg ':8086|:7879|:3724|:3306'
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
  -e "SELECT id, source_event_id, status, bot_name, action_type, channel, text, command, strategy, bot_state, result, error FROM agent_playerbot_actions ORDER BY id DESC LIMIT 20\\G"

mysql -h127.0.0.1 -P3306 -uacore -pacore --default-character-set=utf8mb4 acore_playerbots \
  -e "SELECT id, created_at, group_leader_name, duration_ms, kills, deaths, summary_text, LEFT(facts_json, 1200) AS facts FROM agent_playerbot_combat_summaries ORDER BY id DESC LIMIT 5\\G"
```

`playerbot-agent.log` 是大脑侧日志，会记录 `chat_event`、`llm_prompt`、`llm_response`、`llm_skill_mapping`、`combat_summary_seen`、`combat_memory_used`、`decision`、`action_enqueued`。`Server.log` 是小脑执行日志，会记录动作是否执行成 `done`，或被权限/白名单挡成 `error`。数据库里的 `agent_playerbot_actions.status/result/error` 是动作真相源，`agent_playerbot_combat_summaries.summary_text/facts_json` 是战斗记忆真相源。

进入 worldserver 控制台：

```bash
tmux attach -t playerbot-world
```

从 tmux 外停止 worldserver：

```bash
tmux send-keys -t playerbot-world C-c
```

从 tmux 外启动 worldserver：

```bash
tmux new-session -d -s playerbot-world \
  "cd /home/wuya/git/azerothcore-wotlk-git/env/dist/bin && exec ./worldserver --config /home/wuya/git/azerothcore-wotlk-git/env/dist/etc/worldserver.conf >> /home/wuya/git/azerothcore-wotlk-git/env/dist/logs/worldserver.playerbot.stdout.log 2>&1"
```

启动 Python Agent 侧车：

```bash
tmux new-session -d -s playerbot-agent \
  "cd /home/wuya/git/azerothcore-wotlk-git && exec python3 tools/playerbot-agent/agent_bridge.py --rule-only >> env/dist/logs/playerbot-agent.log 2>&1"
```

停止 Python Agent 侧车：

```bash
tmux send-keys -t playerbot-agent C-c
```

如需接大模型，在启动前设置：

```bash
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=https://api.deepseek.com
export PLAYERBOT_AGENT_MODEL=deepseek-v4-flash
export PLAYERBOT_AGENT_LLM_THINKING=disabled
export PLAYERBOT_AGENT_LLM_MAX_TOKENS=512
export PLAYERBOT_AGENT_TRACE=1
export PLAYERBOT_AGENT_TRACE_PROMPT=1
export PLAYERBOT_AGENT_COMBAT_MEMORY_LIMIT=3
```

DeepSeek V4 Flash 当前建议显式关闭 thinking，避免把输出预算花在思考过程上，导致侧车拿到空 `content`。侧车也会在检测到 `https://api.deepseek.com` + `deepseek-v4*` 时默认补上 `thinking={"type":"disabled"}`。其他 OpenAI-compatible 模型可以不设置 `PLAYERBOT_AGENT_LLM_THINKING`。

没有这些环境变量时，侧车会以规则模式运行，仍支持“跟我、停下、加我、安心奶、捡垃圾”等中文指令。

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

v1 只执行三类动作：

- `reply`：bot 以队伍/密语/附近说话回复。
- `command`：白名单 Playerbots 聊天快捷命令。
- `strategy`：白名单策略开关。
- `combat_summary`：C++ 聚合战斗事实，Python 侧车压缩成短战斗记忆，供下一次 Agent 判断使用。

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
