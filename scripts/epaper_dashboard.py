#!/usr/bin/env python3
import os
import time
import datetime
from PIL import Image, ImageDraw, ImageFont
from influxdb_client import InfluxDBClient
from waveshare_epd import epd7in5_V2  # Waveshare 7.5" V2 driver module, 800x480 monochrome
# Different 7.5" model? Swap the import/class above:
#   tri-color (red/black/white): epd7in5b_V2
#   HD (880x528, grayscale):     epd7in5_HD

# Runs on the host OS (see scripts/install-epaper-service.sh), which loads
# these from the repo's .env via the systemd unit's EnvironmentFile=.
INFLUX_URL = os.environ.get("INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = os.environ["INFLUXDB_ADMIN_TOKEN"]
INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "solar")
INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET_POWER", "power")

FONT_BOLD = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
FONT_REGULAR = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'


def fetch_latest_metrics():
    """Queries InfluxDB for the most recent system readings."""
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    query_api = client.query_api()

    flux_query = f'''
    from(bucket: "{INFLUX_BUCKET}")
      |> range(start: -5m)
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
        print(f"InfluxDB Query Error: {e}")
    finally:
        client.close()

    return metrics


def draw_stat(draw, x, y, w, label, value, font_label, font_value):
    """A label/value pair, value right-aligned within width w."""
    draw.text((x, y), label, font=font_label, fill=0)
    value_w = draw.textbbox((0, 0), value, font=font_value)[2]
    draw.text((x + w - value_w, y + 20), value, font=font_value, fill=0)


def draw_hero(draw, x, y, w, label, value, font_label, font_hero):
    draw.text((x, y), label, font=font_label, fill=0)
    value_w = draw.textbbox((0, 0), value, font=font_hero)[2]
    draw.text((x + (w - value_w) // 2, y + 26), value, font=font_hero, fill=0)


def draw_soc_gauge(draw, x, y, w, h, soc_pct):
    draw.rectangle((x, y, x + w, y + h), outline=0, width=2)
    fill_w = int((w - 6) * max(0.0, min(100.0, soc_pct)) / 100.0)
    if fill_w > 0:
        draw.rectangle((x + 3, y + 3, x + 3 + fill_w, y + h - 3), fill=0)


def draw_and_update_display():
    """Renders pixel canvas and flashes e-Paper screen."""
    metrics = fetch_latest_metrics()

    epd = epd7in5_V2.EPD()
    epd.init()

    # 800x480 monochrome canvas
    image = Image.new('1', (epd.width, epd.height), 255)
    draw = ImageDraw.Draw(image)

    font_title = ImageFont.truetype(FONT_BOLD, 30)
    font_section = ImageFont.truetype(FONT_BOLD, 18)
    font_hero = ImageFont.truetype(FONT_BOLD, 76)
    font_stat_label = ImageFont.truetype(FONT_REGULAR, 16)
    font_stat_value = ImageFont.truetype(FONT_BOLD, 26)
    font_footer = ImageFont.truetype(FONT_REGULAR, 16)
    font_footer_bold = ImageFont.truetype(FONT_BOLD, 16)

    W, H = epd.width, epd.height  # 800, 480
    MARGIN = 10
    HEADER_Y = 60
    FOOTER_Y = H - 50
    MID_X = W // 2

    # Frame
    draw.rectangle((MARGIN, MARGIN, W - MARGIN, H - MARGIN), outline=0, width=2)
    draw.line((MARGIN, HEADER_Y, W - MARGIN, HEADER_Y), fill=0, width=2)
    draw.line((MID_X, HEADER_Y, MID_X, FOOTER_Y), fill=0, width=2)
    draw.line((MARGIN, FOOTER_Y, W - MARGIN, FOOTER_Y), fill=0, width=2)

    # Header
    draw.text((MARGIN + 15, 15), "OFF-GRID SOLAR POWER", font=font_title, fill=0)

    # --- Left column: Battery (Shunt) ---
    lx = MARGIN + 20
    lw = MID_X - lx - 20
    draw_hero(draw, lx, HEADER_Y + 15, lw, "BATTERY (SHUNT)", f"{metrics['soc']:.0f}%",
              font_section, font_hero)
    draw_soc_gauge(draw, lx, HEADER_Y + 125, lw, 26, metrics['soc'])

    stat_y = HEADER_Y + 165
    stat_w = lw // 2
    draw_stat(draw, lx, stat_y, stat_w - 10, "Battery Voltage", f"{metrics['battery_volts']:.2f} V",
              font_stat_label, font_stat_value)
    draw_stat(draw, lx + stat_w + 10, stat_y, stat_w - 10, "Net Power", f"{metrics['net_power']:.0f} W",
              font_stat_label, font_stat_value)
    stat_y += 70
    draw_stat(draw, lx, stat_y, stat_w - 10, "Time To Go", f"{metrics['time_to_go_min'] / 60.0:.1f} h",
              font_stat_label, font_stat_value)
    draw_stat(draw, lx + stat_w + 10, stat_y, stat_w - 10, "Consumed", f"{metrics['consumed_ah']:.1f} Ah",
              font_stat_label, font_stat_value)

    # --- Right column: Solar (MPPT) ---
    rx = MID_X + 20
    rw = (W - MARGIN) - rx - 20
    draw_hero(draw, rx, HEADER_Y + 15, rw, "SOLAR (MPPT)", f"{metrics['pv_power']:.0f} W",
              font_section, font_hero)

    stat_y = HEADER_Y + 165
    stat_w = rw // 2
    draw_stat(draw, rx, stat_y, stat_w - 10, "PV Voltage", f"{metrics['pv_voltage']:.1f} V",
              font_stat_label, font_stat_value)
    draw_stat(draw, rx + stat_w + 10, stat_y, stat_w - 10, "PV Current", f"{metrics['pv_current']:.2f} A",
              font_stat_label, font_stat_value)
    stat_y += 70
    draw_stat(draw, rx, stat_y, stat_w - 10, "Charge Current", f"{metrics['charging_current']:.2f} A",
              font_stat_label, font_stat_value)
    draw_stat(draw, rx + stat_w + 10, stat_y, stat_w - 10, "Controller Temp", f"{metrics['controller_temp']:.0f} C",
              font_stat_label, font_stat_value)

    # Footer: timestamp (left) + charge state (right)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    draw.text((MARGIN + 15, FOOTER_Y + 15), f"Updated: {timestamp}", font=font_footer, fill=0)

    if metrics['net_power'] > 5:
        state = "CHARGING"
    elif metrics['net_power'] < -5:
        state = "DISCHARGING"
    else:
        state = "IDLE"
    state_w = draw.textbbox((0, 0), state, font=font_footer_bold)[2]
    draw.text((W - MARGIN - 15 - state_w, FOOTER_Y + 15), state, font=font_footer_bold, fill=0)

    # Update Display & Sleep Screen (Prevents burn-in)
    epd.display(epd.getbuffer(image))
    epd.sleep()


if __name__ == "__main__":
    while True:
        draw_and_update_display()
        time.sleep(60)
