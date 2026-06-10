---
title: Hermes全链路日志排查指引
---

# Hermes全链路日志排查指引

更新时间：2026-06-10

本文用于排查瓦小狸从游戏消息进入 worldserver，到 Hermes Agent 调用 MCP 工具，再回到游戏内回复或执行动作的完整闭环。现在生产服和敏捷测试服都在 GJZN 本机上；如果命令是在 GJZN 上执行，直接运行本地命令，不要 `ssh GJZN` 回本机。从其他机器连入时再使用 `ssh GJZN` 或 `ssh GJZN-public`。

## 一眼判断查哪条线

| 环境 | 服务器 | Realm / 入口 | Runtime 根目录 | Playerbots DB | Hermes API | MCP | 主要用途 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 生产服 | GJZN，hostname `GJZN` | `线路一`/`线路二`，world `8085` | `/home/wuya/git/azerothcore-wotlk-git` | `acore_playerbots` | `127.0.0.1:8642` | `127.0.0.1:18765` | 正式玩家链路 |
| 敏捷测试服 | GJZN，hostname `GJZN` | `敏捷测试`，world `8086`，realm id `3` | `/home/wuya/git/azerothcore-wotlk-dev` | `acore_dev_playerbots` | `127.0.0.1:8643` | `127.0.0.1:18766` | GM 白名单测试 |

## 全链路地图

```text
玩家聊天
  -> worldserver / mod-playerbot-agent
  -> agent_playerbot_events
  -> azerothcore-*-playerbot-hermes-relay.service
  -> Hermes /v1/responses
  -> Hermes Agent
  -> MCP /mcp 工具调用
  -> agent_playerbot_actions
  -> worldserver 轮询并执行 action
  -> bot 在游戏内 reply / command / strategy / typed action
  -> agent_playerbot_actions.status/result/error
```

排查时先拿 `event_id`，然后按同一个 `event_id` 查 relay 日志、Hermes 容器日志、MCP 日志和 MySQL 动作表。不要只看 Hermes 最终 assistant 文本；玩家能看到的回复必须落到 `wow_reply` 或 relay 兜底产生的 `reply` action。

## 生产服位置表

| 项 | 值 |
| --- | --- |
| 主机 | GJZN，本机 hostname `GJZN` |
| runtime | `/home/wuya/git/azerothcore-wotlk-git` |
| world service | `azerothcore-world.service` |
| world 端口 | `0.0.0.0:8085` |
| SOAP | `0.0.0.0:7879` |
| Hermes 容器 | `hermes-wow` |
| Hermes compose | `/home/wuya/srv/hermes-wow/docker-compose.yml` |
| Hermes 镜像 | `docker.1panel.live/nousresearch/hermes-agent:latest`；2026-06-10 生产服已更新到 Hermes Agent `0.16.0`，镜像 digest `sha256:4cf80cce5e92a503d8feae898fd7ba063e208d89bf67806f37198bbe3a3b6d9f` |
| Hermes 默认 provider | `custom:xfyun-wow` / `xopqwen36v35b`；旧 `custom:siliconflow-wow` 仅保留为非默认备用 |
| Hermes API | `http://127.0.0.1:8642/v1/responses` |
| Hermes dashboard | `http://127.0.0.1:9119` |
| MCP service | `azerothcore-playerbot-mcp.service` |
| MCP URL | `http://127.0.0.1:18765/mcp` |
| relay service | `azerothcore-playerbot-hermes-relay.service` |
| relay env | `/home/wuya/git/azerothcore-wotlk-git/env/dist/etc/playerbot-hermes-relay.env` |
| MCP env | `/home/wuya/git/azerothcore-wotlk-git/env/dist/etc/playerbot-mcp.env` |
| relay state | `/home/wuya/git/azerothcore-wotlk-git/var/playerbot-hermes-relay/state.json` |
| relay log | `/home/wuya/git/azerothcore-wotlk-git/env/dist/logs/playerbot-hermes-relay.log` |
| MCP log | `/home/wuya/git/azerothcore-wotlk-git/env/dist/logs/playerbot-mcp.log` |
| world logs | `/home/wuya/git/azerothcore-wotlk-git/env/dist/logs/Server.log`、`Playerbots.log` |
| DB | `acore_playerbots.agent_playerbot_events`、`acore_playerbots.agent_playerbot_actions` |

