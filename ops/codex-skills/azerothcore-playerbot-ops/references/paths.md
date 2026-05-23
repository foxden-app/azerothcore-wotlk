# AzerothCore PlayerBot Paths

## Development Machine

- Current dev checkout: `/home/wuya/git_wsl/azerothcore-wotlk`
- Repo skill source: `ops/codex-skills/azerothcore-playerbot-ops`
- Installed Codex skill target: `${CODEX_HOME:-/mnt/c/Users/chuan/.codex}/skills/azerothcore-playerbot-ops`
- GJZN runbook: `ops/gjzn-bootstrap/README.md`
- RT rollback runbook: `ops/rt-wow-migration/README.md`

Build artifacts may be absent on a new dev machine. Verify before C++ deploy:

- CMake cache: `var/build/obj/CMakeCache.txt`
- Runtime binaries: `env/dist/bin/authserver`, `env/dist/bin/worldserver`
- Expected install prefix when using `var/build/obj`: `env/dist`
- Ignored external module: `modules/mod-playerbots/`
- GJZN production module source: `/home/wuya/git/azerothcore-wotlk-git/modules/mod-playerbots`

Tencent Docs:

- Manifest: `ops/tencent-docs/cloud-docs.json`
- Sync helper: `ops/tencent-docs/sync-docs.sh`
- Folder: `AzerothCore PlayerBot RT 文档`
- Folder URL: `https://docs.qq.com/desktop/mydoc/folder/dCqgFyBeqUwT`
- Architecture doc: `https://docs.qq.com/doc/DZFVEZWVnU0hyb1ZS`
- Runtime README: `https://docs.qq.com/doc/DZFlaZE10TXpWQm9Y`
- RT runbook: `https://docs.qq.com/doc/DZEpTcUdvWWtiVWtE`
- GJZN runbook: `https://docs.qq.com/doc/DZFFKWFpMa2pVeHpT`
- Agent README: `https://docs.qq.com/markdown/DZERueEhFekd1aFR2`
- Tencent Docs sync runbook: `https://docs.qq.com/doc/DZG9wYWhpTmRLQXVJ`

## GJZN Production

- LAN SSH: `ssh GJZN`
- Public SSH: `ssh GJZN-public`
- LAN address: `192.168.1.248`
- GJZN runtime root: `/home/wuya/git/azerothcore-wotlk-git`
- Hermes compose path: `/home/wuya/srv/hermes-wow/docker-compose.yml`
- Runtime files: `/home/wuya/git/azerothcore-wotlk-git/env/dist`
- Configs: `/home/wuya/git/azerothcore-wotlk-git/env/dist/etc`
- Logs: `/home/wuya/git/azerothcore-wotlk-git/env/dist/logs`

GJZN services:

- `azerothcore-auth.service`: authserver, port `3724`
- `azerothcore-world.service`: worldserver, ports `8085` and SOAP `7879`
- `hermes-wow`: Hermes API container, port `8642`
- `frpc-acore-main.service`: public line 1
- `frpc-chml-unicom.service`: public line 2

GJZN systemd sidecars:

- `azerothcore-playerbot-mcp.service`: MCP, port `18765`
- `azerothcore-playerbot-hermes-relay.service`: event relay
- `azerothcore-account-register.service`: registration page, `127.0.0.1:18080`

GJZN databases:

- Login DB: `acore_auth`
- World DB: `acore_playerbot_world`
- Character DB: `acore_playerbot_characters`
- Playerbots DB: `acore_playerbots`

Realms:

- `id=1`, `线路一`, `38.207.189.99:8085`
- `id=2`, `线路二`, `8.162.5.68:8085`
- `RealmList.RealmIDAliases = "2:1"`
- Both realms use `flag=0`, `timezone=16`, `localSubnetMask=255.255.255.255`, build `12340`

GJZN production config facts:

- `RealmID = 1`
- `RealmZone = 16`
- `Console.Enable = 0`
- `Updates.EnableDatabases = 0`
- `MySQLExecutable = "/usr/bin/true"`
- `SourceDirectory = "/home/wuya/git/azerothcore-wotlk-git"`
- `AiPlayerbot.RandomBotAutologin = 0`
- `AiPlayerbot.MinRandomBots = 0`
- `AiPlayerbot.MaxRandomBots = 0`
- `AiPlayerbot.AddClassAccountPoolSize = 10`
- `AgentPlayerbot.AnchorBotAutologin = 1`
- `AgentPlayerbot.AnchorBotName = "瓦小狸"`

Do not expose GJZN MySQL to the public internet. Do not commit env files, GM password files, DB dumps, logs, or runtime `libs/`.
