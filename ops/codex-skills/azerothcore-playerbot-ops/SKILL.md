---
name: azerothcore-playerbot-ops
description: Operate, debug, build, deploy, document, commit, and push the AzerothCore WotLK PlayerBot/Hermes stack. Use when working on GJZN production, RT legacy rollback, local dev builds, MCP/Hermes relay sidecars, 瓦小狸/playerbot behavior, new-player starter gifts, heirloom/GM mail grants, realmlist lines, systemd/container health, architecture truth docs, Chinese commit messages, or routine compile/deploy/restart troubleshooting.
---

# AzerothCore PlayerBot Ops

## Current Truth

GJZN is production. The development machine is for editing, testing, building, and publishing. Do not restart or resurrect T490/local worldserver as production unless the user explicitly asks. Do not deploy to RT as production unless the user explicitly asks for rollback.

Production shape:

- GJZN LAN SSH: `ssh GJZN` / `wuya@192.168.1.203`.
- GJZN public SSH: `ssh GJZN-public` / `wuya@38.207.189.99 -p 8026`.
- GJZN runtime root: `/home/wuya/git/azerothcore-wotlk-git`.
- GJZN runs `azerothcore-auth.service` and `azerothcore-world.service` natively under systemd.
- GJZN runs `hermes-wow` as a Docker container from `/home/wuya/srv/hermes-wow/docker-compose.yml`.
- GJZN runs MCP, Hermes relay, and account register as systemd services.
- GJZN runs public game FRP with `frpc-acore-main.service` and `frpc-chml-unicom.service`.
- GJZN uses wired `eno1` for production. WiFi autoconnect is disabled.
- RT is the default foreign-network proxy for GJZN tools: HTTP `192.168.1.179:20171`, SOCKS `192.168.1.179:20170`.
- GJZN foxclaw/Codex uses `/home/wuya/.foxclaw/.env` plus `/home/wuya/.config/systemd/user/foxclaw.service.d/10-rt-proxychains.conf` to route Telegram and Codex traffic through RT. GJZN local `xray.service` remains only as fallback on `127.0.0.1:20170/20171`.
- AzerothCore auth/world and game FRP services do not use proxy env vars. Do not import proxy variables into systemd global environment.
- Public realms: `线路一 -> 38.207.189.99:8085`, `线路二 -> 8.162.5.68:8085`.
- Auth alias: `RealmList.RealmIDAliases = "2:1"`; both realms point to the same worldserver.
- Production DBs on GJZN MySQL: `acore_auth`, `acore_playerbot_world`, `acore_playerbot_characters`, `acore_playerbots`.

RT is now legacy/rollback for WoW:

- RT SSH still works through `ssh RT` / `wuya@38.207.189.99 -p 8022`.
- RT auth/world containers, MCP, relay, register, and `frpc-chml-unicom.service` are stopped after the 2026-05-23 GJZN cutover.
- RT main `frpc.service` remains active for non-game proxies; its game proxies for 3724/8085 were removed.
- Final RT cutover backup: `/home/wuya/backups/acore/acore-gjzn-cutover-20260523-230612.sql.gz`.
- RT runbook is now historical unless explicitly rolling back.

Primary production checks:

```bash
ssh GJZN 'systemctl is-active azerothcore-auth.service azerothcore-world.service azerothcore-playerbot-mcp.service azerothcore-playerbot-hermes-relay.service azerothcore-account-register.service frpc-acore-main.service frpc-chml-unicom.service mysql docker xray'
ssh GJZN 'ss -ltnp | egrep ":(3724|8085|7879|8642|18765|18080)\b" || true'
ssh GJZN 'docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"'
```

`ops/rt-wow-migration/deploy.sh` still targets RT by default. Do not use it for GJZN production until it is updated for the native GJZN topology.

## First Moves

Before changing anything:

```bash
pwd
git status --short --branch
ssh GJZN 'hostname; systemctl is-active azerothcore-world.service azerothcore-playerbot-hermes-relay.service frpc-acore-main.service frpc-chml-unicom.service'
```

If investigating a player report like “瓦小狸不说话”:

