#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
vendor_dir="$repo_root/mixins/vendor"

if ! command -v jsonnet >/dev/null 2>&1; then
  echo "jsonnet is required." >&2
  exit 1
fi

if ! command -v yq >/dev/null 2>&1; then
  echo "yq is required." >&2
  exit 1
fi

config_path="$repo_root/mixins/build/config.yaml"

render_rule() {
  local name="$1"
  local output_dir="$2"
  local source_path="$3"

  mkdir -p "$output_dir"

  local output_file="$output_dir/${name}.yaml"
  local header_comment="# This manifest is auto-generated. Do not edit."

  {
    printf '%s\n' "$header_comment"
    jsonnet -J "$vendor_dir" "$repo_root/$source_path" \
      | yq -P -N -oy -p json
  } > "$output_file"

  echo "Generated: $output_file"
}

if [[ ! -f "$config_path" ]]; then
  echo "Config not found: $config_path" >&2
  exit 1
fi

while IFS=$'\t' read -r name entrypoint destination; do
  render_rule "$name" "$repo_root/$destination" "$entrypoint"
done < <(
  yq -r '.entries[] | [.name, .entrypoint, .destination] | @tsv' "$config_path"
)
