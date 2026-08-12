FROM telegraf:latest

# telegraf:latest is Debian-based and ships without Python, but
# inputs.exec runs scripts/read_vedirect.py to parse the Victron VE.Direct
# stream. python3-serial (pyserial) is installed via apt so the container
# has no external network/pip dependency at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-serial \
    && rm -rf /var/lib/apt/lists/*

# ALPACA_BASE_URL (roof controller) is a .local mDNS hostname, not a static
# IP, since it's a safety-relevant reading (roof/shutter state) and a
# hardcoded IP would silently go stale if the device's DHCP lease changes.
# Telegraf ships as a static Go binary (CGO_ENABLED=0), so it never consults
# glibc/NSS or libnss-mdns for DNS - it only reads /etc/resolv.conf and
# /etc/hosts directly. mdns_resolve.py does the actual mDNS multicast query;
# refresh-mdns-hosts.sh runs it on a loop in the background (started by
# entrypoint-wrapper.sh) to keep /etc/hosts current. Needs
# network_mode: host in docker-compose.yml for multicast to reach the LAN.
COPY mdns_resolve.py /usr/local/bin/mdns_resolve.py
COPY refresh-mdns-hosts.sh /usr/local/bin/refresh-mdns-hosts.sh
COPY entrypoint-wrapper.sh /usr/local/bin/entrypoint-wrapper.sh
RUN chmod +x /usr/local/bin/mdns_resolve.py /usr/local/bin/refresh-mdns-hosts.sh /usr/local/bin/entrypoint-wrapper.sh

ENTRYPOINT ["/usr/local/bin/entrypoint-wrapper.sh"]
CMD ["telegraf"]
