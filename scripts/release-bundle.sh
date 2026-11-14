#!/usr/bin/env bash
# Compatibility entry point: Drone now clones directly from Gitea.
# Push only the requested tag; no bundle or production SSH is needed.
# Usage: scripts/release-bundle.sh v3.0.124
set -euo pipefail

tag="${1:?usage: release-bundle.sh <tag>}"
remote="origin"

git_command=(git)
if [[ -r /proc/sys/kernel/osrelease ]] \
  && grep -qi microsoft /proc/sys/kernel/osrelease \
  && command -v git.exe >/dev/null 2>&1; then
  # Reuse Windows Git Credential Manager when the script runs inside WSL.
  git_command=(git.exe)
fi

"${git_command[@]}" rev-parse -q --verify "refs/tags/${tag}^{commit}" >/dev/null \
  || { echo "tag ${tag} does not exist locally; create it first" >&2; exit 1; }

# Reject GitHub or multi-destination remotes that could publish elsewhere.
push_urls="$("${git_command[@]}" remote get-url --push --all "$remote" | tr -d '\r')"
if [[ -z "$push_urls" || "$push_urls" == *$'\n'* || "$push_urls" == *github.com* ]]; then
  echo "origin must push only to the Gitea repository" >&2
  exit 1
fi

"${git_command[@]}" push "${remote}" "refs/tags/${tag}"
echo "tag ${tag} pushed to Gitea; follow the Drone pipeline for this project"
