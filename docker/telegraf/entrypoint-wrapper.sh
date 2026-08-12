#!/bin/sh
# Resolves ALPACA_BASE_URL's hostname over mDNS before telegraf starts (so
# the first poll cycle doesn't fail), then keeps it refreshed in the
# background for the life of the container, then hands off to the image's
# normal entrypoint. See Dockerfile for why this exists.
set -eu

if [ -n "${ALPACA_BASE_URL:-}" ]; then
    ALPACA_HOST="$(python3 -c "import os,urllib.parse as u; print(u.urlparse(os.environ['ALPACA_BASE_URL']).hostname or '')")"
else
    ALPACA_HOST=""
fi

case "$ALPACA_HOST" in
    *.local)
        echo "entrypoint-wrapper: resolving $ALPACA_HOST over mDNS..."
        for _ in 1 2 3 4 5; do
            ip="$(/usr/local/bin/mdns_resolve.py "$ALPACA_HOST" 2>/dev/null || true)"
            if [ -n "$ip" ]; then
                echo "entrypoint-wrapper: $ALPACA_HOST -> $ip"
                echo "$ip $ALPACA_HOST" >> /etc/hosts
                break
            fi
            sleep 2
        done
        /usr/local/bin/refresh-mdns-hosts.sh "$ALPACA_HOST" &
        ;;
    "")
        ;;
    *)
        echo "entrypoint-wrapper: ALPACA_BASE_URL host '$ALPACA_HOST' is not .local, skipping mDNS refresh"
        ;;
esac

exec /usr/bin/tini -- /entrypoint.sh "$@"
