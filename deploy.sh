#!/usr/bin/env bash
# Agants deploy script.
#
# Usage:
#   ./deploy.sh            — sync (additive, no --delete) + restart game server
#   ./deploy.sh --prune    — sync WITH --delete (mirror remote to repo); combine w/ any mode
#   ./deploy.sh --full     — sync + restart game server + restart cloudflared
#   ./deploy.sh --install  — first-time: install deps, enable systemd services
#   ./deploy.sh --pages    — deploy frontend to Cloudflare Pages + update backend URL
#   ./deploy.sh --url      — print the current public tunnel URL (from remote logs)
#
# DNS note: CLOUDFLARE_API_TOKEN has tunnel-only scope — it cannot create DNS records.
#   Any new subdomain (CNAMEs for tunnel ingress or MCP) must be added manually via
#   the Cloudflare dashboard or by granting DNS:Edit scope to the token.

set -e

REMOTE_HOST="192.168.1.100"
REMOTE_PORT="2222"
REMOTE_USER="deshiel"
REMOTE_DIR="~/projects/agants"
SSH_KEY="$HOME/.ssh/id_ed25519_desktop"
SSH="ssh -p $REMOTE_PORT -i $SSH_KEY $REMOTE_USER@$REMOTE_HOST"
RSYNC_SSH="ssh -p $REMOTE_PORT -i $SSH_KEY"
CF_ACCOUNT="1d3c37467a2f0a2128f79d23c842e932"
CF_PAGES_PROJECT="agants"
# CF_TOKEN must be set in environment or .env (never committed)
CF_TOKEN="${CLOUDFLARE_API_TOKEN:-}"

STABLE_BACKEND="https://api.datthemaster.com/agants"

# --prune (anywhere in args) opts in to `rsync --delete` (mirror remote to repo exactly).
# Default sync is additive so a deploy can never wipe remote-only state.
PRUNE=0
ARGS=()
for a in "$@"; do
  if [[ "$a" == "--prune" ]]; then PRUNE=1; else ARGS+=("$a"); fi
done
set -- "${ARGS[@]}"
MODE="${1:-}"

_sync() {
  # The remote ~/projects/agants is NOT a git checkout, so `rsync --delete` would wipe
  # any remote-only state that isn't in this repo (this clobbered routing config in s51).
  # Default is therefore ADDITIVE (no --delete). Pass `--prune` to opt in to --delete
  # when you deliberately want the remote to mirror the repo exactly.
  local DELETE_FLAG=""
  if [[ "$PRUNE" == "1" ]]; then
    DELETE_FLAG="--delete"
    echo "==> PRUNE mode: remote files not in this repo WILL be deleted."
  fi
  echo "==> Syncing code to $REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR …"
  rsync -az $DELETE_FLAG \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.git' \
    --exclude 'logs/' \
    --exclude 'data/' \
    --exclude '.env' \
    --exclude 'node_modules' \
    -e "$RSYNC_SSH" \
    . "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR"
}

if [[ "$MODE" == "--url" ]]; then
  echo "$STABLE_BACKEND"
  exit 0
fi

if [[ "$MODE" == "--pages" ]]; then
  if [[ -z "$CF_TOKEN" ]]; then
    echo "ERROR: set CLOUDFLARE_API_TOKEN in your environment" >&2
    exit 1
  fi
  # Stable backend URL — no need to fetch dynamic tunnel URL anymore.
  echo "==> Backend: $STABLE_BACKEND"
  # CF Pages Functions read env vars from Worker bindings baked in at deploy time,
  # not from dashboard env vars at runtime. Inject via wrangler.toml [vars] before
  # deploying, then restore the placeholder afterwards.
  FRONTEND_DIR="$(cd "$(dirname "$0")/frontend" && pwd)"
  WRANGLER_TOML="$FRONTEND_DIR/wrangler.toml"
  WRANGLER_BACKUP=$(cat "$WRANGLER_TOML")
  cat > "$WRANGLER_TOML" << TOML
name = "agants"
pages_build_output_dir = "."
compatibility_date = "2024-09-23"

[vars]
AGANTS_BACKEND = "${STABLE_BACKEND}"
AGANTS_AUTH_URL = "https://agants-auth.hermesagent424.workers.dev"
AGANTS_ADMIN = "false"
TOML
  echo "==> Deploying frontend/  to Cloudflare Pages …"
  cd "$FRONTEND_DIR"
  CLOUDFLARE_API_TOKEN="$CF_TOKEN" CLOUDFLARE_ACCOUNT_ID="$CF_ACCOUNT" npx wrangler pages deploy . \
    --project-name "$CF_PAGES_PROJECT" --branch main --commit-dirty=true
  # Restore placeholder wrangler.toml (URL must not be committed)
  echo "$WRANGLER_BACKUP" > "$WRANGLER_TOML"
  echo "==> Pages deploy complete. Site: https://agants.datthemaster.com"
  exit 0
fi

_sync

echo "==> Restarting agants game server …"
$SSH "systemctl --user restart agants.service && systemctl --user status agants.service --no-pager -l | head -12"

if [[ "$MODE" == "--full" ]]; then
  echo "==> Restarting cloudflared tunnel …"
  $SSH "systemctl --user restart cloudflared-agants.service && sleep 3 && systemctl --user status cloudflared-agants.service --no-pager -l | head -12"
fi

if [[ "$MODE" == "--install" ]]; then
  echo "==> First-time install …"
  $SSH bash << 'REMOTE'
    set -e
    [ -f ~/projects/agants/.env ] || cp ~/projects/agants/.env.example ~/projects/agants/.env
    loginctl enable-linger "$USER" 2>/dev/null || true
    mkdir -p ~/.config/systemd/user
    cp ~/projects/agants/deploy/agants.service ~/.config/systemd/user/agants.service
    cp ~/projects/agants/deploy/cloudflared-agants.service ~/.config/systemd/user/cloudflared-agants.service
    systemctl --user daemon-reload
    systemctl --user enable agants.service cloudflared-agants.service
    echo "Services enabled. Run: systemctl --user start agants cloudflared-agants"
REMOTE
fi

echo "==> Done."
