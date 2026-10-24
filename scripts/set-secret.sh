#!/usr/bin/env bash
# Save an API key as a GitHub Actions secret without it showing on screen, in shell
# history or on disk. Needs the gh CLI, logged in.
#   scripts/set-secret.sh                    # ANTHROPIC_API_KEY (Claude tie-break in CI)
#   scripts/set-secret.sh SEC_USER_AGENT
set -euo pipefail

name="${1:-ANTHROPIC_API_KEY}"
repo="LBB2005/formd-tracker"

read -rsp "Paste your $name and press Enter (input is hidden): " key
echo
key="$(printf '%s' "$key" | tr -d '[:space:]')"
if [ -z "$key" ]; then
  echo "Nothing pasted; secret unchanged." >&2
  exit 1
fi

printf '%s' "$key" | gh secret set "$name" -R "$repo"
echo "Saved $name to $repo secrets (ends in ...${key: -4})."
