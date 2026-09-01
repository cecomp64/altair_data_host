#!/usr/bin/env python3
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

# Reolink's on-device AI classifier - GetAiState just reads whatever the
# camera has already computed (person/vehicle/dog_cat/face), no extra
# processing triggered by asking. Confirmed cheap directly against all
# three cameras: ~470-570 bytes, ~0.15-0.25s - closer to GetDevInfo
# (scripts/read_camera_health.py) than to Snap, so polling this often costs
# nothing meaningful even on CAMERA_POLE_SOUTH's WiFi link.
AISTATE_URL = "https://{ip}/cgi-bin/api.cgi?cmd=GetAiState&channel={channel}&user={user}&password={password}"

_UNVERIFIED_CONTEXT = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
_UNVERIFIED_CONTEXT.check_hostname = False
_UNVERIFIED_CONTEXT.verify_mode = ssl.CERT_NONE

# Same stream-level identity as read_cameras.py's camera_snapshot (one
# entry per lens, not per device) - AI detection is scene-dependent, and
# CAMERA_POLE_SOUTH's two lenses point at different fields of view, so
# unlike camera_health (device-level liveness) this needs to stay per-lens.
def build_targets():
    targets = {}

    pole_south_ip = os.environ.get("CAMERA_POLE_SOUTH")
    if pole_south_ip:
        ptz_channel = os.environ.get("CAMERA_POLE_SOUTH_PTZ_CHANNEL", "1")
        pano_channel = os.environ.get("CAMERA_POLE_SOUTH_PANO_CHANNEL", "0")
        targets["pole_south_ptz"] = (pole_south_ip, ptz_channel)
        targets["pole_south_pano"] = (pole_south_ip, pano_channel)

    obs_west_ip = os.environ.get("CAMERA_OBS_WEST")
    if obs_west_ip:
        targets["obs_west"] = (obs_west_ip, "0")

    obs_east_ip = os.environ.get("CAMERA_OBS_EAST")
    if obs_east_ip:
        targets["obs_east"] = (obs_east_ip, "0")

    return targets


def fetch_ai_state(ip, channel, user, password, timeout):
    url = AISTATE_URL.format(ip=ip, channel=channel, user=user, password=password)
    with urllib.request.urlopen(url, timeout=timeout, context=_UNVERIFIED_CONTEXT) as resp:
        parsed = json.loads(resp.read())
    if not (isinstance(parsed, list) and parsed and parsed[0].get("code") == 0):
        raise ValueError(f"unexpected GetAiState response: {parsed!r}")
    return parsed[0]["value"]


if __name__ == "__main__":
    user = os.environ.get("REOLINK_USERNAME")
    password = os.environ.get("REOLINK_PASSWORD")
    timeout = float(os.environ.get("CAMERA_AI_TIMEOUT", "5"))

    if not user or not password:
        sys.stderr.write("REOLINK_USERNAME / REOLINK_PASSWORD not set\n")
        sys.exit(1)

    targets = build_targets()
    if not targets:
        sys.stderr.write("No CAMERA_* IPs set in .env\n")
        sys.exit(1)

    lines = []
    any_ok = False
    for name, (ip, channel) in targets.items():
        try:
            state = fetch_ai_state(ip, channel, user, password, timeout)
            # Only emit fields this specific lens actually supports (all
            # three support people/vehicle/dog_cat, none support face, per
            # GetAbility - checked directly rather than assumed, but a
            # future firmware/model could differ).
            fields = {
                key: int(bool(state[key]["alarm_state"]))
                for key in ("people", "vehicle", "dog_cat")
                if state.get(key, {}).get("support")
            }
            if not fields:
                raise ValueError("no supported AI detection types in response")
            field_str = ",".join(f"{k}={v}i" for k, v in fields.items())
            lines.append(f"camera_ai,camera={name} {field_str}")
            any_ok = True
        except (urllib.error.URLError, ValueError, TimeoutError, OSError, KeyError) as e:
            sys.stderr.write(f"Error reading AI state for {name} ({ip} ch{channel}): {e}\n")

    print("\n".join(lines))
    sys.exit(0 if any_ok else 1)
