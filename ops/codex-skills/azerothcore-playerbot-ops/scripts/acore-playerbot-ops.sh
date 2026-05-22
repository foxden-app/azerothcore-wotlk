#!/usr/bin/env bash
set -euo pipefail

AUTH_SERVICE=azerothcore-auth.service
WORLD_SERVICE=azerothcore-world.service
MCP_SERVICE=azerothcore-playerbot-mcp.service
HERMES_RELAY_SERVICE=azerothcore-playerbot-hermes-relay.service
ACCOUNT_REGISTER_SERVICE=azerothcore-account-register.service
FRPC_SERVICE=frpc.service
if git_root=$(git rev-parse --show-toplevel 2>/dev/null); then
  DEFAULT_REPO=$git_root
elif [[ -d /home/wuya/git_wsl/azerothcore-wotlk ]]; then
  DEFAULT_REPO=/home/wuya/git_wsl/azerothcore-wotlk
else
  DEFAULT_REPO=/home/wuya/git/azerothcore-wotlk-git
fi
REPO=${ACORE_REPO:-$DEFAULT_REPO}
BUILD_DIR=$REPO/var/build-agent-release
EXPECTED_PREFIX=$REPO/env/dist
BUILT_WORLDSERVER=$BUILD_DIR/src/server/apps/worldserver
RUNTIME_WORLDSERVER=$REPO/env/dist/bin/worldserver
OLD_PATH=/home/wuya/git/azerothcore-wotlk
QUARANTINE=/home/wuya/git/azerothcore-wotlk.disabled-20260516
SOAP_URL=${ACORE_SOAP_URL:-http://127.0.0.1:7879/}

usage() {
  cat <<'USAGE'
Usage: acore-playerbot-ops.sh <command> [args]

Commands:
  start              Start frpc, auth, then world.
  start-stack        Start auth/world plus MCP, Hermes relay, and account register.
  stop               Stop world, then auth. Leaves frpc running.
  stop-stack         Stop world/auth plus MCP, Hermes relay, and account register.
  stop-all           Stop stack plus frpc.
  restart            Restart auth/world using the safe order.
  restart-sidecars   Restart MCP, Hermes relay, and account register.
  status             Show service status, ports, and processes.
  rt-status          Show RT production containers, sidecars, ports, and realmlist.
  rt-deploy-sidecar  Sync Python sidecars to RT and restart RT sidecar services.
  rt-deploy-world    Backup RT DB, sync auth/world runtime, rebuild image, restart auth/world.
  rt-backup-db       Create a timestamped RT production DB backup.
  check              Compact health check.
  check-all          Compact health check for auth/world/MCP/relay/register.
  ready              Search current worldserver startup markers in journal.
  logs [n]           Show combined auth/world/frpc logs, default 160 lines.
  stack-logs [n]     Show auth/world/MCP/relay/register logs, default 160 lines.
  world-log [n]      Show worldserver logs, default 160 lines.
  relay-summary [n]  Summarize recent Hermes relay JSON events, default 40.
  build-world [jobs] Validate install prefix and build worldserver, default -j4.
  deploy-world       Backup/install built worldserver, restart world, verify ready.
  build-deploy-world [jobs]
                     Stop stack, build, deploy, start stack, verify, default -j4.
  gm <command>       Run a GM command through SOAP.
  install-units      Install repo systemd unit templates and reload systemd.
  old-refs           Check references to the old non-git runtime path.
  quarantine-old     Rename the old runtime path if it still exists.
USAGE
}

rt_deploy() {
  local command=$1
  local script="$REPO/ops/rt-wow-migration/deploy.sh"
  if [[ ! -x "$script" ]]; then
    echo "Missing executable RT deploy script: $script" >&2
    exit 1
  fi
  (cd "$REPO" && "$script" "$command")
}

ports() {
  ss -ltnp | rg ':3724|:8085|:7879|:18765' || true
}

processes() {
  pgrep -af '[a]uthserver|[w]orldserver|[f]rpc|tools/playerbot-mcp/[s]erver.py|tools/playerbot-mcp/[h]ermes_relay.py|tools/account-register/[s]erver.py' || true
}

ready_line() {
  {
    journalctl -u "$WORLD_SERVICE" -n 500 --no-pager
    tail -500 "$REPO/env/dist/logs/Server.log" 2>/dev/null || true
  } | rg 'WORLD: World Initialized|worldserver-daemon.*ready|Playerbot Agent bridge|MaxRandomBots|Account type assignment' \
    | tail -20 || true
}

xml_escape() {
  local value=${1-}
  value=${value//&/&amp;}
  value=${value//</&lt;}
  value=${value//>/&gt;}
  printf '%s' "$value"
}

validate_build_prefix() {
  local cache=$BUILD_DIR/CMakeCache.txt
  local expected="CMAKE_INSTALL_PREFIX:PATH=$EXPECTED_PREFIX"
  if [[ ! -f "$cache" ]]; then
    echo "Missing CMake cache: $cache" >&2
    exit 1
  fi
  if ! grep -Fxq "$expected" "$cache"; then
    echo "Refusing to build/deploy: CMAKE_INSTALL_PREFIX is not $EXPECTED_PREFIX" >&2
    echo "Expected exact line: $expected" >&2
    echo "Current value:" >&2
    grep '^CMAKE_INSTALL_PREFIX:PATH=' "$cache" >&2 || true
    echo "Fix deliberately with: cmake -S $REPO -B $BUILD_DIR -DCMAKE_INSTALL_PREFIX=$EXPECTED_PREFIX" >&2
    exit 1
  fi
}

build_world() {
  local jobs=${1:-4}
  validate_build_prefix
  (cd "$REPO" && cmake --build "$BUILD_DIR" --target worldserver -j"$jobs")
}

install_built_world() {
  validate_build_prefix
  if [[ ! -x "$BUILT_WORLDSERVER" ]]; then
    echo "Missing built worldserver: $BUILT_WORLDSERVER" >&2
    exit 1
  fi
  local ts backup
  ts=$(date +%Y%m%d-%H%M%S)
  backup="$RUNTIME_WORLDSERVER.bak-$ts"
  cp -a "$RUNTIME_WORLDSERVER" "$backup"
  install -m 0755 "$BUILT_WORLDSERVER" "$RUNTIME_WORLDSERVER"
  echo "Installed $BUILT_WORLDSERVER -> $RUNTIME_WORLDSERVER"
  echo "Backup: $backup"
}

wait_world_ready() {
  local i
  for i in $(seq 1 60); do
    if ss -ltnp | rg -q ':8085\b' \
      && tail -300 "$REPO/env/dist/logs/Server.log" 2>/dev/null | rg -q 'worldserver-daemon\) ready|WORLD: World Initialized'; then
      return 0
    fi
    sleep 2
  done
  echo "worldserver did not become ready within 120s" >&2
  journalctl -u "$WORLD_SERVICE" -n 160 --no-pager >&2 || true
  return 1
}

check_prefix_runtime_errors() {
  {
    journalctl -u "$WORLD_SERVICE" -n 500 --no-pager
    tail -300 "$REPO/env/dist/logs/Server.log" 2>/dev/null || true
  } | if rg -q 'env/agent-release|Failed open|Database Playerbots not specified'; then
    echo "Detected runtime config path/database error after start" >&2
    return 1
  fi
}

relay_summary() {
  local lines=${1:-40}
  python3 - "$REPO/env/dist/logs/playerbot-hermes-relay.log" "$lines" <<'PY'
import json
import sys
from collections import deque
from pathlib import Path

path = Path(sys.argv[1])
limit = int(sys.argv[2])
rows = deque(maxlen=limit)

if path.exists():
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

def brief(value, width=150):
    if value is None:
        return ""
    text = str(value).replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "..."

for obj in rows:
    event = obj.get("event_zh") or obj.get("event") or ""
    event_id = obj.get("event_id") or ""
    conversation = obj.get("conversation") or ""
    channel = obj.get("channel") or ""
    speaker = obj.get("speaker") or obj.get("speaker_name") or ""
    status = obj.get("status") or obj.get("response_status") or ""
    latency = obj.get("latency_ms")
    message = obj.get("message") or obj.get("output_text") or obj.get("reason") or ""
    extras = []
    if status:
        extras.append(f"status={status}")
    if latency is not None:
        extras.append(f"{latency}ms")
    prefix = " ".join(
        part
        for part in [
            obj.get("ts", ""),
            str(event),
            f"id={event_id}" if event_id else "",
            str(conversation),
            str(channel),
            str(speaker),
        ]
        if part
    )
    suffix = " ".join(extras)
    print(f"{prefix} {suffix} :: {brief(message)}".strip())
PY
}

gm_command() {
  if [[ $# -lt 1 ]]; then
    echo "Usage: $0 gm <command>" >&2
    exit 2
  fi

  local user=${ACORE_GM_USER:-SOAP_PANEL}
  local pass=${ACORE_GM_PASS:-}
  if [[ -z "$pass" ]]; then
    read -rsp "Password for ${user}: " pass
    echo
  fi

  local command escaped
  command="$*"
  escaped=$(xml_escape "$command")

  curl -sS --fail-with-body \
    --config <(printf 'user = "%s:%s"\n' "$user" "$pass") \
    -H 'Content-Type: text/xml; charset=utf-8' \
    -H 'SOAPAction: "urn:AC#executeCommand"' \
    --data-binary @- "$SOAP_URL" <<XML
<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns1="urn:AC">
  <SOAP-ENV:Body>
    <ns1:executeCommand>
      <command>${escaped}</command>
    </ns1:executeCommand>
  </SOAP-ENV:Body>
</SOAP-ENV:Envelope>
XML
  echo
}

cmd=${1:-status}
case "$cmd" in
  start)
    sudo systemctl start "$FRPC_SERVICE"
    sudo systemctl start "$AUTH_SERVICE"
    sudo systemctl start "$WORLD_SERVICE"
    "$0" check
    ;;
  start-stack)
    sudo systemctl start "$AUTH_SERVICE"
    sudo systemctl start "$WORLD_SERVICE"
    sudo systemctl start "$MCP_SERVICE" "$HERMES_RELAY_SERVICE" "$ACCOUNT_REGISTER_SERVICE"
    "$0" check-all
    ;;
  stop)
    sudo systemctl stop "$WORLD_SERVICE"
    sudo systemctl stop "$AUTH_SERVICE"
    "$0" status
    ;;
  stop-stack)
    sudo systemctl stop "$HERMES_RELAY_SERVICE" "$MCP_SERVICE" "$ACCOUNT_REGISTER_SERVICE" "$WORLD_SERVICE" "$AUTH_SERVICE"
    "$0" check-all || true
    ;;
  stop-all)
    sudo systemctl stop "$HERMES_RELAY_SERVICE" "$MCP_SERVICE" "$ACCOUNT_REGISTER_SERVICE" "$WORLD_SERVICE" "$AUTH_SERVICE" "$FRPC_SERVICE"
    "$0" status
    ;;
  restart)
    sudo systemctl stop "$WORLD_SERVICE"
    sudo systemctl restart "$AUTH_SERVICE"
    sudo systemctl start "$WORLD_SERVICE"
    "$0" check
    ;;
  restart-sidecars)
    sudo systemctl restart "$MCP_SERVICE" "$HERMES_RELAY_SERVICE" "$ACCOUNT_REGISTER_SERVICE"
    "$0" check-all
    ;;
  status)
    if [[ "${ACORE_OPS_DEFAULT:-rt}" == "rt" && -x "$REPO/ops/rt-wow-migration/deploy.sh" ]]; then
      rt_deploy status
    else
      systemctl --no-pager --full status "$AUTH_SERVICE" "$WORLD_SERVICE" "$MCP_SERVICE" "$HERMES_RELAY_SERVICE" "$ACCOUNT_REGISTER_SERVICE" "$FRPC_SERVICE" || true
      ports
      processes
    fi
    ;;
  rt-status)
    rt_deploy status
    ;;
  rt-deploy-sidecar)
    rt_deploy deploy-sidecar
    ;;
  rt-deploy-world)
    rt_deploy deploy-world
    ;;
  rt-backup-db)
    rt_deploy backup-db
    ;;
  check)
    systemctl is-active "$AUTH_SERVICE" "$WORLD_SERVICE" "$FRPC_SERVICE"
    ports
    processes
    journalctl -u "$WORLD_SERVICE" -n 300 --no-pager \
      | rg 'WORLD: World Initialized|worldserver-daemon.*ready' || true
    ;;
  check-all)
    systemctl is-active "$AUTH_SERVICE" "$WORLD_SERVICE" "$MCP_SERVICE" "$HERMES_RELAY_SERVICE" "$ACCOUNT_REGISTER_SERVICE"
    ports
    processes
    curl -fsS http://127.0.0.1:18765/health || true
    echo
    ready_line
    check_prefix_runtime_errors
    ;;
  ready)
    ready_line
    ;;
  logs)
    lines=${2:-160}
    journalctl -u "$AUTH_SERVICE" -u "$WORLD_SERVICE" -u "$FRPC_SERVICE" -n "$lines" --no-pager
    ;;
  stack-logs)
    lines=${2:-160}
    journalctl -u "$AUTH_SERVICE" -u "$WORLD_SERVICE" -u "$MCP_SERVICE" -u "$HERMES_RELAY_SERVICE" -u "$ACCOUNT_REGISTER_SERVICE" -n "$lines" --no-pager
    ;;
  world-log)
    lines=${2:-160}
    journalctl -u "$WORLD_SERVICE" -n "$lines" --no-pager
    ;;
  relay-summary)
    relay_summary "${2:-40}"
    ;;
  build-world)
    jobs=${2:-4}
    build_world "$jobs"
    ;;
  deploy-world)
    sudo systemctl stop "$WORLD_SERVICE"
    install_built_world
    sudo systemctl start "$WORLD_SERVICE"
    wait_world_ready
    check_prefix_runtime_errors
    "$0" check
    ;;
  build-deploy-world)
    jobs=${2:-4}
    "$0" stop-stack
    build_world "$jobs"
    install_built_world
    "$0" start-stack
    ;;
  gm)
    shift
    gm_command "$@"
    ;;
  install-units)
    sudo install -m 0644 "$REPO/ops/systemd/azerothcore-auth.service" /etc/systemd/system/azerothcore-auth.service
    sudo install -m 0644 "$REPO/ops/systemd/azerothcore-world.service" /etc/systemd/system/azerothcore-world.service
    sudo install -m 0644 "$REPO/ops/systemd/azerothcore-playerbot-mcp.service" /etc/systemd/system/azerothcore-playerbot-mcp.service
    sudo install -m 0644 "$REPO/ops/systemd/azerothcore-playerbot-hermes-relay.service" /etc/systemd/system/azerothcore-playerbot-hermes-relay.service
    sudo install -m 0644 "$REPO/ops/systemd/azerothcore-account-register.service" /etc/systemd/system/azerothcore-account-register.service
    sudo systemctl daemon-reload
    sudo systemctl enable "$AUTH_SERVICE" "$WORLD_SERVICE" "$MCP_SERVICE" "$HERMES_RELAY_SERVICE" "$ACCOUNT_REGISTER_SERVICE" "$FRPC_SERVICE"
    systemctl cat "$AUTH_SERVICE" "$WORLD_SERVICE" "$MCP_SERVICE" "$HERMES_RELAY_SERVICE" "$ACCOUNT_REGISTER_SERVICE"
    ;;
  old-refs)
    rg -n '/home/wuya/git/azerothcore-wotlk(/|"| |$)' \
      /etc/systemd/system "$REPO/env/dist/etc" /home/wuya/.config/systemd/user 2>/dev/null || true
    pgrep -af 'azerothcore-wotlk/|authserver|worldserver|frpc' || true
    ;;
  quarantine-old)
    if [[ -e "$OLD_PATH" ]]; then
      if [[ -e "$QUARANTINE" ]]; then
        echo "Quarantine path already exists: $QUARANTINE" >&2
        exit 1
      fi
      mv "$OLD_PATH" "$QUARANTINE"
      echo "Moved $OLD_PATH -> $QUARANTINE"
    else
      echo "Old path is absent: $OLD_PATH"
    fi
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
