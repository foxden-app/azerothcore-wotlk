#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

if [[ -n "${PLAYERBOT_MCP_PYTHON:-}" ]]; then
  PYTHON="${PLAYERBOT_MCP_PYTHON}"
elif [[ -x "${REPO_ROOT}/var/playerbot-mcp-venv/bin/python" ]]; then
  PYTHON="${REPO_ROOT}/var/playerbot-mcp-venv/bin/python"
elif [[ -x "/home/wuya/git/azerothcore-wotlk-git/var/playerbot-mcp-venv/bin/python" ]]; then
  PYTHON="/home/wuya/git/azerothcore-wotlk-git/var/playerbot-mcp-venv/bin/python"
elif [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
  PYTHON="${REPO_ROOT}/.venv/bin/python"
else
  PYTHON="python3"
fi

echo "Using Python: ${PYTHON}"

"${PYTHON}" -m py_compile \
  "${SCRIPT_DIR}/wow_common.py" \
  "${SCRIPT_DIR}/hermes_relay.py" \
  "${SCRIPT_DIR}/server.py" \
  "${SCRIPT_DIR}/export_hl_trial.py" \
  "${SCRIPT_DIR}/summarize_hl_trials.py"

cd "${SCRIPT_DIR}"
"${PYTHON}" -m unittest \
  test_playerbot_mcp.py \
  test_hl_trial_export.py \
  test_hl_trial_summary.py
