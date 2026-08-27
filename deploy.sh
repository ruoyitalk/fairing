#!/usr/bin/env bash
# Build and replace Fairing from the Git checkout. Runtime data and secrets stay
# on the homeserver; the image and its OCI revision are reproducible from Git.

set -euo pipefail

DEPLOY_HOST="${FAIRING_DEPLOY_HOST:-homeserver-cf}"
SSH_KEY="${FAIRING_DEPLOY_SSH_KEY:-}"
REMOTE_DIR="${FAIRING_REMOTE_DIR:-/opt/docker/fairing_git}"
REMOTE_ENV_FILE="${FAIRING_REMOTE_ENV_FILE:-/opt/docker/fairing/.env}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_REVISION="$(git -C "$SCRIPT_DIR" rev-parse --short=12 HEAD)"
if [[ -n "$(git -C "$SCRIPT_DIR" status --porcelain)" ]]; then
  SOURCE_REVISION="${SOURCE_REVISION}-dirty"
fi
IMAGE="fairing:${SOURCE_REVISION}"

# Bash 3.2 expands empty arrays as unset under `set -u`. BatchMode is also the
# correct deployment boundary, so keep the arrays non-empty on every platform.
SSH_ARGS=( -o BatchMode=yes )
RSYNC_SSH="ssh -o BatchMode=yes"
if [[ -n "$SSH_KEY" ]]; then
  [[ -r "$SSH_KEY" ]] || { echo "unreadable FAIRING_DEPLOY_SSH_KEY" >&2; exit 1; }
  SSH_ARGS+=( -i "$SSH_KEY" -o IdentitiesOnly=yes )
  printf -v SSH_KEY_QUOTED '%q' "$SSH_KEY"
  RSYNC_SSH+=" -i $SSH_KEY_QUOTED -o IdentitiesOnly=yes"
fi

echo "==> Syncing Fairing revision $SOURCE_REVISION"
ssh "${SSH_ARGS[@]}" "$DEPLOY_HOST" "install -d -m 0755 '$REMOTE_DIR'"
rsync -rlptDz --delete \
  --exclude='.git/' --exclude='.env' --exclude='.venv/' \
  --exclude='__pycache__/' --exclude='*.pyc' --exclude='.pytest_cache/' \
  --exclude='.scoring_store.jsonl' --exclude='seen_urls.json' \
  --exclude='scoring_store.jsonl' --exclude='title_index.jsonl' \
  --exclude='rate_pending.json' --exclude='payload_queue.json' \
  --exclude='feed_errors.json' --exclude='last_run_time' \
  --exclude='config/sources.local.yaml' \
  -e "$RSYNC_SSH" "$SCRIPT_DIR/" "$DEPLOY_HOST:$REMOTE_DIR/"

echo "==> Building and replacing Fairing"
ssh "${SSH_ARGS[@]}" "$DEPLOY_HOST" "
  set -Eeuo pipefail
  test -r '$REMOTE_ENV_FILE'
  cd '$REMOTE_DIR'
  docker build --build-arg SOURCE_REVISION='$SOURCE_REVISION' -t '$IMAGE' .
  docker run --rm -v '$REMOTE_DIR':/source:ro -w /source '$IMAGE' pytest -q
  previous_image=\"\$(docker inspect -f '{{.Config.Image}}' fairing 2>/dev/null || true)\"
  run_fairing() {
    local image=\"\$1\"
    docker run -d \\
      --name fairing \\
      --restart unless-stopped \\
      --label com.centurylinklabs.watchtower.enable=false \\
      --security-opt no-new-privileges:true \\
      --network docker_proxy \\
      --gpus all \\
      -p 8501:8501 \\
      --env-file '$REMOTE_ENV_FILE' \\
      -e DATA_DIR=/data/fairing \\
      -e FAIRING_ROOT=/fairing \\
      -e PAYLOAD_ROOT=/payload \\
      -e NEWS_DIR=/data/news \\
      -e KNOWLEDGE_DIR=/data/ruoyi_download \\
      -e PAYLOAD_DATA_DIR=/data/payload \\
      -e QDRANT_URL=http://qdrant:6333 \\
      -v /data/fairing:/data/fairing \\
      -v /data/news:/data/news \\
      -v /data/ruoyi_download:/data/ruoyi_download \\
      -v /data/data_bak:/data/data_bak \\
      -v /data/data_bak/payload:/data/payload \\
      -v '$REMOTE_DIR':/fairing:ro \\
      -v /opt/docker/payload_git:/payload:ro \\
      -v /opt/docker/fairing/config:/app/config:ro \\
      -v /opt/docker/hf_cache:/root/.cache/huggingface \\
      \"\$image\" >/dev/null
  }
  docker stop fairing >/dev/null 2>&1 || true
  docker rm fairing >/dev/null 2>&1 || true
  run_fairing '$IMAGE'
  healthy=0
  for attempt in \$(seq 1 45); do
    state=\"\$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' fairing 2>/dev/null || true)\"
    if [[ \"\$state\" == healthy ]]; then healthy=1; break; fi
    if [[ \"\$state\" == unhealthy ]]; then break; fi
    sleep 2
  done
  if [[ \"\$healthy\" != 1 ]]; then
    docker logs --tail 100 fairing >&2 || true
    docker stop fairing >/dev/null 2>&1 || true
    docker rm fairing >/dev/null 2>&1 || true
    if [[ -n \"\$previous_image\" ]]; then run_fairing \"\$previous_image\"; fi
    exit 1
  fi
  docker inspect fairing --format 'image={{.Config.Image}} revision={{index .Config.Labels \"org.opencontainers.image.revision\"}} state={{.State.Status}}/{{.State.Health.Status}} restarts={{.RestartCount}}'
"

echo "==> Fairing deployed from Git revision $SOURCE_REVISION"
