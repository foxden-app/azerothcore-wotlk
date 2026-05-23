# GJZN 初始化与运营记录

更新时间：2026-05-23

GJZN（龟机智能）是新的 i7 机器，当前先按“开发构建机 / 热备候选机”初始化。它还不是正式生产服：当前只插上 4GB 内存，运行完整 WoW 生产栈会非常紧，等内存修复到至少 8GB 后再考虑承接 RT 的生产职责。

## 主机信息

- SSH：`ssh GJZN`
- 当前地址：`192.168.1.248`
- 用户：`wuya`
- sudo：免密 sudo 已验证
- Hostname：`GJZN`
- 系统：Ubuntu 24.04 LTS
- CPU：Intel i7-3770K，4 核 8 线程
- 主板：ASUS P8Z77-V
- 内存：当前 4GB DDR3，实际可用约 3.8GiB
- 交换分区：`/swap.img`，16GB
- 根分区：约 110GB，总剩余约 78GB
- 当前网络：WiFi `wlp6s0`，连接 `CMCC-AU2A`，已启用 autoconnect

本地 SSH alias 记录在开发机的 `~/.ssh/config`：

```sshconfig
Host GJZN
    HostName 192.168.1.248
    Port 22
    User wuya
    ControlMaster auto
    ControlPersist 10m
    ControlPath ~/.ssh/cm-%C
    ServerAliveInterval 30
    ServerAliveCountMax 3
```

## 已完成的持久化配置

GJZN 已按无图形服务器方式配置：

- 默认 target：`multi-user.target`
- display manager：已停止/禁用；当前服务不存在或 inactive
- 睡眠/休眠：`sleep.target`、`suspend.target`、`hibernate.target`、`hybrid-sleep.target` 已 mask
- 开机自启：`ssh`、`docker`、`xray`
- Docker：已安装并启用，`wuya` 已加入 `docker` 组
- Docker Compose：`docker compose version` 可用
- ccache：已设置 20GB 上限

常用检查：

```bash
ssh GJZN 'systemctl get-default'
ssh GJZN 'systemctl is-active ssh docker xray'
ssh GJZN 'systemctl is-enabled ssh docker xray'
ssh GJZN 'nmcli -t -f NAME,DEVICE,TYPE,AUTOCONNECT connection show --active'
ssh GJZN 'free -h && swapon --show && df -h /'
ssh GJZN 'docker info | egrep "HTTP Proxy|HTTPS Proxy|No Proxy"'
```

## 代理与 Docker 拉取

T490 关机前已把代理配置备份到本地私有目录：

```text
var/private/t490-proxy-backup-20260523-174422/
```

该目录不提交到 git。里面包含 Xray 配置、原 Docker 代理 drop-in 和排障快照。

GJZN 已从这份备份恢复本机 Xray：

- Xray 配置：`/home/wuya/.xray/config.json`
- HTTP 代理：`127.0.0.1:20171`
- SOCKS 代理：`127.0.0.1:20170`
- systemd unit：`xray.service`
- Docker daemon 代理 drop-in：`/etc/systemd/system/docker.service.d/http-proxy.conf`

Docker 当前通过本机 HTTP 代理拉取外部镜像：

```text
HTTP_PROXY=http://127.0.0.1:20171
HTTPS_PROXY=http://127.0.0.1:20171
NO_PROXY=localhost,127.0.0.1,::1,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12
```

GJZN 的 `wuya` 用户也已配置 git 走同一个 HTTP 代理，否则直接 `git fetch` GitHub 会超时：

```bash
git config --global http.proxy http://127.0.0.1:20171
git config --global https.proxy http://127.0.0.1:20171
```

已验证：

```bash
ssh GJZN 'curl -fsS -x http://127.0.0.1:20171 https://api.github.com/repos/XTLS/Xray-core/releases/latest | jq -r .tag_name'
ssh GJZN 'git -C /home/wuya/git/azerothcore-wotlk-git fetch foxden-app playerbot-agent'
ssh GJZN 'docker pull hello-world'
ssh GJZN 'docker run --rm hello-world'
```

不要把 `/home/wuya/.xray/config.json`、订阅信息、token、代理节点或任何密钥写进仓库。

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

当前 4GB 内存只适合做轻量开发、文档、脚本、sidecar 测试和低并发编译。

不要在 4GB 状态下把完整 RT 生产栈直接迁到 GJZN：

- RT 上 `worldserver` 空载约 3GB 内存。
- MySQL、Hermes、MCP、relay、注册页、Docker 本身还会继续吃内存。
- 16GB swap 只能防止构建或临时任务 OOM，不等于运行内存。

C++ 编译建议先低并发：

```bash
cmake --build var/build/obj --target authserver worldserver -j2
```

内存修复到 8GB 后，可以重新评估是否让 GJZN 承接 RT 生产或热备。16GB 更适合长期构建和生产冗余。

## 插电自动启动

插电自动启动通常是 BIOS/UEFI 设置，不是 Ubuntu 里能可靠统一配置的项目。

ASUS P8Z77-V 建议在 BIOS 里检查：

```text
Advanced Mode -> Advanced -> APM -> Restore AC Power Loss = Power On
```

如果有 `ErP Ready`，生产/远程机器建议关闭，否则可能影响断电恢复、USB 或网络唤醒。

当前活跃网络是 WiFi。Wake-on-LAN 一般只适用于有线网卡；如果 GJZN 后续作为生产或热备机器，建议接有线网。

## 后续动作

1. 处理第二条内存无法启动的问题，目标至少 8GB。
2. 接有线网并固定 DHCP 或静态地址。
3. 首次重启后验证 `ssh/docker/xray` 自动恢复。
4. 建立本机 CMake build cache，不要从 RT 或旧开发机拷 build 目录。
5. 如果要做热备，再设计 RT -> GJZN 的数据库备份、运行产物同步和明确的人工切换流程。
