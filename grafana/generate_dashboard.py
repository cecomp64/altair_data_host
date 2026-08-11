#!/usr/bin/env python3
"""
Generates provisioning/dashboards/solar-observatory.json.

Hand-writing Grafana's dashboard JSON (grid math, ids, repeated fieldConfig
boilerplate) is error-prone; this script builds it from small panel helpers
instead. Re-run after editing to regenerate the committed JSON:

    python3 grafana/generate_dashboard.py
"""
import json
import os

WIDTH = 24
STAT_H = 6
TS_H = 8
TABLE_H = 10
STATE_H = 6
ROW_H = 1

DS = {
    "power": {"type": "influxdb", "uid": "influxdb-power"},
    "weather": {"type": "influxdb", "uid": "influxdb-weather"},
    "observatory": {"type": "influxdb", "uid": "influxdb-observatory"},
    "imaging": {"type": "influxdb", "uid": "influxdb-imaging"},
}

_next_id = [1]


def next_id():
    _next_id[0] += 1
    return _next_id[0]


def row(title, y):
    return {"id": next_id(), "type": "row", "title": title,
            "gridPos": {"h": ROW_H, "w": WIDTH, "x": 0, "y": y}, "collapsed": False, "panels": []}


def _target(domain, flux, ref_id="A"):
    return {"datasource": DS[domain], "query": flux, "refId": ref_id}


def stat_panel(title, domain, flux, x, y, w=6, h=STAT_H, unit="none", thresholds=None, mappings=None, color_mode="thresholds"):
    defaults = {"unit": unit, "color": {"mode": color_mode}}
    if thresholds:
        defaults["thresholds"] = {"mode": "absolute", "steps": thresholds}
    if mappings:
        defaults["mappings"] = mappings
    return {
        "id": next_id(), "type": "stat", "title": title,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "datasource": DS[domain],
        "targets": [_target(domain, flux)],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "orientation": "auto", "textMode": "auto", "colorMode": "background", "graphMode": "none"},
    }


def gauge_panel(title, domain, flux, x, y, w=6, h=STAT_H, unit="percent", thresholds=None):
    return {
        "id": next_id(), "type": "gauge", "title": title,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "datasource": DS[domain],
        "targets": [_target(domain, flux)],
        "fieldConfig": {"defaults": {"unit": unit, "min": 0, "max": 100,
                                      "thresholds": {"mode": "absolute", "steps": thresholds}}, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "showThresholdLabels": False, "showThresholdMarkers": True},
    }


def timeseries_panel(title, domain, queries, x, y, w=12, h=TS_H, unit="none"):
    targets = [_target(domain, flux, ref_id=chr(65 + i)) for i, (_, flux) in enumerate(queries)]
    # Flux queries all return a generically-named value field (or, post the
    # _start/_stop fix, one named after the InfluxDB field) - with two
    # targets in one panel that's not enough to tell series apart in the
    # legend/tooltip, so pin each target's series name explicitly via a
    # byFrameRefID override rather than relying on whatever Grafana infers.
    overrides = [
        {"matcher": {"id": "byFrameRefID", "options": chr(65 + i)},
         "properties": [{"id": "displayName", "value": label}]}
        for i, (label, _) in enumerate(queries)
    ]
    return {
        "id": next_id(), "type": "timeseries", "title": title,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "datasource": DS[domain],
        "targets": targets,
        "fieldConfig": {"defaults": {
            "unit": unit,
            "color": {"mode": "palette-classic"},
            "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 10,
                       "pointSize": 5, "spanNulls": True, "showPoints": "never"},
        }, "overrides": overrides},
        "options": {"legend": {"displayMode": "list", "placement": "bottom", "calcs": []},
                    "tooltip": {"mode": "multi"}},
    }


def bar_panel(title, domain, queries, x, y, w=12, h=TS_H, unit="none"):
    p = timeseries_panel(title, domain, queries, x, y, w, h, unit)
    p["type"] = "barchart"
    return p


