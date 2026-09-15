#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly DEPLOY_SCRIPT="$SCRIPT_DIR/../deploy.sh"

for contract in \
  '--user 1000:1000' \
  '--group-add 0' \
  '--group-add 44' \
  '--group-add 993' \
  '--group-add 65532' \
  '--read-only' \
  '--tmpfs /run/fairing-auth:rw,noexec,nosuid,size=1m,mode=0700,uid=1000,gid=1000' \
  '--tmpfs /tmp:rw,noexec,nosuid,size=512m,mode=1777,uid=1000,gid=1000' \
  '--memory 4g' \
  '--memory-reservation 3g' \
  '--memory-swap 4g' \
  '--cpu-shares 256' \
  '--pids-limit 512' \
  '--cap-drop ALL' \
  '--security-opt no-new-privileges:true'; do
  if ! grep -Fq -- "$contract" "$DEPLOY_SCRIPT"; then
    printf 'Fairing deploy script is missing resource contract: %s\n' "$contract" >&2
    exit 1
  fi
done

for contract in \
  '-e HOME=/tmp/fairing-home' \
  '-e HF_HOME=/cache/huggingface' \
  '-e HF_HUB_OFFLINE=1' \
  '-v /opt/docker/hf_cache:/cache/huggingface:ro'; do
  if ! grep -Fq -- "$contract" "$DEPLOY_SCRIPT"; then
    printf 'Fairing deploy script is missing non-root runtime contract: %s\n' "$contract" >&2
    exit 1
  fi
done

if ! grep -Fq 'USER 1000:1000' "$SCRIPT_DIR/../Dockerfile"; then
  printf 'Fairing image must declare the non-root runtime user\n' >&2
  exit 1
fi

for contract in \
  "install -o root -g root -m 0644 '\$REMOTE_DIR/config/sources.yaml' /opt/docker/fairing/config/sources.yaml" \
  "install -o root -g root -m 0644 '\$REMOTE_DIR/config/subscriptions.yaml' /opt/docker/fairing/config/subscriptions.yaml" \
  "install -o root -g root -m 0755 '\$REMOTE_DIR/deploy/fairing-daily-run' /usr/local/sbin/fairing-daily-run" \
  "install -o root -g root -m 0755 '\$REMOTE_DIR/deploy/sync-fairing-cron' /usr/local/sbin/sync-fairing-cron" \
  '* * * * * /usr/local/sbin/sync-fairing-cron'; do
  if ! grep -Fq -- "$contract" "$DEPLOY_SCRIPT"; then
    printf 'Fairing deploy script is missing scheduler contract: %s\n' "$contract" >&2
    exit 1
  fi
done

if ! grep -Fq 'docker exec fairing python /fairing/deploy/fairing-runtime-smoke.py' \
  "$DEPLOY_SCRIPT"; then
  printf 'Fairing deploy must run the production data and model smoke probe\n' >&2
  exit 1
fi

if grep -Fqi -- 'n8n' "$SCRIPT_DIR/../deploy/sync-fairing-cron"; then
  printf 'Fairing cron sync must not mutate the retired n8n scheduler\n' >&2
  exit 1
fi

if ! grep -Fqx 'docker exec "$containers" python /fairing/main.py run --no-mail' \
  "$SCRIPT_DIR/../deploy/fairing-daily-run"; then
  printf 'Fairing daily run must respect the label gate and suppress legacy mail\n' >&2
  exit 1
fi
if grep -Fq -- '--force' "$SCRIPT_DIR/../deploy/fairing-daily-run"; then
  printf 'Fairing daily run must not bypass the label gate\n' >&2
  exit 1
fi

test_dir=$(mktemp -d /tmp/fairing-cron-contract.XXXXXX)
trap 'rm -rf "$test_dir"' EXIT
printf '10:00\n' >"$test_dir/cron-time"
cat >"$test_dir/crontab" <<'EOF'
15 3 * * * /usr/local/sbin/unrelated-backup
5 9 * * * docker exec fairing python /fairing/main.py run --force && curl legacy
EOF
cat >"$test_dir/fake-crontab" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "${1:-}" == "-l" ]]; then
  cat "$CRONTAB_STATE"
  exit 0
fi
cp "$1" "$CRONTAB_STATE"
EOF
chmod 0755 "$test_dir/fake-crontab"
CRONTAB_STATE="$test_dir/crontab" \
CRONTAB_BIN="$test_dir/fake-crontab" \
FAIRING_CRON_TIME_FILE="$test_dir/cron-time" \
  bash "$SCRIPT_DIR/../deploy/sync-fairing-cron"
grep -Fqx '15 3 * * * /usr/local/sbin/unrelated-backup' "$test_dir/crontab"
grep -Fqx '0 10 * * * /usr/local/bin/cron-alert-wrapper.sh fairing-daily-run /usr/local/sbin/fairing-daily-run' "$test_dir/crontab"
[[ $(grep -Fc 'fairing-daily-run' "$test_dir/crontab") -eq 1 ]]
if grep -Eq '/fairing/main\.py|docker exec.*fairing.*&&.*curl' "$test_dir/crontab"; then
  printf 'Fairing cron sync retained a legacy execution path\n' >&2
  exit 1
fi

bash -n "$DEPLOY_SCRIPT"
bash -n "$SCRIPT_DIR/../deploy/fairing-daily-run"
bash -n "$SCRIPT_DIR/../deploy/sync-fairing-cron"
printf 'Fairing deploy contract tests passed\n'
