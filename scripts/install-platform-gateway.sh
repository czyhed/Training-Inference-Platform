#!/usr/bin/env bash
# Usage: install-platform-gateway.sh [nginx-conf-directory] [backup-directory]
# Install only cppt-platform.conf; never rewrite another application's gateway.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
template="$script_dir/../deploy/nginx/cppt-platform.production.conf"
config_dir="${1:-/etc/nginx/conf.d}"
backup_dir="${2:-/var/backups/cppt-platform-gateway}"
config="$config_dir/cppt-platform.conf"
command -v nginx >/dev/null
test -f "$template"
mkdir -p -- "$config_dir" "$backup_dir"
backup=""
if [[ -e "$config" ]]; then
    backup="$(mktemp "$backup_dir/cppt-platform.conf.XXXXXXXX")"
    cp -p -- "$config" "$backup"
fi
temporary="$(mktemp "$config_dir/.cppt-platform.XXXXXXXX")"
trap 'rm -f -- "$temporary"' EXIT
install -m 644 -- "$template" "$temporary"
mv -f -- "$temporary" "$config"
if nginx -t && nginx -s reload; then
    printf 'Installed cppt-platform gateway; backup=%s\n' "${backup:-none}"
else
    if [[ -n "$backup" ]]; then
        cp -p -- "$backup" "$config"
    else
        rm -f -- "$config"
    fi
    if ! nginx -t || ! nginx -s reload; then
        echo 'Original gateway restored on disk, but reload failed; inspect nginx before continuing.' >&2
    fi
    echo 'Gateway installation failed; original configuration restored.' >&2
    exit 1
fi
