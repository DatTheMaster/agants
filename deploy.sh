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
REMOTE_DIR="~/projects/swarm-wars"
SSH_KEY="$HOME/.ssh/id_ed25519_desktop"
SSH="ssh -p $REMOTE_PORT -i $SSH_KEY $REMOTE_USER@$REMOTE_HOST"
RSYNC_SSH="ssh -p $REMOTE_PORT -i $SSH_KEY"
CF_ACCOUNT="1d3c37467a2f0a2128f79d23c842e932"
CF_PAGES_PROJECT="agants"
# CF_TOKEN must be set in environment or .env (never committed)
CF_TOKEN="${CLOUDFLARE_API_TOKEN:-}"

MODE="${1:-}"

_get_tunnel_url() {
  $SSH "bash ~/projects/swarm-wars/tunnel-url.sh 2>/dev/null"
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
  echo "==> Updating AGANTS_BACKEND on Cloudflare Pages …"
  RESULT=$(curl -s -X PATCH \
    "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT}/pages/projects/${CF_PAGES_PROJECT}" \
    -H "Authorization: Bearer ${CF_TOKEN}" \
    -H "Content-Type: application/json" \
    --data '{
      "deployment_configs": {
        "production": {
          "env_vars": {
            "AGANTS_BACKEND": {"value": "'"$TUNNEL_URL"'"},
            "AGANTS_ADMIN": {"value": "false"}
          }
        }
      }
    }')
  if echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('success') else 1)" 2>/dev/null; then
    echo "    Env var updated."
  else
    echo "    WARNING: env var update may have failed. Response: $RESULT"
  fi
  echo "==> Deploying frontend/  to Cloudflare Pages …"
  cd "$(dirname "$0")/frontend"
  CLOUDFLARE_API_TOKEN="$CF_TOKEN" npx wrangler pages deploy . \
    --project-name "$CF_PAGES_PROJECT" --branch main --commit-dirty=true
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
    [ -f ~/projects/swarm-wars/.env ] || cp ~/projects/swarm-wars/.env.example ~/projects/swarm-wars/.env
    loginctl enable-linger "$USER" 2>/dev/null || true
    mkdir -p ~/.config/systemd/user
    cp ~/projects/swarm-wars/deploy/agants.service ~/.config/systemd/user/agants.service
    cp ~/projects/swarm-wars/deploy/cloudflared-agants.service ~/.config/systemd/user/cloudflared-agants.service
    systemctl --user daemon-reload
    systemctl --user enable agants.service cloudflared-agants.service
    echo "Services enabled. Run: systemctl --user start agants cloudflared-agants"
REMOTE
fi

echo "==> Done."
