# AzerothCore PlayerBot Paths

## Development Machine

- Current dev checkout: `/home/wuya/git_wsl/azerothcore-wotlk`
- Repo skill source: `ops/codex-skills/azerothcore-playerbot-ops`
- Installed Codex skill target: `${CODEX_HOME:-/mnt/c/Users/chuan/.codex}/skills/azerothcore-playerbot-ops`
- RT deploy script: `ops/rt-wow-migration/deploy.sh`
- RT deploy runbook: `ops/rt-wow-migration/README.md`

Build artifacts may be absent on a new dev machine. Verify before C++ deploy:

- CMake cache: `var/build/obj/CMakeCache.txt`
- Runtime binaries: `env/dist/bin/authserver`, `env/dist/bin/worldserver`
- Expected install prefix when using `var/build/obj`: `env/dist`
- Ignored external module: `modules/mod-playerbots/`
- RT production module source: `/home/wuya/git/azerothcore-wotlk-git/modules/mod-playerbots`

Tencent Docs:

- Manifest: `ops/tencent-docs/cloud-docs.json`
- Sync helper: `ops/tencent-docs/sync-docs.sh`
- Folder: `AzerothCore PlayerBot RT 文档`
- Folder URL: `https://docs.qq.com/desktop/mydoc/folder/dCqgFyBeqUwT`
- Architecture smart doc: `https://docs.qq.com/aio/DZHBDcVVDWE5Ma1F3`
- Runtime README: `https://docs.qq.com/doc/DZHZaVFRnaFptZVdy`
- RT runbook: `https://docs.qq.com/doc/DZE9mZmVETnVMdktD`
- Agent README: `https://docs.qq.com/markdown/DZERueEhFekd1aFR2`
- Tencent Docs sync runbook: `https://docs.qq.com/doc/DZG9wYWhpTmRLQXVJ`

## RT Production

- SSH: `ssh -p 8022 wuya@38.207.189.99`
- LAN address: `192.168.1.179`
- RT runtime root: `/home/wuya/git/azerothcore-wotlk-git`
- RT compose path: `/home/wuya/git/azerothcore-wotlk-git/ops/rt-wow-migration`
- Runtime files: `/home/wuya/git/azerothcore-wotlk-git/env/dist`
- Configs: `/home/wuya/git/azerothcore-wotlk-git/env/dist/etc`
- Logs: `/home/wuya/git/azerothcore-wotlk-git/env/dist/logs`
- Runtime image libs: `/home/wuya/git/azerothcore-wotlk-git/ops/rt-wow-migration/libs` (ignored by git)

RT containers:

- `wow-auth`: authserver, host network, port `3724`
- `wow-world`: worldserver, host network, ports `8085` and SOAP `7879`
- `hermes-wow`: Hermes API, port `8642`

RT systemd sidecars:

- `azerothcore-playerbot-mcp.service`: MCP, port `18765`
- `azerothcore-playerbot-hermes-relay.service`: event relay
- `azerothcore-account-register.service`: registration page, `127.0.0.1:18080`

RT databases:

- Login DB: `acore_auth`
- World DB: `acore_playerbot_world`
- Character DB: `acore_playerbot_characters`
- Playerbots DB: `acore_playerbots`

Realms:

- `id=1`, `线路一`, `38.207.189.99:8085`
- `id=2`, `线路二`, `8.162.5.68:8085`
- `RealmList.RealmIDAliases = "2:1"`
- Both realms use `flag=0`, `timezone=16`, `localSubnetMask=255.255.255.255`, build `12340`

RT production config facts:

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

Do not expose RT MySQL to the public internet. Do not commit RT env files, GM password files, DB dumps, logs, or runtime `libs/`.
