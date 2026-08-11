#!/usr/bin/env python3
import datetime
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request

# This gateway's local HTTP API (GW1000/GW2000-style firmware, confirmed via
# `curl --compressed http://<station-ip>/get_livedata_info`) returns nested
# id/val arrays with units baked into the value string (e.g. "0.00 mph",
# "31%"), not the flat {"tempf": ...} schema telegraf.conf originally assumed
# - hence a script (like read_vedirect.py) instead of inputs.http/json_v2.
#
# common_list/piezoRain ids per Ecowitt's local API doc; "0x0F" (rain hour)
# isn't emitted by this station's rain gauge, so rain_hourly_in is omitted
# rather than faked.
COMMON_LIST_IDS = {
    "0x02": "temp_f",
    "0x07": "humidity_pct",
    "0x0A": "wind_dir_deg",
    "0x0B": "wind_speed_mph",
    "0x0C": "wind_gust_mph",
    "0x15": "solar_radiation_wm2",
    "0x17": "uv_index",
}
PIEZO_RAIN_IDS = {
    "0x10": "rain_daily_in",
}

# The console's built-in indoor sensor (wh25) and any not-connected channel
# always sit at these sentinel ids in get_sensors_info - excluding them
# leaves only the real, currently-registered outdoor sensor(s).
SENSORS_INFO_SENTINEL_IDS = {"FFFFFFFF", "FFFFFFFE"}

NUMERIC_PREFIX = re.compile(r"[-+]?\d*\.?\d+")

_MOON_PHASE_NAMES = [
    "New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous",
    "Full Moon", "Waning Gibbous", "Last Quarter", "Waning Crescent",
]


def to_float(raw):
    """Strips a trailing unit like ' mph' or '%' and parses the leading number."""
    match = NUMERIC_PREFIX.match(raw.strip())
    if not match:
        raise ValueError(f"no numeric prefix in {raw!r}")
    return float(match.group())


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.load(resp)


def sensors_info_url(livedata_url):
    """The station has no way to give us this URL directly - it's the same
    host as WEATHER_API_URL, just a different local-API path."""
    parts = urllib.parse.urlsplit(livedata_url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, "/get_sensors_info", "page=1", ""))


def moon_phase(now=None):
    """Approximate lunar phase from a known new moon reference and the
    synodic month length - accurate to well within a day, which is all a
    hobby dashboard needs. The gateway has no astro endpoint (confirmed:
    /get_sunmoon_info and /get_astro_info both 404 on this firmware), so
    this is computed locally rather than read from the station.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    known_new_moon = datetime.datetime(2000, 1, 6, 18, 14, tzinfo=datetime.timezone.utc)
    synodic_month_days = 29.53058867
    days_since = (now - known_new_moon).total_seconds() / 86400.0
    phase_fraction = (days_since % synodic_month_days) / synodic_month_days
    illumination_pct = (1 - math.cos(2 * math.pi * phase_fraction)) / 2 * 100
    name = _MOON_PHASE_NAMES[int(phase_fraction * 8 + 0.5) % 8]
    return illumination_pct, name


def parse_livedata(data):
    fields = {}

    for entry in data.get("common_list", []):
        field = COMMON_LIST_IDS.get(entry.get("id"))
        if field:
            fields[field] = to_float(entry["val"])

    for entry in data.get("piezoRain", []):
        field = PIEZO_RAIN_IDS.get(entry.get("id"))
        if field:
            fields[field] = to_float(entry["val"])

    wh25 = data.get("wh25") or []
    if wh25:
        if "rel" in wh25[0]:
            fields["pressure_inhg"] = to_float(wh25[0]["rel"])
        if "intemp" in wh25[0]:
            fields["temp_in_f"] = to_float(wh25[0]["intemp"])
        if "inhumi" in wh25[0]:
            fields["humidity_in_pct"] = to_float(wh25[0]["inhumi"])

    return fields


def parse_sensor_battery(sensors):
    """Finds the currently-registered outdoor sensor array (excludes the
    console's built-in wh25 and any unpopulated channel) and reports its
    battery. These combo arrays (wh69/wh68/wh90) report battery as a 0-5
    level rather than a raw voltage, per Ecowitt's local API doc - the
    higher, the better.
    """
    for entry in sensors:
        if entry.get("id") in SENSORS_INFO_SENTINEL_IDS:
            continue
        if entry.get("idst") != "1":
            continue
        try:
            level = to_float(entry["batt"])
        except (KeyError, ValueError):
            continue
        return {
            "battery_level": level,
            "battery_pct": min(100.0, level / 5.0 * 100.0),
        }
    return {}


if __name__ == "__main__":
    url = os.environ.get("WEATHER_API_URL")
    station = os.environ.get("WEATHER_STATION_NAME", "primary")
    if not url:
        sys.stderr.write("WEATHER_API_URL is not set\n")
        sys.exit(1)

    fields = {}
    try:
        fields.update(parse_livedata(fetch_json(url)))
    except Exception as e:
        sys.stderr.write(f"Error reading Ecowitt live data: {e}\n")
        sys.exit(1)

    try:
        fields.update(parse_sensor_battery(fetch_json(sensors_info_url(url))))
    except Exception as e:
        # Battery is a nice-to-have, not core weather data - don't fail the
        # whole metric over it.
        sys.stderr.write(f"Warning: couldn't read Ecowitt sensor battery: {e}\n")

    illumination_pct, phase_name = moon_phase()
    fields["moon_illumination_pct"] = round(illumination_pct, 1)

    if not fields:
        sys.stderr.write("No recognized fields in Ecowitt response\n")
        sys.exit(1)

    numeric_str = ",".join(f"{k}={v}" for k, v in fields.items())
    field_str = f'{numeric_str},moon_phase_name="{phase_name}"'
    print(f"weather_station,station={station} {field_str}")
