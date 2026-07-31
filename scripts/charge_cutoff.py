#!/usr/bin/env python3
import logging
import os
import signal
import sys
import time

from gpiozero import DigitalOutputDevice
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

# Runs on the host OS (see scripts/install-charge-cutoff-service.sh), which
# loads these from the repo's .env via the systemd unit's EnvironmentFile=.
INFLUX_URL = os.environ.get("INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = os.environ["INFLUXDB_ADMIN_TOKEN"]
INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "solar")
INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET_POWER", "power")

# BCM pin driving the opto-isolated coil driver for the charge-cutoff
# contactor. The contactor is normally-closed / energize-to-open: driving
# this pin HIGH opens it and stops charging, LOW lets it spring back to
# closed and charging resumes. No default - wiring is site-specific, and a
# wrong guess here drives real hardware.
GPIO_PIN = int(os.environ["CHARGE_CUTOFF_GPIO_PIN"])

SOC_DISCONNECT_PCT = float(os.environ.get("CHARGE_CUTOFF_SOC_HIGH", "85.0"))
SOC_RECONNECT_PCT = float(os.environ.get("CHARGE_CUTOFF_SOC_LOW", "80.0"))
POLL_INTERVAL_S = float(os.environ.get("CHARGE_CUTOFF_POLL_INTERVAL", "60"))
MIN_DWELL_S = float(os.environ.get("CHARGE_CUTOFF_MIN_DWELL", "300"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("charge_cutoff")

if SOC_RECONNECT_PCT >= SOC_DISCONNECT_PCT:
    sys.exit(
        f"CHARGE_CUTOFF_SOC_LOW ({SOC_RECONNECT_PCT}) must be lower than "
        f"CHARGE_CUTOFF_SOC_HIGH ({SOC_DISCONNECT_PCT}), or the contactor "
        "would chatter open/closed at a single threshold."
    )


class _Stop(Exception):
    """Raised from the SIGTERM handler so `finally` runs and the coil is
    explicitly de-energized on a clean `systemctl stop`, rather than relying
    on the process just dying (Python does not run cleanup on a default
    SIGTERM)."""


def _handle_sigterm(signum, frame):
    raise _Stop()


def fetch_soc(query_api):
    """Returns the shunt's most recent SOC reading (0-100), or None if
    unavailable."""
    flux_query = f'''
    from(bucket: "{INFLUX_BUCKET}")
      |> range(start: -5m)
      |> filter(fn: (r) => r._measurement == "victron_smartshunt" and r._field == "soc")
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


def write_heartbeat(write_api, charging_disabled, soc):
    """Records relay state + the SOC it was evaluated against, so Grafana can
    show/alert on both the cutoff state and whether this daemon is still
    running at all."""
    point = Point("charge_cutoff").field("charging_disabled", int(charging_disabled))
    if soc is not None:
        point = point.field("soc_at_eval", soc)
    try:
        write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
    except Exception as e:
        log.error("InfluxDB heartbeat write failed: %s", e)


def main():
    signal.signal(signal.SIGTERM, _handle_sigterm)

    # Coil starts de-energized (contactor closed, charging allowed) until a
    # confirmed high SOC reading says otherwise - fail-closed on startup,
    # matching the hardware's own de-energized-is-closed default.
    coil = DigitalOutputDevice(GPIO_PIN, initial_value=False)
    charging_disabled = False
    last_transition = 0.0

    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    query_api = client.query_api()
    write_api = client.write_api(write_options=SYNCHRONOUS)

    log.info(
        "Starting: disconnect >= %.1f%%, reconnect <= %.1f%%, poll every %ss, "
        "min dwell %ss, GPIO%d",
        SOC_DISCONNECT_PCT, SOC_RECONNECT_PCT, POLL_INTERVAL_S, MIN_DWELL_S, GPIO_PIN,
    )

    try:
        while True:
            soc = fetch_soc(query_api)
            now = time.monotonic()
            dwell_elapsed = (now - last_transition) >= MIN_DWELL_S

            if soc is None:
                log.warning(
                    "No SOC reading available this cycle - holding current state (%s)",
                    "disabled" if charging_disabled else "enabled",
                )
            elif not charging_disabled and soc >= SOC_DISCONNECT_PCT and dwell_elapsed:
                coil.on()
                charging_disabled = True
                last_transition = now
                log.info("SOC %.1f%% >= %.1f%% - opening contactor, charging disabled",
                          soc, SOC_DISCONNECT_PCT)
            elif charging_disabled and soc <= SOC_RECONNECT_PCT and dwell_elapsed:
                coil.off()
                charging_disabled = False
                last_transition = now
                log.info("SOC %.1f%% <= %.1f%% - closing contactor, charging enabled",
                          soc, SOC_RECONNECT_PCT)

            write_heartbeat(write_api, charging_disabled, soc)
            time.sleep(POLL_INTERVAL_S)
    except _Stop:
        log.info("Received SIGTERM, shutting down")
    except KeyboardInterrupt:
        log.info("Received SIGINT, shutting down")
    finally:
        # De-energize on any exit path so the contactor returns to its
        # normally-closed / charging-enabled state.
        coil.off()
        client.close()


if __name__ == "__main__":
    main()
