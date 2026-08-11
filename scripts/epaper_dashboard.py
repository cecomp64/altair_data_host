#!/usr/bin/env python3
import os
import time
import datetime
from PIL import Image, ImageDraw, ImageFont
from influxdb_client import InfluxDBClient
from waveshare_epd import epd7in5b_V2  # Waveshare 7.5" B V2 tri-color (black/white/red) driver, 800x480
# Different 7.5" model? Swap the import/class above:
#   monochrome:                  epd7in5_V2
#   HD (880x528, grayscale):     epd7in5_HD
# These have a different display()/Clear() protocol - the tri-color driver writes
# separate black-plane (0x10) and red-plane (0x13) bitmaps, while the monochrome
# driver's display() uses those same two commands for an old/new frame pair
# instead. Passing a mono-driver buffer through the tri-color driver's display()
# (or vice versa) silently misinterprets the second buffer as red-channel data -
# every "black" pixel bleeds into red on this panel.

# Runs on the host OS (see scripts/install-epaper-service.sh), which loads
# these from the repo's .env via the systemd unit's EnvironmentFile=.
INFLUX_URL = os.environ.get("INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = os.environ["INFLUXDB_ADMIN_TOKEN"]
INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "solar")
INFLUX_BUCKET_POWER = os.environ.get("INFLUXDB_BUCKET_POWER", "power")
INFLUX_BUCKET_WEATHER = os.environ.get("INFLUXDB_BUCKET_WEATHER", "weather")
WEATHER_STATION_NAME = os.environ.get("WEATHER_STATION_NAME", "primary")

FONT_BOLD = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
FONT_REGULAR = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'

EPD_W, EPD_H = 800, 480  # panel resolution, also used to size the offscreen canvas


def fetch_power_metrics():
    """Queries InfluxDB for the most recent battery/solar readings."""
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    query_api = client.query_api()

    # drop/group on host: telegraf tags every metric with its collecting
    # container's hostname (pinned in telegraf.conf, but historical/stale
    # values can still linger in a lookback window right after a container
    # recreate) - without this, last() runs per host-tag group instead of
    # globally, and results become dependent on flux's group iteration order.
    flux_query = f'''
    from(bucket: "{INFLUX_BUCKET_POWER}")
      |> range(start: -5m)
      |> drop(columns: ["host"])
      |> group(columns: ["_measurement", "_field"])
      |> last()
    '''

    metrics = {
        'soc': 0.0,
        'battery_volts': 0.0,
        'net_power': 0.0,
        'time_to_go_min': 0.0,
        'consumed_ah': 0.0,
        'pv_power': 0.0,
        'pv_voltage': 0.0,
        'pv_current': 0.0,
        'charging_current': 0.0,
        'controller_temp': 0.0,
    }

    try:
        result = query_api.query(query=flux_query, org=INFLUX_ORG)
        for table in result:
            for record in table.records:
                measurement = record.get_measurement()
                field = record.get_field()
                val = record.get_value()

                if measurement == 'victron_smartshunt':
                    if field == 'soc': metrics['soc'] = float(val)
                    elif field == 'voltage': metrics['battery_volts'] = float(val)
                    elif field == 'power': metrics['net_power'] = float(val)
                    elif field == 'time_to_go_min': metrics['time_to_go_min'] = float(val)
                    elif field == 'consumed_ah': metrics['consumed_ah'] = float(val)
                elif measurement == 'eco_worthy_mppt':
                    if field == 'pv_power': metrics['pv_power'] = float(val)
                    elif field == 'pv_voltage': metrics['pv_voltage'] = float(val)
                    elif field == 'pv_current': metrics['pv_current'] = float(val)
                    elif field == 'charging_current': metrics['charging_current'] = float(val)
                    elif field == 'controller_temp': metrics['controller_temp'] = float(val)
    except Exception as e:
        print(f"InfluxDB Query Error (power): {e}")
    finally:
        client.close()

    return metrics


