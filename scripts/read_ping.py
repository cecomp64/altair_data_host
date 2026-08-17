#!/usr/bin/env python3
"""Pings a configurable, comma-separated list of hosts per network group and
emits Influx Line Protocol shaped like telegraf's own inputs.ping plugin
(same field names/semantics, including result_code == the ping subprocess's
own exit code: 0 = replies received, 1 = no reply, 2 = local error such as
an unresolvable host).

This goes through a script rather than inputs.ping directly because the host
list needs to be a variable-length, user-edited list in .env
(PING_TARGETS_INTERNET / PING_TARGETS_TAILSCALE) - telegraf's own env var
substitution can fill in a scalar inside a quoted TOML array element (e.g.
urls = ["$HOST"]), confirmed via `telegraf --test`, but can't expand a single
variable into a variable number of array elements under the strict env
handling that's been the default since telegraf 1.38 (non-strict mode would
allow that, but turning it off - via a --non-strict-env-handling flag on the
whole telegraf process - would also silently weaken typo-checking everywhere
else in telegraf.conf, which is worse than one extra script).

Pings run in parallel (one thread per host) so wall-clock time stays roughly
one ping-count's worth regardless of how many hosts are configured.
"""
import concurrent.futures
import os
import re
import subprocess
import sys

PING_COUNT = 3
PING_TIMEOUT_S = 2

SUMMARY_RE = re.compile(r"(\d+) packets transmitted, (\d+) (?:packets )?received.*?([\d.]+)% packet loss")
RTT_RE = re.compile(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms")


def ping_host(network, host):
    try:
        proc = subprocess.run(
            ["ping", "-c", str(PING_COUNT), "-W", str(PING_TIMEOUT_S), host],
            capture_output=True, text=True,
            timeout=PING_COUNT * PING_TIMEOUT_S + 5,
        )
        stdout, result_code = proc.stdout, proc.returncode
    except subprocess.TimeoutExpired:
        stdout, result_code = "", 2

    fields = {}
    summary = SUMMARY_RE.search(stdout)
    if summary:
        fields["packets_transmitted"] = f"{summary.group(1)}i"
        fields["packets_received"] = f"{summary.group(2)}i"
        fields["percent_packet_loss"] = summary.group(3)

    rtt = RTT_RE.search(stdout)
    if rtt:
        minimum, average, maximum, stddev = rtt.groups()
        fields["minimum_response_ms"] = minimum
        fields["average_response_ms"] = average
        fields["maximum_response_ms"] = maximum
        fields["standard_deviation_ms"] = stddev

    fields["result_code"] = f"{result_code}i"

    field_str = ",".join(f"{k}={v}" for k, v in fields.items())
    return f"ping,network={network},url={host} {field_str}"


def targets():
    groups = {
        "internet": os.environ.get("PING_TARGETS_INTERNET", ""),
        "tailscale": os.environ.get("PING_TARGETS_TAILSCALE", ""),
    }
    for network, raw in groups.items():
        for host in raw.split(","):
            host = host.strip()
            if host:
                yield network, host


if __name__ == "__main__":
    jobs = list(targets())
    if not jobs:
        sys.exit(0)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        lines = pool.map(lambda job: ping_host(*job), jobs)
        for line in lines:
            print(line)
