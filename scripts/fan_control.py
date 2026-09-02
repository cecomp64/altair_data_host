#!/usr/bin/env python3
import logging
import os
import signal
import sys
import time

from gpiozero import PWMOutputDevice
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

# Runs on the host OS (see scripts/install-fan-control-service.sh), which
# loads these from the repo's .env via the systemd unit's EnvironmentFile=.
INFLUX_URL = os.environ.get("INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = os.environ["INFLUXDB_ADMIN_TOKEN"]
INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "solar")
INFLUX_BUCKET_WEATHER = os.environ.get("INFLUXDB_BUCKET_WEATHER", "weather")
INFLUX_BUCKET_SYSTEM = os.environ.get("INFLUXDB_BUCKET_SYSTEM", "system")

# BCM pin driving the fan's PWM/Trigger input through a MOSFET. No default -
# wiring is site-specific, and a wrong guess here drives real hardware.
GPIO_PIN = int(os.environ["FAN_CONTROL_GPIO_PIN"])

# Software PWM (via gpiozero's lgpio backend, same as charge_cutoff.py) - this
# pin has no hardware PWM channel on a Raspberry Pi, so timing comes from the
# CPU rather than dedicated silicon. 1kHz is a conservative default that's
# glitch-free on a Pi under normal load; push higher only if you've confirmed
# it stays stable (e.g. by listening for the fan itself changing pitch/tone
# oddly at a given speed, or switching to the pigpio pin factory for DMA-timed
# PWM if jitter is a problem at higher frequencies).
PWM_FREQUENCY_HZ = float(os.environ.get("FAN_CONTROL_PWM_FREQUENCY_HZ", "1000"))

# Stage thresholds (deg F, indoor/enclosure ambient - the weather station's
# own indoor sensor, weather_station.temp_in_f). Fan is off below LOW.
TEMP_LOW = float(os.environ.get("FAN_CONTROL_TEMP_LOW", "80.0"))
TEMP_MEDIUM = float(os.environ.get("FAN_CONTROL_TEMP_MEDIUM", "90.0"))
TEMP_HIGH = float(os.environ.get("FAN_CONTROL_TEMP_HIGH", "100.0"))

# Deg F a stage's temperature must drop below its own turn-on threshold
# before stepping back down a stage - without this the fan would chatter
# on/off right at a single threshold as temp hovers around it.
HYSTERESIS_F = float(os.environ.get("FAN_CONTROL_HYSTERESIS_F", "5.0"))

# PWM duty cycle (0.0-1.0) per stage. Defaults assume the fan's driver board
# needs some minimum duty to actually spin (many small DC fans stall below
# ~30-40% duty) - raise FAN_CONTROL_DUTY_LOW if yours doesn't spin at 0.4.
DUTY_LOW = float(os.environ.get("FAN_CONTROL_DUTY_LOW", "0.4"))
DUTY_MEDIUM = float(os.environ.get("FAN_CONTROL_DUTY_MEDIUM", "0.7"))
DUTY_HIGH = float(os.environ.get("FAN_CONTROL_DUTY_HIGH", "1.0"))

# read_ecowitt.py's indoor sensor updates every 5s (telegraf.conf's global
# agent interval) - indoor ambient temp moves slowly, so polling this much
# less often than that is plenty responsive without hammering InfluxDB.
POLL_INTERVAL_S = float(os.environ.get("FAN_CONTROL_POLL_INTERVAL", "30"))

# Fan's DC supply rail (this repo's own fan: a 12V 120mm blower, confirmed
# from its listing) - used only to compute an approximate *average* voltage
# from PWM duty cycle (duty * supply), not a measured reading. There's no
# voltage sensor on this circuit; if you add one (e.g. an INA219 on the
# MOSFET's output), report that instead of this estimate.
SUPPLY_VOLTAGE_V = float(os.environ.get("FAN_CONTROL_SUPPLY_VOLTAGE_V", "12.0"))

# Fan's datasheet-rated free-air speed at 100% duty (this repo's own fan:
# 3000 RPM, confirmed from its listing) - used only to derive a rough
# estimated_rpm field (duty * rated, assumed linear) for the dashboard. This
# fan has no tachometer wire, so it's an estimate, not a measured RPM - real
# DC fans aren't perfectly linear against duty cycle, especially near stall.
RATED_RPM = float(os.environ.get("FAN_CONTROL_RATED_RPM", "3000"))

# Fan's rated max current draw at 100% duty (this repo's own fan: 1.2A,
# confirmed from its label) - combined with SUPPLY_VOLTAGE_V to get rated
# max power, then scaled by duty the same way voltage_v/speed_rpm_est are -
# an estimate, not a measured reading. There's no current sensor on this
# circuit; if you add one (e.g. an INA219 on the MOSFET's output), report
# that instead of this estimate.
RATED_CURRENT_A = float(os.environ.get("FAN_CONTROL_RATED_CURRENT_A", "1.2"))
RATED_POWER_W = SUPPLY_VOLTAGE_V * RATED_CURRENT_A

STAGE_NAMES = ["off", "low", "medium", "high"]
STAGE_DUTY = [0.0, DUTY_LOW, DUTY_MEDIUM, DUTY_HIGH]
# Index i = temp needed (at stage i) to step up to stage i+1.
STEP_UP_AT = [TEMP_LOW, TEMP_MEDIUM, TEMP_HIGH]
# Index i = temp must drop below this (at stage i) to step down to stage i-1.
STEP_DOWN_BELOW = [None, TEMP_LOW - HYSTERESIS_F, TEMP_MEDIUM - HYSTERESIS_F, TEMP_HIGH - HYSTERESIS_F]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fan_control")

