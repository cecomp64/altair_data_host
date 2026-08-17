FROM telegraf:latest

# telegraf:latest is Debian-based and ships without Python, but
# inputs.exec runs scripts/read_vedirect.py to parse the Victron VE.Direct
# stream. python3-serial (pyserial) is installed via apt so the container
# has no external network/pip dependency at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-serial python3-pip python3-ephem \
    && rm -rf /var/lib/apt/lists/*

# Pi power telemetry: scripts/read_pi_power.py shells out to vcgencmd for
# under-voltage/throttle status (all Pi models) and PMIC rail voltage/current
# (Pi 5 only). vcgencmd (libraspberrypi-bin) isn't in Debian's own repos -
# it's Raspberry Pi OS-specific - so this adds Raspberry Pi Ltd's apt repo
# (per their own install instructions) rather than trying to vendor a
# compiled binary. It talks to the VideoCore/RP1 firmware over
# /dev/vcio_gencmd, which is already visible in this container via the
# docker-compose.yml /dev:/dev mount - no extra device config needed here.
# Best-effort: this repo also targets non-Pi "Mini PC" hosts (per the
# README's own framing) where this package won't exist/apply, so a failed
# install here doesn't fail the whole image build - read_pi_power.py checks
# for the binary itself and emits nothing if it's missing.
RUN curl -fsSL https://archive.raspberrypi.com/debian/raspberrypi.gpg.key | gpg --dearmor -o /usr/share/keyrings/raspberrypi-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/raspberrypi-archive-keyring.gpg] https://archive.raspberrypi.com/debian/ bookworm main" > /etc/apt/sources.list.d/raspi.list \
    && apt-get update \
    && (apt-get install -y --no-install-recommends libraspberrypi-bin || echo "libraspberrypi-bin unavailable - not Raspberry Pi hardware, skipping") \
    && rm -rf /var/lib/apt/lists/*

# telegraf's own process drops from root to the unprivileged `telegraf` user
# (confirmed: `ps aux` inside the container shows the telegraf process as
# uid 999, not root) - the official image's entrypoint does this even
# though docker-compose.yml's privileged:true means an interactive `docker
# exec` (which defaults to root) doesn't see the same restriction. /dev/vcio_gencmd
# is root:video 0660, so vcgencmd silently failed ("no usable output") when
# telegraf itself called it via inputs.exec despite working fine under an
# interactive root shell - confirmed by testing both. video's gid (44) is
# already baked into this base Debian image, so this is enough.
RUN usermod -aG video telegraf

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
