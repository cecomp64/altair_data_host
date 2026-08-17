#!/usr/bin/env python3
"""Pulls Starlink dish status/alerts and recent power draw over the dish's
local gRPC API (192.168.100.1:9200 by default - the same one the mobile app
uses) and emits Influx Line Protocol. Wraps the vendored
docker/telegraf/third_party/starlink_grpc.py (see that file for why it's vendored
rather than pip-installed - it isn't on PyPI).

Field groups pulled (see starlink_grpc.py's module docstring for the full
schema each call can return - only a subset is used here):
  - status_data() group 0: link state, uptime, latency/throughput,
    obstruction fraction, GPS satellite count.
  - status_data() group 2: the ~20 boolean alert flags (thermal throttle,
    roaming, mast not vertical, etc.) - this is the "events" data. Also
    folded into a single alert_active convenience field so Grafana doesn't
    need to OR twenty booleans together for a "something's wrong" tile.
  - history_stats(N) group 5/6: power draw and bandwidth usage averaged over
    the last N one-second samples the dish keeps internally. N is set to
    match this script's own polling interval (STARLINK_POLL_INTERVAL_S) so
    each sample window abuts the previous one with neither gap nor overlap.
"""
import os
import sys

import starlink_grpc

DISH_ADDR = os.environ.get("STARLINK_DISH_ADDR", "192.168.100.1:9200")
POLL_INTERVAL_S = int(os.environ.get("STARLINK_POLL_INTERVAL_S", "60"))


def fmt_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return f'"{v}"'
    return str(v)


def line_protocol(measurement, fields):
    field_str = ",".join(f"{k}={fmt_value(v)}" for k, v in fields.items() if v is not None)
    return f"{measurement} {field_str}"


def main():
    ctx = starlink_grpc.ChannelContext(target=DISH_ADDR)

    general, _obstruction_detail, alerts = starlink_grpc.status_data(context=ctx)
    _general, _ping_drop, _run_length, _ping_stats, _load_buckets, usage, power = \
        starlink_grpc.history_stats(POLL_INTERVAL_S, context=ctx)

    fields = {
        "state": general["state"],
        "uptime_s": general["uptime"],
        "pop_ping_latency_ms": general["pop_ping_latency_ms"],
        "downlink_throughput_bps": general["downlink_throughput_bps"],
        "uplink_throughput_bps": general["uplink_throughput_bps"],
        "fraction_obstructed": general["fraction_obstructed"],
        "currently_obstructed": general["currently_obstructed"],
        "gps_sats": general["gps_sats"],
        "power_latest_w": power["latest_power"],
        "power_mean_w": power["mean_power"],
        "power_min_w": power["min_power"],
        "power_max_w": power["max_power"],
        "energy_wh": None if power["total_energy"] is None else power["total_energy"] * 1000.0,
        "download_bytes": usage["download_usage"],
        "upload_bytes": usage["upload_usage"],
    }
    fields.update(alerts)
    fields["alert_active"] = any(v for k, v in alerts.items() if k.startswith("alert_"))

    print(line_protocol("starlink", fields))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.stderr.write(f"Error reading Starlink status: {e}\n")
        sys.exit(1)