if not (TEMP_LOW < TEMP_MEDIUM < TEMP_HIGH):
    sys.exit(
        f"FAN_CONTROL_TEMP_LOW ({TEMP_LOW}) < _MEDIUM ({TEMP_MEDIUM}) < _HIGH "
        f"({TEMP_HIGH}) must all hold."
    )
if HYSTERESIS_F <= 0:
    sys.exit(f"FAN_CONTROL_HYSTERESIS_F ({HYSTERESIS_F}) must be positive.")


class _Stop(Exception):
    """Raised from the SIGTERM handler so `finally` runs and the fan is
    explicitly stopped on a clean `systemctl stop`, rather than relying on
    the process just dying (Python does not run cleanup on a default
    SIGTERM)."""


def _handle_sigterm(signum, frame):
    raise _Stop()


def fetch_indoor_temp(query_api):
    """Returns the weather station's most recent indoor ambient reading
    (deg F, wh25 sensor), or None if unavailable."""
    flux_query = f'''
    from(bucket: "{INFLUX_BUCKET_WEATHER}")
      |> range(start: -5m)
      |> filter(fn: (r) => r._measurement == "weather_station" and r._field == "temp_in_f")
      |> last()
    '''
    try:
        result = query_api.query(query=flux_query, org=INFLUX_ORG)
        for table in result:
            for record in table.records:
                return float(record.get_value())
    except Exception as e:
        log.error("InfluxDB query error: %s", e)
    return None


def next_stage(stage, temp):
    if stage < 3 and temp >= STEP_UP_AT[stage]:
        return stage + 1
    if stage > 0 and temp < STEP_DOWN_BELOW[stage]:
        return stage - 1
    return stage


def write_heartbeat(write_api, stage, temp):
    """Records fan state (on/off, stage, duty cycle, estimated
    voltage/RPM/power) and the temp it was evaluated against, so Grafana can
    show/alert on the fan's behavior and whether this daemon is still
    running at all."""
    duty = STAGE_DUTY[stage]
    point = (
        Point("fan_control")
        .field("stage", stage)
        .field("stage_name", STAGE_NAMES[stage])
        .field("fan_on", stage > 0)
        .field("duty_cycle", duty)
        .field("voltage_v", round(duty * SUPPLY_VOLTAGE_V, 2))
        .field("speed_rpm_est", round(duty * RATED_RPM))
        .field("power_w", round(duty * RATED_POWER_W, 2))
    )
    if temp is not None:
        point = point.field("temp_at_eval", temp)
    try:
        write_api.write(bucket=INFLUX_BUCKET_SYSTEM, org=INFLUX_ORG, record=point)
    except Exception as e:
        log.error("InfluxDB heartbeat write failed: %s", e)


def main():
    signal.signal(signal.SIGTERM, _handle_sigterm)

    fan = PWMOutputDevice(GPIO_PIN, frequency=PWM_FREQUENCY_HZ, initial_value=0.0)
    stage = 0

    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    query_api = client.query_api()
    write_api = client.write_api(write_options=SYNCHRONOUS)

    log.info(
        "Starting: low >= %.1fF (duty %.2f), medium >= %.1fF (duty %.2f), "
        "high >= %.1fF (duty %.2f), hysteresis %.1fF, poll every %ss, GPIO%d @ %.0fHz, "
        "%.1fV supply, %.0f RPM rated, %.1fA / %.1fW rated",
        TEMP_LOW, DUTY_LOW, TEMP_MEDIUM, DUTY_MEDIUM, TEMP_HIGH, DUTY_HIGH,
        HYSTERESIS_F, POLL_INTERVAL_S, GPIO_PIN, PWM_FREQUENCY_HZ,
        SUPPLY_VOLTAGE_V, RATED_RPM, RATED_CURRENT_A, RATED_POWER_W,
    )

    try:
        while True:
            temp = fetch_indoor_temp(query_api)

            if temp is None:
                log.warning(
                    "No indoor temp reading available this cycle - holding current stage (%s)",
                    STAGE_NAMES[stage],
                )
            else:
                new_stage = stage
                while True:
                    candidate = next_stage(new_stage, temp)
                    if candidate == new_stage:
                        break
                    new_stage = candidate
                if new_stage != stage:
                    log.info(
                        "Indoor %.1fF - stage %s -> %s (duty %.2f, ~%.1fV, ~%.0f RPM, ~%.1fW)",
                        temp, STAGE_NAMES[stage], STAGE_NAMES[new_stage], STAGE_DUTY[new_stage],
                        STAGE_DUTY[new_stage] * SUPPLY_VOLTAGE_V, STAGE_DUTY[new_stage] * RATED_RPM,
                        STAGE_DUTY[new_stage] * RATED_POWER_W,
                    )
                    stage = new_stage
                    fan.value = STAGE_DUTY[stage]

            write_heartbeat(write_api, stage, temp)
            time.sleep(POLL_INTERVAL_S)
    except _Stop:
        log.info("Received SIGTERM, shutting down")
    except KeyboardInterrupt:
        log.info("Received SIGINT, shutting down")
    finally:
        # Stop the fan on any exit path rather than leaving it stuck at
        # whatever duty cycle was last set. If the MOSFET's gate floats
        # (rather than being pulled low) once this process exits, add a
        # physical pull-down resistor at the gate so an unmanaged pin state
        # can't be misread as "on".
        fan.off()
        client.close()


if __name__ == "__main__":
    main()
