FROM telegraf:latest

# telegraf:latest is Debian-based and ships without Python, but
# inputs.exec runs scripts/read_vedirect.py to parse the Victron VE.Direct
# stream. python3-serial (pyserial) is installed via apt so the container
# has no external network/pip dependency at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-serial python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Starlink: scripts/read_starlink.py talks to the dish's local gRPC API via
# the vendored starlink_grpc.py (see third_party/starlink_grpc.py for why it's
# committed rather than fetched at build time) and its grpcio/protobuf/yagrc
# deps (requirements-starlink.txt) - pip rather than apt, see that file for
# why. --break-system-packages overrides Debian's PEP 668 guard against
# pip writes to the system interpreter; fine here since this container runs
# nothing else Python-based that pip could conflict with.
COPY third_party/starlink_grpc.py requirements-starlink.txt /opt/starlink/
ENV PYTHONPATH=/opt/starlink
RUN pip3 install --no-cache-dir --break-system-packages -r /opt/starlink/requirements-starlink.txt

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
