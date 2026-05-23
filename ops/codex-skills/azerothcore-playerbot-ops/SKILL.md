---
name: azerothcore-playerbot-ops
description: Operate, debug, build, deploy, document, commit, and push the AzerothCore WotLK PlayerBot/Hermes stack. Use when working on RT production, local dev builds, MCP/Hermes relay sidecars, 瓦小狸/playerbot behavior, realmlist lines, systemd/container health, architecture truth docs, Chinese commit messages, or routine compile/deploy/restart troubleshooting.
---

# AzerothCore PlayerBot Ops

## Current Truth

RT is production. The development machine is for editing, testing, building, and publishing. Do not restart or resurrect T490/local worldserver as production unless the user explicitly asks.

Production shape:

- RT SSH: `wuya@38.207.189.99 -p 8022`.
- RT runtime root: `/home/wuya/git/azerothcore-wotlk-git`.
- RT runs `wow-auth`, `wow-world`, and `hermes-wow` containers.
- RT runs MCP, Hermes relay, and account register as systemd services.
- Public realms: `线路一 -> 38.207.189.99:8085`, `线路二 -> 8.162.5.68:8085`.
- Auth alias: `RealmList.RealmIDAliases = "2:1"`; both realms point to the same worldserver.
- RT production DBs: `acore_auth`, `acore_playerbot_world`, `acore_playerbot_characters`, `acore_playerbots`.

GJZN is a new i7 development/build and standby candidate, not current production while it only has 4GB RAM:

- GJZN SSH: `ssh GJZN` / `wuya@192.168.1.248`.
- GJZN repo root: `/home/wuya/git/azerothcore-wotlk-git`.
- GJZN runbook: `ops/gjzn-bootstrap/README.md`.
- It has Ubuntu 24.04, Docker, Xray proxy, build deps, 16GB swap, no GUI target, and boot-enabled `ssh/docker/xray`.
- Do not move RT production WoW to GJZN until memory is fixed to at least 8GB; 16GB is preferred.

Primary ops entrypoint from the repo root:

```bash
ops/rt-wow-migration/deploy.sh status
ops/rt-wow-migration/deploy.sh deploy-sidecar
ops/rt-wow-migration/deploy.sh deploy-world
ops/rt-wow-migration/deploy.sh backup-db
```

Use `RT_HOST=192.168.1.179` only when intentionally targeting RT over LAN.

## First Moves

Before changing anything:

```bash
pwd
git status --short --branch
ops/rt-wow-migration/deploy.sh status
```

If investigating a player report like “瓦小狸不说话”:

1. Check RT services and ports with `deploy.sh status`.
2. Check relay/MCP health and recent event/action rows; do not dump full raw relay JSON unless necessary.
3. Confirm the reporter is online before expecting replies or bot actions; old offline events often fail with `requester is not online`.
4. If stale events accumulated while relay was broken, mark only those stale rows processed and advance relay state to the latest event, then restart the relay.

Talent and GM-operation truth:

- `wow_set_bot_role` and `wow_set_bot_strategy` change AI strategies only; they do not reset real talents or create a second talent group.
- For “second spec / dual spec / reset talents / switch talent page” reports, check `wow_get_bot_profile` or the `characters.activeTalentGroup` and `characters.talentGroupsCount` fields first.
- If the talent group fact is known and `talentGroupsCount < 2`, reply that the target has no second talent group and include the level/config reason when known. RT currently uses `MinDualSpecLevel = 40`, so level-30 bots normally have one group. If the count is unknown, say the talent-page data is unavailable instead of guessing.
- GM/high-privilege operations are allowed only through MCP tools with explicit audit/admin checks. Wuya has GM authority; do not execute arbitrary terminal, SQL, or raw GM commands on behalf of Hermes.

Useful targeted query:

```bash
ssh -p 8022 wuya@38.207.189.99 'MYSQL_PWD=acore mysql -uacore -e "
  SELECT id,speaker_name,message,created_at,processed_at
  FROM acore_playerbots.agent_playerbot_events
  ORDER BY id DESC LIMIT 12;
  SELECT id,source_event_id,action_type,status,command,error
  FROM acore_playerbots.agent_playerbot_actions
  ORDER BY id DESC LIMIT 12;
  SELECT name,online
  FROM acore_playerbot_characters.characters
  WHERE name IN (\"瓦小狸\",\"小德\",\"Wuya\")
  ORDER BY name;
"'
```

## Deploy Rules

Sidecar-only changes include `tools/playerbot-mcp/`, `tools/account-register/`, RT systemd unit changes, relay logic, MCP tools, and registration page code.

```bash
python3 -m py_compile tools/playerbot-mcp/wow_common.py tools/playerbot-mcp/hermes_relay.py tools/playerbot-mcp/server.py
ops/rt-wow-migration/deploy.sh deploy-sidecar
ssh -p 8022 wuya@38.207.189.99 '/home/wuya/git/azerothcore-wotlk-git/var/playerbot-mcp-venv/bin/python -m unittest /home/wuya/git/azerothcore-wotlk-git/tools/playerbot-mcp/test_playerbot_mcp.py'
```

C++/world changes include `modules/mod-playerbot-agent/`, core code, `modules/mod-playerbots/`, SQL source, or runtime binary changes. These require a local build and `env/dist/bin/authserver` plus `env/dist/bin/worldserver`.

`modules/mod-playerbots/` is an ignored external module checkout. If it is absent on a new dev machine, hydrate it before configuring CMake. Prefer the RT production working tree when trying to reproduce production exactly:

