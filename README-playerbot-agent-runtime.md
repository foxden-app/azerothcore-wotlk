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
- Hermes MCP 环境文件：`/home/wuya/git/azerothcore-wotlk-git/env/dist/etc/playerbot-mcp.env`
- Hermes Relay 环境文件：`/home/wuya/git/azerothcore-wotlk-git/env/dist/etc/playerbot-hermes-relay.env`
- 轻量账号注册页环境文件：`/home/wuya/git/azerothcore-wotlk-git/env/dist/etc/account-register.env`
- 日志目录：`/home/wuya/git/azerothcore-wotlk-git/env/dist/logs`
- systemd 服务：`azerothcore-auth.service`、`azerothcore-world.service`、`azerothcore-playerbot-agent.service`、`azerothcore-playerbot-mcp.service`、`azerothcore-playerbot-hermes-relay.service`、`azerothcore-account-register.service`

端口：

- 共享 authserver：`3724`
- PlayerBot worldserver：`8085`
- SOAP：`7879`
- 账号注册页：`18080`，默认只监听 `127.0.0.1`

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

## Git 提交边界

要提交到 GitHub 的是真相源、模板和可恢复脚本，不提交本机运行态和密钥。

应该提交：

- `modules/mod-playerbot-agent/` 的 C++ 桥和 `conf/*.dist`。
- `tools/playerbot-mcp/`、`tools/account-register/`、`tools/playerbot-agent/` 的源码和测试。
- `ops/systemd/*.service`、`ops/systemd/*.env.dist`。
- `ops/hermes-wow/compose.yaml`、`ops/hermes-wow/config.yaml.template`、`ops/hermes-wow/wow-playerbot-control/SKILL.md`。
- `ops/codex-skills/azerothcore-playerbot-ops/`，这是本地 Codex 运维 skill 的仓库副本，换环境时可恢复。
- `ops/patches/`，保存根仓库不跟踪的嵌套模块补丁，例如 `mod-playerbots-player-hotspot.patch`。
- `ARCHITECTURE-agent-playerbots.md`、`ROADMAP-agent-playerbots.md`、本运行手册和 `doc/agent-playerbot-architecture.*`。

不要提交：

- `env/dist/etc/*.env`、`~/.hermes-wow/.env`、RT 上真实 `config.yaml`。
- API key、MCP bearer token、SOAP 密码、注册邀请码、数据库真实密码。
- `env/dist/logs/`、`var/build-*`、`*.bak-*`、`.telegram-inbox/`、`.antigravitycli/`。

恢复 Codex 运维 skill：

```bash
mkdir -p ~/.codex/skills
rsync -a ops/codex-skills/azerothcore-playerbot-ops/ ~/.codex/skills/azerothcore-playerbot-ops/
```

恢复 `modules/mod-playerbots` 本地补丁：

```bash
git -C modules/mod-playerbots apply ../../ops/patches/mod-playerbots-player-hotspot.patch
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

## 轻量账号注册页

账号注册页是一个 Python 标准库小服务，不引入 PHP/WordPress/CMS，也不直接写 `account.salt/verifier`。它只负责表单校验、CSRF、限流、邀请码校验，然后通过 worldserver SOAP 执行 `account create <account> <password> [email]`，让 AzerothCore 自己生成 SRP6 字段。

首次安装：

```bash
test -f env/dist/etc/account-register.env || install -m 0600 ops/systemd/account-register.env.dist env/dist/etc/account-register.env
sudo install -m 0644 ops/systemd/azerothcore-account-register.service /etc/systemd/system/azerothcore-account-register.service
sudo systemctl daemon-reload
sudo systemctl enable --now azerothcore-account-register.service
```

运行和排障：

```bash
systemctl --no-pager --full status azerothcore-account-register.service
curl http://127.0.0.1:18080/health
tail -f env/dist/logs/account-register.log
```

安全边界：

- 默认 `ACCOUNT_REGISTER_HOST="127.0.0.1"`，不要直接裸露到公网。
- 默认 `ACCOUNT_REGISTER_REQUIRE_INVITE="1"`，邀请码写在 `env/dist/etc/account-register.env`，该文件权限应保持 `0600`。
- `ACCOUNT_REGISTER_SOAP_PASSWORD` 可留空；服务会回退读取 `~/.config/acore/gm.env` 里的 `ACORE_GM_PASS`。不要把 SOAP 密码或邀请码提交到 git。
- 注册失败日志会记录 IP、账号名和失败原因，但不会记录玩家输入的密码。

## Hermes MCP 模式

Hermes Harness 模式把旧 Python 侧车拆成两层：

```text
agent_playerbot_events
  -> azerothcore-playerbot-hermes-relay.service
  -> RT Hermes API
  -> azerothcore-playerbot-mcp.service
  -> agent_playerbot_actions
  -> worldserver