生产 Hermes 没有单独的 `azerothcore-hermes-wow.service`。它由 Docker Compose 创建，容器配置 `restart: unless-stopped`，随 Docker daemon 恢复。生产 relay 当前关键配置是：`PLAYERBOT_HERMES_LISTEN_SCOPE=direct`、`PLAYERBOT_HERMES_PAYLOAD_MODE=minimal`、`PLAYERBOT_HERMES_STORE=0`、`PLAYERBOT_HERMES_SKIP_BACKLOG_ON_START=1`、`PLAYERBOT_MCP_REJECT_PROCESSED_EVENT_ACTIONS=1`。

Hermes Agent `0.16.0` 的 Docker 镜像使用 s6 overlay 自行作为 PID 1 监管 gateway/dashboard；生产和测试服 compose 都不要设置 Docker `init: true`，否则会出现 `s6-overlay-suexec: fatal: can only run as pid 1` 并反复重启。生产 dashboard 在容器内绑定 `0.0.0.0`，宿主机仅发布 `127.0.0.1:9119`，因此 compose 显式设置 `HERMES_DASHBOARD_INSECURE=1` 让本机面板可用。

2026-06-10 生产服将默认模型从失效的 SiliconFlow `nex-agi/Nex-N2-Pro` 切到测试服同款讯飞 `xopqwen36v35b`。验证结果：`http://127.0.0.1:8642/v1/responses` 使用生产 API key 返回 HTTP 200，文本 `pong`。

## 测试服位置表

| 项 | 值 |
| --- | --- |
| 主机 | GJZN，本机 hostname `GJZN` |
| runtime | `/home/wuya/git/azerothcore-wotlk-dev` |
| world service | `azerothcore-dev-world.service` |
| world 端口 | `127.0.0.1:8086`，公网 FRP `38.207.189.99:8086` |
| SOAP | `127.0.0.1:7880` |
| Realm | `敏捷测试`，realm id `3`，`allowedSecurityLevel=3` |
| Hermes service | `azerothcore-dev-hermes-wow.service` |
| Hermes 容器 | `hermes-wow-dev` |
| Hermes compose | `/home/wuya/srv/hermes-wow-dev/docker-compose.yml` |
| Hermes 镜像 | `docker.1panel.live/nousresearch/hermes-agent:latest`；2026-06-09 测试服已更新到 Hermes Agent `0.16.0`，镜像 digest `sha256:4cf80cce5e92a503d8feae898fd7ba063e208d89bf67806f37198bbe3a3b6d9f` |
| Hermes API | `http://127.0.0.1:8643/v1/responses` |
| Hermes dashboard | `http://127.0.0.1:9120` |
| MCP service | `azerothcore-dev-playerbot-mcp.service` |
| MCP URL | `http://127.0.0.1:18766/mcp` |
| relay service | `azerothcore-dev-playerbot-hermes-relay.service` |
| relay env | `/home/wuya/git/azerothcore-wotlk-dev/env/dist/etc/playerbot-hermes-relay-dev.env` |
| MCP env | `/home/wuya/git/azerothcore-wotlk-dev/env/dist/etc/playerbot-mcp-dev.env` |
| relay state | `/home/wuya/git/azerothcore-wotlk-dev/var/playerbot-hermes-relay-dev/state.json` |
| relay log | `/home/wuya/git/azerothcore-wotlk-dev/env/dist/logs/playerbot-hermes-relay-dev.log` |
| MCP log | `/home/wuya/git/azerothcore-wotlk-dev/env/dist/logs/playerbot-mcp-dev.log` |
| world logs | `/home/wuya/git/azerothcore-wotlk-dev/env/dist/logs/Server.log`、`Playerbots.log` |
| DB | `acore_dev_playerbots.agent_playerbot_events`、`acore_dev_playerbots.agent_playerbot_actions` |

