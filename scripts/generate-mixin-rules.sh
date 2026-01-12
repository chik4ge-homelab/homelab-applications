#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
vendor_dir="$repo_root/mixins/vendor"
config_path="$repo_root/mixins/build/config.yaml"
mixin_subdir="mixin-generated"

render_rule() {
  local name="$1"
  local output_dir="$2"
  local source_path="$3"

  mkdir -p "$output_dir"

  local output_file="$output_dir/${name}.yaml"
  local header_comment="# This manifest is auto-generated. Do not edit."

  {
    printf '%s\n' "$header_comment"
    if [[ "$source_path" = /* ]]; then
      jsonnet -J "$vendor_dir" "$source_path"
    else
      jsonnet -J "$vendor_dir" "$repo_root/$source_path"
    fi | yq -P -N -oy -p json
  } > "$output_file"

  echo "Generated: $output_file"
}

[[ -f "$config_path" ]] || { echo "Config not found: $config_path" >&2; exit 1; }

mapfile -t destinations < <(
  yq -r '
    .entries[]
    | select(.destination != null and .destination != "")
    | .destination
  ' "$config_path"
)

declare -A uniq_dirs
for d in "${destinations[@]}"; do
  d_trimmed="${d%/}"
  resolved="$(realpath -m -- "$repo_root/$d_trimmed/$mixin_subdir")"
  case "$resolved" in
    "$repo_root"/manifests/*)
      uniq_dirs["$resolved"]=1
      ;;
    *)
      echo "Skipping unsafe destination: $resolved" >&2
      ;;
  esac
done

if [[ ${#uniq_dirs[@]} -gt 0 ]]; then
  shopt -s dotglob nullglob
  for td in "${!uniq_dirs[@]}"; do
    [[ -d "$td" ]] && rm -rf "$td"/*
  done
  shopt -u dotglob nullglob
fi

while IFS=$'\t' read -r name entrypoint destination; do
  destination_trimmed="${destination%/}"
  resolved="$(realpath -m -- "$repo_root/$destination_trimmed/$mixin_subdir")"
  render_rule "$name" "$resolved" "$entrypoint"
done < <(
  yq -r '
    .entries[]
    | select(.name != null and .entrypoint != null and .destination != null)
    | select(.name != "" and .entrypoint != "" and .destination != "")
    | [.name, .entrypoint, .destination]
    | @tsv
  ' "$config_path"
)
