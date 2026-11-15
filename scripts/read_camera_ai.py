#!/usr/bin/env python3
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

# Reolink's on-device AI classifier - GetAiState just reads whatever the
# camera has already computed (person/vehicle/dog_cat/face), no extra
# processing triggered by asking. Confirmed cheap directly against all
# three cameras: ~470-570 bytes, ~0.15-0.25s - closer to GetDevInfo
# (scripts/read_camera_health.py) than to Snap, so polling this often costs
# nothing meaningful even on CAMERA_POLE_SOUTH's WiFi link.
AISTATE_URL = "https://{ip}/cgi-bin/api.cgi?cmd=GetAiState&channel={channel}&user={user}&password={password}"

# Same Snap CGI call as scripts/read_cameras.py, used here only to archive a
# still the moment an animal shows up in frame - see save_detection_snapshot.
SNAP_URL = "https://{ip}/cgi-bin/api.cgi?cmd=Snap&channel={channel}&rs={nonce}&user={user}&password={password}"

_UNVERIFIED_CONTEXT = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
_UNVERIFIED_CONTEXT.check_hostname = False
_UNVERIFIED_CONTEXT.verify_mode = ssl.CERT_NONE

# Only the two outdoor Pole South lenses, and only on dog_cat - obs_west/
# obs_east and the people/vehicle fields stay dashboard-only, per README §7
# (a raw person/vehicle alert was tried and pulled for being too noisy at a
# rural site). Archiving a still isn't paging anyone, so that objection
# doesn't apply here, but this still stays scoped to what was asked for.
DETECTION_SNAPSHOT_CAMERAS = {"pole_south_ptz", "pole_south_pano"}
DETECTION_SNAPSHOT_FIELD = "dog_cat"

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


# Edge-triggered rather than snapping on every poll an animal's in frame -
# a sighting commonly spans several minutes of 30s polls, and this only
# needs one archived frame per sighting, not a burst of near-duplicates.
# "Last state" has to be persisted to disk rather than kept in memory,
# since telegraf's inputs.exec runs this as a fresh process every poll.
def load_last_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def save_last_state(path, state):
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(state, f)
    os.replace(tmp_path, path)


def save_detection_snapshot(out_dir, name, ip, channel, user, password, timeout):
    url = SNAP_URL.format(ip=ip, channel=channel, nonce=int(time.time() * 1000), user=user, password=password)
    with urllib.request.urlopen(url, timeout=timeout, context=_UNVERIFIED_CONTEXT) as resp:
        content_type = resp.headers.get("Content-Type", "")
        data = resp.read()
        if "image" not in content_type:
            raise ValueError(f"unexpected content-type {content_type!r} (bad credentials or channel?)")

    # Timestamped filename (unlike read_cameras.py's <name>.jpg, overwritten
    # every poll) so each sighting gets its own file instead of clobbering
    # the last one - this is an archive, not a live-view still.
    filename = f"{name}_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.jpg"
    final_path = os.path.join(out_dir, filename)
    tmp_path = f"{final_path}.tmp"
    with open(tmp_path, "wb") as f:
        f.write(data)
    os.replace(tmp_path, final_path)
    return filename


if __name__ == "__main__":
    user = os.environ.get("REOLINK_USERNAME")
    password = os.environ.get("REOLINK_PASSWORD")
    timeout = float(os.environ.get("CAMERA_AI_TIMEOUT", "5"))
    snapshot_timeout = float(os.environ.get("CAMERA_SNAPSHOT_TIMEOUT", "8"))

    if not user or not password:
        sys.stderr.write("REOLINK_USERNAME / REOLINK_PASSWORD not set\n")
        sys.exit(1)

    targets = build_targets()
    if not targets:
        sys.stderr.write("No CAMERA_* IPs set in .env\n")
        sys.exit(1)

    # Detection snapshots land in their own subdir of the same camera_snapshots
    # volume read_cameras.py already writes to, so camera-web (docker-compose.yml)
    # serves them too without any nginx/volume changes.
    detections_dir = os.path.join(os.environ.get("CAMERA_SNAPSHOT_DIR", "/snapshots"), "detections")
    os.makedirs(detections_dir, exist_ok=True)
    state_path = os.path.join(detections_dir, ".last_state.json")
    last_state = load_last_state(state_path)

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

            if name in DETECTION_SNAPSHOT_CAMERAS and DETECTION_SNAPSHOT_FIELD in fields:
                current = fields[DETECTION_SNAPSHOT_FIELD]
                if current and not last_state.get(name):
                    try:
                        filename = save_detection_snapshot(
                            detections_dir, name, ip, channel, user, password, snapshot_timeout
                        )
                        # Relative path, not a full URL - Grafana's dashboard
                        # already carries the camera_web_url variable that
                        # resolves this the same way the live-still panels do.
                        field_str += f',snapshot_path="detections/{filename}"'
                    except (urllib.error.URLError, ValueError, TimeoutError, OSError) as e:
                        sys.stderr.write(f"Error saving detection snapshot for {name}: {e}\n")
                last_state[name] = current

            lines.append(f"camera_ai,camera={name} {field_str}")
            any_ok = True
        except (urllib.error.URLError, ValueError, TimeoutError, OSError, KeyError) as e:
            sys.stderr.write(f"Error reading AI state for {name} ({ip} ch{channel}): {e}\n")

    save_last_state(state_path, last_state)

    print("\n".join(lines))
    sys.exit(0 if any_ok else 1)
