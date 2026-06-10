#!/usr/bin/env bash
# Print the current public tunnel URL (trycloudflare.com).
# Run this after the cloudflared-agants service starts to get the backend URL
# for updating Cloudflare Pages AGANTS_BACKEND or local config.js.
LOGFILE="$HOME/projects/agants/logs/cloudflared.log"
if [[ ! -f "$LOGFILE" ]]; then
  echo "No cloudflared log found at $LOGFILE" >&2
  exit 1
fi
URL=$(grep -o 'https://[^ ]*trycloudflare\.com' "$LOGFILE" | tail -1)
if [[ -z "$URL" ]]; then
  echo "URL not found in log yet — is cloudflared-agants.service running?" >&2
  exit 1
fi
echo "$URL"
