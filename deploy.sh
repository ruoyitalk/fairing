#!/usr/bin/env bash
# Build and replace Fairing from a clean Git commit. Runtime data and secrets
# stay on the homeserver; the release directory is immutable.

set -euo pipefail

DEPLOY_HOST="${FAIRING_DEPLOY_HOST:-homeserver-cf}"
SSH_KEY="${FAIRING_DEPLOY_SSH_KEY:-}"
REMOTE_ROOT="${FAIRING_REMOTE_ROOT:-/opt/docker/fairing-releases}"
REMOTE_ENV_FILE="${FAIRING_REMOTE_ENV_FILE:-/opt/docker/fairing/.env}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FULL_REVISION="$(git -C "$SCRIPT_DIR" rev-parse HEAD)"
SOURCE_REVISION="$(git -C "$SCRIPT_DIR" rev-parse --short=12 HEAD)"
if [[ -n "$(git -C "$SCRIPT_DIR" status --porcelain)" ]]; then
  echo "Refusing to deploy a dirty worktree" >&2
  exit 1
fi
REMOTE_DIR="$REMOTE_ROOT/$FULL_REVISION"
IMAGE="fairing:${SOURCE_REVISION}"

# Bash 3.2 expands empty arrays as unset under `set -u`. BatchMode is also the
# correct deployment boundary, so keep the arrays non-empty on every platform.
SSH_ARGS=( -o BatchMode=yes )
if [[ -n "$SSH_KEY" ]]; then
  [[ -r "$SSH_KEY" ]] || { echo "unreadable FAIRING_DEPLOY_SSH_KEY" >&2; exit 1; }
  SSH_ARGS+=( -i "$SSH_KEY" -o IdentitiesOnly=yes )
fi

echo "==> Preparing Fairing revision $SOURCE_REVISION"
incoming="$(ssh "${SSH_ARGS[@]}" "$DEPLOY_HOST" "install -d -m 0755 '$REMOTE_ROOT'; mktemp -d '$REMOTE_ROOT/.incoming-${SOURCE_REVISION}.XXXXXX'")"
git -C "$SCRIPT_DIR" archive --format=tar HEAD |
  ssh "${SSH_ARGS[@]}" "$DEPLOY_HOST" "tar -xf - -C '$incoming'"
ssh "${SSH_ARGS[@]}" "$DEPLOY_HOST" "set -eu
  if [ -d '$REMOTE_DIR' ]; then
    diff -qr '$incoming' '$REMOTE_DIR' >/dev/null
    find '$incoming' -depth -delete
  else
    mv '$incoming' '$REMOTE_DIR'
  fi
  chmod 0755 '$REMOTE_DIR'"