1. Check GJZN services and ports with the production checks above.
2. Check relay/MCP health and recent event/action rows; do not dump full raw relay JSON unless necessary.
3. Confirm the reporter is online before expecting replies or bot actions; old offline events often fail with `requester is not online`.
4. If stale events accumulated while relay was broken, mark only those stale rows processed and advance relay state to the latest event, then restart the relay.
5. If relay reaches Hermes but Hermes returns `HTTP 402: Insufficient Balance`, the game/MCP path is up and the model provider balance/key must be fixed.
6. Keep production resource use low: `PLAYERBOT_HERMES_STORE=0`, `PLAYERBOT_HERMES_TRACE_RAW=0`, and a current `PLAYERBOT_HERMES_CONVERSATION_EPOCH` should be set on GJZN. Pure greetings/thanks, consumables, offline-party wakeups, and in-game context reset commands use relay fast-paths and should not call Hermes.
7. Players can tell 瓦小狸 “新建会话 / 重置上下文 / 清空上下文 / 压缩上下文” from WoW. Relay should rotate the current conversation epoch directly; “压缩上下文” currently means “start a new Hermes conversation” until Hermes has a reliable summary-writeback API.
8. 瓦小狸入口 is whitelist-gated in relay. Production should keep `PLAYERBOT_HERMES_ALLOW_ALL_PLAYERS=0`; add trusted characters through `PLAYERBOT_HERMES_ALLOWED_PLAYER_NAMES`, `PLAYERBOT_HERMES_ALLOWED_PLAYER_GUIDS`, or `PLAYERBOT_HERMES_ALLOWED_ACCOUNTS`. Unauthorized events should log `relay_event_unauthorized` and must not call Hermes or enqueue actions.
9. Direct conversations with 瓦小狸 are speaker-GUID scoped for whisper and addressed say/yell; party/raid remains group-scoped by design.

Talent and GM-operation truth:

- `wow_set_bot_role` and `wow_set_bot_strategy` change AI strategies only; they do not reset real talents or create a second talent group.
- For “second spec / dual spec / reset talents / switch talent page” reports, check `wow_get_bot_profile` or the `characters.activeTalentGroup` and `characters.talentGroupsCount` fields first.
- If the talent group fact is known and `talentGroupsCount < 2`, reply that the target has no second talent group and include the level/config reason when known. RT currently uses `MinDualSpecLevel = 40`, so level-30 bots normally have one group. If the count is unknown, say the talent-page data is unavailable instead of guessing.
- GM/high-privilege operations are allowed only through MCP tools with explicit audit/admin checks. Wuya has GM authority; do not execute arbitrary terminal, SQL, or raw GM commands on behalf of Hermes.

Useful targeted query:

```bash
ssh GJZN 'MYSQL_PWD=acore mysql -uacore -h127.0.0.1 -e "
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

## New Player Starter Gifts

Use worldserver SOAP GM commands for starter gifts, especially when the player is online. Do not write `characters`, `mail`, `mail_items`, or `item_instance` directly unless SOAP is unavailable and the player is offline or has logged out.

Before granting anything, query the target character and confirm class, faction, level, current money, and online state. Replace `火球树` with the actual character name:

```bash
ssh GJZN 'MYSQL_PWD=acore mysql -uacore -h127.0.0.1 -N -B -e "
  SELECT c.guid,c.name,c.race,c.class,c.level,c.money,c.online,c.account,a.username
  FROM acore_playerbot_characters.characters c
  LEFT JOIN acore_auth.account a ON a.id=c.account
  WHERE c.name=\"火球树\";
