#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly DEPLOY_SCRIPT="$SCRIPT_DIR/../deploy.sh"

for contract in \
  '--memory 4g' \
  '--memory-reservation 3g' \
  '--memory-swap 4g' \
  '--cpu-shares 256' \
  '--pids-limit 512'; do
  if ! grep -Fq -- "$contract" "$DEPLOY_SCRIPT"; then
    printf 'Fairing deploy script is missing resource contract: %s\n' "$contract" >&2
    exit 1
  fi
done

bash -n "$DEPLOY_SCRIPT"
printf 'Fairing deploy contract tests passed\n'
