#!/usr/bin/env python3
"""
DEV/DEMO ONLY - generates synthetic InfluxDB line protocol so you can see
what the Grafana dashboard looks like without real hardware attached. Do not
run this against a real deployment's buckets - it will mix fake data in with
real telemetry.

Pure stdlib (no influxdb_client dependency) - writes four .lp files that
scripts/seed-mock-data.sh then loads via `docker compose exec influxdb influx
write`. Run directly for a custom range/output dir; normally invoked through
that wrapper script.
"""
import argparse
import math
import random
from datetime import datetime, timedelta


def esc_tag(v):
    return str(v).replace(" ", r"\ ").replace(",", r"\,").replace("=", r"\=")


def esc_str_field(v):
    return v.replace('"', r"\"")


def lp_line(measurement, tags, fields, ts):
    tag_str = "".join(f",{k}={esc_tag(v)}" for k, v in tags.items())
    parts = []
    for k, v in fields.items():
        if isinstance(v, bool):
            parts.append(f"{k}={'true' if v else 'false'}")
        elif isinstance(v, int):
            parts.append(f"{k}={v}i")
        elif isinstance(v, float):
            parts.append(f"{k}={v:.3f}")
        else:
            parts.append(f'{k}="{esc_str_field(v)}"')
    field_str = ",".join(parts)
    return f"{measurement}{tag_str} {field_str} {int(ts)}"


def clamp(v, lo, hi=None):
    # Every field that flows through here is a float field in InfluxDB - a
    # field's type must stay consistent across all points in a series, so
    # this always returns float even when an int bound (e.g. clamp(x, 0, 100))
    # would otherwise win the max()/min() comparison and silently flip the type.
    v = max(float(lo), v)
    return v if hi is None else min(float(hi), v)


def diurnal(dt, peak_hour, amplitude, baseline, width_hours=5.0):
    """Bell-curve-ish diurnal value peaking at peak_hour (wraps over 24h)."""
    hour = dt.hour + dt.minute / 60.0
    delta = (hour - peak_hour + 12) % 24 - 12
    return baseline + amplitude * math.exp(-(delta ** 2) / (2 * width_hours ** 2))


def is_night(dt, dusk_hour=20.75, dawn_hour=4.25):
    hour = dt.hour + dt.minute / 60.0
    return hour >= dusk_hour or hour < dawn_hour


def roof_shutter_status(dt, error_at=None):
    """0=Open 1=Closed 2=Opening 3=Closing 4=Error. Transitions at dusk/dawn."""
    if error_at is not None and abs((dt - error_at).total_seconds()) < 10 * 60:
        return 4
    hour = dt.hour + dt.minute / 60.0
    if 20.75 <= hour < 21.0:
        return 2  # opening
    if 21.0 <= hour or hour < 4.0:
        return 0  # open
    if 4.0 <= hour < 4.25:
        return 3  # closing
    return 1  # closed


def gen_power(start, end, rng):
    lines = []
    t = start
    consumed_ah = 8.0
    while t < end:
        pv_power = max(0.0, diurnal(t, peak_hour=13, amplitude=340, baseline=0, width_hours=3.2)
                        + rng.gauss(0, 12))
        pv_power *= rng.uniform(0.85, 1.0)  # occasional cloud haze
        pv_voltage = 19.5 + rng.gauss(0, 0.3) if pv_power > 5 else 0.0
        pv_current = (pv_power / pv_voltage) if pv_voltage > 1 else 0.0
        controller_temp = diurnal(t, peak_hour=14, amplitude=22, baseline=16, width_hours=4) + rng.gauss(0, 1.5)

        load_w = 55 + rng.gauss(0, 8)
        net_power = pv_power - load_w
        battery_voltage = clamp(12.2 + (net_power / 400.0), 11.8, 13.6)
        charging_current = max(0.0, net_power / battery_voltage)

        if net_power > 0:
            consumed_ah = max(0.0, consumed_ah - (net_power / battery_voltage) * (60 / 3600))
        else:
            consumed_ah = min(60.0, consumed_ah + (-net_power / battery_voltage) * (60 / 3600))
        soc = clamp(100 - (consumed_ah / 60.0) * 100, 15, 100)
        time_to_go_min = 99999.0 if net_power >= 0 else clamp((soc / 100 * 60) / max(0.5, -net_power / battery_voltage) * 60, 30, 6000)

        lines.append(lp_line("victron_smartshunt", {}, {
            "voltage": battery_voltage,
            "current": (net_power / battery_voltage),
            "power": net_power,
            "soc": soc,
            "time_to_go_min": time_to_go_min,
            "consumed_ah": consumed_ah,
        }, t.timestamp()))

        lines.append(lp_line("eco_worthy_mppt", {}, {
            "pv_voltage": pv_voltage,
            "pv_current": pv_current,
            "pv_power": pv_power,
            "controller_temp": controller_temp,
            "battery_voltage": battery_voltage,
            "charging_current": charging_current,
        }, t.timestamp()))

        t += timedelta(seconds=60)
    return lines


