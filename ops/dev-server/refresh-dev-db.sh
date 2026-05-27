#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" != "--yes" ]]; then
  echo "This drops and recreates acore_dev_world, acore_dev_characters, and acore_dev_playerbots from production." >&2
  echo "It also ensures acore_auth.realmlist id=3 is the GM-locked agile test realm." >&2
  echo "Run: $0 --yes" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROD_ROOT=/home/wuya/git/azerothcore-wotlk-git
LOGIN_INFO="$(awk -F'"' '/^LoginDatabaseInfo[[:space:]]*=/{print $2; exit}' "$PROD_ROOT/env/dist/etc/authserver.conf")"
IFS=';' read -r HOST PORT USER PASS _DB <<< "$LOGIN_INFO"

mysql_cmd=(mysql -h "$HOST" -P "$PORT" -u "$USER" "-p$PASS")
dump_cmd=(mysqldump -h "$HOST" -P "$PORT" -u "$USER" "-p$PASS" \
  --single-transaction --quick --routines --triggers --events --set-gtid-purged=OFF)

pairs=(
  "acore_playerbot_world:acore_dev_world"
  "acore_playerbot_characters:acore_dev_characters"
  "acore_playerbots:acore_dev_playerbots"
)

for pair in "${pairs[@]}"; do
  src="${pair%%:*}"
  dst="${pair##*:}"
  echo "==> cloning $src -> $dst"
  "${mysql_cmd[@]}" -e "DROP DATABASE IF EXISTS \`$dst\`; CREATE DATABASE \`$dst\` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
  "${dump_cmd[@]}" "$src" | "${mysql_cmd[@]}" "$dst"
done

"${mysql_cmd[@]}" acore_auth -e "
INSERT INTO realmlist
  (id, name, address, localAddress, localSubnetMask, port, icon, flag, timezone, allowedSecurityLevel, population, gamebuild)
VALUES
  (3, '敏捷测试', '38.207.189.99', '127.0.0.1', '255.255.255.255', 8086, 0, 0, 16, 3, 0, 12340)
ON DUPLICATE KEY UPDATE
  name = VALUES(name),
  address = VALUES(address),
  localAddress = VALUES(localAddress),
  localSubnetMask = VALUES(localSubnetMask),
  port = VALUES(port),
  icon = VALUES(icon),
  flag = flag & ~2,
  timezone = VALUES(timezone),
  allowedSecurityLevel = VALUES(allowedSecurityLevel),
  gamebuild = VALUES(gamebuild);
"

"${mysql_cmd[@]}" acore_dev_playerbots -e "
UPDATE agent_playerbot_actions
SET status = 'stale', error = 'staled by dev database refresh', updated_at = NOW()
WHERE status IN ('pending', 'running');
UPDATE agent_playerbot_events
SET processed_at = COALESCE(processed_at, NOW())
WHERE processed_at IS NULL;
"

"${mysql_cmd[@]}" --batch acore_auth -e "SELECT id,name,address,localAddress,localSubnetMask,port,allowedSecurityLevel,gamebuild FROM realmlist ORDER BY id;"

echo "Dev DB refresh complete for $ROOT"
