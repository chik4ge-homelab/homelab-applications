#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
vendor_dir="$repo_root/mixins/vendor"
config_path="$repo_root/mixins/build/config.yaml"
mixin_subdir="mixin-generated"
header_comment="# This manifest is auto-generated. Do not edit."

collect_glob_matches() {
  local pattern="$1"
  local -n matches_ref="$2"

  mapfile -t matches_ref < <(compgen -G "$pattern" || true)
}

resolve_source_path() {
  local source_path="$1"

  if [[ "$source_path" = /* ]]; then
    printf '%s' "$source_path"
  else
    printf '%s' "$repo_root/$source_path"
  fi
}

render_rule() {
  local name="$1"
  local output_dir="$2"
  local source_path="$3"

  mkdir -p "$output_dir"

  local output_file="$output_dir/${name}.yaml"
  local resolved_source
  resolved_source="$(resolve_source_path "$source_path")"

  {
    printf '%s\n' "$header_comment"
    jsonnet -J "$vendor_dir" "$resolved_source" | yq -P -N -oy -p json
  } > "$output_file"

  echo "Generated: $output_file"
}

render_prometheus_rule() {
  local name="$1"
  local output_dir="$2"
  local mixin_import="$3"
  local config_import="${4:-}"
  local groups_key="${5:-prometheusRules}"

  mkdir -p "$output_dir"

  local output_file="$output_dir/${name}.yaml"
  local mixin_code="import '$mixin_import'"
  local config_code="{}"

  if [[ -n "$config_import" ]]; then
    local resolved_config
    resolved_config="$(resolve_source_path "$config_import")"
    config_code="import '$resolved_config'"
  fi

  {
    printf '%s\n' "$header_comment"
    jsonnet -J "$vendor_dir" "$repo_root/mixins/build/common/prometheus-rule.jsonnet" \
      --ext-str name="$name" \
      --ext-code mixin="$mixin_code" \
      --ext-code config="$config_code" \
      --ext-str groups_key="$groups_key" \
      | yq -P -N -oy -p json
  } > "$output_file"

  echo "Generated: $output_file"
}

render_yaml() {
  local name="$1"
  local output_dir="$2"
  local source_path="$3"

  mkdir -p "$output_dir"

  local output_file="$output_dir/${name}.yaml"
  local resolved_source
  resolved_source="$(resolve_source_path "$source_path")"

  {
    printf '%s\n' "$header_comment"
    yq -P -N -oy -p yaml 'sort_keys(..)' "$resolved_source"
  } > "$output_file"

  echo "Generated: $output_file"
}

render_kustomization() {
  local output_dir="$1"
  local resources_csv="$2"
  local output_file="$output_dir/kustomization.yaml"

  {
    printf '%s\n' "$header_comment"
    jsonnet "$repo_root/mixins/build/common/kustomize.jsonnet" \
      --ext-str resources="$resources_csv" \
      | yq -P -N -oy -p json
  } > "$output_file"

  echo "Generated: $output_file"
}

[[ -f "$config_path" ]] || { echo "Config not found: $config_path" >&2; exit 1; }

if [[ -d "$repo_root/manifests" ]]; then
  find "$repo_root/manifests" -type d -name "$mixin_subdir" -print0 \
    | while IFS= read -r -d '' dir; do
        case "$dir" in
          "$repo_root"/manifests/*/"$mixin_subdir")
            find "$dir" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
            ;;
          *)
            echo "Skipping unsafe cleanup path: $dir" >&2
            ;;
        esac
      done
fi

declare -A destination_resources

while IFS='|' read -r destination name type entrypoint entrypoint_glob source_glob mixin_import config_import groups_key exclude_files; do
  destination_trimmed="${destination%/}"
  output_dir="$repo_root/$destination_trimmed/$mixin_subdir"

  case "$output_dir" in
    "$repo_root"/manifests/*) ;;
    *) echo "Skipping unsafe destination: $output_dir" >&2; continue ;;
  esac

  destination_resources["$output_dir"]+=""

  if [[ -z "$type" ]]; then
    if [[ -n "$source_glob" ]]; then
      type="yaml"
    else
      type="jsonnet"
    fi
  fi

  case "$type" in
    yaml)
      if [[ -z "$source_glob" ]]; then
        echo "Missing source_glob for destination: $destination" >&2
        exit 1
      fi

      if [[ "$source_glob" = /* ]]; then
        glob_pattern="$source_glob"
      else
        glob_pattern="$repo_root/$source_glob"
      fi

      collect_glob_matches "$glob_pattern" glob_matches

      if [[ ${#glob_matches[@]} -eq 0 ]]; then
        echo "No files matched glob: $source_glob" >&2
        exit 1
      fi

      exclude_list=()
      if [[ -n "$exclude_files" ]]; then
        IFS=',' read -r -a exclude_list <<< "$exclude_files"
      fi

      for source_path in "${glob_matches[@]}"; do
        file_name="$(basename "$source_path")"
        case "$file_name" in
          *.yaml) file_name="${file_name%.yaml}" ;;
          *.yml) file_name="${file_name%.yml}" ;;
        esac

        for excluded in "${exclude_list[@]}"; do
          if [[ "$excluded" == "$file_name" || "$excluded" == "$file_name.yaml" || "$excluded" == "$file_name.yml" ]]; then
            continue 2
          fi
        done

        render_yaml "$file_name" "$output_dir" "$source_path"
        destination_resources["$output_dir"]+="$file_name.yaml"$'\n'
      done
      continue
      ;;
    jsonnet)
      if [[ -n "$entrypoint_glob" ]]; then
        if [[ "$entrypoint_glob" = /* ]]; then
          glob_pattern="$entrypoint_glob"
        else
          glob_pattern="$repo_root/$entrypoint_glob"
        fi

        collect_glob_matches "$glob_pattern" glob_matches

        if [[ ${#glob_matches[@]} -eq 0 ]]; then
          echo "No files matched glob: $entrypoint_glob" >&2
          exit 1
        fi

        for source_path in "${glob_matches[@]}"; do
          file_name="$(basename "$source_path")"
          file_name="${file_name%.jsonnet}"
          render_rule "$file_name" "$output_dir" "$source_path"
          destination_resources["$output_dir"]+="$file_name.yaml"$'\n'
        done
        continue
      fi

      if [[ -z "$entrypoint" || -z "$name" ]]; then
        echo "Missing entrypoint or name for destination: $destination" >&2
        exit 1
      fi

      render_rule "$name" "$output_dir" "$entrypoint"
      destination_resources["$output_dir"]+="$name.yaml"$'\n'
      continue
      ;;
    prometheus-rule)
      if [[ -z "$mixin_import" || -z "$name" ]]; then
        echo "Missing mixin_import or name for destination: $destination" >&2
        exit 1
      fi

      if [[ -z "$groups_key" ]]; then
        groups_key="prometheusRules"
      fi

      render_prometheus_rule "$name" "$output_dir" "$mixin_import" "$config_import" "$groups_key"
      destination_resources["$output_dir"]+="$name.yaml"$'\n'
      continue
      ;;
    *)
      echo "Unknown mixin type: $type for destination: $destination" >&2
      exit 1
      ;;
  esac
 done < <(
  yq -r '
    .entries[]
    | select(.destination != null and .destination != "")
    | .destination as $destination
    | .mixins[]
    | [
        $destination,
        .name // "",
        .type // "",
        .entrypoint // "",
        .entrypoint_glob // "",
        .source_glob // "",
        .mixin_import // "",
        .config_import // "",
        .groups_key // "",
        (.exclude_files // []) | join(",")
      ]
    | join("|")
  ' "$config_path"
)

for output_dir in "${!destination_resources[@]}"; do
  resource_block="${destination_resources[$output_dir]}"
  if [[ -z "$resource_block" ]]; then
    continue
  fi

  mapfile -t sorted_resources < <(printf '%s' "$resource_block" | sort -u)
  resources_csv=$(IFS=','; echo "${sorted_resources[*]}")
  render_kustomization "$output_dir" "$resources_csv"
done
