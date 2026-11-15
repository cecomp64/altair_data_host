#!/usr/bin/env python3
"""Publishes this station's weather to CWOP (Citizen Weather Observing
Program) as an APRS-IS weather packet, on a fixed interval.

Data comes from the `weather_station` measurement already in InfluxDB (see
read_ecowitt.py / README §7), so this never talks to the station directly.
findU (and MADIS/NOAA) all read CWOP's own APRS-IS network - there's no
separate findU upload, so posting to cwop.aprs.net is what makes the
station show up on findU.

Stdlib only (no venv needed). `--dry-run` builds and prints the packet
without connecting; `--once` sends one packet and exits.
"""
import argparse
import csv
import io
import logging
import math
import os
import signal
import socket
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

log = logging.getLogger("cwop")

INFLUX_URL = os.environ.get("INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = os.environ.get("INFLUXDB_ADMIN_TOKEN", "")
INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "solar")
BUCKET_WEATHER = os.environ.get("INFLUXDB_BUCKET_WEATHER", "weather")
BUCKET_SYSTEM = os.environ.get("INFLUXDB_BUCKET_SYSTEM", "system")
STATION = os.environ.get("WEATHER_STATION_NAME", "primary")

CWOP_ID = os.environ.get("CWOP_ID", "").strip().upper()
# CWOP asks for no more than one report per 5 min per station; 10 is typical.
MIN_INTERVAL = 300
INTERVAL = max(MIN_INTERVAL, int(os.environ.get("CWOP_INTERVAL", "600")))
SERVERS = [s.strip() for s in os.environ.get(
    "CWOP_SERVERS", "cwop.aprs.net:14580,cwop5.aprs.net:14580").split(",") if s.strip()]
# Don't report data older than this - better to skip a cycle than to
# publish a stale reading under a fresh timestamp.
MAX_AGE_S = int(os.environ.get("CWOP_MAX_AGE", "600"))
# Set to 1 if the station console's relative pressure is already calibrated
# to sea level (the default assumes it's raw station pressure).
PRESSURE_IS_SEA_LEVEL = os.environ.get("CWOP_PRESSURE_IS_SEA_LEVEL", "0") == "1"
SOFTWARE_TAG = "eAltair"  # APRS "software type" suffix: free text after 'e'

_stop = False


