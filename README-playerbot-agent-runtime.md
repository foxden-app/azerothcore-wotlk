# PlayerBot Agent Runtime

Local runtime notes for the `playerbot-agent` branch.

## Source

- Core branch: `playerbot-agent`
- Core upstream: `playerbots-core/Playerbot`
- Module path: `modules/mod-playerbots`
- Module upstream: `https://github.com/mod-playerbots/mod-playerbots.git`

The module directory is a local nested Git checkout and is ignored by the
root repository, matching AzerothCore's normal module workflow.

## Runtime

- Worldserver: `/home/wuya/git/azerothcore-wotlk-git/env/dist/bin/worldserver`
- Config: `/home/wuya/git/azerothcore-wotlk-git/env/dist/etc/worldserver.conf`
- Playerbots config: `/home/wuya/git/azerothcore-wotlk-git/env/dist/etc/modules/playerbots.conf`
- Logs: `/home/wuya/git/azerothcore-wotlk-git/env/dist/logs`
- tmux session: `playerbot-world`

Ports:

- Authserver: `3724`
- PlayerBot realm: `8086`
- SOAP: `7879`

Databases:

- Shared auth: `acore_auth`
- PlayerBot world: `acore_playerbot_world`
- PlayerBot characters: `acore_playerbot_characters`
- PlayerBot module: `acore_playerbots`

Realm entry:

- `realmlist.id = 2`
- name: `Agent PlayerBot`
- address: `38.207.189.99`
- port: `8086`

## Current Tuning

This server is tuned for Agent-driven bot control instead of ambient random
population:

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

The bot account prefix is `pbagent`. The initial database contains 55 bot
accounts and 550 generated bot characters, with 50 accounts assigned to the
AddClass pool for quick party creation.

## Operations

Check status:

```bash
tmux ls
ss -ltnp | rg ':8086|:7879|:3724|:3306'
tail -n 80 env/dist/logs/Server.log
tail -n 80 env/dist/logs/Playerbots.log
```

Attach to the server console:

```bash
tmux attach -t playerbot-world
```

Stop from outside tmux:

```bash
tmux send-keys -t playerbot-world C-c
```

Start from outside tmux:

```bash
tmux new-session -d -s playerbot-world \
  "cd /home/wuya/git/azerothcore-wotlk-git/env/dist/bin && exec ./worldserver --config /home/wuya/git/azerothcore-wotlk-git/env/dist/etc/worldserver.conf >> /home/wuya/git/azerothcore-wotlk-git/env/dist/logs/worldserver.playerbot.stdout.log 2>&1"
```
