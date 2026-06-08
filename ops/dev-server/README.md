# Agile Dev Server

This checkout owns the agile test world. Production stays in
`/home/wuya/git/azerothcore-wotlk-git`; only the authserver is shared so GM
accounts can enter the test line from the normal public realmlist.

- Runtime: `/home/wuya/git/azerothcore-wotlk-dev/env/dist`
- Shared auth: `38.207.189.99:3724`
- Agile test realm: `id=3`, `敏捷测试`, GM locked with `allowedSecurityLevel=3`
- World: `127.0.0.1:8086`, exposed by FRP as `38.207.189.99:8086`
- SOAP: `127.0.0.1:7880`
- Databases: production `acore_auth`, plus isolated `acore_dev_world`,
  `acore_dev_characters`, `acore_dev_playerbots`
- Dev Agent: MCP `127.0.0.1:18766`, Hermes `127.0.0.1:8643`,
  dashboard `127.0.0.1:9120`, relay state under this checkout's `var/`

The dev world service also sets `AC_PLAYERBOTS_DATABASE_INFO` to
`acore_dev_playerbots`. Keep this service-level override in place: it is the
guard that prevents the dev world from polling the production
`agent_playerbot_actions` queue.

Useful commands:

```bash
sudo systemctl start azerothcore-dev-world.service frpc-acore-dev.service
sudo systemctl start azerothcore-dev-playerbot-mcp.service azerothcore-dev-hermes-wow.service azerothcore-dev-playerbot-hermes-relay.service
sudo systemctl stop azerothcore-dev-playerbot-hermes-relay.service azerothcore-dev-hermes-wow.service azerothcore-dev-playerbot-mcp.service
sudo systemctl stop frpc-acore-dev.service azerothcore-dev-world.service
sudo systemctl status azerothcore-dev-world.service azerothcore-dev-playerbot-mcp.service azerothcore-dev-hermes-wow.service azerothcore-dev-playerbot-hermes-relay.service frpc-acore-dev.service
tail -f env/dist/logs/Server.log env/dist/logs/playerbot-mcp-dev.log env/dist/logs/playerbot-hermes-relay-dev.log
```

Refresh dev databases from current production. This does not clone or drop
`acore_auth`; it only ensures the GM-locked `敏捷测试` realm row exists:

```bash
ops/dev-server/refresh-dev-db.sh --yes
```

Build and install dev binaries into this checkout only:

```bash
ops/dev-server/build-dev-runtime.sh 4
sudo systemctl restart azerothcore-dev-world.service
```

Client realmlist for GM testing:

```text
set realmlist 38.207.189.99:3724
```

`azerothcore-dev-auth.service` remains available only as a local fallback for
experiments that must not touch production auth at all.

The service units are intentionally not enabled at boot.