```bash
rsync -az --delete --exclude='.git/' -e 'ssh -p 8022' \
  wuya@38.207.189.99:/home/wuya/git/azerothcore-wotlk-git/modules/mod-playerbots/ \
  modules/mod-playerbots/
```

If the current dev machine has no `CMakeCache.txt`, no built `worldserver`, or no `env/dist/bin/authserver/worldserver`, do not fake a C++ deploy. Create a proper local build or only deploy sidecars/docs.

Expected local build checks before C++ deploy:

```bash
find var -name CMakeCache.txt -o -name worldserver -o -name authserver
test -x env/dist/bin/authserver
test -x env/dist/bin/worldserver
```

When build artifacts exist:

```bash
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
cmake --build var/build/obj --target authserver worldserver -j16
cmake --install var/build/obj --config Release
ops/rt-wow-migration/deploy.sh deploy-world
```

`deploy-world` backs up RT DBs, rebuilds the runtime image from local binary libraries, syncs only auth/world binaries plus SQL/module sources, and restarts RT auth/world. It intentionally does not sync all of `env/dist/bin`, to avoid deleting RT maps/vmaps/mmaps from a new dev machine.

## Service And Log Hygiene

RT service checks:

```bash
ssh -p 8022 wuya@38.207.189.99 'systemctl is-active azerothcore-playerbot-mcp.service azerothcore-playerbot-hermes-relay.service azerothcore-account-register.service'
ssh -p 8022 wuya@38.207.189.99 'ss -ltnp | egrep ":(3724|8085|7879|8642|18765|18080)\b" || true'
ssh -p 8022 wuya@38.207.189.99 'cd /home/wuya/git/azerothcore-wotlk-git/ops/rt-wow-migration && docker compose ps'
```

Prefer compact logs:

```bash
ssh -p 8022 wuya@38.207.189.99 'journalctl -u azerothcore-playerbot-mcp.service -u azerothcore-playerbot-hermes-relay.service -n 120 --no-pager'
ssh -p 8022 wuya@38.207.189.99 'tail -120 /home/wuya/git/azerothcore-wotlk-git/env/dist/logs/playerbot-mcp.log'
ssh -p 8022 wuya@38.207.189.99 'docker logs --tail 160 wow-world'
```

Do not print full env files, Hermes config, bearer tokens, API keys, DB passwords, SOAP passwords, or full raw relay JSON. Redact secrets and use narrow `rg`/SQL selections.

## Architecture Docs

When changing PlayerBot behavior, MCP/Hermes sidecars, RT deploy flow, service topology, DB tables, realmlist, or operational rules, update truth-source docs in the same change:

- `ARCHITECTURE-agent-playerbots.md`
- `README-playerbot-agent-runtime.md`
- `ops/rt-wow-migration/README.md`
- `ops/codex-skills/azerothcore-playerbot-ops/`
- `doc/agent-playerbot-architecture.dot` and rendered `.svg/.png` if topology changes
- `ROADMAP-agent-playerbots.md` only when the product/implementation roadmap changes

Keep docs concise and factual. Use Chinese prose unless the surrounding file is English-only. Keep runtime paths, ports, service names, and DB names aligned with RT truth.

Cloud docs are part of the operating surface because teammates read there. When updating architecture/runbook/README docs, also update the Tencent Docs copies in the same turn.

- Source-of-truth manifest and helper: `ops/tencent-docs/cloud-docs.json`, `ops/tencent-docs/sync-docs.sh`
- Folder: `AzerothCore PlayerBot RT 文档`
- Folder URL: `https://docs.qq.com/desktop/mydoc/folder/dCqgFyBeqUwT`
- Cloud architecture smart doc: `https://docs.qq.com/aio/DZHBDcVVDWE5Ma1F3`
- Runtime README: `https://docs.qq.com/doc/DZHZaVFRnaFptZVdy`
- RT runbook: `https://docs.qq.com/doc/DZE9mZmVETnVMdktD`
- Agent README: `https://docs.qq.com/markdown/DZERueEhFekd1aFR2`
- Tencent Docs sync runbook: `https://docs.qq.com/doc/DZG9wYWhpTmRLQXVJ`

For local Markdown with relative images, upload images with `tencent-docs.upload_image` first and replace local paths with the returned `image_id`; raw Markdown imports will not reliably package relative repo images.

## Skill Install

The repo copy is the source of truth for this skill. After editing it, refresh the installed Codex skill so new sessions load the same behavior:

```bash
CODEX_HOME_DIR="${CODEX_HOME:-/mnt/c/Users/chuan/.codex}"
mkdir -p "$CODEX_HOME_DIR/skills"
rsync -a --delete ops/codex-skills/azerothcore-playerbot-ops/ "$CODEX_HOME_DIR/skills/azerothcore-playerbot-ops/"
```

## Commit And Push

When the user asks to commit this stack:

1. Inspect status and diffs; never revert unrelated user changes.
2. Run relevant tests. For Python sidecar changes, run local syntax checks and RT venv unit tests after deploy. For C++ changes, compile/deploy when build artifacts exist; otherwise state the blocker.
3. Run `git diff --check`.
4. Use a Chinese commit message.
5. In detached HEAD on this repo, push explicitly to `foxden-app`:

```bash
git add <files>
git commit -m "中文提交信息"
git push foxden-app HEAD:playerbot-agent
```

Do not commit secrets, runtime env files, logs, DB dumps, `ops/rt-wow-migration/libs/`, build directories, backup binaries, or Telegram inbox files.

## Reference

For exact paths and realm values, read `references/paths.md` only when needed.