测试服 world service 有专门的 drop-in：`/etc/systemd/system/azerothcore-dev-world.service.d/playerbots-db.conf`，必须设置 `AC_PLAYERBOTS_DATABASE_INFO=127.0.0.1;3306;acore;acore;acore_dev_playerbots`。如果测试服 action 跑到生产库，优先查这个 drop-in。

Hermes Agent `0.16.0` 的 Docker 镜像使用 s6 overlay 自行作为 PID 1 监管 gateway/dashboard；测试服 compose 不要设置 Docker `init: true`，否则会出现 `s6-overlay-suexec: fatal: can only run as pid 1` 并反复重启。0.16 首次启动会把旧 `custom_providers` 自动迁移为 `providers:` schema，手写模板优先使用 `api`、`key_env`、`transport` 字段。

2026-06-09 验证 sub2api OpenAI Responses 接入：`/v1/models` 可列出 `gpt-5.5` 等模型，但 `/v1/responses` 普通请求返回 `403 This account only allows Codex official clients`；加 Codex CLI 风格 `User-Agent`、`originator` 和 `OpenAI-Beta` 后，上游返回 `401 token_invalidated`。结论是当前 sub2api key/上游 OpenAI OAuth 账号不能作为 Hermes 默认 provider 使用；测试服 Hermes 已回切原 xfyun provider，保留 sub2api provider 条目仅用于后续换有效账号后再测。

## 快速健康检查

### 生产服

```bash
hostname
systemctl is-active azerothcore-world.service azerothcore-playerbot-hermes-relay.service azerothcore-playerbot-mcp.service docker mysql
ss -ltnp | egrep ':(8085|7879|8642|9119|18765)\b' || true
docker ps | egrep 'hermes|CONTAINER'
curl -fsS 127.0.0.1:18765/health
curl -fsS 127.0.0.1:8642/health
```

### 测试服

```bash
hostname
systemctl is-active azerothcore-dev-world.service azerothcore-dev-hermes-wow.service azerothcore-dev-playerbot-hermes-relay.service azerothcore-dev-playerbot-mcp.service docker mysql
ss -ltnp | egrep ':(8086|7880|8643|9120|18766)\b' || true
docker ps | egrep 'hermes|CONTAINER'
curl -fsS 127.0.0.1:18766/health
curl -fsS 127.0.0.1:8643/health
```

## 一条消息的标准排查步骤

### 1. 找 event_id

生产服：

```bash
MYSQL_PWD=acore mysql -uacore -h127.0.0.1 -e "
  SELECT id,created_at,channel,speaker_name,target_name,bot_name,message,processed_at
  FROM acore_playerbots.agent_playerbot_events
  ORDER BY id DESC LIMIT 10;
"
```

测试服：

```bash
MYSQL_PWD=acore mysql -uacore -h127.0.0.1 -e "
  SELECT id,created_at,channel,speaker_name,target_name,bot_name,message,processed_at
  FROM acore_dev_playerbots.agent_playerbot_events
  ORDER BY id DESC LIMIT 10;
"
```

如果没有新 event：先查 world 是否在线、玩家是否真的发到 `direct` 范围、瓦小狸/小狸是否被点名、`Playerbots.log` 是否有 bridge 启动信息。

### 2. 查 relay 是否处理这个 event

把 `EVENT_ID` 替换成目标 id。

生产服：

```bash
EVENT_ID=1310
rg "\"id\":$EVENT_ID|event_id.?$EVENT_ID|current_event_id.?$EVENT_ID|relay_event|relay_event_skipped|relay_event_unauthorized|hermes_request|hermes_response|hermes_http_error|hermes_error|fast_|fallback_" \
  /home/wuya/git/azerothcore-wotlk-git/env/dist/logs/playerbot-hermes-relay.log
journalctl -u azerothcore-playerbot-hermes-relay.service --since '30 min ago' --no-pager
```

