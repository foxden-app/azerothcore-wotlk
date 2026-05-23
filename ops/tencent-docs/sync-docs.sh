#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MANIFEST="${TENCENT_DOCS_MANIFEST:-$ROOT_DIR/ops/tencent-docs/cloud-docs.json}"

usage() {
  cat <<'USAGE'
Usage:
  ops/tencent-docs/sync-docs.sh --list
  ops/tencent-docs/sync-docs.sh --key <doc-key> [--replace]
  ops/tencent-docs/sync-docs.sh --all [--replace]

Creates a Tencent Docs doc from local Markdown and updates cloud-docs.json.
Secrets are read from the local Tencent Docs/mcporter environment; they are not
stored or printed by this script.
USAGE
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

json_arg() {
  jq -cn "$@"
}

extract_file_id() {
  jq -r '.file_id // .id // .data.file_id // .data.id // empty'
}

extract_url() {
  jq -r '.url // .file_url // .data.url // .data.file_url // empty'
}

list_docs() {
  jq -r '.documents[] | [.key, .sync.mode, .title, .url] | @tsv' "$MANIFEST"
}

manifest_value() {
  local key="$1"
  local expr="$2"
  jq -r --arg key "$key" ".documents[] | select(.key == \$key) | $expr // empty" "$MANIFEST"
}

update_manifest_doc() {
  local key="$1"
  local file_id="$2"
  local url="$3"
  local updated_at
  local tmp
  updated_at="$(date -Iseconds)"
  tmp="$(mktemp)"
  jq --arg key "$key" --arg file_id "$file_id" --arg url "$url" --arg updated_at "$updated_at" '
    .documents |= map(
      if .key == $key then
        .file_id = $file_id
        | .url = $url
        | .last_synced_at = $updated_at
      else
        .
      end
    )
  ' "$MANIFEST" > "$tmp"
  mv "$tmp" "$MANIFEST"
}

create_doc_from_markdown() {
  local key="$1"
  local replace_old="$2"
  local folder_id title source old_id result file_id url pos_result pos b64 args insert_result

  folder_id="$(jq -r '.folder.id' "$MANIFEST")"
  title="$(manifest_value "$key" '.title')"
  source="$(manifest_value "$key" '.source')"
  old_id="$(manifest_value "$key" '.file_id')"

  if [[ -z "$title" || -z "$source" ]]; then
    echo "unknown document key: $key" >&2
    exit 1
  fi
  if [[ ! -f "$ROOT_DIR/$source" ]]; then
    echo "source file not found for $key: $source" >&2
    exit 1
  fi

  echo "creating Tencent doc for $key from $source" >&2
  result="$(
    mcporter call tencent-docs manage.create_file --args "$(
      json_arg --arg title "$title" --arg parent "$folder_id" \
        '{file_type:"doc", title:$title, parent_id:$parent}'
    )"
  )"
  file_id="$(printf '%s' "$result" | extract_file_id)"
  url="$(printf '%s' "$result" | extract_url)"
  if [[ -z "$file_id" || "$file_id" == "null" ]]; then
    echo "failed to parse file_id from create_file response:" >&2
    printf '%s\n' "$result" >&2
    exit 1
  fi
  if [[ -z "$url" || "$url" == "null" ]]; then
    url="https://docs.qq.com/doc/${file_id}"
  fi

  pos_result="$(
    mcporter call tencent-docs doc.get_last_operable_pos --args "$(
      json_arg --arg file_id "$file_id" '{file_id:$file_id}'
    )"
  )"
  pos="$(printf '%s' "$pos_result" | jq -r '.position // .index // .pos // .data.position // .data.index // 1')"
  b64="$(base64 -w 0 "$ROOT_DIR/$source")"
  args="$(json_arg --arg file_id "$file_id" --arg b64 "$b64" --argjson index "$pos" \
    '{file_id:$file_id, index:$index, base64_markdown:$b64}')"

  insert_result="$(mcporter call tencent-docs doc.insert_markdown --args "$args")"
  if printf '%s\n' "$insert_result" | jq -e 'has("error") and (.error | length > 0)' >/dev/null; then
    echo "insert_markdown returned an error:" >&2
    printf '%s\n' "$insert_result" >&2
    exit 1
  fi

  update_manifest_doc "$key" "$file_id" "$url"

  if [[ "$replace_old" == "1" && -n "$old_id" && "$old_id" != "$file_id" ]]; then
    echo "deleting old Tencent doc for $key: $old_id" >&2
    mcporter call tencent-docs manage.delete_file --args "$(
      json_arg --arg file_id "$old_id" '{file_id:$file_id, remove_type:"current"}'
    )" >/dev/null || true
  fi

  printf '%s\t%s\t%s\n' "$key" "$file_id" "$url"
}

sync_key() {
  local key="$1"
  local replace_old="$2"
  local mode
  mode="$(manifest_value "$key" '.sync.mode')"
  case "$mode" in
    doc_from_markdown)
      create_doc_from_markdown "$key" "$replace_old"
      ;;
    "")
      echo "unknown document key: $key" >&2
      exit 1
      ;;
    *)
      echo "document $key uses sync mode '$mode'; handle it manually" >&2
      ;;
  esac
}

main() {
  local key=""
  local all="0"
  local replace_old="0"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --list)
        list_docs
        return 0
        ;;
      --key)
        key="${2:-}"
        shift 2
        ;;
      --all)
        all="1"
        shift
        ;;
      --replace)
        replace_old="1"
        shift
        ;;
      -h|--help)
        usage
        return 0
        ;;
      *)
        echo "unknown argument: $1" >&2
        usage >&2
        exit 1
        ;;
    esac
  done

  require_cmd jq
  require_cmd base64
  require_cmd mcporter
  jq empty "$MANIFEST"

  if [[ "$all" == "1" ]]; then
    jq -r '.documents[] | select(.sync.mode == "doc_from_markdown") | .key' "$MANIFEST" |
      while IFS= read -r item; do
        sync_key "$item" "$replace_old"
      done
    return 0
  fi

  if [[ -z "$key" ]]; then
    usage >&2
    exit 1
  fi
  sync_key "$key" "$replace_old"
}

main "$@"
