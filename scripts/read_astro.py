#!/usr/bin/env python3
"""Computes sun and moon altitude locally via pyephem, independent of both
N.I.N.A. (which only reports astro_sun_altitude/astro_moon_altitude to the
imaging bucket, and only while a session is actually running) and the
weather station's own uptime (unlike read_ecowitt.py's already-local moon
phase/illumination calc, which is still bundled into that station's own API
poll - if the station's offline, that point is lost too). No I/O beyond
reading the clock, so it's cheap enough to run on any interval regardless
of how often anything else here polls; telegraf.conf runs it every 60s.

Uses LATITUDE/LONGITUDE (decimal degrees, required) and ELEVATION_M
(meters, optional - only affects the horizon-dip correction by a fraction
of a degree, not worth being precise about) from .env. pyephem's Observer
takes lat/lon as *strings* (interpreted as degrees) - passing floats would
silently be interpreted as radians instead, so these are passed straight
through from the env var strings rather than parsed to float.
"""
import math
import os
import sys

import ephem

LATITUDE = os.environ.get("LATITUDE")
LONGITUDE = os.environ.get("LONGITUDE")
ELEVATION_M = float(os.environ.get("ELEVATION_M", "0"))


if __name__ == "__main__":
    if not LATITUDE or not LONGITUDE:
        sys.stderr.write("LATITUDE/LONGITUDE not set\n")
        sys.exit(1)

    observer = ephem.Observer()
    observer.lat = LATITUDE
    observer.lon = LONGITUDE
    observer.elevation = ELEVATION_M
    observer.date = ephem.now()

    sun_altitude_deg = math.degrees(ephem.Sun(observer).alt)
    moon_altitude_deg = math.degrees(ephem.Moon(observer).alt)

    print(f"astro sun_altitude_deg={sun_altitude_deg},moon_altitude_deg={moon_altitude_deg}")