def state_timeline_panel(title, domain, flux, x, y, mappings, w=WIDTH, h=STATE_H):
    return {
        "id": next_id(), "type": "state-timeline", "title": title,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "datasource": DS[domain],
        "targets": [_target(domain, flux)],
        "fieldConfig": {"defaults": {"mappings": mappings, "color": {"mode": "thresholds"},
                                      "thresholds": {"mode": "absolute", "steps": [{"value": None, "color": "green"}]}},
                        "overrides": []},
        "options": {"mergeValues": True, "showValue": "auto", "rowHeight": 0.9},
    }


def table_panel(title, domain, flux, x, y, w=WIDTH, h=TABLE_H):
    return {
        "id": next_id(), "type": "table", "title": title,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "datasource": DS[domain],
        "targets": [_target(domain, flux)],
        "fieldConfig": {"defaults": {}, "overrides": []},
        "options": {"showHeader": True, "sortBy": [{"displayName": "Time", "desc": True}]},
    }


def bool_mappings(true_text, true_color, false_text, false_color):
    return [
        {"type": "special", "options": {"match": "true", "result": {"text": true_text, "color": true_color}}},
        {"type": "special", "options": {"match": "false", "result": {"text": false_text, "color": false_color}}},
    ]


# Every query below drops "host" and regroups on just (_measurement, _field)
# before returning data. telegraf tags every metric with the collecting
# container's hostname; without a pinned hostname (see telegraf.conf
# [agent].hostname), each container recreate got a new random one, silently
# fragmenting every field into a separate "host" series - which Grafana then
# rendered as multiple overlapping lines for what's actually one continuous
# reading. The pinned hostname stops new fragmentation; this stops the
# already-recorded fragments from still showing as duplicates, without
# having to delete/rewrite any historical points.
#
# Also drops "_start"/"_stop" - Flux's range() annotates every row with the
# query window's boundaries, not the row's own timestamp. Those two columns
# are also time-typed and, left in, sort ahead of "_time" in the returned
# frame's field order - Grafana's timeseries panel picks the first
# time-typed field as its X axis, so it was plotting every row at the
# constant "_start" value instead of its actual "_time", collapsing an
# entire range of points onto one X position (confirmed via Panel ->
# Inspect -> Data: full, correctly-varying series arrives in the browser,
# it's only the chart's X-axis field choice that was wrong).
def _drop_host(flux):
    return flux + '\n  |> drop(columns: ["host", "_start", "_stop"])\n  |> group(columns: ["_measurement", "_field"])'


def flux_range(bucket, measurement, fields, aggregate="mean"):
    # telegraf samples most of these every 5s, so an un-aggregated 24h range
    # is 17000+ raw points - Grafana silently truncates any single query at
    # 1001 points ("results have been truncated..."), which keeps only the
    # earliest chronological slice and drops everything after, making a
    # perfectly continuous series look like it stops partway through the
    # window. aggregateWindow(v.windowPeriod) downsamples to roughly one
    # point per pixel instead, which stays under that cap at any zoom level.
    # `aggregate` is the Flux reducer name; pass "last" for discrete/enum
    # fields (e.g. shutter_status) where averaging would be meaningless.
    field_filter = " or ".join(f'r._field == "{f}"' for f in fields)
    flux = (
        f'from(bucket: "{bucket}")\n'
        f'  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
        f'  |> filter(fn: (r) => r._measurement == "{measurement}" and ({field_filter}))'
    )
    if aggregate:
        flux += f'\n  |> aggregateWindow(every: v.windowPeriod, fn: {aggregate}, createEmpty: false)'
    return _drop_host(flux)


def flux_last(bucket, measurement, field):
    return _drop_host(
        f'from(bucket: "{bucket}")\n'
        f'  |> range(start: -1h)\n'
        f'  |> filter(fn: (r) => r._measurement == "{measurement}" and r._field == "{field}")'
    ) + '\n  |> last()'