def fetch_weather_metrics():
    """Queries InfluxDB for the most recent weather station readings."""
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    query_api = client.query_api()

    # See fetch_power_metrics for why host is dropped before last().
    flux_query = f'''
    from(bucket: "{INFLUX_BUCKET_WEATHER}")
      |> range(start: -30m)
      |> filter(fn: (r) => r.station == "{WEATHER_STATION_NAME}")
      |> drop(columns: ["host"])
      |> group(columns: ["_measurement", "_field"])
      |> last()
    '''

    metrics = {
        'temp_f': 0.0,
        'humidity_pct': 0.0,
        'pressure_inhg': 0.0,
        'wind_speed_mph': 0.0,
        'wind_gust_mph': 0.0,
        'rain_daily_in': 0.0,
        'solar_radiation_wm2': 0.0,
        'uv_index': 0.0,
        'temp_in_f': 0.0,
        'humidity_in_pct': 0.0,
        'battery_pct': 0.0,
        'moon_illumination_pct': 0.0,
        'moon_phase_name': '',
    }
    string_fields = {'moon_phase_name'}

    try:
        result = query_api.query(query=flux_query, org=INFLUX_ORG)
        for table in result:
            for record in table.records:
                if record.get_measurement() != 'weather_station':
                    continue
                field = record.get_field()
                val = record.get_value()
                if field not in metrics:
                    continue
                metrics[field] = val if field in string_fields else float(val)
    except Exception as e:
        print(f"InfluxDB Query Error (weather): {e}")
    finally:
        client.close()

    return metrics


def draw_stat(draw, x, y, w, label, value, font_label, font_value):
    """A label/value pair, value right-aligned within width w."""
    draw.text((x, y), label, font=font_label, fill=0)
    value_w = draw.textbbox((0, 0), value, font=font_value)[2]
    draw.text((x + w - value_w, y + 18), value, font=font_value, fill=0)


def draw_heading(draw, x, y, label, value, font):
    """Section heading with the label and value inline, e.g. 'SOLAR 318 W'."""
    draw.text((x, y), f"{label} {value}", font=font, fill=0)


def draw_gauge(draw, x, y, w, h, pct):
    """A horizontal fill bar for a 0-100 quantity (SOC, humidity, ...)."""
    draw.rectangle((x, y, x + w, y + h), outline=0, width=2)
    fill_w = int((w - 6) * max(0.0, min(100.0, pct)) / 100.0)
    if fill_w > 0:
        draw.rectangle((x + 3, y + 3, x + 3 + fill_w, y + h - 3), fill=0)


# Full phase names ("Waxing Crescent") don't fit a stat_value column at this
# panel's width/font size - abbreviated for display only, not stored data.
MOON_PHASE_ABBREV = {
    "New Moon": "New",
    "Waxing Crescent": "Waxing Cr.",
    "First Quarter": "1st Qtr",
    "Waxing Gibbous": "Waxing Gib.",
    "Full Moon": "Full",
    "Waning Gibbous": "Waning Gib.",
    "Last Quarter": "Last Qtr",
    "Waning Crescent": "Waning Cr.",
}


