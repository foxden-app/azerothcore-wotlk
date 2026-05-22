# RT 生产运行和部署

RT 现在是 AzerothCore PlayerBot 的生产服务器。开发机负责写代码、编译、测试和发布；RT 负责运行，不在 RT 上编译。

## 当前形态

- RT 运行 `wow-auth`、`wow-world` 容器，使用 host network。
- RT MySQL 承载生产库：`acore_auth`、`acore_playerbot_world`、`acore_playerbot_characters`、`acore_playerbots`。
- Hermes 容器运行在 RT：`hermes-wow`，监听 `8642`。
- MCP、Hermes relay、账号注册页运行在 RT systemd。
- 线路一：`38.207.189.99:8085`。
- 线路二：`8.162.5.68:8085`，通过 auth alias `2:1` 指向同一个 worldserver。

## 关键路径

- RT repo/run path: `/home/wuya/git/azerothcore-wotlk-git`
- RT compose path: `/home/wuya/git/azerothcore-wotlk-git/ops/rt-wow-migration`
- Runtime files: `/home/wuya/git/azerothcore-wotlk-git/env/dist`
- Core SQL source: `/home/wuya/git/azerothcore-wotlk-git/data/sql`
- Playerbots SQL source: `/home/wuya/git/azerothcore-wotlk-git/modules/mod-playerbots`
- Agent bridge source mirror: `/home/wuya/git/azerothcore-wotlk-git/modules/mod-playerbot-agent`

## 一条命令入口

从开发机仓库根目录运行：

```bash
ops/rt-wow-migration/deploy.sh status
ops/rt-wow-migration/deploy.sh backup-db
ops/rt-wow-migration/deploy.sh deploy-sidecar
ops/rt-wow-migration/deploy.sh deploy-world
```

新开发机先做前置判断：

```bash
pwd
git status --short --branch
python3 -m py_compile tools/playerbot-mcp/wow_common.py tools/playerbot-mcp/hermes_relay.py tools/playerbot-mcp/server.py
test -d modules/mod-playerbots/src/Script || rsync -az --delete --exclude='.git/' -e 'ssh -p 8022' \
  wuya@38.207.189.99:/home/wuya/git/azerothcore-wotlk-git/modules/mod-playerbots/ \
  modules/mod-playerbots/
cmake -S . -B var/build/obj -G Ninja \
  -DCMAKE_INSTALL_PREFIX="$PWD/env/dist" \
  -DAPPS_BUILD=all -DTOOLS_BUILD=none \
  -DSCRIPTS=static -DMODULES=static \
  -DBUILD_TESTING=OFF -DUSE_SCRIPTPCH=ON -DUSE_COREPCH=ON \
  -DCMAKE_BUILD_TYPE=Release -DWITH_WARNINGS=OFF \
  -DCMAKE_C_COMPILER=/usr/bin/clang \
  -DCMAKE_CXX_COMPILER=/usr/bin/clang++ \
  -DCMAKE_C_COMPILER_LAUNCHER=ccache \
  -DCMAKE_CXX_COMPILER_LAUNCHER=ccache \
  -DBoost_USE_STATIC_LIBS=ON
test -x env/dist/bin/authserver
test -x env/dist/bin/worldserver
```

如果本机没有 CMake cache 或 `env/dist/bin/authserver/worldserver`，不要执行 C++/world 部署；先按上面的流程建立本地构建并安装产物。RT 不编译。

默认目标：

- `RT_USER=wuya`
- `RT_HOST=38.207.189.99`
- `RT_PORT=8022`
- `RT_ROOT=/home/wuya/git/azerothcore-wotlk-git`

需要临时改目标时用环境变量覆盖：

```bash
RT_HOST=192.168.1.179 ops/rt-wow-migration/deploy.sh status
```

## 部署分级

### 1. 侧车热部署

适用于：

- `tools/playerbot-mcp/`
- `tools/account-register/`
- MCP / Hermes relay / 注册页的 Python 改动
- systemd unit 改动

命令：

```bash
ops/rt-wow-migration/deploy.sh deploy-sidecar
ssh -p 8022 wuya@38.207.189.99 '/home/wuya/git/azerothcore-wotlk-git/var/playerbot-mcp-venv/bin/python -m unittest /home/wuya/git/azerothcore-wotlk-git/tools/playerbot-mcp/test_playerbot_mcp.py'
```

它会同步 Python 代码和 RT ops 文件，然后重启：

- `azerothcore-playerbot-mcp.service`
- `azerothcore-playerbot-hermes-relay.service`
- `azerothcore-account-register.service`

不会重启 auth/world，不影响已在线玩家。

### 2. world/auth 部署

适用于：