echo "==> Building and replacing Fairing"
ssh "${SSH_ARGS[@]}" "$DEPLOY_HOST" "
  set -Eeuo pipefail
  test -r '$REMOTE_ENV_FILE'
  cd '$REMOTE_DIR'
  docker build --build-arg SOURCE_REVISION='$SOURCE_REVISION' -t '$IMAGE' .
  # Test the built artifact itself. Mounting the mutable checkout here could
  # validate code that is not actually present in the image.
  docker run --rm '$IMAGE' pytest -q
  current_week=\"\$(date +%G-W%V)\"
  install -d -o root -g root -m 2775 /data/news \"/data/news/\$current_week\"
  if [[ -f \"/data/news/\$current_week/\$(date +%F).md\" ]]; then
    chmod 0664 \"/data/news/\$current_week/\$(date +%F).md\"
  fi
  install -o root -g root -m 0644 '$REMOTE_DIR/config/sources.yaml' /opt/docker/fairing/config/sources.yaml
  install -o root -g root -m 0644 '$REMOTE_DIR/config/subscriptions.yaml' /opt/docker/fairing/config/subscriptions.yaml
  run_lock=/data/fairing/fairing_run.lock
  if [[ -e \"\$run_lock\" ]]; then
    if [[ -L \"\$run_lock\" || ! -f \"\$run_lock\" ]]; then
      echo \"Fairing run lock is not a regular file: \$run_lock\" >&2
      exit 1
    fi
    if ! flock -n \"\$run_lock\" -c true; then
      echo \"Fairing run lock is active; refusing deployment\" >&2
      exit 1
    fi
    chown 1000:1000 \"\$run_lock\"
    chmod 0644 \"\$run_lock\"
  fi
  python3 '$REMOTE_DIR/fairing/backup_ownership.py' \
    --root /data/data_bak --uid 1000 --gid 1000
  run_fairing() {
    local image=\"\$1\"
    docker run -d \\
      --name fairing \\
      --restart unless-stopped \\
      --user 1000:1000 \\
      --group-add 0 \\
      --group-add 44 \\
      --group-add 993 \\
      --group-add 65532 \\
      --read-only \\
      --tmpfs /run/fairing-auth:rw,noexec,nosuid,size=1m,mode=0700,uid=1000,gid=1000 \\
      --tmpfs /tmp:rw,noexec,nosuid,size=512m,mode=1777,uid=1000,gid=1000 \\
      --memory 4g \\
      --memory-reservation 3g \\
      --memory-swap 4g \\
      --cpu-shares 256 \\
      --pids-limit 512 \\
      --label com.centurylinklabs.watchtower.enable=false \\
      --cap-drop ALL \\
      --security-opt no-new-privileges:true \\
      --network docker_proxy \\
      -p 127.0.0.1:8501:8501 \\
      --env-file '$REMOTE_ENV_FILE' \\
      -e FAIRING_SECRET_DIR=/run/homeserver-secrets \\
      -e FAIRING_OIDC_REDIRECT_URI=https://ruoyi.net.cn/oauth2callback \\
      -e HOME=/tmp/fairing-home \\
      -e HF_HOME=/cache/huggingface \\
      -e HF_HUB_OFFLINE=1 \\
      -e CUDA_VISIBLE_DEVICES= \\
      -e DATA_DIR=/data/fairing \\
      -e PAYLOAD_ROOT=/payload \\
      -e NEWS_DIR=/data/news \\
      -e KNOWLEDGE_DIR=/data/ruoyi_download \\
      -e PAYLOAD_DATA_DIR=/data/payload \\
      -v /data/fairing:/data/fairing \\
      -v /data/news:/data/news \\
      -v /data/ruoyi_download:/data/ruoyi_download \\
      -v /data/data_bak:/data/data_bak \\
      -v /data/data_bak/payload:/data/payload \\
      -v /opt/docker/homeserver-context/secrets:/run/homeserver-secrets:ro \\
      -v /opt/docker/payload_git:/payload:ro \\
      -v /opt/docker/fairing/config:/app/config:ro \\
      -v /opt/docker/hf_cache:/cache/huggingface:ro \\
      \"\$image\" >/dev/null
  }
  docker rm -f fairing-previous >/dev/null 2>&1 || true
  if docker inspect fairing >/dev/null 2>&1; then
    docker stop fairing >/dev/null
    docker rename fairing fairing-previous
  fi
  rollback() {
    docker rm -f fairing >/dev/null 2>&1 || true
    if docker inspect fairing-previous >/dev/null 2>&1; then
      docker rename fairing-previous fairing
      docker start fairing >/dev/null
    fi
  }
  trap rollback ERR
  run_fairing '$IMAGE'
  healthy=0
  for attempt in \$(seq 1 45); do
    state=\"\$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' fairing 2>/dev/null || true)\"
    if [[ \"\$state\" == healthy ]]; then healthy=1; break; fi
    if [[ \"\$state\" == unhealthy ]]; then break; fi
    sleep 2
  done
  if [[ \"\$healthy\" == 1 ]] && ! docker exec fairing python /app/deploy/fairing-runtime-smoke.py; then
    healthy=0
  fi
  if [[ \"\$healthy\" != 1 ]]; then
    docker logs --tail 100 fairing >&2 || true
    rollback
    exit 1
  fi
  install -o root -g root -m 0755 '$REMOTE_DIR/deploy/fairing-daily-run' /usr/local/sbin/fairing-daily-run
  install -o root -g root -m 0755 '$REMOTE_DIR/deploy/sync-fairing-cron' /usr/local/sbin/sync-fairing-cron
  cron_file=\"\$(mktemp /tmp/fairing-owner-cron.XXXXXX)\"
  trap 'rm -f \"\$cron_file\"' EXIT
  (crontab -u jieker -l 2>/dev/null || true) \\
    | grep -Ev '/home/jieker/sync_fairing_cron\.sh|/usr/local/sbin/sync-fairing-cron' \\
    >\"\$cron_file\" || true
  printf '%s\n' '* * * * * /usr/local/sbin/sync-fairing-cron' >>\"\$cron_file\"
  crontab -u jieker \"\$cron_file\"
  rm -f \"\$cron_file\"
  trap - EXIT
  runuser -u jieker -- /usr/local/sbin/sync-fairing-cron
  trap - ERR
  docker inspect fairing --format 'image={{.Config.Image}} revision={{index .Config.Labels \"org.opencontainers.image.revision\"}} state={{.State.Status}}/{{.State.Health.Status}} restarts={{.RestartCount}}'
"

echo "==> Fairing deployed from Git revision $SOURCE_REVISION"