```

T490 跑 WoW MCP 服务和事件 relay，RT 跑 Hermes 容器。MCP 服务默认监听 `0.0.0.0:18765`，通过 bearer token 接受 Hermes 调用；T490 防火墙只放行 RT 的 LAN 地址访问该端口。

RT 部署文件在 `ops/hermes-wow/`：

- `compose.yaml`：Hermes 容器，API 端口 `8642` 暴露给 T490，dashboard 端口 `9119` 只绑定 RT 本机。
- `config.yaml.template`：DeepSeek provider 和 `wow_playerbot` MCP server 配置模板。
- `wow-playerbot-control/SKILL.md`：给 Hermes 的 WoW 队伍级操作约束。

查看 dashboard 时先开 SSH 隧道：

```bash
ssh -p 922 -L 9119:127.0.0.1:9119 wuya@192.168.1.179
```

然后在本机打开 `http://127.0.0.1:9119`。

首次安装 T490 服务：

```bash
python3 -m venv var/playerbot-mcp-venv
var/playerbot-mcp-venv/bin/python -m pip install --upgrade pip wheel setuptools
var/playerbot-mcp-venv/bin/python -m pip install -r tools/playerbot-mcp/requirements.txt
test -f env/dist/etc/playerbot-mcp.env || install -m 0600 ops/systemd/playerbot-mcp.env.dist env/dist/etc/playerbot-mcp.env
test -f env/dist/etc/playerbot-hermes-relay.env || install -m 0600 ops/systemd/playerbot-hermes-relay.env.dist env/dist/etc/playerbot-hermes-relay.env
sudo install -m 0644 ops/systemd/azerothcore-playerbot-mcp.service /etc/systemd/system/azerothcore-playerbot-mcp.service
sudo install -m 0644 ops/systemd/azerothcore-playerbot-hermes-relay.service /etc/systemd/system/azerothcore-playerbot-hermes-relay.service
sudo systemctl daemon-reload
sudo systemctl enable azerothcore-playerbot-mcp.service azerothcore-playerbot-hermes-relay.service
```

联调顺序：

```bash
sudo systemctl restart azerothcore-playerbot-mcp.service
curl http://127.0.0.1:18765/health

# RT Hermes 容器就绪后再切流量，避免旧侧车和 Hermes 双响应。
sudo systemctl stop azerothcore-playerbot-agent.service
sudo systemctl restart azerothcore-playerbot-hermes-relay.service
```

日志：

```bash
tail -f env/dist/logs/playerbot-mcp.log
tail -f env/dist/logs/playerbot-hermes-relay.log
ssh-RT 'cd /home/wuya/srv/hermes-wow && docker compose logs -f hermes-wow'
ssh-RT 'docker exec hermes-wow tail -f /opt/data/logs/agent.log'
```

`PLAYERBOT_HERMES_TRACE_RAW=1` 时，relay 日志会记录发给 Hermes 的完整事件包、最近动作结果、Hermes 响应文本和原始响应。API key 和 MCP bearer token 只放在 `env/dist/etc/*.env` 与 RT 的 `/home/wuya/.hermes-wow/.env`/`config.yaml`，不要提交到 git。

Hermes 的最终 assistant 文本只会进入 relay/Hermes 日志，不会显示在游戏聊天里。玩家需要看见的回答、失败原因、澄清问题或闲聊回复，都必须由 Agent 调用 `wow_reply`；`no_action` 只用于确实不需要可见回复、也不需要动作的背景消息。

relay 每轮都会把当前事件写成 `current_event_id` 发给 Hermes。所有会产生可见回复或游戏动作的 MCP 调用都必须传这个 ID；MCP 入队层默认开启 `PLAYERBOT_MCP_REJECT_PROCESSED_EVENT_ACTIONS=1`，会拒绝已经处理过的旧事件动作，避免 Hermes 沿用历史 `event_id` 后把新指令落到旧上下文。