def render_dashboard(power, weather, W=EPD_W, H=EPD_H):
    """Renders the dashboard to a 1-bit PIL Image. Pure function of the metrics
    dicts, so it can be exercised without any e-Paper hardware present."""
    font_heading = ImageFont.truetype(FONT_BOLD, 36)
    font_stat_label = ImageFont.truetype(FONT_REGULAR, 15)
    font_stat_value = ImageFont.truetype(FONT_BOLD, 23)
    font_footer = ImageFont.truetype(FONT_REGULAR, 16)
    font_footer_bold = ImageFont.truetype(FONT_BOLD, 16)

    image = Image.new('1', (W, H), 255)
    draw = ImageDraw.Draw(image)

    MARGIN = 10
    CONTENT_TOP = MARGIN + 14
    FOOTER_Y = H - 50
    MID_X = W // 2

    # Frame
    draw.rectangle((MARGIN, MARGIN, W - MARGIN, H - MARGIN), outline=0, width=2)
    draw.line((MID_X, MARGIN, MID_X, FOOTER_Y), fill=0, width=2)
    draw.line((MARGIN, FOOTER_Y, W - MARGIN, FOOTER_Y), fill=0, width=2)

    # ================= Left half: POWER (battery + solar, stacked) =================
    lx = MARGIN + 20
    lw = MID_X - lx - 20
    stat_w = lw // 2

    # Battery and Solar each get an equal half of the vertical space, so the
    # two zones read as evenly spaced regardless of content.
    ZONE_H = (FOOTER_Y - CONTENT_TOP) // 2
    zone1_top = CONTENT_TOP
    zone2_top = CONTENT_TOP + ZONE_H

    # Divider between the battery and solar zones
    div_y = zone2_top
    draw.line((lx, div_y, lx + lw, div_y), fill=0, width=1)

    # --- Battery (shunt) zone ---
    by = zone1_top + 18
    draw_heading(draw, lx, by, "BATTERY", f"{power['soc']:.0f}%", font_heading)
    draw_gauge(draw, lx, by + 48, lw, 14, power['soc'])

    stat_y = by + 70
    draw_stat(draw, lx, stat_y, stat_w - 10, "Battery Voltage", f"{power['battery_volts']:.2f} V",
              font_stat_label, font_stat_value)
    draw_stat(draw, lx + stat_w + 10, stat_y, stat_w - 10, "Net Power", f"{power['net_power']:.0f} W",
              font_stat_label, font_stat_value)
    stat_y += 46
    draw_stat(draw, lx, stat_y, stat_w - 10, "Time To Go", f"{power['time_to_go_min'] / 60.0:.1f} h",
              font_stat_label, font_stat_value)
    draw_stat(draw, lx + stat_w + 10, stat_y, stat_w - 10, "Consumed", f"{power['consumed_ah']:.1f} Ah",
              font_stat_label, font_stat_value)

    # --- Solar (MPPT) zone ---
    sy = zone2_top + 18
    draw_heading(draw, lx, sy, "SOLAR", f"{power['pv_power']:.0f} W", font_heading)

    stat_y = sy + 50
    draw_stat(draw, lx, stat_y, stat_w - 10, "PV Voltage", f"{power['pv_voltage']:.1f} V",
              font_stat_label, font_stat_value)
    draw_stat(draw, lx + stat_w + 10, stat_y, stat_w - 10, "PV Current", f"{power['pv_current']:.2f} A",
              font_stat_label, font_stat_value)
    stat_y += 46
    draw_stat(draw, lx, stat_y, stat_w - 10, "Charge Current", f"{power['charging_current']:.2f} A",
              font_stat_label, font_stat_value)
    draw_stat(draw, lx + stat_w + 10, stat_y, stat_w - 10, "Controller Temp", f"{power['controller_temp']:.0f} C",
              font_stat_label, font_stat_value)

    # ================= Right half: WEATHER =================
    # Outdoor temp/humidity get the heading + gauge treatment (like SOC on
    # the power side); everything else - wind, pressure, solar/UV, rain,
    # indoor conditions, sensor battery, moon phase - is a tight 5-row x
    # 2-column stat grid below, sized to fit this panel's ~406px height.
    rx = MID_X + 20
    rw = (W - MARGIN) - rx - 20
    wy = zone1_top + 18
    draw_heading(draw, rx, wy, "WEATHER", f"{weather['temp_f']:.0f}°F", font_heading)
    draw_gauge(draw, rx, wy + 48, rw, 18, weather['humidity_pct'])
    draw.text((rx, wy + 74), f"Outdoor Humidity {weather['humidity_pct']:.0f}%",
              font=font_stat_label, fill=0)

    moon_label = MOON_PHASE_ABBREV.get(weather['moon_phase_name'], weather['moon_phase_name'] or "-")
    stat_w = rw // 2
    grid_rows = [
        ("Wind Speed", f"{weather['wind_speed_mph']:.0f} mph", "Wind Gust", f"{weather['wind_gust_mph']:.0f} mph"),
        ("Pressure", f"{weather['pressure_inhg']:.2f} inHg", "Solar", f"{weather['solar_radiation_wm2']:.0f} W/m2"),
        ("UV Index", f"{weather['uv_index']:.1f}", "Rain Today", f"{weather['rain_daily_in']:.2f} in"),
        ("Indoor Temp", f"{weather['temp_in_f']:.0f}°F", "Indoor Humidity", f"{weather['humidity_in_pct']:.0f}%"),
        ("Sensor Battery", f"{weather['battery_pct']:.0f}%", "Moon Phase", moon_label),
    ]
    grid_top = wy + 102
    row_pitch = 54
    for i, (label1, value1, label2, value2) in enumerate(grid_rows):
        gy = grid_top + i * row_pitch
        draw_stat(draw, rx, gy, stat_w - 10, label1, value1, font_stat_label, font_stat_value)
        draw_stat(draw, rx + stat_w + 10, gy, stat_w - 10, label2, value2, font_stat_label, font_stat_value)

    # Footer: timestamp (left) + charge state (right)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    draw.text((MARGIN + 15, FOOTER_Y + 15), f"Updated: {timestamp}", font=font_footer, fill=0)

    if power['net_power'] > 5:
        state = "CHARGING"
    elif power['net_power'] < -5:
        state = "DISCHARGING"
    else:
        state = "IDLE"
    state_w = draw.textbbox((0, 0), state, font=font_footer_bold)[2]
    draw.text((W - MARGIN - 15 - state_w, FOOTER_Y + 15), state, font=font_footer_bold, fill=0)

    return image