def influx_query(flux):
    """Returns [{field, value, time}, ...] from an annotated-CSV Flux result."""
    url = f"{INFLUX_URL}/api/v2/query?org={urllib.parse.quote(INFLUX_ORG)}"
    req = urllib.request.Request(
        url, data=flux.encode(), method="POST",
        headers={"Authorization": f"Token {INFLUX_TOKEN}",
                 "Content-Type": "application/vnd.flux", "Accept": "application/csv"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        text = resp.read().decode()
    rows = []
    for table in text.split("\r\n\r\n"):
        lines = [l for l in table.splitlines() if l and not l.startswith("#")]
        if len(lines) < 2:
            continue
        for rec in csv.DictReader(io.StringIO("\n".join(lines))):
            if rec.get("_value") not in (None, ""):
                rows.append({"field": rec["_field"], "value": float(rec["_value"]),
                             "time": rec["_time"]})
    return rows


def _base(range_start):
    return (f'from(bucket: "{BUCKET_WEATHER}") |> range(start: {range_start}) '
            f'|> filter(fn: (r) => r._measurement == "weather_station" and r.station == "{STATION}")')


def collect():
    """Gathers everything the packet needs. Missing values are None."""
    latest = {}
    ages = []
    flux = (_base("-15m") + ' |> filter(fn: (r) => r._field == "temp_f" or r._field == "humidity_pct" '
            'or r._field == "pressure_inhg" or r._field == "solar_radiation_wm2" '
            'or r._field == "rain_daily_in") |> last()')
    now = datetime.now(timezone.utc)
    for row in influx_query(flux):
        latest[row["field"]] = row["value"]
        t = datetime.fromisoformat(row["time"].replace("Z", "+00:00"))
        ages.append((now - t).total_seconds())
    if "temp_f" not in latest or (ages and min(ages) > MAX_AGE_S):
        raise RuntimeError("no fresh weather data in InfluxDB (station offline?)")

    # Wind: CWOP wants a ~1-2 min sustained value, so average the last two
    # minutes. Direction must be a vector mean (350 and 10 degrees -> 0, not 180).
    spd, dirs = [], []
    wind = influx_query(_base("-2m") + ' |> filter(fn: (r) => r._field == "wind_speed_mph" '
                        'or r._field == "wind_dir_deg")')
    for row in wind:
        (spd if row["field"] == "wind_speed_mph" else dirs).append(row["value"])
    if spd:
        latest["wind_speed_mph"] = sum(spd) / len(spd)
    if dirs and spd and latest["wind_speed_mph"] > 0:
        x = sum(math.cos(math.radians(d)) for d in dirs)
        y = sum(math.sin(math.radians(d)) for d in dirs)
        latest["wind_dir_deg"] = math.degrees(math.atan2(y, x)) % 360
    elif dirs:
        latest["wind_dir_deg"] = dirs[-1]

    # Gust: peak over the last 5 minutes.
    gust = influx_query(_base("-5m") + ' |> filter(fn: (r) => r._field == "wind_gust_mph") |> max()')
    if gust:
        latest["wind_gust_mph"] = gust[0]["value"]

    # Rain: the station reports a since-midnight total that resets daily
    # (no hourly/24h figures), so derive those. increase() treats the
    # midnight reset as a counter reset instead of negative rain.
    for key, window in (("rain_1h_in", "-1h"), ("rain_24h_in", "-24h")):
        r = influx_query(_base(window) + ' |> filter(fn: (r) => r._field == "rain_daily_in") '
                         '|> increase() |> last()')
        if r:
            latest[key] = r[0]["value"]
    return latest


def sea_level_mb(station_mb, temp_f):
    """Reduces station pressure to sea level (hypsometric formula). This
    station's "relative" pressure equals its absolute pressure (not
    calibrated in the console), but CWOP requires sea-level pressure."""
    h = float(os.environ.get("ELEVATION_M", "0"))
    t_c = ((temp_f if temp_f is not None else 59.0) - 32) / 1.8
    return station_mb * (1 - 0.0065 * h / (t_c + 0.0065 * h + 273.15)) ** -5.257


def _num(value, width, signed=False):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "." * width
    n = int(round(value))
    return f"{n:0{width}d}" if n >= 0 else f"-{abs(n):0{width - 1}d}"


def _lat(deg):
    h = "N" if deg >= 0 else "S"
    deg = abs(deg)
    d = int(deg)
    return f"{d:02d}{(deg - d) * 60:05.2f}{h}"


def _lon(deg):
    h = "E" if deg >= 0 else "W"
    deg = abs(deg)
    d = int(deg)
    return f"{d:03d}{(deg - d) * 60:05.2f}{h}"


def build_packet(d, now=None):
    """APRS weather packet w/ position + timestamp, per the CWOP spec."""
    now = now or datetime.now(timezone.utc)
    lat = float(os.environ["LATITUDE"])
    lon = float(os.environ["LONGITUDE"])
    hum = d.get("humidity_pct")
    if hum is not None:
        hum = 0 if round(hum) >= 100 else round(hum)  # APRS encodes 100% as 00
    baro = d.get("pressure_inhg")
    if baro is not None:
        baro *= 33.8639  # inHg -> mb
        if not PRESSURE_IS_SEA_LEVEL:
            baro = sea_level_mb(baro, d.get("temp_f"))
        baro *= 10  # APRS wants tenths of mb
    hundredths = lambda k: None if d.get(k) is None else d[k] * 100  # noqa: E731

    pkt = (f"{CWOP_ID}>APRS,TCPIP*:@{now:%d%H%M}z{_lat(lat)}/{_lon(lon)}_"
           f"{_num(d.get('wind_dir_deg'), 3)}/{_num(d.get('wind_speed_mph'), 3)}"
           f"g{_num(d.get('wind_gust_mph'), 3)}"
           f"t{_num(d.get('temp_f'), 3)}"
           f"r{_num(hundredths('rain_1h_in'), 3)}"
           f"p{_num(hundredths('rain_24h_in'), 3)}"
           f"P{_num(hundredths('rain_daily_in'), 3)}"
           f"h{_num(hum, 2)}"
           f"b{_num(baro, 5)}")
    sr = d.get("solar_radiation_wm2")
    if sr is not None:
        pkt += f"L{_num(sr, 3)}" if sr < 1000 else f"l{_num(sr - 1000, 3)}"
    return pkt + SOFTWARE_TAG


def send(packet):
    """Logs in (unverified, passcode -1, as CWOP allows) and sends one packet."""
    last_err = None
    for server in SERVERS:
        host, _, port = server.partition(":")
        try:
            with socket.create_connection((host, int(port or 14580)), timeout=15) as s:
                s.settimeout(15)
                s.recv(512)  # banner
                s.sendall(f"user {CWOP_ID} pass -1 vers altair-cwop 1.0\r\n".encode())
                login = s.recv(512).decode(errors="replace").strip()
                log.debug("login reply: %s", login)
                s.sendall((packet + "\r\n").encode())
                time.sleep(3)  # let the server ingest before we close
            return server
        except OSError as e:
            last_err = e
            log.warning("CWOP server %s failed: %s", server, e)
    raise RuntimeError(f"all CWOP servers failed: {last_err}")


def heartbeat(ok, detail):
    """Records each attempt in the system bucket so Grafana can alert on silence."""
    line = f'cwop,station={STATION} ok={"true" if ok else "false"},detail="{detail[:200].replace(chr(34), chr(39))}"'
    url = (f"{INFLUX_URL}/api/v2/write?org={urllib.parse.quote(INFLUX_ORG)}"
           f"&bucket={BUCKET_SYSTEM}&precision=s")
    req = urllib.request.Request(url, data=line.encode(), method="POST",
                                 headers={"Authorization": f"Token {INFLUX_TOKEN}"})
    try:
        urllib.request.urlopen(req, timeout=10).close()
    except Exception as e:  # noqa: BLE001
        log.warning("heartbeat write failed: %s", e)


def cycle(dry_run):
    try:
        packet = build_packet(collect())
        if dry_run:
            print(packet)
            return True
        server = send(packet)
        log.info("sent to %s: %s", server, packet)
        heartbeat(True, packet)
        return True
    except Exception as e:  # noqa: BLE001
        log.error("cycle failed: %s", e)
        if not dry_run:
            heartbeat(False, str(e))
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the packet, don't send")
    ap.add_argument("--once", action="store_true", help="run one cycle and exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not CWOP_ID:
        sys.exit("CWOP_ID is not set")
    if not INFLUX_TOKEN:
        sys.exit("INFLUXDB_ADMIN_TOKEN is not set")
    if "LATITUDE" not in os.environ or "LONGITUDE" not in os.environ:
        sys.exit("LATITUDE/LONGITUDE must be set (decimal degrees, W/S negative)")

    if args.once or args.dry_run:
        sys.exit(0 if cycle(args.dry_run) else 1)

    def _term(*_):
        global _stop
        _stop = True
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)

    while not _stop:
        cycle(False)
        for _ in range(INTERVAL):
            if _stop:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
