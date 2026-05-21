---
name: azerothcore-playerbot-ops
description: Operate and maintain the local AzerothCore WotLK playerbot test server. Use when starting, stopping, restarting, health-checking, reading logs, fixing port conflicts, updating systemd units, compiling/deploying worldserver, validating playerbot configuration, updating playerbot/Hermes architecture runbooks, making Chinese commits for this stack, or deciding whether old AzerothCore directories can be removed.
---

# AzerothCore PlayerBot Ops

## Quick Start

Use systemd as the default supervisor. This server should not normally run under tmux.

For running GM commands, prefer the dedicated `azerothcore-gm-commands` skill.

Run the helper script for routine work:

```bash
/home/wuya/.codex/skills/azerothcore-playerbot-ops/scripts/acore-playerbot-ops.sh status
/home/wuya/.codex/skills/azerothcore-playerbot-ops/scripts/acore-playerbot-ops.sh start
/home/wuya/.codex/skills/azerothcore-playerbot-ops/scripts/acore-playerbot-ops.sh start-stack
/home/wuya/.codex/skills/azerothcore-playerbot-ops/scripts/acore-playerbot-ops.sh restart
/home/wuya/.codex/skills/azerothcore-playerbot-ops/scripts/acore-playerbot-ops.sh stop
/home/wuya/.codex/skills/azerothcore-playerbot-ops/scripts/acore-playerbot-ops.sh build-deploy-world 4
/home/wuya/.codex/skills/azerothcore-playerbot-ops/scripts/acore-playerbot-ops.sh gm 'server info'
```

Core services:

- `azerothcore-auth.service`: authserver, port `3724`
- `azerothcore-world.service`: worldserver, port `8085`, SOAP `7879`
- `azerothcore-playerbot-mcp.service`: local MCP server, port `18765`
- `azerothcore-playerbot-hermes-relay.service`: game event relay to Hermes
- `azerothcore-account-register.service`: lightweight account registration page/API
- `frpc.service`: public tunnel, usually keep running unless the user asks to stop external access

Canonical repo and runtime path:

- Repo: `/home/wuya/git/azerothcore-wotlk-git`
- Build dir: `/home/wuya/git/azerothcore-wotlk-git/var/build-agent-release`
- Binaries: `/home/wuya/git/azerothcore-wotlk-git/env/dist/bin`
- Configs: `/home/wuya/git/azerothcore-wotlk-git/env/dist/etc`
- Unit templates: `/home/wuya/git/azerothcore-wotlk-git/ops/systemd`

## Operating Rules

Prefer these sequences:

- Start: start `frpc`, then `azerothcore-auth`, then `azerothcore-world`.
- Full stack start: use `acore-playerbot-ops.sh start-stack`.
- Stop: stop `azerothcore-world` first, then `azerothcore-auth`; leave `frpc` running unless external access should be closed.
- Full stack stop for long compiles: use `acore-playerbot-ops.sh stop-stack`; it stops world/auth plus MCP, Hermes relay, and account registration.
- Restart: stop world, restart auth, start world, then verify ports and logs.
- Health check: use `acore-playerbot-ops.sh check-all`; confirm services are active, ports `3724/8085/18765` listen, MCP `/health` is OK, and world log has `worldserver-daemon) ready`.

Do not resurrect `/home/wuya/git/azerothcore-wotlk`; that was the old non-playerbot runtime. The old directory may be quarantined as `/home/wuya/git/azerothcore-wotlk.disabled-20260516`.

Use tmux only for an exceptional interactive-console session. systemd is the production-like default. For GM/world commands under systemd, prefer SOAP if credentials are available; otherwise use DB-safe offline changes or temporarily stop systemd and run a controlled foreground/tmux session.

SOAP GM commands use a GM level 3 account. Known admin-capable accounts in this environment include `WUYA_TEST`, `GM`, and `SOAP_PANEL`. Do not store passwords in the skill; pass them with `ACORE_GM_PASS` or enter them interactively.

## Build And Deploy

Use the helper script for `worldserver` build/deploy. It validates the CMake install prefix before compiling or installing; do not manually deploy a binary from a build dir with the wrong prefix.

Expected cache line:

```text
CMAKE_INSTALL_PREFIX:PATH=/home/wuya/git/azerothcore-wotlk-git/env/dist
```

Normal incremental compile:

```bash
/home/wuya/.codex/skills/azerothcore-playerbot-ops/scripts/acore-playerbot-ops.sh build-world 4
```

Compile and deploy with stack shutdown for CPU/memory headroom:

```bash
/home/wuya/.codex/skills/azerothcore-playerbot-ops/scripts/acore-playerbot-ops.sh build-deploy-world 4
```

For long compiles, do not stream every progress line to the user. Give one estimate, then check every 5-10 minutes or at phase changes/errors. Keep command output caps small and summarize warnings/errors instead of pasting full build logs.

Deploy an already-built binary:

```bash
/home/wuya/.codex/skills/azerothcore-playerbot-ops/scripts/acore-playerbot-ops.sh deploy-world
```

The script backs up `env/dist/bin/worldserver`, installs `var/build-agent-release/src/server/apps/worldserver`, restarts services, waits for readiness, and fails if startup logs mention `env/agent-release`, `Failed open`, or `Database Playerbots not specified`.

