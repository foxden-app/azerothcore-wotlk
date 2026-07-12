# GJZN 初始化与运营记录

> **现状（2026-07-12）**：Foxden 与 AzerothCore 生产栈已回迁到 `T490-Agent`。本文以下内容保留为 GJZN 历史部署和回滚参考，不再代表当前生产位置。GJZN 上相关 systemd 服务已停用，并通过 `ConditionPathExists=/etc/allow-gjzn-production` 冷备保护；只有显式创建该文件后才允许重新启动。`frpc-pentagi.service` 仍在 GJZN 正常运行。

更新时间：2026-05-24

GJZN（龟机智能）是新的 i7 机器。2026-05-23 已从 RT 接管 AzerothCore PlayerBot 生产服：auth/world 原生 systemd 运行，Hermes 用 Docker 容器，MCP/relay/注册页用 systemd，公网游戏入口通过 FRP 转发到 GJZN。

## 主机信息

- 本机执行：Codex/运维 shell 在 GJZN 上时，`hostname` 为 `GJZN`，直接运行本地命令。
- SSH：仅从其他机器连入 GJZN 时使用。
- LAN SSH：`ssh GJZN`，当前地址 `192.168.1.203`
- 公网 SSH：`ssh GJZN-public`，`38.207.189.99:8026`
- 用户：`wuya`
- sudo：免密 sudo 已验证
- Hostname：`GJZN`
- 系统：Ubuntu 24.04 LTS
- CPU：Intel i7-3770K，4 核 8 线程
- 主板：ASUS P8Z77-V
- 内存：8GB DDR3，实际可用约 7.7GiB
- 交换分区：`/swap.img`，16GB
- 根分区：约 110GB，迁服后剩余约 60GB
- 当前网络：有线 `eno1`，地址 `192.168.1.203`。WiFi `CMCC-AU2A` 已断开并禁用 autoconnect。

本地 SSH alias 记录在开发机的 `~/.ssh/config`：

```sshconfig
Host GJZN
    HostName 192.168.1.203
    Port 22
    User wuya
    ControlMaster auto
    ControlPersist 10m
    ControlPath ~/.ssh/cm-%C
    ServerAliveInterval 30
    ServerAliveCountMax 3

Host GJZN-public
    HostName 38.207.189.99
    Port 8026
    User wuya
    ControlMaster auto
    ControlPersist 10m
    ControlPath ~/.ssh/cm-%C
    ServerAliveInterval 30
    ServerAliveCountMax 3
```

## 当前生产服务

生产运行目录：

```text
/home/wuya/git/azerothcore-wotlk-git
```

生产服务：

- `azerothcore-auth.service`：authserver，`0.0.0.0:3724`
- `azerothcore-world.service`：worldserver，`0.0.0.0:8085`，SOAP `0.0.0.0:7879`
- `hermes-wow`：Docker 容器，Hermes API `0.0.0.0:8642`，dashboard `127.0.0.1:9119`。生产服 2026-06-10 已更新到 Hermes Agent `0.16.0`，默认 provider 为测试服同款 `custom:xfyun-wow` / `xopqwen36v35b`；该镜像使用 s6 overlay，compose 不要设置 `init: true`。
- `azerothcore-playerbot-mcp.service`：MCP，`0.0.0.0:18765`
- `azerothcore-playerbot-hermes-relay.service`：游戏事件转 Hermes
- `azerothcore-account-register.service`：注册页，`127.0.0.1:18080`
- `frpc-acore-main.service`：线路一公网 `38.207.189.99:3724/8085`
- `frpc-chml-unicom.service`：线路二公网 `8.162.5.68:8085`，备用 auth `8.162.5.68:8724`
- `azerothcore-dev-world.service`：敏捷测试 world，`127.0.0.1:8086`，SOAP `127.0.0.1:7880`
- `azerothcore-dev-playerbot-mcp.service` / `azerothcore-dev-playerbot-hermes-relay.service`：敏捷测试 Agent 链路
- `azerothcore-dev-hermes-wow.service`：敏捷测试 Hermes，API `127.0.0.1:8643`，dashboard `127.0.0.1:9120`。测试服 2026-06-09 已更新到 Hermes Agent `0.16.0`；该镜像使用 s6 overlay，compose 不要设置 `init: true`。
- `frpc-acore-dev.service`：敏捷测试公网 world `38.207.189.99:8086`
- `frpc-gjzn.service`：GJZN 公网 SSH `38.207.189.99:8026`