# N.I.N.A.'s InfluxDB Exporter plugin (daleghent/nina-influxdb-exporter) uses
# a different schema than our own telegraf-authored buckets: every metric is
# its OWN InfluxDB measurement (e.g. "image_hfr", "camera_sensor_temperature")
# with a single field always literally named "value". Confirmed against the
# plugin's README and its own example Grafana dashboard (both fetched from
# github.com/daleghent/nina-influxdb-exporter).
def nina_range(bucket, measurements, aggregate="mean"):
    measurement_filter = " or ".join(f'r._measurement == "{m}"' for m in measurements)
    flux = (
        f'from(bucket: "{bucket}")\n'
        f'  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
        f'  |> filter(fn: (r) => ({measurement_filter}) and r._field == "value")'
    )
    if aggregate:
        flux += f'\n  |> aggregateWindow(every: v.windowPeriod, fn: {aggregate}, createEmpty: false)'
    return flux


def nina_last(bucket, measurement):
    return (
        f'from(bucket: "{bucket}")\n'
        f'  |> range(start: -1d)\n'
        f'  |> filter(fn: (r) => r._measurement == "{measurement}" and r._field == "value")\n'
        f'  |> last()'
    )


def build():
    panels = []
    y = 0

    # ---------------------------------------------------------------- Power
    panels.append(row("Power", y)); y += ROW_H
    panels.append(gauge_panel("Battery SOC", "power", flux_last("power", "victron_smartshunt", "soc"),
                               0, y, thresholds=[{"value": None, "color": "red"},
                                                  {"value": 20, "color": "yellow"},
                                                  {"value": 50, "color": "green"}]))
    panels.append(stat_panel("Time To Go", "power", flux_last("power", "victron_smartshunt", "time_to_go_min"),
                              6, y, unit="m"))
    panels.append(stat_panel("Controller Temp", "power", flux_last("power", "eco_worthy_mppt", "controller_temp"),
                              12, y, unit="celsius",
                              thresholds=[{"value": None, "color": "green"}, {"value": 45, "color": "yellow"}, {"value": 60, "color": "red"}]))
    panels.append(stat_panel("Consumed Ah", "power", flux_last("power", "victron_smartshunt", "consumed_ah"),
                              18, y, unit="none"))
    y += STAT_H

    panels.append(timeseries_panel("Battery Voltage", "power",
                                    [("voltage", flux_range("power", "victron_smartshunt", ["voltage"]))],
                                    0, y, unit="volt"))
    panels.append(timeseries_panel("Battery & Charging Current", "power",
                                    [("current", flux_range("power", "victron_smartshunt", ["current"])),
                                     ("charging_current", flux_range("power", "eco_worthy_mppt", ["charging_current"]))],
                                    12, y, unit="amp"))
    y += TS_H

    panels.append(timeseries_panel("Net Power vs Solar (PV) Power", "power",
                                    [("net_power", flux_range("power", "victron_smartshunt", ["power"])),
                                     ("pv_power", flux_range("power", "eco_worthy_mppt", ["pv_power"]))],
                                    0, y, unit="watt"))
    panels.append(timeseries_panel("PV Voltage & Current", "power",
                                    [("pv_voltage", flux_range("power", "eco_worthy_mppt", ["pv_voltage"])),
                                     ("pv_current", flux_range("power", "eco_worthy_mppt", ["pv_current"]))],
                                    12, y, unit="none"))
    y += TS_H

    # -------------------------------------------------------------- Weather
    panels.append(row("Weather", y)); y += ROW_H
    panels.append(stat_panel("Outdoor Temperature", "weather", flux_last("weather", "weather_station", "temp_f"),
                              0, y, unit="fahrenheit"))
    panels.append(stat_panel("Outdoor Humidity", "weather", flux_last("weather", "weather_station", "humidity_pct"),
                              6, y, unit="percent"))
    panels.append(stat_panel("Indoor Temperature", "weather", flux_last("weather", "weather_station", "temp_in_f"),
                              12, y, unit="fahrenheit"))
    panels.append(stat_panel("Indoor Humidity", "weather", flux_last("weather", "weather_station", "humidity_in_pct"),
                              18, y, unit="percent"))
    y += STAT_H

    panels.append(stat_panel("Wind Speed", "weather", flux_last("weather", "weather_station", "wind_speed_mph"),
                              0, y, unit="velocitymph"))
    panels.append(stat_panel("Pressure", "weather", flux_last("weather", "weather_station", "pressure_inhg"),
                              6, y, unit="pressurehg"))
    panels.append(gauge_panel("Sensor Battery", "weather", flux_last("weather", "weather_station", "battery_pct"),
                               12, y, unit="percent",
                               thresholds=[{"value": None, "color": "red"},
                                           {"value": 40, "color": "yellow"},
                                           {"value": 60, "color": "green"}]))
    panels.append(stat_panel("Moon Phase", "weather", flux_last("weather", "weather_station", "moon_phase_name"),
                              18, y, unit="none", color_mode="fixed"))
    y += STAT_H

    panels.append(timeseries_panel("Outdoor vs Indoor Temperature", "weather",
                                    [("Outdoor", flux_range("weather", "weather_station", ["temp_f"])),
                                     ("Indoor", flux_range("weather", "weather_station", ["temp_in_f"]))],
                                    0, y, unit="fahrenheit"))
    panels.append(timeseries_panel("Outdoor vs Indoor Humidity", "weather",
                                    [("Outdoor", flux_range("weather", "weather_station", ["humidity_pct"])),
                                     ("Indoor", flux_range("weather", "weather_station", ["humidity_in_pct"]))],
                                    12, y, unit="percent"))
    y += TS_H

    panels.append(timeseries_panel("Wind Speed & Gust", "weather",
                                    [("Speed", flux_range("weather", "weather_station", ["wind_speed_mph"])),
                                     ("Gust", flux_range("weather", "weather_station", ["wind_gust_mph"]))],
                                    0, y, unit="velocitymph"))
    panels.append(bar_panel("Rain Accumulation", "weather",
                             [("Hourly", flux_range("weather", "weather_station", ["rain_hourly_in"])),
                              ("Daily", flux_range("weather", "weather_station", ["rain_daily_in"]))],
                             12, y, unit="lengthin"))
    y += TS_H

    panels.append(timeseries_panel("Solar Radiation & UV Index", "weather",
                                    [("Solar Radiation (W/m²)", flux_range("weather", "weather_station", ["solar_radiation_wm2"])),
                                     ("UV Index", flux_range("weather", "weather_station", ["uv_index"]))],
                                    0, y, w=12, unit="none"))
    panels.append(timeseries_panel("Moon Illumination", "weather",
                                    [("Illumination", flux_range("weather", "weather_station", ["moon_illumination_pct"]))],
                                    12, y, w=12, unit="percent"))
    y += TS_H

    # --------------------------------------------------------- Observatory
    panels.append(row("Observatory", y)); y += ROW_H
    shutter_mappings = [{"type": "value", "options": {
        "0": {"text": "Open", "color": "green"},
        "1": {"text": "Closed", "color": "blue"},
        "2": {"text": "Opening", "color": "yellow"},
        "3": {"text": "Closing", "color": "yellow"},
        "4": {"text": "Error", "color": "red"},
    }}]
    panels.append(stat_panel("Roof Shutter Status", "observatory",
                              flux_last("observatory", "observatory_roof", "shutter_status"),
                              0, y, mappings=shutter_mappings, color_mode="fixed"))
    panels.append(stat_panel("Slewing", "observatory", flux_last("observatory", "observatory_roof", "slewing"),
                              6, y, mappings=bool_mappings("Slewing", "yellow", "Idle", "green"), color_mode="fixed"))
    panels.append(stat_panel("At Home", "observatory", flux_last("observatory", "observatory_roof", "at_home"),
                              12, y, mappings=bool_mappings("Yes", "green", "No", "text"), color_mode="fixed"))
    panels.append(stat_panel("Connected", "observatory", flux_last("observatory", "observatory_roof", "connected"),
                              18, y, mappings=bool_mappings("Yes", "green", "No", "red"), color_mode="fixed"))
    y += STAT_H

    panels.append(state_timeline_panel("Roof Shutter Status History", "observatory",
                                        flux_range("observatory", "observatory_roof", ["shutter_status"], aggregate="last"),
                                        0, y, shutter_mappings))
    y += STATE_H

    # ------------------------------------------------------------- Imaging
    # Schema here comes from N.I.N.A.'s InfluxDB Exporter plugin, not
    # telegraf - see the nina_last/nina_range helpers above.
    panels.append(row("Imaging", y)); y += ROW_H
    panels.append(stat_panel("Camera Sensor Temp", "imaging", nina_last("imaging", "camera_sensor_temperature"),
                              0, y, unit="celsius"))
    panels.append(stat_panel("Cooler Power", "imaging", nina_last("imaging", "camera_cooler_power"),
                              6, y, unit="percent"))
    panels.append(stat_panel("Focuser Position", "imaging", nina_last("imaging", "focuser_position"),
                              12, y, unit="none"))
    panels.append(stat_panel("Sun Altitude", "imaging", nina_last("imaging", "astro_sun_altitude"),
                              18, y, unit="degree"))
    y += STAT_H

    panels.append(timeseries_panel("Image HFR Trend", "imaging",
                                    [("image_hfr", nina_range("imaging", ["image_hfr"]))],
                                    0, y, unit="none"))
    panels.append(timeseries_panel("Image Star Count Trend", "imaging",
                                    [("image_star_count", nina_range("imaging", ["image_star_count"]))],
                                    12, y, unit="none"))
    y += TS_H

    panels.append(timeseries_panel("Guiding RMS", "imaging",
                                    [("ra", nina_range("imaging", ["guider_rms_ra_arcsec"])),
                                     ("dec", nina_range("imaging", ["guider_rms_dec_arcsec"])),
                                     ("total", nina_range("imaging", ["guider_rms_arcsec"]))],
                                    0, y, unit="none"))
    panels.append(timeseries_panel("Sun & Moon Altitude", "imaging",
                                    [("sun", nina_range("imaging", ["astro_sun_altitude"])),
                                     ("moon", nina_range("imaging", ["astro_moon_altitude"]))],
                                    12, y, unit="degree"))
    y += TS_H

    panels.append(timeseries_panel("Camera Sensor Temp Trend", "imaging",
                                    [("camera_sensor_temperature", nina_range("imaging", ["camera_sensor_temperature"]))],
                                    0, y, unit="celsius"))
    panels.append(timeseries_panel("Focuser Temp Trend", "imaging",
                                    [("focuser_temperature", nina_range("imaging", ["focuser_temperature"]))],
                                    12, y, unit="celsius"))
    y += TS_H

    panels.append(table_panel("Recent Frames (LIGHT only)", "imaging",
                               nina_range("imaging", ["image_hfr", "image_star_count", "image_mean",
                                                       "image_median", "image_eccentricity"])
                               + '\n  |> pivot(rowKey: ["_time"], columnKey: ["_measurement"], valueColumn: "_value")'
                               + '\n  |> sort(columns: ["_time"], desc: true)',
                               0, y))
    y += TABLE_H

    return {
        "id": None,
        "uid": "solar-observatory",
        "title": "Solar / Weather / Observatory / Imaging",
        "tags": ["solar", "weather", "observatory", "imaging"],
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 1,
        "refresh": "30s",
        "time": {"from": "now-24h", "to": "now"},
        "panels": panels,
    }


if __name__ == "__main__":
    out_dir = os.path.join(os.path.dirname(__file__), "provisioning", "dashboards")
    out_path = os.path.join(out_dir, "solar-observatory.json")
    with open(out_path, "w") as f:
        json.dump(build(), f, indent=2)
        f.write("\n")
    print(f"Wrote {out_path}")