def draw_and_update_display(safety_clear=False):
    """Renders pixel canvas and flashes e-Paper screen.

    Every call here is already a full refresh (never partial - see the
    REFRESH_SECONDS comment below on why), and a full refresh's waveform
    flashes every pixel through black/red/white regardless of the previous
    frame before settling on the new one - it's a reset-and-redraw, not a
    diff against the old frame. Testing confirmed this directly: drawing a
    solid black block, then a different solid block, then plain white, with
    no Clear() anywhere in between, came out completely clean - no ghosting
    from either prior frame. So a separate deep-clear before drawing is not
    needed for routine ghosting; safety_clear exists purely as a cheap,
    rare insurance pass (see DEEP_CLEAR_EVERY_N_UPDATES) against slower
    degradation a few back-to-back refreshes wouldn't reveal.
    """
    power = fetch_power_metrics()
    weather = fetch_weather_metrics()

    epd = epd7in5b_V2.EPD()
    # init_Fast() (different booster/temperature registers than init()) selects
    # a shorter waveform - measured ~12.6s per full refresh here vs ~19s under
    # the standard init(), for both Clear() and display(), with no visible
    # quality loss. This chip has no exposed way to load a custom LUT over SPI
    # (no 0x20-0x24 commands in this driver), so init_Fast() is the fastest
    # full-refresh mode actually available - not something to hand-roll further.
    epd.init_Fast()
    if safety_clear:
        epd.Clear()

    image = render_dashboard(power, weather, epd.width, epd.height)

    # Update Display & Sleep Screen (Prevents burn-in)
    # The dashboard is drawn black-on-white only, so the red plane stays blank -
    # this driver's display() takes black/red as two separate bitmaps, not an
    # old/new frame pair like the monochrome driver.
    black_buf = epd.getbuffer(image)
    red_buf = bytearray(len(black_buf))
    epd.display(black_buf, red_buf)
    epd.sleep()


# A full display() on this panel takes ~12.6s under init_Fast() regardless of
# whether Clear() ran first - full refresh is a flash-and-reset waveform, not a
# diff against the old frame, so it's already self-cleaning every time (see
# draw_and_update_display's docstring). Partial refresh (display_Partial) looked
# like a way to go faster still, but this driver's partial mode only touches the
# black plane - it can't move the red pigment at all, and testing showed a few
# partial cycles in a row leave visible red bleed-through and black-plane
# ghosting within minutes. Don't use display_Partial()/init_part() on this
# hardware for anything beyond a single throwaway test.
REFRESH_SECONDS = int(os.environ.get("EPAPER_REFRESH_SECONDS", "60"))
# Purely a rare safety net (see draw_and_update_display) - at 60s/refresh this
# is roughly once a day. Set to 0 to disable it outright.
DEEP_CLEAR_EVERY_N_UPDATES = int(os.environ.get("EPAPER_DEEP_CLEAR_EVERY", "1440"))

if __name__ == "__main__":
    update_count = 0
    while True:
        safety_clear = (
            DEEP_CLEAR_EVERY_N_UPDATES > 0
            and update_count % DEEP_CLEAR_EVERY_N_UPDATES == 0
        )
        draw_and_update_display(safety_clear=safety_clear)
        update_count += 1
        time.sleep(REFRESH_SECONDS)