生产数据库在 GJZN 本机 MySQL：

```text
acore_auth
acore_playerbot_world
acore_playerbot_characters
acore_playerbots
acore_dev_world
acore_dev_characters
acore_dev_playerbots
```

RT 已退为旧生产/回滚来源：RT auth/world、MCP、relay、注册页、Hermes 已停；RT 主 `frpc.service` 保留非游戏代理，游戏端口 3724/8085 已移除；RT 线路二 `frpc-chml-unicom.service` 已停。

最终迁移备份在 RT：

```text
/home/wuya/backups/acore/acore-gjzn-cutover-20260523-230612.sql.gz
```

## 已完成的持久化配置

GJZN 已按无图形服务器方式配置：

- 默认 target：`multi-user.target`
- display manager：已停止/禁用；当前服务不存在或 inactive
- 睡眠/休眠：`sleep.target`、`suspend.target`、`hibernate.target`、`hybrid-sleep.target` 已 mask
- 开机自启：`ssh`、`docker`、`mysql`、AzerothCore 生产服务、FRP 游戏服务、GJZN 本机 `xray` 备用代理
- Docker：已安装并启用，`wuya` 已加入 `docker` 组
- Docker Compose：`docker compose version` 可用
- ccache：已设置 20GB 上限

常用检查：

```bash
hostname
systemctl get-default
systemctl is-active ssh docker xray
systemctl is-enabled ssh docker xray
nmcli -t -f NAME,DEVICE,TYPE,AUTOCONNECT connection show --active
free -h
swapon --show
df -h /
docker info | egrep "HTTP Proxy|HTTPS Proxy|No Proxy"
```

2026-05-23 已做首次重启验证：

- SSH 自动恢复。
- `docker.service`、GJZN 本机 `xray.service` 自动恢复；刚能 SSH 进去时可能还在启动，等几十秒后复查即可。
- GJZN 本机 Xray 端口 `127.0.0.1:20170` / `127.0.0.1:20171` 自动监听，仅作为 RT 统一代理故障时的备用。
- Docker 能运行容器。
- git 能通过本机代理 `fetch` GitHub。

## 代理与 Docker 拉取

T490 关机前已把代理配置备份到本地私有目录：

```text
var/private/t490-proxy-backup-20260523-174422/
```

该目录不提交到 git。里面包含 Xray 配置、原 Docker 代理 drop-in 和排障快照。

生产代理边界：

- 魔兽 `authserver`、`worldserver`、FRP 游戏线路不走代理，不继承 `HTTP_PROXY`、`HTTPS_PROXY` 或 `ALL_PROXY`。
- RT 是统一网络代理入口，Xray 监听 `192.168.1.179:20170` SOCKS 和 `192.168.1.179:20171` HTTP。
- GJZN 的 foxclaw/Codex 走 RT 代理：`/home/wuya/.foxclaw/.env` 指向 RT，`foxclaw.service` 通过 user drop-in `/home/wuya/.config/systemd/user/foxclaw.service.d/10-rt-proxychains.conf` 包一层 `proxychains4`，让 Telegram 的 Node `https.request` 也能走 RT。
- GJZN 本机 Xray 保留为备用，不作为默认路径。GJZN WARP 不应参与魔兽游戏链路；需要国外网络时优先使用 RT 代理或 GJZN 本机 Xray 备用。

GJZN 已从这份备份恢复本机 Xray 备用：

- Xray 配置：`/home/wuya/.xray/config.json`
- 备用 HTTP 代理：`127.0.0.1:20171`
- 备用 SOCKS 代理：`127.0.0.1:20170`
- systemd unit：`xray.service`
- Docker daemon 代理 drop-in：`/etc/systemd/system/docker.service.d/http-proxy.conf`