`wow_reply` 会在 MCP 层自动修正过期发言人：如果 Hermes 沿用历史里的 `Gessa`，但当前事件上下文里只有 `Jeshas` 在线，MCP 会改用当前在线 bot 发送 party/say 回复。relay 还有一层兜底：如果动作已经成功但没有成功的可见回复，或回复因为 `bot is not online` 失败，会自动补发一条短确认或重发原回复。

如果动作结果是 `requester is not online`，relay 不再继续补发同一条兜底回复；这类错误通常表示 worldserver 当刻没拿到请求玩家会话，重复入队只会制造噪声。对应的服务端修正是让 `mod-playerbot-agent` 用 connected player 查找请求者。

固定入口 bot 配置在 MCP 和 relay 的 env 文件里：`PLAYERBOT_AGENT_ANCHOR_BOT_NAME="瓦小狸"`，`PLAYERBOT_AGENT_ANCHOR_ALIASES="小狸"`。say/yell 点名瓦小狸时由它承接；whisper 仍由被私聊的 bot 回复；party/raid 仍按当前队伍上下文回复，只有瓦小狸在当前上下文里时 MCP 才会优先用它。`PLAYERBOT_HERMES_IGNORE_UNADDRESSED_SAY="1"` 时，relay 会跳过没有点名瓦小狸/小狸的普通 `/s` 或 `/y`，避免本地闲聊误触发 Hermes。

worldserver 侧的固定入口由 `env/dist/etc/modules/playerbot_agent.conf` 控制：`AgentPlayerbot.AnchorBotAutologin = 1`、`AgentPlayerbot.AnchorBotName = "瓦小狸"`、`AgentPlayerbot.AnchorBotAliases = "小狸"`。它不依赖 `AiPlayerbot.RandomBotAutologin`，bridge 会按 `AgentPlayerbot.AnchorBotEnsureIntervalMs` 周期确认瓦小狸在线；如果瓦小狸离玩家太远导致 `/s` 不可见，回复会回退为 whisper。

私聊瓦小狸时，事件上下文使用发言玩家自己的队伍，而不是瓦小狸所在队伍。`context.group_members` 会包含队伍列表里的离线成员；离线 bot 会标记为 `online:false`、`offline_in_group:true`、`bot_kind:"group_offline"`。relay 对“队友上线/小队回来/把灰名叫回”等说法有确定性 fast-path，会直接对这些离线 bot 执行 `add <名字>`。

MCP 和 relay 日志采用 JSON Lines，保留稳定英文 `event` 代码给脚本使用，同时输出中文 `event_zh` 给人工审阅。工具说明、Hermes skill 和 relay 指令默认使用中文；AzerothCore/worldserver 自带英文日志不做全局翻译。

MCP 暴露的是基础设施能力，不做上层语义判断。常用队伍操作优先使用 typed tools：`wow_summon_bot`、`wow_init_bot`、`wow_dismiss_bot`、`wow_refresh_bot`、`wow_level_bot`、`wow_init_instance_quests`、`wow_list_bots`、`wow_lookup_bot_pool`、`wow_invite_player`、`wow_bot_follow`、`wow_bot_stay`、`wow_bot_retreat`、`wow_bot_attack_target`、`wow_bot_pull`、`wow_bot_ready`、`wow_bot_burst`、`wow_focus_heal`、`wow_set_loot_mode`、`wow_set_buff`、`wow_set_healer_dps`。`wow_summon_bot` 支持可选 `race_hint`；指定种族时，MCP 会先从 AddClass 池里选择匹配角色，再执行 `add <Botname>`，不要让 Hermes 拼 `addclass priest human female` 这种原生命令不支持的语法。后续缺工具时，Hermes 可以先查 `wow_get_playerbot_command_catalog`，再用 `wow_run_playerbot_command` 执行任意服务端支持的 `.playerbots bot` 参数；`command_line` 优先只写 `.playerbots bot` 后面的部分，例如 `remove Gessa`、`init=auto Gessa`、`addclass priest female`，也兼容完整 `.playerbots bot ...` 或 `.bot ...`。实际权限和失败原因由 worldserver 的 PlayerbotMgr 判断，结果以 `agent_playerbot_actions.status/result/error` 和 `wow_get_action_results` 为准。

回滚：

```bash
sudo systemctl stop azerothcore-playerbot-hermes-relay.service
sudo systemctl stop azerothcore-playerbot-mcp.service
sudo systemctl restart azerothcore-playerbot-agent.service
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