- `env/dist/bin/authserver`
- `env/dist/bin/worldserver`
- core 或 C++ module 行为改动
- `data/sql/`
- `modules/mod-playerbots/`
- `modules/mod-playerbot-agent/`

命令：

```bash
cmake --build var/build/obj --target authserver worldserver -j16
cmake --install var/build/obj --config Release
ops/rt-wow-migration/deploy.sh deploy-world
```

它会按顺序执行：

1. 备份 RT 生产库。
2. 同步 RT ops 文件。
3. 从开发机 auth/world 二进制重新收集运行库到 `ops/rt-wow-migration/libs/`。
4. 在 RT 重建 `wow-auth` / `wow-world` runtime image。
5. 停 RT `wow-world` / `wow-auth`。
6. 同步 `authserver`、`worldserver`、`data/sql/`、`modules/mod-playerbots/`、`modules/mod-playerbot-agent/`。
7. 启动 auth/world 并输出端口和 world 日志。

注意：`deploy-world` 会断开游戏连接。先通知在线玩家。

`deploy-world` 不会同步整个 `env/dist/bin/`，避免新开发机缺少 maps/vmaps/mmaps 时误删 RT 的运行数据。地图数据只在重新抽取或完整迁移时手工同步。

如果 C++ 改动只改了源码但本机没有编译产物，不要把旧二进制部署到 RT。先建立本机 build，或只提交源码并说明未部署。

### 3. 数据库备份

RT 的角色库是生产数据。正常部署只备份，不从开发机覆盖 RT 数据库。

```bash
ops/rt-wow-migration/deploy.sh backup-db
```

备份路径在 RT：

```text
/home/wuya/backups/acore/acore-YYYYMMDD-HHMMSS.sql.gz
```

只有明确要重置或迁移生产数据时，才手工导入 dump。

## 生产配置

不要无脑同步整个 `env/dist/etc/`。RT 有生产专用配置：

- `authserver.conf`
  - `BindIP = "0.0.0.0"`
  - `RealmList.RealmIDAliases = "2:1"`
- `worldserver.conf`
  - `RealmID = 1`
  - `RealmZone = 16`
  - `Console.Enable = 0`
  - `MinDualSpecLevel = 40`
  - `Updates.EnableDatabases = 0`
  - `MySQLExecutable = "/usr/bin/true"`
- `acore_auth.realmlist`
  - `线路一` 和 `线路二` 的 `flag=0`
  - `timezone=16`
  - `localAddress=address`
  - `localSubnetMask=255.255.255.255`

如果确实要改配置，先在 RT 备份对应文件：

```bash
ssh -p 8022 wuya@38.207.189.99 'cp -a /home/wuya/git/azerothcore-wotlk-git/env/dist/etc/worldserver.conf /home/wuya/git/azerothcore-wotlk-git/env/dist/etc/worldserver.conf.bak-$(date +%Y%m%d-%H%M%S)'
```

## 手动启动和检查

在 RT 上：

```bash
cd /home/wuya/git/azerothcore-wotlk-git/ops/rt-wow-migration
docker compose up -d wow-auth wow-world
docker ps --format 'table {{.Names}}\t{{.Status}}'
ss -ltnp | grep -E ':(3724|8085|7879)\b'
docker logs --tail 120 wow-world
```

期望端口：

- `3724`: authserver
- `8085`: worldserver
- `7879`: SOAP
- `8642`: Hermes API
- `18765`: MCP
- `18080`: 注册页，仅 localhost

期望 world 日志：

```text
AzerothCore ... (worldserver-daemon) ready...
Ensuring anchor bot '瓦小狸' ... is online
```

## 典型排障：瓦小狸不说话

先查服务和积压：

```bash
ops/rt-wow-migration/deploy.sh status
ssh -p 8022 wuya@38.207.189.99 'MYSQL_PWD=acore mysql -uacore -e "
  SELECT COUNT(*) AS unprocessed_events FROM acore_playerbots.agent_playerbot_events WHERE processed_at IS NULL;
  SELECT id,speaker_name,message,created_at,processed_at FROM acore_playerbots.agent_playerbot_events ORDER BY id DESC LIMIT 12;
  SELECT id,source_event_id,action_type,status,command,error FROM acore_playerbots.agent_playerbot_actions ORDER BY id DESC LIMIT 12;
"'
```

判断：

- relay 报 `fetch_events() got an unexpected keyword argument 'unprocessed_only'`：RT sidecar 文件版本不一致，运行 `deploy-sidecar`。
- action 报 `requester is not online`：玩家已经离线，旧消息不能补送；让玩家上线后重新密语瓦小狸。
- 旧事件积压且玩家离线：可以把确认无效的旧事件标记 `processed_at=NOW()`，把 `var/playerbot-hermes-relay/state.json` 推到最新 id，再重启 relay。
- `瓦小狸 online=0`：查 `AgentPlayerbot.AnchorBotAutologin`、world 日志和 `acore_playerbot_characters.characters`。

