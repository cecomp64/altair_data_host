#!/usr/bin/env python3
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

# Reolink's "Snap" CGI command returns a single JPEG frame directly (not
# wrapped in the JSON envelope the rest of the Reolink API uses) - this is
# the same call the camera's own web UI makes for its live still. `rs` is
# just a cache-busting nonce Reolink's own docs recommend (any string); it
# has no bearing on auth. Confirmed against a real Reolink Duo/TrackMix-style
# camera: https://<ip>/cgi-bin/api.cgi?cmd=Snap&channel=0&user=...&password=...
SNAP_URL = "https://{ip}/cgi-bin/api.cgi?cmd=Snap&channel={channel}&rs={nonce}&user={user}&password={password}"

# All three cameras use self-signed certs (confirmed: no CA chain, cert CN
# doesn't match the LAN IP either) - same trust model as every other local
# API this repo talks to (Alpaca, the weather station, DragonLAIR), just
# over https instead of http because that's what Reolink's firmware exposes.
# There's no cert to pin without hardcoding a fingerprint per camera, which
# would break the moment a camera's firmware regenerates its self-signed
# cert - so this matches curl -k / the camera's own mobile app, not a
# reduced security posture relative to the rest of the stack.
_UNVERIFIED_CONTEXT = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
_UNVERIFIED_CONTEXT.check_hostname = False
_UNVERIFIED_CONTEXT.verify_mode = ssl.CERT_NONE

# name -> (ip, channel). CAMERA_POLE_SOUTH is a dual-lens camera (one IP,
# two channels) - which channel is the panoramic lens vs. the PTZ lens isn't
# discoverable from the API itself, so it's configurable rather than
# guessed. Defaults (1=ptz, 0=pano) match this repo's own camera, confirmed
# by comparing the two saved frames - not a documented Reolink convention,
# so a different dual-lens model may need CAMERA_POLE_SOUTH_PTZ_CHANNEL /
# CAMERA_POLE_SOUTH_PANO_CHANNEL flipped in .env.
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


def fetch_snapshot(ip, channel, user, password, timeout):
    url = SNAP_URL.format(ip=ip, channel=channel, nonce=int(time.time() * 1000), user=user, password=password)
    with urllib.request.urlopen(url, timeout=timeout, context=_UNVERIFIED_CONTEXT) as resp:
        content_type = resp.headers.get("Content-Type", "")
        body = resp.read()
        # A wrong password/channel still gets a 200 from this endpoint, just
        # with a JSON error body instead of image bytes - Content-Type is
        # the only reliable way to tell the two apart without parsing both
        # shapes.
        if "image" not in content_type:
            raise ValueError(f"unexpected content-type {content_type!r} (bad credentials or channel?)")
        return body


def write_snapshot_atomically(out_dir, name, data):
    # Written to a temp file and renamed into place so the nginx container
    # reading the same volume (docker-compose.yml's camera-web service)
    # never serves a half-written frame - os.replace is atomic within the
    # same filesystem, which a single bind-mounted volume always is.
    final_path = os.path.join(out_dir, f"{name}.jpg")
    tmp_path = f"{final_path}.tmp"
    with open(tmp_path, "wb") as f:
        f.write(data)
    os.replace(tmp_path, final_path)


if __name__ == "__main__":
    user = os.environ.get("REOLINK_USERNAME")
    password = os.environ.get("REOLINK_PASSWORD")
    out_dir = os.environ.get("CAMERA_SNAPSHOT_DIR", "/snapshots")
    timeout = float(os.environ.get("CAMERA_SNAPSHOT_TIMEOUT", "8"))

    if not user or not password:
        sys.stderr.write("REOLINK_USERNAME / REOLINK_PASSWORD not set\n")
        sys.exit(1)

    targets = build_targets()
    if not targets:
        sys.stderr.write("No CAMERA_* IPs set in .env\n")
        sys.exit(1)

    os.makedirs(out_dir, exist_ok=True)

    lines = []
    any_ok = False
    for name, (ip, channel) in targets.items():
        start = time.monotonic()
        try:
            data = fetch_snapshot(ip, channel, user, password, timeout)
            write_snapshot_atomically(out_dir, name, data)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            lines.append(f'camera_snapshot,camera={name} success=1i,bytes={len(data)}i,fetch_ms={elapsed_ms}i')
            any_ok = True
        except (urllib.error.URLError, ValueError, TimeoutError, OSError) as e:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            sys.stderr.write(f"Error fetching {name} ({ip} ch{channel}): {e}\n")
            lines.append(f'camera_snapshot,camera={name} success=0i,fetch_ms={elapsed_ms}i')

    print("\n".join(lines))
    # Exit 0 as long as at least one camera succeeded - telegraf's own
    # inputs.exec error log covers per-camera failures via stderr above,
    # and one dead camera shouldn't blank out line protocol for the other
    # two (a failed exec drops the *whole* batch, not just the bad line).
    sys.exit(0 if any_ok else 1)
