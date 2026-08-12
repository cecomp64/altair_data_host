#!/bin/sh
# Keeps /etc/hosts entries for the given .local hostname(s) current by
# re-resolving over mDNS on a loop. Telegraf's static Go binary reads
# /etc/hosts directly but never consults NSS/mDNS, so this is what makes
# ALPACA_BASE_URL's hostname resolve at all - see Dockerfile for why.
set -eu

INTERVAL="${MDNS_REFRESH_INTERVAL:-60}"

while true; do
    for host in "$@"; do
        ip="$(/usr/local/bin/mdns_resolve.py "$host" 2>/dev/null || true)"
        if [ -n "$ip" ]; then
            tmp="$(mktemp /etc/hosts.XXXXXX)"
            grep -v "[[:space:]]$host\$" /etc/hosts > "$tmp" 2>/dev/null || true
            echo "$ip $host" >> "$tmp"
            cat "$tmp" > /etc/hosts
            rm -f "$tmp"
        else
            echo "refresh-mdns-hosts: could not resolve $host this cycle, keeping previous entry" >&2
        fi
    done
    sleep "$INTERVAL"
done