def gen_weather(start, end, rng, station_name):
    lines = []
    t = start
    rain_event_start = start + timedelta(hours=rng.uniform(10, 30))
    rain_event_end = rain_event_start + timedelta(hours=rng.uniform(1, 3))
    daily_rain = 0.0
    last_day = None
    while t < end:
        if last_day != t.date():
            daily_rain = 0.0
            last_day = t.date()

        temp_f = diurnal(t, peak_hour=15, amplitude=22, baseline=62, width_hours=4.5) + rng.gauss(0, 1.2)
        humidity = clamp(diurnal(t, peak_hour=4, amplitude=30, baseline=35, width_hours=5) + rng.gauss(0, 2), 15, 95)
        pressure = 30.0 + 0.15 * math.sin(t.timestamp() / 36000) + rng.gauss(0, 0.02)
        wind_speed = clamp(4 + 3 * math.sin(t.timestamp() / 5400) + rng.gauss(0, 1.5), 0, None)
        wind_gust = wind_speed + abs(rng.gauss(3, 2))
        wind_dir = (t.timestamp() / 300 * 7) % 360

        raining = rain_event_start <= t <= rain_event_end
        rain_hourly = rng.uniform(0.02, 0.15) if raining else 0.0
        daily_rain += rain_hourly * (5 / 60)

        solar_rad = max(0.0, diurnal(t, peak_hour=13, amplitude=880, baseline=0, width_hours=3.0) + rng.gauss(0, 20))
        if raining:
            solar_rad *= 0.3
        uv = clamp(solar_rad / 105.0, 0, 11)

        lines.append(lp_line("weather_station", {"station": station_name}, {
            "temp_f": temp_f,
            "humidity_pct": humidity,
            "pressure_inhg": pressure,
            "wind_speed_mph": wind_speed,
            "wind_gust_mph": wind_gust,
            "wind_dir_deg": wind_dir,
            "rain_hourly_in": rain_hourly,
            "rain_daily_in": daily_rain,
            "solar_radiation_wm2": solar_rad,
            "uv_index": uv,
        }, t.timestamp()))
        t += timedelta(minutes=5)
    return lines


def gen_observatory(start, end, rng, device_number):
    lines = []
    t = start
    # One deliberate fault blip partway through, so the dashboard shows the
    # Error state/color at least once instead of only ever green/blue/yellow.
    total_hours = (end - start).total_seconds() / 3600
    error_at = start + timedelta(hours=rng.uniform(min(6, total_hours / 2), max(6, total_hours - 6)))
    candidate_hours = list(range(6, int(total_hours), 6))
    slew_pulses = [start + timedelta(hours=h) for h in rng.sample(candidate_hours, k=min(3, len(candidate_hours)))]

    while t < end:
        ts = t.timestamp()
        shutter = roof_shutter_status(t, error_at=error_at)
        slewing = any(abs((t - p).total_seconds()) < 4 * 60 for p in slew_pulses) and shutter == 0
        at_home = not (shutter == 0 and not slewing and is_night(t))
        connected = shutter != 4  # brief disconnect alongside the fault blip

        lines.append(lp_line("observatory_roof", {"device_number": str(device_number)}, {"shutter_status": shutter}, ts))
        lines.append(lp_line("observatory_roof", {"device_number": str(device_number)}, {"slewing": slewing}, ts))
        lines.append(lp_line("observatory_roof", {"device_number": str(device_number)}, {"at_home": at_home}, ts))
        lines.append(lp_line("observatory_roof", {"device_number": str(device_number)}, {"connected": connected}, ts))
        t += timedelta(minutes=5)
    return lines


