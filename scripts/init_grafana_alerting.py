#!/usr/bin/env python3
"""Provisions Discord alerting (contact point, default notification policy,
and alert rules) into Grafana via its REST API.

Not done as committed YAML under grafana/provisioning/alerting/, unlike
every other piece of Grafana config in this repo (dashboards, datasources) -
Grafana's alerting file-provisioner has a confirmed, still-open upstream bug
where $ENV_VAR expansion (which works fine in provisioning/datasources/*.yml)
silently does not happen for alerting resources: grafana/grafana#54984 and
#56437. Committing contactpoints.yaml with `url: $DISCORD_WEBHOOK_URL`
actually crash-looped this Grafana instance on startup ("could not find
webhook url property in settings") rather than just failing to interpolate -
confirmed by testing directly against this exact deployment (Grafana
13.1.1). Hence this script instead: idempotent, safe to re-run, called by
scripts/init-grafana-alerting.sh the same way scripts/init-influx-buckets.sh
sets up InfluxDB after the container's up.

Tradeoff worth knowing: because these are created via the API rather than
mounted from a read-only file, they're editable in the Grafana UI without
this script silently overwriting UI changes (dashboards.yml's
allowUiUpdates behavior does NOT apply here) - but they also don't
self-heal from git if the grafana_data volume is ever wiped; re-run this
script after any fresh volume the way init-influx-buckets.sh gets re-run
for InfluxDB.
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request

GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://localhost:3000")
GRAFANA_USER = os.environ.get("GRAFANA_ADMIN_USER")
GRAFANA_PASSWORD = os.environ.get("GRAFANA_ADMIN_PASSWORD")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

ALERTS_FOLDER_UID = "altair-alerts"
ALERTS_FOLDER_TITLE = "Alerts"
CONTACT_POINT_NAME = "discord"
CONTACT_POINT_UID = "discord-webhook"


def request(method, path, body=None):
    url = f"{GRAFANA_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    auth = base64.b64encode(f"{GRAFANA_USER}:{GRAFANA_PASSWORD}".encode()).decode()
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw else None)


def ensure_folder():
    status, folders = request("GET", "/api/folders")
    if status != 200:
        sys.exit(f"Failed to list folders: {status} {folders}")
    if any(f["uid"] == ALERTS_FOLDER_UID for f in folders):
        return
    status, resp = request("POST", "/api/folders", {"title": ALERTS_FOLDER_TITLE, "uid": ALERTS_FOLDER_UID})
    if status not in (200, 412):  # 412 = already exists (race with a parallel run)
        sys.exit(f"Failed to create '{ALERTS_FOLDER_TITLE}' folder: {status} {resp}")
    print(f"Folder '{ALERTS_FOLDER_TITLE}' ready.")


def ensure_contact_point():
    status, points = request("GET", "/api/v1/provisioning/contact-points")
    if status != 200:
        sys.exit(f"Failed to list contact points: {status} {points}")
    exists = any(p["name"] == CONTACT_POINT_NAME for p in points)

    body = {
        "uid": CONTACT_POINT_UID,
        "name": CONTACT_POINT_NAME,
        "type": "discord",
        "settings": {"url": DISCORD_WEBHOOK_URL, "use_discord_username": False},
        "disableResolveMessage": False,
    }
    if exists:
        status, resp = request("PUT", f"/api/v1/provisioning/contact-points/{CONTACT_POINT_UID}", body)
        verb = "Updated"
    else:
        status, resp = request("POST", "/api/v1/provisioning/contact-points", body)
        verb = "Created"
    if status not in (200, 202):
        sys.exit(f"Failed to {verb.lower()} discord contact point: {status} {resp}")
    print(f"{verb} discord contact point.")


def ensure_default_policy():
    status, resp = request(
        "PUT", "/api/v1/provisioning/policies",
        {"receiver": CONTACT_POINT_NAME, "group_by": ["grafana_folder", "alertname"]},
    )
    if status not in (200, 202):
        sys.exit(f"Failed to set default notification policy: {status} {resp}")
    print("Default notification policy -> discord.")


def flux_query_node(ref_id, datasource_uid, query):
    return {
        "refId": ref_id,
        "relativeTimeRange": {"from": 600, "to": 0},
        "datasourceUid": datasource_uid,
        "model": {"refId": ref_id, "query": query},
    }


def reduce_node(ref_id, expression):
    return {
        "refId": ref_id,
        "relativeTimeRange": {"from": 600, "to": 0},
        "datasourceUid": "__expr__",
        "model": {"type": "reduce", "expression": expression, "reducer": "last", "refId": ref_id},
    }


def threshold_node(ref_id, expression, evaluator_type, params):
    return {
        "refId": ref_id,
        "relativeTimeRange": {"from": 600, "to": 0},
        "datasourceUid": "__expr__",
        "model": {
            "type": "threshold",
            "expression": expression,
            "conditions": [{"evaluator": {"type": evaluator_type, "params": params}}],
            "refId": ref_id,
        },
    }


def rule(uid, title, query, datasource_uid, evaluator_type, params, for_duration, severity, summary):
    return {
        "orgID": 1,
        "folderUID": ALERTS_FOLDER_UID,
        "ruleGroup": "safety-and-health",
        "uid": uid,
        "title": title,
        "condition": "C",
        "data": [
            flux_query_node("A", datasource_uid, query),
            reduce_node("B", "A"),
            threshold_node("C", "B", evaluator_type, params),
        ],
        "noDataState": "OK",
        "execErrState": "Error",
        "for": for_duration,
        "labels": {"severity": severity},
        "annotations": {"summary": summary},
    }


RULES = [
    rule(
        "battery-soc-critical", "Battery SOC Critical",
        'from(bucket: "power")\n'
        '  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
        '  |> filter(fn: (r) => r._measurement == "victron_smartshunt" and r._field == "soc")\n'
        '  |> last()',
        "influxdb-power", "lt", [15], "5m", "critical",
        "Battery SOC has dropped below 15% - risk of deep discharge.",
    ),
    rule(
        "starlink-alert-active", "Starlink Alert Active",
        'from(bucket: "starlink")\n'
        '  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
        '  |> filter(fn: (r) => r._measurement == "starlink" and r._field == "alert_active")\n'
        '  |> last()\n'
        '  |> map(fn: (r) => ({r with _value: if r._value then 1.0 else 0.0}))',
        "influxdb-starlink", "gt", [0.5], "2m", "warning",
        "Starlink dish reports an active alert (see the Alert Detail Timeline panel for which one).",
    ),
    rule(
        "internet-fully-down", "Internet Fully Down",
        'from(bucket: "network")\n'
        '  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
        '  |> filter(fn: (r) => r._measurement == "ping" and r._field == "percent_packet_loss" and r.network == "internet")\n'
        '  |> last()\n'
        '  |> group(columns: ["_measurement", "_field"])\n'
        '  |> min()\n'
        '  |> keep(columns: ["_time", "_value"])',
        "influxdb-network", "gte", [100], "3m", "critical",
        "Every configured internet ping target (PING_TARGETS_INTERNET) has 100% packet loss.",
    ),
    rule(
        "disk-usage-critical", "Disk Usage Critical",
        'from(bucket: "system")\n'
        '  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
        '  |> filter(fn: (r) => r._measurement == "disk" and r._field == "used_percent" and r.path == "/")\n'
        '  |> last()',
        "influxdb-system", "gt", [90], "10m", "warning",
        "Root filesystem is over 90% full.",
    ),
    rule(
        "pi-undervoltage", "Pi Under-Voltage",
        'from(bucket: "system")\n'
        '  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
        '  |> filter(fn: (r) => r._measurement == "pi_power" and r._field == "undervoltage_now")\n'
        '  |> last()\n'
        '  |> map(fn: (r) => ({r with _value: if r._value then 1.0 else 0.0}))',
        "influxdb-system", "gt", [0.5], "2m", "critical",
        "Pi's 5V supply is under-voltage right now - failing/inadequate power supply or cable, risk of SD card corruption or brownout.",
    ),
    rule(
        "cpu-temp-critical", "CPU Temp Critical",
        'from(bucket: "system")\n'
        '  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
        '  |> filter(fn: (r) => r._measurement == "temp" and r._field == "temp" and r.sensor == "cpu_thermal")\n'
        '  |> last()',
        "influxdb-system", "gt", [80], "5m", "warning",
        "Pi SoC temperature is above 80C - approaching thermal throttling.",
    ),
]


def ensure_rules():
    for r in RULES:
        status, _ = request("GET", f"/api/v1/provisioning/alert-rules/{r['uid']}")
        if status == 200:
            status, resp = request("PUT", f"/api/v1/provisioning/alert-rules/{r['uid']}", r)
            verb = "Updated"
        else:
            status, resp = request("POST", "/api/v1/provisioning/alert-rules", r)
            verb = "Created"
        if status not in (200, 201):
            sys.exit(f"Failed to {verb.lower()} rule '{r['title']}': {status} {resp}")
        print(f"{verb} rule: {r['title']}")


if __name__ == "__main__":
    if not GRAFANA_USER or not GRAFANA_PASSWORD:
        sys.exit("GRAFANA_ADMIN_USER / GRAFANA_ADMIN_PASSWORD not set")
    if not DISCORD_WEBHOOK_URL:
        sys.exit("DISCORD_WEBHOOK_URL not set in .env")

    ensure_folder()
    ensure_contact_point()
    ensure_default_policy()
    ensure_rules()
    print("Done.")