Only rerun CMake configuration deliberately:

```bash
cmake -S /home/wuya/git/azerothcore-wotlk-git -B /home/wuya/git/azerothcore-wotlk-git/var/build-agent-release -DCMAKE_INSTALL_PREFIX=/home/wuya/git/azerothcore-wotlk-git/env/dist
```

## Common Checks

Status and ports:

```bash
/home/wuya/.codex/skills/azerothcore-playerbot-ops/scripts/acore-playerbot-ops.sh check-all
ss -ltnp | rg ':3724|:8085|:18765'
pgrep -af 'authserver|worldserver|playerbot-mcp|hermes_relay|account-register'
```

Logs:

```bash
journalctl -u azerothcore-world.service -n 160 --no-pager
journalctl -u azerothcore-auth.service -n 80 --no-pager
journalctl -u azerothcore-playerbot-mcp.service -u azerothcore-playerbot-hermes-relay.service -n 160 --no-pager
/home/wuya/.codex/skills/azerothcore-playerbot-ops/scripts/acore-playerbot-ops.sh relay-summary 40
tail -f /home/wuya/git/azerothcore-wotlk-git/env/dist/logs/playerbot-mcp.log
```

Log hygiene:

- Do not dump full `playerbot-hermes-relay.log` unless explicitly needed; each JSON line can contain large context, raw model responses, and tool outputs. Prefer `relay-summary`.
- Do not print full env files, Hermes config, or remote config sections that may include `Authorization`, `API_KEY`, database passwords, or bearer tokens. Use targeted `rg` patterns that show keys/tool names without secret values.
- When investigating "no response", first use `relay-summary`, `playerbot-mcp.log`, `journalctl -u azerothcore-playerbot-hermes-relay.service`, and MCP `/health`.
- If a raw log excerpt is necessary, redact secrets and keep only the smallest relevant event.

Run a GM command through SOAP:

```bash
ACORE_GM_USER=SOAP_PANEL ACORE_GM_PASS='...' /home/wuya/.codex/skills/azerothcore-playerbot-ops/scripts/acore-playerbot-ops.sh gm 'server info'
/home/wuya/.codex/skills/azerothcore-playerbot-ops/scripts/acore-playerbot-ops.sh gm 'teleport name Wuya DeadminesShip'
```

Expected playerbot startup markers:

- `Playerbot Agent bridge disabled`
- `MaxRandomBots set to 0`
- `Account type assignment complete: 0 RNDbot accounts, 10 AddClass accounts, 45 unassigned`
- `AzerothCore rev. ... (playerbot-agent branch) ... ready`

## Architecture Truth Source

When changing Playerbot Agent, MCP/Hermes skills, sidecar services, GM/audit rules, account registration, or operational workflow, update the truth-source docs in the same change:

- `ARCHITECTURE-agent-playerbots.md`: architecture truth source.
- `ROADMAP-agent-playerbots.md`: roadmap, status, and next implementation slices.
- `README-playerbot-agent-runtime.md`: runtime/runbook behavior.
- `ops/hermes-wow/config.yaml.template`: Hermes MCP tool allowlist and model/runtime config template.
- `ops/systemd/*.service` and `ops/systemd/*.env.dist`: service/env source templates.
- `doc/agent-playerbot-architecture.dot`, `.svg`, `.png`: architecture diagram when topology changes.

For architecture docs, write concise Chinese prose unless the surrounding document is already English-only. Keep diagrams and docs consistent with actual ports, service names, env files, and runtime paths.

## Commit Rules

When asked to commit this stack:

- Use a Chinese commit message.
- Inspect `git status --short` first and avoid reverting unrelated user changes.
- Include source, tests, systemd/env templates, Hermes templates, and architecture docs that belong to the same behavioral change.
- Do not commit runtime secrets, API keys, database passwords, generated logs, or local backup binaries.
- Run at least the relevant Python tests and `git diff --check`; for C++/runtime changes, use the build/deploy script or explicitly state why it was not run.

## Maintenance

Install or refresh systemd unit files from the repo templates:

```bash
sudo install -m 0644 /home/wuya/git/azerothcore-wotlk-git/ops/systemd/azerothcore-auth.service /etc/systemd/system/azerothcore-auth.service
sudo install -m 0644 /home/wuya/git/azerothcore-wotlk-git/ops/systemd/azerothcore-world.service /etc/systemd/system/azerothcore-world.service
sudo systemctl daemon-reload
sudo systemctl enable azerothcore-auth.service azerothcore-world.service frpc.service
```

Before deleting any old runtime directory:

1. Verify no live process references it.
2. Verify no systemd unit or current config references it.
3. Restart the systemd services and confirm the server is ready.
4. Prefer quarantining first by renaming the directory. Delete only after the service survives a restart and login test.

Useful old-reference check:

```bash
rg -n '/home/wuya/git/azerothcore-wotlk(/|"| |$)' /etc/systemd/system /home/wuya/git/azerothcore-wotlk-git/env/dist/etc /home/wuya/.config/systemd/user 2>/dev/null || true
pgrep -af 'azerothcore-wotlk/|authserver|worldserver|frpc'
```
