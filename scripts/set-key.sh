#!/usr/bin/env bash
# Save an API key to .env without it showing on screen or in shell history.
#   scripts/set-key.sh                      # TYPESAFE_API_KEY (Jev)
#   scripts/set-key.sh ANTHROPIC_API_KEY
set -euo pipefail

name="${1:-TYPESAFE_API_KEY}"
env_file="$(cd "$(dirname "$0")/.." && pwd)/.env"

read -rsp "Paste your $name and press Enter (input is hidden): " key
echo
key="$(printf '%s' "$key" | tr -d '[:space:]')"
if [ -z "$key" ]; then
  echo "Nothing pasted; .env unchanged." >&2
  exit 1
fi

touch "$env_file"
chmod 600 "$env_file"
tmp="$(mktemp)"
grep -v "^${name}=" "$env_file" > "$tmp" || true
printf '%s=%s\n' "$name" "$key" >> "$tmp"
mv "$tmp" "$env_file"
chmod 600 "$env_file"

echo "Saved $name to .env (ends in ...${key: -4})."