测试服：

```bash
EVENT_ID=1262
rg "\"id\":$EVENT_ID|event_id.?$EVENT_ID|current_event_id.?$EVENT_ID|relay_event|relay_event_skipped|relay_event_unauthorized|hermes_request|hermes_response|hermes_http_error|hermes_error|fast_|fallback_" \
  /home/wuya/git/azerothcore-wotlk-dev/env/dist/logs/playerbot-hermes-relay-dev.log
journalctl -u azerothcore-dev-playerbot-hermes-relay.service --since '30 min ago' --no-pager
```

relay 关键字：

| 关键字 | 含义 |
| --- | --- |
| `startup` / `shutdown` | relay 启停 |
| `relay_event` | 事件进入 Hermes 前的主路径 |
| `relay_event_skipped` | 被监听范围、任务进度噪声、backlog 等规则跳过 |
| `relay_event_unauthorized` | 非白名单玩家被本地拒绝，不进入 Hermes |
| `hermes_request` | 已请求 Hermes API |
| `hermes_response` | Hermes 返回成功 |
| `hermes_http_error` / `hermes_error` | Hermes API 或网络异常 |
| `fast_social_reply` | 问候/感谢等本地快捷回复 |
| `fast_context_reset` | 新建会话/重置上下文本地处理，不进入 Hermes |
| `fast_path_reply` / `action_enqueued` | relay 本地入队了 reply/action |
| `fallback_reply_enqueued` | Hermes 没调用 `wow_reply`，relay 把最终文本兜底转成游戏内回复 |
| `fallback_reply_skipped` | 兜底被跳过，常见原因是没可用回复 bot 或文本为空 |

### 3. 查 Hermes Agent 是否收到并调工具

生产服：

```bash
EVENT_ID=1310
docker logs --since 30m hermes-wow 2>&1 | rg "$EVENT_ID|current_event_id|POST /v1/responses|conversation|tool|tools/call|wow_|wow_reply|error|HTTP 402|Insufficient Balance"
```

测试服：

```bash
EVENT_ID=1262
docker logs --since 30m hermes-wow-dev 2>&1 | rg "$EVENT_ID|current_event_id|POST /v1/responses|conversation|tool|tools/call|wow_|wow_reply|error|HTTP 402|Insufficient Balance"
```

Hermes 关键字：

| 关键字 | 含义 |
| --- | --- |
| `POST /v1/responses` | relay 调到了 Hermes API |
| `current_event_id` | 当前事件锚点，应等于正在排查的 event_id |
| `tools/list` | Hermes 正在发现 MCP 工具 |
| `tools/call` / `wow_` | Hermes 调用了 MCP 工具 |
| `wow_reply` | Hermes 主动要求游戏内回复 |
| `wow_get_party_state`、`wow_get_inventory`、`wow_get_recent_events` | Agent 按需查询游戏环境 |
| `HTTP 402` / `Insufficient Balance` | 模型 provider 余额不足；游戏链路可能是通的 |

正常现象：普通闲聊不应每轮自动带队伍、背包、任务、位置等大块上下文；需要环境时才出现 `wow_get_*` 工具调用。`PLAYERBOT_HERMES_PAYLOAD_MODE=minimal` 时，relay 请求只带当前消息和基础路由字段。

### 4. 查 MCP 接口和工具调用

生产服：

```bash
rg "POST /mcp|ListToolsRequest|CallToolRequest|wow_|tool_reply|error|whisper_event_requires_whisper_reply|recent_events_unanchored_limited" \
  /home/wuya/git/azerothcore-wotlk-git/env/dist/logs/playerbot-mcp.log
journalctl -u azerothcore-playerbot-mcp.service --since '30 min ago' --no-pager
```

测试服：

```bash
rg "POST /mcp|ListToolsRequest|CallToolRequest|wow_|tool_reply|error|whisper_event_requires_whisper_reply|recent_events_unanchored_limited" \
  /home/wuya/git/azerothcore-wotlk-dev/env/dist/logs/playerbot-mcp-dev.log
journalctl -u azerothcore-dev-playerbot-mcp.service --since '30 min ago' --no-pager
```

