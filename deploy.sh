#!/usr/bin/env bash
# Agants deploy script.
#
# Usage:
#   ./deploy.sh            — sync + restart game server
#   ./deploy.sh --full     — sync + restart game server + restart cloudflared
#   ./deploy.sh --install  — first-time: install deps, enable systemd services
#   ./deploy.sh --pages    — deploy frontend to Cloudflare Pages + update backend URL
#   ./deploy.sh --url      — print the current public tunnel URL (from remote logs)

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

MODE="${1:-}"

_get_tunnel_url() {
  $SSH "bash ~/projects/agants/tunnel-url.sh 2>/dev/null"
}

_sync() {
  echo "==> Syncing code to $REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR …"
  rsync -az --delete \
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
  URL=$(_get_tunnel_url)
  echo "$URL"
  exit 0
fi

if [[ "$MODE" == "--pages" ]]; then
  if [[ -z "$CF_TOKEN" ]]; then
    echo "ERROR: set CLOUDFLARE_API_TOKEN in your environment" >&2
    exit 1
  fi
  # Fetch current tunnel URL from remote and update Pages env var
  echo "==> Fetching current tunnel URL from remote …"
  TUNNEL_URL=$(_get_tunnel_url)
  if [[ -z "$TUNNEL_URL" ]]; then
    echo "ERROR: could not get tunnel URL — is cloudflared-agants.service running?" >&2
    exit 1
  fi
  echo "    Tunnel URL: $TUNNEL_URL"
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
AGANTS_BACKEND = "${TUNNEL_URL}"
AGANTS_AUTH_URL = ""
AGANTS_ADMIN = "false"
TOML
  echo "==> Deploying frontend/  to Cloudflare Pages …"
  cd "$FRONTEND_DIR"
  CLOUDFLARE_API_TOKEN="$CF_TOKEN" CLOUDFLARE_ACCOUNT_ID="$CF_ACCOUNT" npx wrangler pages deploy . \
    --project-name "$CF_PAGES_PROJECT" --branch main --commit-dirty=true
  # Restore placeholder wrangler.toml (URL must not be committed)
  echo "$WRANGLER_BACKUP" > "$WRANGLER_TOML"
  echo "==> Pages deploy complete. Site: https://${CF_PAGES_PROJECT}.pages.dev"
  exit 0
fi

_sync

echo "==> Restarting agants game server …"
$SSH "systemctl --user restart agants.service && systemctl --user status agants.service --no-pager -l | head -12"

if [[ "$MODE" == "--full" ]]; then
  echo "==> Restarting cloudflared tunnel …"
  $SSH "systemctl --user restart cloudflared-agants.service"
  sleep 6
  URL=$(_get_tunnel_url)
  echo "    New tunnel URL: $URL"
  echo "    Run ./deploy.sh --pages to push the new URL to Cloudflare Pages."
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
