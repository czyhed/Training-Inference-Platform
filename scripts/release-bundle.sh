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

# Reject old multi-destination remotes that could also publish to GitHub.
push_urls="$("${git_command[@]}" remote get-url --push --all "$remote" | tr -d '\r')"
case "$push_urls" in
  http://git.leiziapi.cn:3003/leifengyang/cppt-platform.git|ssh://git@git.leiziapi.cn:2222/leifengyang/cppt-platform.git) ;;
  *) echo "origin must push only to the Gitea cppt-platform repository" >&2; exit 1 ;;
esac

"${git_command[@]}" push "${remote}" "refs/tags/${tag}"
echo "tag ${tag} pushed to Gitea; follow http://drone.leiziapi.cn:4400/leifengyang/cppt-platform"
