#!/usr/bin/env python3
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

# Cheap liveness check, run far more often than scripts/read_cameras.py's
# actual image poll. GetDevInfo returns ~800 bytes of static device info
# (model/serial/firmware) regardless of camera state - no image capture/
# encode on the camera side, no meaningful payload over the wire. Measured
# directly against these three cameras: ~0.18-0.2s and ~835 bytes each, vs.
# 0.6-1.2s and 500KB-1.1MB for a Snap - answers "is this camera reachable"
# without paying Snap's cost (or, for CAMERA_POLE_SOUTH, triggering its WiFi
# radio to move that much data) every time we just want a yes/no.
DEVINFO_URL = "https://{ip}/cgi-bin/api.cgi?cmd=GetDevInfo&user={user}&password={password}"

# CAMERA_POLE_SOUTH only - confirmed via GetLocalLink (activeLink: "Wifi")
# that it's the one camera actually using its radio; CAMERA_OBS_WEST/_EAST
# report activeLink: "LAN" despite also having WiFi hardware (GetDevInfo's
# wifi:1 flag is true for all three - it's a capability flag, not "in use").
# Reolink's wifiSignal is a 0-4 bar scale for this camera - wired cameras
# were observed returning a meaningless 100 here, so this is only fetched
# for the one camera it actually describes.
WIFI_SIGNAL_URL = "https://{ip}/cgi-bin/api.cgi?cmd=GetWifiSignal&user={user}&password={password}"

_UNVERIFIED_CONTEXT = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
_UNVERIFIED_CONTEXT.check_hostname = False
_UNVERIFIED_CONTEXT.verify_mode = ssl.CERT_NONE


# name -> ip. One entry per physical device, not per lens/channel - unlike
# read_cameras.py's camera_snapshot measurement (tagged pole_south_ptz/
# pole_south_pano/obs_west/obs_east, one per image stream), camera_health
# is tagged pole_south/obs_west/obs_east: a dual-lens camera is one device
# on the network, so one liveness check covers both its snapshot streams.
def build_targets():
    targets = {}
    for name, env_var in (("pole_south", "CAMERA_POLE_SOUTH"), ("obs_west", "CAMERA_OBS_WEST"), ("obs_east", "CAMERA_OBS_EAST")):
        ip = os.environ.get(env_var)
        if ip:
            targets[name] = ip
    return targets


def fetch_json(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout, context=_UNVERIFIED_CONTEXT) as resp:
        return json.loads(resp.read())


def is_ok_response(parsed):
    return isinstance(parsed, list) and parsed and parsed[0].get("code") == 0


if __name__ == "__main__":
    user = os.environ.get("REOLINK_USERNAME")
    password = os.environ.get("REOLINK_PASSWORD")
    timeout = float(os.environ.get("CAMERA_HEALTH_TIMEOUT", "5"))

    if not user or not password:
        sys.stderr.write("REOLINK_USERNAME / REOLINK_PASSWORD not set\n")
        sys.exit(1)

    targets = build_targets()
    if not targets:
        sys.stderr.write("No CAMERA_* IPs set in .env\n")
        sys.exit(1)

    lines = []
    any_ok = False
    for name, ip in targets.items():
        try:
            devinfo = fetch_json(DEVINFO_URL.format(ip=ip, user=user, password=password), timeout)
            online = is_ok_response(devinfo)
        except (urllib.error.URLError, ValueError, TimeoutError, OSError) as e:
            sys.stderr.write(f"Error checking {name} ({ip}): {e}\n")
            online = False

        fields = f"online={int(online)}i"

        if name == "pole_south" and online:
            try:
                wifi = fetch_json(WIFI_SIGNAL_URL.format(ip=ip, user=user, password=password), timeout)
                if is_ok_response(wifi):
                    fields += f",wifi_signal_bars={int(wifi[0]['value']['wifiSignal'])}i"
            except (urllib.error.URLError, ValueError, TimeoutError, OSError, KeyError) as e:
                sys.stderr.write(f"Warning: couldn't read {name} WiFi signal: {e}\n")

        lines.append(f"camera_health,camera={name} {fields}")
        any_ok = any_ok or online

    print("\n".join(lines))
    sys.exit(0 if any_ok else 1)