"'
```

Known starter gift baseline:

- 4x `23162` 弗洛尔的无尽抗性宝箱. This container is non-stackable, so `send items ... 23162:4` becomes four mail attachments.
- 100 gold startup funds = `1000000` copper.
- For a level-1 alliance mage/caster, use `42947` 高贵的院长之杖, `42985` 褴褛的鬼雾衬肩, `48691` 破烂的鬼雾长袍, `42992` 敏锐的比斯巨兽之眼, and `44098` 家传的联盟徽记. For horde, use `44097` 家传的部落徽记 instead of `44098`.
- For other classes, first search `item_template` where `Quality=7` and choose class-appropriate heirlooms by armor/weapon role. Do not guess from the player name.

Grant the starter gift through the existing SOAP helper on GJZN. Keep the GM password in `/home/wuya/.config/acore/gm.env` and do not print it:

```bash
ssh GJZN 'set -a; . /home/wuya/.config/acore/gm.env; set +a; /home/wuya/git/azerothcore-wotlk-git/ops/codex-skills/azerothcore-playerbot-ops/scripts/acore-playerbot-ops.sh gm '\''send items 火球树 "新手礼包：法师传家宝" "欢迎来到艾泽拉斯。这封邮件包含法师传家宝、联盟徽记和4个弗洛尔的无尽抗性宝箱。" 42947 42985 48691 42992 44098 23162:4'\'''
ssh GJZN 'set -a; . /home/wuya/.config/acore/gm.env; set +a; /home/wuya/git/azerothcore-wotlk-git/ops/codex-skills/azerothcore-playerbot-ops/scripts/acore-playerbot-ops.sh gm '\''send money 火球树 "新手礼包：启动资金" "这是给新角色的100金启动资金。" 1000000'\'''
```

Verify with both the GM mail view and a narrow SQL check:

```bash
ssh GJZN 'set -a; . /home/wuya/.config/acore/gm.env; set +a; /home/wuya/git/azerothcore-wotlk-git/ops/codex-skills/azerothcore-playerbot-ops/scripts/acore-playerbot-ops.sh gm '\''mail list 火球树'\'''
ssh GJZN 'MYSQL_PWD=acore mysql -uacore -h127.0.0.1 -t -e "
  SELECT m.id,m.subject,m.has_items,m.money,FROM_UNIXTIME(m.deliver_time) AS deliver_at
  FROM acore_playerbot_characters.mail m
  JOIN acore_playerbot_characters.characters c ON c.guid=m.receiver
  WHERE c.name=\"火球树\"
  ORDER BY m.id DESC LIMIT 6;
  SELECT mi.mail_id,ii.itemEntry,COALESCE(NULLIF(l.Name,\"\"),t.name) AS item_name,ii.count,mi.item_guid
  FROM acore_playerbot_characters.mail_items mi
  JOIN acore_playerbot_characters.characters c ON c.guid=mi.receiver
  JOIN acore_playerbot_characters.item_instance ii ON ii.guid=mi.item_guid
  JOIN acore_playerbot_world.item_template t ON t.entry=ii.itemEntry
  LEFT JOIN acore_playerbot_world.item_template_locale l ON l.ID=t.entry AND l.locale=\"zhCN\"
  WHERE c.name=\"火球树\"
  ORDER BY mi.mail_id DESC,ii.itemEntry,mi.item_guid
  LIMIT 20;
"'
```

## Deploy Rules

Sidecar-only changes include `tools/playerbot-mcp/`, `tools/account-register/`, GJZN systemd unit changes, relay logic, MCP tools, and registration page code.

```bash
python3 -m py_compile tools/playerbot-mcp/wow_common.py tools/playerbot-mcp/hermes_relay.py tools/playerbot-mcp/server.py
rsync -az tools/playerbot-mcp/ GJZN:/home/wuya/git/azerothcore-wotlk-git/tools/playerbot-mcp/
rsync -az tools/account-register/ GJZN:/home/wuya/git/azerothcore-wotlk-git/tools/account-register/
ssh GJZN 'sudo systemctl restart azerothcore-playerbot-mcp.service azerothcore-playerbot-hermes-relay.service azerothcore-account-register.service'
ssh GJZN '/home/wuya/git/azerothcore-wotlk-git/var/playerbot-mcp-venv/bin/python -m unittest /home/wuya/git/azerothcore-wotlk-git/tools/playerbot-mcp/test_playerbot_mcp.py'
```

C++/world changes include `modules/mod-playerbot-agent/`, core code, `modules/mod-playerbots/`, SQL source, or runtime binary changes. These require a local build and `env/dist/bin/authserver` plus `env/dist/bin/worldserver`.

`modules/mod-playerbots/` is an ignored external module checkout. If it is absent on a new dev machine, hydrate it before configuring CMake. Prefer the GJZN production working tree when trying to reproduce production exactly:

```bash
rsync -az --delete --exclude='.git/' \
  GJZN:/home/wuya/git/azerothcore-wotlk-git/modules/mod-playerbots/ \
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
echo "GJZN deploy-world path is native/systemd now; update the runbook before using old RT deploy-world."
```

The old `deploy-world` backs up RT DBs and deploys to RT containers. It is not the GJZN production deploy path after 2026-05-23.

## Service And Log Hygiene

GJZN service checks:

```bash
ssh GJZN 'systemctl is-active azerothcore-auth.service azerothcore-world.service azerothcore-playerbot-mcp.service azerothcore-playerbot-hermes-relay.service azerothcore-account-register.service'
ssh GJZN 'ss -ltnp | egrep ":(3724|8085|7879|8642|18765|18080)\b" || true'
ssh GJZN 'docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"'
```

Prefer compact logs:

```bash
ssh GJZN 'journalctl -u azerothcore-world.service -u azerothcore-playerbot-mcp.service -u azerothcore-playerbot-hermes-relay.service -n 160 --no-pager'
ssh GJZN 'tail -120 /home/wuya/git/azerothcore-wotlk-git/env/dist/logs/playerbot-mcp.log'
ssh GJZN 'docker logs --tail 160 hermes-wow'
```

Do not print full env files, Hermes config, bearer tokens, API keys, DB passwords, SOAP passwords, or full raw relay JSON. Redact secrets and use narrow `rg`/SQL selections.

## Architecture Docs

When changing PlayerBot behavior, MCP/Hermes sidecars, RT deploy flow, service topology, DB tables, realmlist, or operational rules, update truth-source docs in the same change:

- `ARCHITECTURE-agent-playerbots.md`
- `README-playerbot-agent-runtime.md`
- `ops/rt-wow-migration/README.md`
- `ops/gjzn-bootstrap/README.md`
- `ops/codex-skills/azerothcore-playerbot-ops/`
- `doc/agent-playerbot-architecture.dot` and rendered `.svg/.png` if topology changes
- `ROADMAP-agent-playerbots.md` only when the product/implementation roadmap changes

Keep docs concise and factual. Use Chinese prose unless the surrounding file is English-only. Keep runtime paths, ports, service names, and DB names aligned with RT truth.

Cloud docs are part of the operating surface because teammates read there. When updating architecture/runbook/README docs, also update the Tencent Docs copies in the same turn.

- Source-of-truth manifest and helper: `ops/tencent-docs/cloud-docs.json`, `ops/tencent-docs/sync-docs.sh`
- Folder: `AzerothCore PlayerBot RT 文档`
- Folder URL: `https://docs.qq.com/desktop/mydoc/folder/dCqgFyBeqUwT`
- Cloud architecture doc: `https://docs.qq.com/doc/DZFVEZWVnU0hyb1ZS`
- Runtime README: `https://docs.qq.com/doc/DZHBQSVZ2enp0TFFj`
- RT runbook: `https://docs.qq.com/doc/DZEpTcUdvWWtiVWtE`
- GJZN runbook: `https://docs.qq.com/doc/DZFFKWFpMa2pVeHpT`
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

Default to committing and pushing repo-tracked work after completing this stack, unless the user explicitly says not to commit, asks to pause, or the change is only a live machine/runtime-only operation with no repo files modified. This includes docs, Tencent Docs manifest updates, runbooks, skill updates, sidecar code, tests, deployment scripts, and architecture truth changes.

When committing this stack:

1. Inspect status and diffs; never revert unrelated user changes.
2. Run relevant tests. For Python sidecar changes, run local syntax checks and RT venv unit tests after deploy. For C++ changes, compile/deploy when build artifacts exist; otherwise state the blocker.
3. Run `git diff --check`.
4. Use a Chinese commit message.
5. Stage only intentional files with explicit paths; do not use broad staging if unrelated changes exist.
6. In detached HEAD on this repo, push explicitly to `foxden-app`:

```bash
git add <files>
git commit -m "中文提交信息"
git push foxden-app HEAD:playerbot-agent
```

Do not commit secrets, runtime env files, logs, DB dumps, `ops/rt-wow-migration/libs/`, build directories, backup binaries, or Telegram inbox files.

## Reference

For exact paths and realm values, read `references/paths.md` only when needed.
