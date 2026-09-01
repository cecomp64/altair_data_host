#!/bin/sh
# Resolves ALPACA_BASE_URL's hostname over mDNS before telegraf starts (so
# the first poll cycle doesn't fail), then keeps it refreshed in the
# background for the life of the container, then hands off to the image's
# normal entrypoint. See Dockerfile for why this exists.
set -eu

# The camera_snapshots volume (docker-compose.yml) is also mounted into the
# camera-web (nginx) container at /usr/share/nginx/html - on a brand-new
# volume, Docker seeds it from whichever image's directory content is
# mounted there *first*, which race-wise can be nginx's default
# index.html/50x.html rather than this container's empty /snapshots. They're
# harmless (camera-web just serves them alongside the real *.jpg files) but
# not real snapshots, so clear them if present. More importantly: a
# fresh/root-seeded volume is root:root, but read_cameras.py runs as the
# unprivileged `telegraf` user (same gosu drop the base image's own
# entrypoint.sh does for the telegraf process itself - confirmed via `ps
# aux` inside the container, same as the /dev/vcio_gencmd permission issue
# in the Dockerfile above) - without this chown, every write to /snapshots
# fails with EACCES.
mkdir -p /snapshots
rm -f /snapshots/index.html /snapshots/50x.html
chown telegraf:telegraf /snapshots

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
