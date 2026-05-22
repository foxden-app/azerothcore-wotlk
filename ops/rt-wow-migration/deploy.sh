#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RT_USER="${RT_USER:-wuya}"
RT_HOST="${RT_HOST:-38.207.189.99}"
RT_PORT="${RT_PORT:-8022}"
RT_ROOT="${RT_ROOT:-/home/wuya/git/azerothcore-wotlk-git}"
RT_TARGET="${RT_USER}@${RT_HOST}"
SSH_OPTS=(-p "$RT_PORT")
RSYNC_SSH="ssh -p $RT_PORT"

usage() {
  cat <<'USAGE'
Usage: ops/rt-wow-migration/deploy.sh <command>

Commands:
  status          Show RT containers, sidecar services, ports, and realmlist.
  backup-db       Create a timestamped RT production DB backup.
  deploy-sidecar  Sync Python sidecars and restart MCP/relay/register services.
  deploy-ops      Sync RT ops files and install systemd units. Does not touch libs.
  deploy-libs     Rebuild runtime libs from local auth/world binaries and sync them.
  deploy-world    Backup DB, sync auth/world runtime, rebuild image, restart auth/world.

Environment overrides:
  RT_USER, RT_HOST, RT_PORT, RT_ROOT
USAGE
}

remote() {
  ssh "${SSH_OPTS[@]}" "$RT_TARGET" "$@"
}

rsync_dir() {
  local source="$1"
  local dest="$2"
  shift 2
  rsync -az --delete "$@" -e "$RSYNC_SSH" "$source" "$RT_TARGET:$dest"
}

rsync_files() {
  local dest="$1"
  shift
  rsync -az -e "$RSYNC_SSH" "$@" "$RT_TARGET:$dest"
}

require_dir() {
  local path="$1"
  if [[ ! -d "$path" ]]; then
    echo "Missing directory: $path" >&2
    exit 1
  fi
}

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "Missing file: $path" >&2
    exit 1
  fi
}

backup_db() {
  remote "set -euo pipefail
mkdir -p ~/backups/acore
backup=~/backups/acore/acore-\$(date +%Y%m%d-%H%M%S).sql.gz
MYSQL_PWD=acore mysqldump -uacore --no-tablespaces --single-transaction --quick --routines --triggers --events \
  --databases acore_auth acore_playerbot_world acore_playerbot_characters acore_playerbots \
  | gzip > \"\$backup\"
ls -lh \"\$backup\""
}

status() {
  remote "set -e
cd '$RT_ROOT/ops/rt-wow-migration'
echo '--- containers ---'
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | egrep 'wow|hermes|NAMES' || true
echo '--- sidecars ---'
systemctl is-active azerothcore-playerbot-mcp.service azerothcore-playerbot-hermes-relay.service azerothcore-account-register.service || true
echo '--- ports ---'
ss -ltnp | egrep ':(3724|8085|7879|8642|18765|18080)\b' || true
echo '--- realmlist ---'
MYSQL_PWD=acore mysql -uacore -N -e \"SELECT id,name,address,localAddress,localSubnetMask,port,flag,timezone,gamebuild FROM acore_auth.realmlist ORDER BY id;\"
echo '--- world tail ---'
docker logs --tail 80 wow-world 2>&1 | sed -r 's/\x1b\[[0-9;]*m//g' | tail -80"
}

deploy_ops() {
  require_dir "$ROOT/ops/rt-wow-migration"
  rsync_dir "$ROOT/ops/rt-wow-migration/" "$RT_ROOT/ops/rt-wow-migration/" --exclude "libs/"
  remote "set -e
cd '$RT_ROOT'
sudo install -m 0644 ops/rt-wow-migration/systemd/azerothcore-playerbot-mcp.service /etc/systemd/system/azerothcore-playerbot-mcp.service
sudo install -m 0644 ops/rt-wow-migration/systemd/azerothcore-playerbot-hermes-relay.service /etc/systemd/system/azerothcore-playerbot-hermes-relay.service
sudo install -m 0644 ops/rt-wow-migration/systemd/azerothcore-account-register.service /etc/systemd/system/azerothcore-account-register.service
sudo systemctl daemon-reload"
}

deploy_sidecar() {
  require_dir "$ROOT/tools/playerbot-mcp"
  require_dir "$ROOT/tools/account-register"
  rsync_dir "$ROOT/tools/playerbot-mcp/" "$RT_ROOT/tools/playerbot-mcp/"
  rsync_dir "$ROOT/tools/account-register/" "$RT_ROOT/tools/account-register/"
  deploy_ops
  remote "set -e
sudo systemctl restart azerothcore-playerbot-mcp.service azerothcore-playerbot-hermes-relay.service azerothcore-account-register.service
systemctl is-active azerothcore-playerbot-mcp.service azerothcore-playerbot-hermes-relay.service azerothcore-account-register.service"
}

deploy_libs() {
  require_file "$ROOT/env/dist/bin/authserver"
  require_file "$ROOT/env/dist/bin/worldserver"
  local libs_dir="$ROOT/ops/rt-wow-migration/libs"
  mkdir -p "$libs_dir"
  find "$libs_dir" -maxdepth 1 -type f -delete
  ldd "$ROOT/env/dist/bin/authserver" "$ROOT/env/dist/bin/worldserver" \
    | awk '/=> \// {print $3} /^\// {print $1}' \
    | sort -u \
    | while read -r lib; do
        [[ -n "$lib" && -f "$lib" ]] && cp -L "$lib" "$libs_dir/"
      done
  rsync_dir "$libs_dir/" "$RT_ROOT/ops/rt-wow-migration/libs/"
  remote "set -e
cd '$RT_ROOT/ops/rt-wow-migration'
docker compose build wow-auth wow-world"
}

deploy_world() {
  require_file "$ROOT/env/dist/bin/authserver"
  require_file "$ROOT/env/dist/bin/worldserver"
  require_dir "$ROOT/data/sql"
  require_dir "$ROOT/modules/mod-playerbots"
  require_dir "$ROOT/modules/mod-playerbot-agent"

  backup_db
  deploy_ops
  deploy_libs

  remote "set -e
cd '$RT_ROOT/ops/rt-wow-migration'
docker compose stop wow-world wow-auth"

  rsync_files "$RT_ROOT/env/dist/bin/" "$ROOT/env/dist/bin/authserver" "$ROOT/env/dist/bin/worldserver"
  rsync_dir "$ROOT/data/sql/" "$RT_ROOT/data/sql/"
  rsync_dir "$ROOT/modules/mod-playerbots/" "$RT_ROOT/modules/mod-playerbots/"
  rsync_dir "$ROOT/modules/mod-playerbot-agent/" "$RT_ROOT/modules/mod-playerbot-agent/"

  remote "set -e
cd '$RT_ROOT/ops/rt-wow-migration'
docker compose up -d wow-auth wow-world
sleep 5
docker ps --format 'table {{.Names}}\t{{.Status}}' | egrep 'wow|NAMES'
ss -ltnp | egrep ':(3724|8085|7879)\b' || true
docker logs --tail 120 wow-world 2>&1 | sed -r 's/\x1b\[[0-9;]*m//g' | tail -120"
}

command="${1:-}"
case "$command" in
  status) status ;;
  backup-db) backup_db ;;
  deploy-sidecar) deploy_sidecar ;;
  deploy-ops) deploy_ops ;;
  deploy-libs) deploy_libs ;;
  deploy-world) deploy_world ;;
  -h|--help|help|"") usage ;;
  *)
    echo "Unknown command: $command" >&2
    usage >&2
    exit 1
    ;;
esac