MCP 日志常见只显示 `POST /mcp`、`ListToolsRequest`、`CallToolRequest` 和 HTTP 200。工具调用的最终真相要回到 `agent_playerbot_actions` 的 `status/result/error`。

### 5. 查 action 是否入队、执行、失败

生产服：

```bash
EVENT_ID=1310
MYSQL_PWD=acore mysql -uacore -h127.0.0.1 -e "
  SELECT id,source_event_id,created_at,updated_at,status,action_type,bot_name,channel,
         LEFT(text,160) AS text,command,strategy,LEFT(result,200) AS result,LEFT(error,200) AS error
  FROM acore_playerbots.agent_playerbot_actions
  WHERE source_event_id=$EVENT_ID
  ORDER BY id;
"
```

测试服：

```bash
EVENT_ID=1262
MYSQL_PWD=acore mysql -uacore -h127.0.0.1 -e "
  SELECT id,source_event_id,created_at,updated_at,status,action_type,bot_name,channel,
         LEFT(text,160) AS text,command,strategy,LEFT(result,200) AS result,LEFT(error,200) AS error
  FROM acore_dev_playerbots.agent_playerbot_actions
  WHERE source_event_id=$EVENT_ID
  ORDER BY id;
"
```

action 状态判断：

| status / error | 含义 | 下一步 |
| --- | --- | --- |
| `pending` 长时间不变 | worldserver 没轮询到 action，或 DB 指错 | 查 world service、测试服 drop-in、`AC_PLAYERBOTS_DATABASE_INFO` |
| `running` 长时间不变 | world 执行中卡住或服务异常退出 | 查 `Server.log`、`Playerbots.log`、world service |
| `done` | world 已执行 | 如果玩家没看见，查 channel、bot 在线状态、是否不是 reply action |
| `error` + `requester is not online` | 玩家已离线，无法私聊/动作 | 让玩家在线后重试 |
| `error` + `bot is not online` | 回复 bot 不在线 | 查瓦小狸/目标 bot 在线状态 |
| `whisper_event_requires_whisper_reply` | whisper 事件被错误要求回 party | Agent 必须用 `wow_reply(channel="whisper")` |
| `cannot invite self` | 邀请目标就是自己 | Agent 逻辑问题或玩家表达需要澄清 |

### 6. 查 worldserver 桥是否工作

生产服：

```bash
rg "Playerbot Agent bridge enabled|agent_playerbot_events|agent_playerbot_actions|reply sent|failed to send bot reply|requester is not online|bot is not online" \
  /home/wuya/git/azerothcore-wotlk-git/env/dist/logs/Server.log \
  /home/wuya/git/azerothcore-wotlk-git/env/dist/logs/Playerbots.log
systemctl status azerothcore-world.service --no-pager
```

测试服：

```bash
rg "Playerbot Agent bridge enabled|agent_playerbot_events|agent_playerbot_actions|reply sent|failed to send bot reply|requester is not online|bot is not online" \
  /home/wuya/git/azerothcore-wotlk-dev/env/dist/logs/Server.log \
  /home/wuya/git/azerothcore-wotlk-dev/env/dist/logs/Playerbots.log
systemctl status azerothcore-dev-world.service --no-pager
systemctl cat azerothcore-dev-world.service | rg "AC_PLAYERBOTS_DATABASE_INFO|playerbots-db.conf"
```

world 侧桥的核心行为：收到聊天后写 `agent_playerbot_events`；每 `AgentPlayerbot.ActionPollIntervalMs` 轮询 `agent_playerbot_actions`；执行后更新 `status/result/error`。

## 常见故障定位表