RT relay 当前支持 `PLAYERBOT_HERMES_SKIP_BACKLOG_ON_START=1`，重启时会跳过积压旧事件，避免迁移/故障恢复后逐条补跑离线玩家旧消息。

## 典型排障：天赋和 GM 操作

天赋请求分两类：

- “切狂暴/切治疗/切坦克”默认是 AI 策略切换，走 `wow_set_bot_role` 或 `wow_set_bot_strategy`，不等于真实天赋重洗。
- “第二天赋/双天赋/重置天赋/洗天赋/切天赋页”是真实天赋页操作。先查 profile 或角色库。

检查目标角色是否有第二天赋：

```bash
ssh -p 8022 wuya@38.207.189.99 'MYSQL_PWD=acore mysql -uacore --default-character-set=utf8mb4 -e "
  SELECT name,level,online,activeTalentGroup,talentGroupsCount
  FROM acore_playerbot_characters.characters
  WHERE name IN (\"Wuya\",\"令狐冲\",\"电子铁拳\",\"快乐铁拳\",\"黑化观音\",\"瓦小狸\")
  ORDER BY name;
"'
```

如果已确认 `talentGroupsCount < 2`，瓦小狸应回复“没有第二套天赋”，并说明 RT 当前 `MinDualSpecLevel=40`，30 级 bot 正常只有一套天赋。拿不到天赋页数量时只能说“当前没拿到天赋页数据”，不要猜，也不要用 AI 策略切换冒充真实第二天赋。

GM/高权限操作只允许走 MCP 已暴露的受审计工具。Wuya 的账号有 GM 权限，可以执行允许的高权限工具；不要让 Hermes 拼任意 GM 命令、SQL 或 shell。

## RT 独立侧车

RT systemd units：

```bash
sudo install -m 0644 ops/rt-wow-migration/systemd/azerothcore-playerbot-mcp.service /etc/systemd/system/azerothcore-playerbot-mcp.service
sudo install -m 0644 ops/rt-wow-migration/systemd/azerothcore-playerbot-hermes-relay.service /etc/systemd/system/azerothcore-playerbot-hermes-relay.service
sudo install -m 0644 ops/rt-wow-migration/systemd/azerothcore-account-register.service /etc/systemd/system/azerothcore-account-register.service
sudo systemctl daemon-reload
sudo systemctl enable --now azerothcore-playerbot-mcp.service azerothcore-playerbot-hermes-relay.service azerothcore-account-register.service
```

注册页需要 SOAP GM 密码，保存在 RT 本机私有文件：

```text
/home/wuya/.config/acore/gm.env
```

不要把密码写进 git。

检查：

```bash
curl -fsS http://127.0.0.1:18765/health
curl -fsS http://127.0.0.1:18080/
systemctl is-active azerothcore-playerbot-mcp.service azerothcore-playerbot-hermes-relay.service azerothcore-account-register.service
journalctl -u azerothcore-playerbot-mcp.service -u azerothcore-playerbot-hermes-relay.service -u azerothcore-account-register.service -n 80 --no-pager
```

## 已知限制

- RT 的 J1900 CPU 只适合临时/轻量生产；world 空载也会吃明显 CPU。
- RT 不编译。C++ 改动必须在开发机编译后部署二进制。
- 当前 runtime image 不跑 apt，而是把开发机 auth/world 需要的动态库复制到 `ops/rt-wow-migration/libs/`，再构建镜像。
- `libs/` 被 git 忽略；换开发机或升级依赖后运行 `deploy-libs` 或 `deploy-world`。
- 容器里没有 `mysql` CLI，所以 RT 运行配置关闭了自动 SQL updater。部署前要确保数据库和二进制版本匹配。
- RT MySQL 不应暴露给公网。

## 回滚

数据库回滚：

```bash
ssh -p 8022 wuya@38.207.189.99
zcat ~/backups/acore/acore-YYYYMMDD-HHMMSS.sql.gz | sudo mysql
```

程序回滚：

1. 在开发机切回上一版代码并重新编译。
2. 运行：

```bash
ops/rt-wow-migration/deploy.sh deploy-world
```

紧急停服：

```bash
ssh -p 8022 wuya@38.207.189.99 '
cd /home/wuya/git/azerothcore-wotlk-git/ops/rt-wow-migration
docker compose down
sudo systemctl stop azerothcore-playerbot-mcp.service azerothcore-playerbot-hermes-relay.service azerothcore-account-register.service
'
```
