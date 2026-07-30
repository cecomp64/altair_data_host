FROM telegraf:latest

# telegraf:latest is Debian-based and ships without Python, but
# inputs.exec runs scripts/read_vedirect.py to parse the Victron VE.Direct
# stream. python3-serial (pyserial) is installed via apt so the container
# has no external network/pip dependency at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-serial \
    && rm -rf /var/lib/apt/lists/*