| 现象 | 最可能位置 | 关键字 / 命令 |
| --- | --- | --- |
| 玩家发了话，但没有 event | world / mod-playerbot-agent | `Playerbot Agent bridge enabled`、`agent_playerbot_events`、监听范围 |
| event 有，但 `processed_at` 为空 | relay | `systemctl is-active ...hermes-relay`、relay log `startup` |
| event 很快 processed，但没进 Hermes | relay 规则 | `relay_event_skipped`、`relay_event_unauthorized`、`fast_*` |
| relay 有 `hermes_request`，没有 `hermes_response` | Hermes API / provider | `hermes_http_error`、`HTTP 402`、`Insufficient Balance` |
| Hermes 有最终文字，游戏没回复 | Agent 没调用 `wow_reply` 或 reply action 失败 | `fallback_reply_enqueued`、actions 表 `reply` |
| actions 一直 `pending` | world 没轮询或 DB 指错 | world service、测试服 `AC_PLAYERBOTS_DATABASE_INFO` |
| actions `error=requester is not online` | 玩家离线 | 让玩家在线再测 |
| actions `done` 但玩家没看到 | channel/bot/聊天范围 | `channel`、`bot_name`、`result`、游戏内是否私聊/队伍 |
| 测试服消息影响生产服 | 测试服 DB 配错 | `systemctl cat azerothcore-dev-world.service` 查 `acore_dev_playerbots` |

## 生产服实时跟一条消息

开四个终端：

```bash
tail -f /home/wuya/git/azerothcore-wotlk-git/env/dist/logs/playerbot-hermes-relay.log
tail -f /home/wuya/git/azerothcore-wotlk-git/env/dist/logs/playerbot-mcp.log
docker logs -f hermes-wow 2>&1
watch -n 1 'MYSQL_PWD=acore mysql -uacore -h127.0.0.1 -e "SELECT id,channel,speaker_name,target_name,bot_name,message,processed_at FROM acore_playerbots.agent_playerbot_events ORDER BY id DESC LIMIT 5; SELECT id,source_event_id,status,action_type,bot_name,channel,LEFT(text,80) text,LEFT(error,120) error FROM acore_playerbots.agent_playerbot_actions ORDER BY id DESC LIMIT 8;"'
```

然后在生产服对瓦小狸发三类消息：

1. 普通闲聊：应看到 `relay_event -> hermes_request -> hermes_response -> wow_reply/action -> done`。
2. 需要环境的问题：Hermes 日志应出现 `wow_get_party_state`、`wow_get_recent_events` 等按需工具。
3. “新建会话/重置上下文”：应只看到 `fast_context_reset` 和确认 reply，不应进入 Hermes API。

## 测试服实时跟一条消息

开四个终端：

```bash
tail -f /home/wuya/git/azerothcore-wotlk-dev/env/dist/logs/playerbot-hermes-relay-dev.log
tail -f /home/wuya/git/azerothcore-wotlk-dev/env/dist/logs/playerbot-mcp-dev.log
docker logs -f hermes-wow-dev 2>&1
watch -n 1 'MYSQL_PWD=acore mysql -uacore -h127.0.0.1 -e "SELECT id,channel,speaker_name,target_name,bot_name,message,processed_at FROM acore_dev_playerbots.agent_playerbot_events ORDER BY id DESC LIMIT 5; SELECT id,source_event_id,status,action_type,bot_name,channel,LEFT(text,80) text,LEFT(error,120) error FROM acore_dev_playerbots.agent_playerbot_actions ORDER BY id DESC LIMIT 8;"'
```

测试服只看 `acore_dev_playerbots`。如果你看到 `acore_playerbots` 有新 event/action，而测试服没有，说明你进错 realm 或测试 world 的 DB override 出问题。

## 安全边界

- 不要把 `/home/wuya/.hermes-wow/.env`、`/home/wuya/.hermes-wow-dev/.env`、MCP bearer token、Hermes API key、DB 密码、SOAP 密码贴进文档或聊天。
- 文档里的 DSN 只写库名和路径，不写密码。
- 生产和测试都在 GJZN 本机；本机排查不用 SSH。
- RT 现在是历史/回滚，不是当前生产 Hermes 链路。
