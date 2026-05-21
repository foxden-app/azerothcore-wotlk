# AzerothCore PlayerBot Paths

- New repo: `/home/wuya/git/azerothcore-wotlk-git`
- Build dir: `/home/wuya/git/azerothcore-wotlk-git/var/build-agent-release`
- Expected CMake install prefix: `/home/wuya/git/azerothcore-wotlk-git/env/dist`
- Built worldserver: `/home/wuya/git/azerothcore-wotlk-git/var/build-agent-release/src/server/apps/worldserver`
- Runtime worldserver: `/home/wuya/git/azerothcore-wotlk-git/env/dist/bin/worldserver`
- Old runtime quarantine: `/home/wuya/git/azerothcore-wotlk.disabled-20260516`
- Auth config: `/home/wuya/git/azerothcore-wotlk-git/env/dist/etc/authserver.conf`
- World config: `/home/wuya/git/azerothcore-wotlk-git/env/dist/etc/worldserver.conf`
- Playerbot config: `/home/wuya/git/azerothcore-wotlk-git/env/dist/etc/modules/playerbots.conf`
- Playerbot agent config: `/home/wuya/git/azerothcore-wotlk-git/env/dist/etc/modules/playerbot_agent.conf`
- MCP env: `/home/wuya/git/azerothcore-wotlk-git/env/dist/etc/playerbot-mcp.env`
- Hermes relay env: `/home/wuya/git/azerothcore-wotlk-git/env/dist/etc/playerbot-hermes-relay.env`
- Runtime data directories under bin: `dbc`, `maps`, `vmaps`, `mmaps`, `Cameras`
- Systemd templates: `/home/wuya/git/azerothcore-wotlk-git/ops/systemd`
- Architecture truth source: `/home/wuya/git/azerothcore-wotlk-git/ARCHITECTURE-agent-playerbots.md`
- Roadmap: `/home/wuya/git/azerothcore-wotlk-git/ROADMAP-agent-playerbots.md`
- Runtime runbook: `/home/wuya/git/azerothcore-wotlk-git/README-playerbot-agent-runtime.md`
- Architecture diagram: `/home/wuya/git/azerothcore-wotlk-git/doc/agent-playerbot-architecture.{dot,svg,png}`

Expected DB targets in `worldserver.conf`:

- Login DB: `acore_auth`
- World DB: `acore_playerbot_world`
- Character DB: `acore_playerbot_characters`

Expected realm:

- `Agent PlayerBot` at `38.207.189.99:8085`

Expected public FRP proxies:

- `azerothcore-auth-3724`
- `azerothcore-world-8085`

Legacy agent proxies `3725/8086` should stay disabled unless a separate hidden realm is intentionally restored.
