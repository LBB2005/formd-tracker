#!/usr/bin/env bash
# Save an API key as a GitHub Actions secret. Opens a blank text file in TextEdit; paste
# the key, save (Cmd+S), and the script sends it to GitHub and deletes the file. The file
# lives in a private temp folder outside the repo, so it can't be committed.
# Needs the gh CLI, logged in.
#   scripts/set-secret.sh                    # ANTHROPIC_API_KEY (Claude tie-break in CI)
#   scripts/set-secret.sh SEC_USER_AGENT
set -euo pipefail

name="${1:-ANTHROPIC_API_KEY}"
repo="LBB2005/formd-tracker"

dir="$(mktemp -d)"
chmod 700 "$dir"
file="$dir/paste-$name-here.txt"
: > "$file"
chmod 600 "$file"
trap 'rm -rf "$dir"' EXIT

open -e "$file"
echo "TextEdit is open. Paste your $name, then save with Cmd+S. Waiting up to 15 minutes..."

key=""
for _ in $(seq 450); do
  key="$(tr -d '[:space:]' < "$file")"
  [ -n "$key" ] && break
  sleep 2
done
if [ -z "$key" ]; then
  echo "Nothing saved; secret unchanged." >&2
  exit 1
fi

printf '%s' "$key" | gh secret set "$name" -R "$repo"
echo "Saved $name to $repo secrets (ends in ...${key: -4}). The text file is deleted; close TextEdit without saving."