foxclaw/Codex 当前通过 RT 代理访问国外网络：

```text
HTTP_PROXY=http://192.168.1.179:20171
HTTPS_PROXY=http://192.168.1.179:20171
ALL_PROXY=socks5://192.168.1.179:20170
NO_PROXY=localhost,127.0.0.1,::1,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12
```

GJZN 的 `wuya` 交互 shell 可以按需配置代理；不要把这些代理变量导入 systemd 全局环境。服务类按需配置，魔兽/FRP 不配置代理。

GJZN 的 `wuya` 用户曾配置 git 走 HTTP 代理，否则直接 `git fetch` GitHub 可能超时。当前推荐优先指向 RT 代理：

```bash
git config --global http.proxy http://192.168.1.179:20171
git config --global https.proxy http://192.168.1.179:20171
```

已验证：

```bash
curl -I -x http://192.168.1.179:20171 https://api.openai.com/v1/models
proxychains4 -f /home/wuya/.proxychains-rt.conf curl -I https://api.telegram.org
git -C /home/wuya/git/azerothcore-wotlk-git fetch foxden-app playerbot-agent
```

不要把 `/home/wuya/.xray/config.json`、订阅信息、token、代理节点、foxclaw `.env` 或任何密钥写进仓库。

## 构建环境

已安装常用构建和运维工具：

```text
git rsync jq unzip curl docker.io docker-compose-v2
python3-venv python3-pip
build-essential clang cmake ninja-build ccache pkg-config
libssl-dev libbz2-dev libreadline-dev libncurses-dev
libboost-all-dev libmysqlclient-dev mysql-client
```

仓库已同步到：

```text
/home/wuya/git/azerothcore-wotlk-git
```

同步时刻意排除了运行产物和本地私有数据：

- `env/`
- `var/`
- `.telegram-inbox/`
- `ops/rt-wow-migration/libs/`
- `*.log`

这些目录里的数据库备份、运行二进制、地图数据、日志和密钥不应从开发机粗暴覆盖到 GJZN。

## 内存限制

当前 8GB 内存可以承接低负载生产栈，但余量不大。迁服后观测到 GJZN 大约 4.8GB 内存已用、约 2.9GB available，swap 有少量使用。

注意：

- `worldserver`、MySQL、Hermes、MCP、relay、注册页和 Docker 会一起吃内存。
- 16GB swap 是保护网，不是运行内存。
- 随机世界 bot 仍不建议大规模打开。

C++ 编译建议先低并发：

```bash
cmake --build var/build/obj --target authserver worldserver -j2
```

16GB 更适合长期构建和生产冗余；当前 8GB 先按低负载生产运行。

## 已知问题

迁移后做过 Hermes 端到端 synthetic event，自检事件已经到达 GJZN Hermes，但 Hermes 模型调用返回：

```text
HTTP 402: Insufficient Balance
```

这表示 WoW -> relay -> Hermes 的链路是通的，但当前 Hermes 使用的 DeepSeek/custom provider 余额不足。瓦小狸不回复时，先修 Hermes `.env` 中的模型 provider/key/余额。

## 插电自动启动

插电自动启动通常是 BIOS/UEFI 设置，不是 Ubuntu 里能可靠统一配置的项目。

ASUS P8Z77-V 建议在 BIOS 里检查：

```text
Advanced Mode -> Advanced -> APM -> Restore AC Power Loss = Power On
```

如果有 `ErP Ready`，生产/远程机器建议关闭，否则可能影响断电恢复、USB 或网络唤醒。

当前活跃网络是有线 `eno1`。Wake-on-LAN 一般只适用于有线网卡；如需远程唤醒，再在 BIOS 和网卡侧单独打开 WOL。

## 后续动作

1. 接有线网并固定 DHCP 或静态地址。
2. 修 Hermes provider 余额/key，让瓦小狸恢复回复。
3. 建立本机 CMake build cache，不要从 RT 或旧开发机拷 build 目录。
4. 后续如要回滚 RT，先恢复 RT game FRP 代理，再导回数据库备份。
