#!/usr/bin/env python3
"""Pi power health via `vcgencmd`: under-voltage/throttle status (works on
any Pi model) and PMIC per-rail voltage/current (Pi 5 only - older models
have no PMIC to query, and pmic_read_adc just errors there, so those fields
are silently omitted rather than faked).

`get_throttled` returns a bitmask; bits 0-3 are "currently true", bits
16-19 are "has happened since boot" versions of the same four conditions
(under-voltage, ARM frequency capped, throttled, soft temp limit) - see
https://www.raspberrypi.com/documentation/computers/os.html#get_throttled.
The "_now" fields are what's actionable for alerting; "_occurred" is kept
around as dashboard context (e.g. a one-time blip during boot, which is
common and not itself a problem).

`EXT5V_V` from pmic_read_adc is the actual 5V input rail voltage - the
closest thing to a single "Pi voltage" number. There's no equivalent single
"total input current" rail; `total_power_w` sums voltage*current across
every internal regulator rail the PMIC reports as an approximation of total
board power draw (close enough for a monitoring dashboard, not billing-grade).
"""
import re
import shutil
import subprocess
import sys

THROTTLED_BITS = {
    0: "undervoltage_now",
    1: "freq_capped_now",
    2: "throttled_now",
    3: "soft_temp_limit_now",
    16: "undervoltage_occurred",
    17: "freq_capped_occurred",
    18: "throttled_occurred",
    19: "soft_temp_limit_occurred",
}

RAIL_RE = re.compile(r"(\S+)_([AV])\s+(?:current|volt)\(\d+\)=([\d.]+)[AV]")


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=5)


def throttled_fields():
    proc = run(["vcgencmd", "get_throttled"])
    match = re.search(r"0x([0-9a-fA-F]+)", proc.stdout)
    if proc.returncode != 0 or not match:
        return {}
    value = int(match.group(1), 16)
    return {name: bool(value & (1 << bit)) for bit, name in THROTTLED_BITS.items()}


def pmic_fields():
    proc = run(["vcgencmd", "pmic_read_adc"])
    if proc.returncode != 0:
        return {}

    fields = {}
    currents, voltages = {}, {}
    for line in proc.stdout.splitlines():
        match = RAIL_RE.search(line.strip())
        if not match:
            continue
        rail, kind, value = match.group(1).lower(), match.group(2), float(match.group(3))
        if kind == "A":
            currents[rail] = value
            fields[f"{rail}_amps"] = value
        else:
            voltages[rail] = value
            fields[f"{rail}_volts"] = value

    if currents:
        fields["total_power_w"] = round(sum(amps * voltages[rail] for rail, amps in currents.items() if rail in voltages), 3)
    return fields


if __name__ == "__main__":
    if not shutil.which("vcgencmd"):
        sys.exit(0)

    fields = {}
    fields.update(throttled_fields())
    fields.update(pmic_fields())
    if not fields:
        sys.stderr.write("vcgencmd produced no usable output\n")
        sys.exit(1)

    field_str = ",".join(f"{k}={v}" if not isinstance(v, bool) else f"{k}={'true' if v else 'false'}" for k, v in fields.items())
    print(f"pi_power {field_str}")