def gen_imaging(start, end, rng, profile_name, host_name):
    lines = []
    tags = {"profile_name": profile_name, "host_name": host_name}
    t = start
    focuser_position = 12500
    targets = ["M31", "M42", "NGC7000"]
    target_switch_at = start + timedelta(hours=rng.uniform(4, (end - start).total_seconds() / 3600 - 4))
    current_target = targets[0]

    frame_every = timedelta(minutes=6)
    next_frame = None

    while t < end:
        if is_night(t):
            if next_frame is None:
                next_frame = t

            camera_tags = {**tags, "camera_name": "ZWO ASI2600MM Pro"}
            sensor_temp = -10.0 + rng.gauss(0, 0.3)
            cooler_power = clamp(65 + rng.gauss(0, 5), 0, 100)
            lines.append(lp_line("camera_sensor_temperature", camera_tags, {"value": sensor_temp}, t.timestamp()))
            lines.append(lp_line("camera_cooler_power", camera_tags, {"value": cooler_power}, t.timestamp()))

            focuser_tags = {**tags, "focuser_name": "ZWO EAF"}
            if rng.random() < 0.03:
                focuser_position += rng.randint(-40, 40)
            focuser_temp = diurnal(t, peak_hour=15, amplitude=22, baseline=62, width_hours=4.5) - 32
            focuser_temp = (focuser_temp) * 5 / 9  # rough F->C, good enough for demo data
            lines.append(lp_line("focuser_position", focuser_tags, {"value": focuser_position}, t.timestamp()))
            lines.append(lp_line("focuser_temperature", focuser_tags, {"value": focuser_temp}, t.timestamp()))

            guider_tags = {**tags, "guider_name": "PHD2"}
            ra_rms = clamp(0.5 + rng.gauss(0, 0.15), 0.1, 2.5)
            dec_rms = clamp(0.5 + rng.gauss(0, 0.15), 0.1, 2.5)
            combined_rms = math.sqrt(ra_rms ** 2 + dec_rms ** 2)
            lines.append(lp_line("guider_rms_ra_arcsec", guider_tags, {"value": ra_rms}, t.timestamp()))
            lines.append(lp_line("guider_rms_dec_arcsec", guider_tags, {"value": dec_rms}, t.timestamp()))
            lines.append(lp_line("guider_rms_arcsec", guider_tags, {"value": combined_rms}, t.timestamp()))

            lines.append(lp_line("astro_sun_altitude", {}, {"value": -35.0 + rng.gauss(0, 1)}, t.timestamp()))
            lines.append(lp_line("astro_moon_altitude", {}, {
                "value": 40 * math.sin(t.timestamp() / 44700 + 1.2) + rng.gauss(0, 1)
            }, t.timestamp()))

            if t >= next_frame:
                if t >= target_switch_at:
                    current_target = targets[1]
                image_tags = {**tags, "camera_name": "ZWO ASI2600MM Pro", "readout_mode": "16-bit",
                              "sequence_title": "Deep Sky LRGB", "target_name": current_target}
                bad_frame = rng.random() < 0.08
                hfr = rng.uniform(3.2, 4.5) if bad_frame else rng.uniform(1.9, 2.7)
                lines.append(lp_line("image_hfr", image_tags, {"value": hfr}, t.timestamp()))
                lines.append(lp_line("image_star_count", image_tags, {"value": rng.randint(250, 350) if bad_frame else rng.randint(550, 950)}, t.timestamp()))
                lines.append(lp_line("image_mean", image_tags, {"value": rng.uniform(1400, 1800)}, t.timestamp()))
                lines.append(lp_line("image_median", image_tags, {"value": rng.uniform(1350, 1700)}, t.timestamp()))
                lines.append(lp_line("image_eccentricity", image_tags, {"value": rng.uniform(0.25, 0.55)}, t.timestamp()))
                next_frame = t + frame_every
        else:
            next_frame = None

        t += timedelta(minutes=2)
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, default=48, help="How many hours of history to generate")
    parser.add_argument("--end", default=None, help="ISO end timestamp (default: now)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out-dir", default=".")
    parser.add_argument("--weather-station-name", default="primary")
    parser.add_argument("--alpaca-device-number", default="0")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    end = datetime.fromisoformat(args.end) if args.end else datetime.now()
    start = end - timedelta(hours=args.hours)

    datasets = {
        "power": gen_power(start, end, rng),
        "weather": gen_weather(start, end, rng, args.weather_station_name),
        "observatory": gen_observatory(start, end, rng, args.alpaca_device_number),
        "imaging": gen_imaging(start, end, rng, "Default", "imaging-pc"),
    }

    for name, lines in datasets.items():
        path = f"{args.out_dir}/{name}.lp"
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        print(f"{name}: {len(lines)} lines -> {path}")


if __name__ == "__main__":
    main()
