#!/usr/bin/env python3
"""Polls the Eco-Worthy MPPT controller's holding registers over Modbus RTU
and emits Influx Line Protocol.

Goes through a script rather than telegraf's native inputs.modbus plugin
because that plugin hangs indefinitely against this controller/adapter
combination - confirmed by direct testing: a plain pyserial request/response
against the same port, baud, and register map gets a clean, CRC-valid reply
every time, but telegraf's own modbus client never returns, even past its
own configured timeout. Root cause not isolated further; this script sidesteps
it the same way read_vedirect.py sidesteps VE.Direct not fitting a stock
telegraf input.

Register map (holding registers 0x0000-0x000B) was reverse-engineered
against this controller live, not sourced from a datasheet - Eco-Worthy
doesn't publish one for this model. The address block this repo originally
used (0x0100-0x0107, still readable, still returns clean CRC-valid data) is
NOT live telemetry - confirmed by polling it repeatedly across minutes of
real, changing solar production and getting byte-identical values every
time. It's a configuration/rated-parameters block; several of its registers
line up exactly with static values in Eco-Worthy's BT-02 Bluetooth app
(e.g. raw 600 x0.1 = 60.0, matching "Maximum Charging Current: 60.0A").
The block below is the one that actually moves.

Confidence per field, from live testing against the BT app and cross-checked
with pv_voltage*pv_current ~= battery_voltage*charging_current (should hold
within a few percent - MPPT conversion losses - at any instant):
  - pv_voltage, battery_voltage: confirmed, matched BT app within 0.1-0.4V
  - charging_current: confirmed via trend + magnitude match to BT app
  - pv_current: confirmed via the power-balance check above (5-7% gap
    across 3 trials, consistent with real conversion loss)
  - temperature: plausible (reads ~11-12 degC on a cool September morning)
    but not cross-checked against a thermometer
  - device_status, load_current, load_power: circumstantial only - raw
    values matched a known state (device on, no load connected) but
    load_current vs load_power register order is a guess, untested with
    an actual load
"""
import struct
import sys

import serial

PORT = "/dev/ttyUSB_ECOWORTHY"
BAUD = 9600
SLAVE_ID = 1
START_ADDR = 0x0000
REGISTER_COUNT = 12
TIMEOUT_S = 2

# (register offset from START_ADDR, field name, scale, signed)
REGISTER_MAP = [
    (0, "device_status", 1.0, False),
    (1, "pv_voltage", 0.1, False),
    (2, "battery_voltage", 0.1, False),
    # 0x0005 duplicates 0x0002 (battery_voltage) - skipped
    (3, "temperature", 0.1, True),
    (4, "load_current", 0.1, False),   # order vs load_power unconfirmed
    (6, "load_power", 1.0, False),     # order vs load_current unconfirmed
    (7, "charging_current", 0.1, False),
    (11, "pv_current", 0.1, False),
    # 0x0009, 0x000a: unidentified, no clean physical match found - omitted
]

# A second, separate block - not contiguous with the one above. Confirmed
# against the BT app: 0x020f matched "Total Generation" (and kept climbing
# past the app's snapshot value, as a lifetime energy counter should - an
# earlier candidate register, 0x000d, looked close but ran backwards
# relative to the app and was dropped). 0x0215 matched "Total Load Power
# Consumption" exactly.
STATS_START_ADDR = 0x020f
STATS_REGISTER_COUNT = 7
STATS_MAP = [
    (0, "total_generation_kwh", 0.001, False),
    (6, "total_load_consumption_kwh", 0.001, False),
]


def crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc.to_bytes(2, "little")


def build_read_holding_request(slave, start_addr, count):
    frame = bytes([slave, 0x03]) + start_addr.to_bytes(2, "big") + count.to_bytes(2, "big")
    return frame + crc16(frame)


def read_response(ser):
    header = ser.read(3)  # slave, function, byte_count (or exception code)
    if len(header) < 3:
        raise TimeoutError("no response from controller")

    slave, func, third = header[0], header[1], header[2]
    if func & 0x80:
        ser.read(2)  # drain the exception frame's CRC
        raise RuntimeError(f"Modbus exception 0x{third:02x} for function 0x{func & 0x7f:02x}")

    byte_count = third
    rest = ser.read(byte_count + 2)  # register data + CRC
    if len(rest) < byte_count + 2:
        raise TimeoutError("incomplete response from controller")

    frame = header + rest
    payload, crc_received = frame[:-2], frame[-2:]
    if crc16(payload) != crc_received:
        raise ValueError("CRC mismatch in controller response")

    return frame[3:-2]


def read_block(ser, start_addr, count, register_map):
    request = build_read_holding_request(SLAVE_ID, start_addr, count)
    ser.reset_input_buffer()
    ser.write(request)
    data = read_response(ser)

    if len(data) != count * 2:
        raise ValueError(f"expected {count * 2} data bytes, got {len(data)}")

    fields = {}
    for offset, name, scale, signed in register_map:
        raw = struct.unpack_from(">h" if signed else ">H", data, offset * 2)[0]
        fields[name] = round(raw * scale, 3)
    return fields


def poll(port_path):
    with serial.Serial(
        port=port_path,
        baudrate=BAUD,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=TIMEOUT_S,
    ) as ser:
        fields = read_block(ser, START_ADDR, REGISTER_COUNT, REGISTER_MAP)
        fields.update(read_block(ser, STATS_START_ADDR, STATS_REGISTER_COUNT, STATS_MAP))

    # Not their own registers - the controller doesn't appear to expose power
    # directly in either block, so derive it the same way the BT app's own
    # numbers are internally consistent with (voltage * current).
    fields["pv_power"] = round(fields["pv_voltage"] * fields["pv_current"], 1)
    fields["charging_power"] = round(fields["battery_voltage"] * fields["charging_current"], 1)

    return fields


if __name__ == "__main__":
    port_path = sys.argv[1] if len(sys.argv) > 1 else PORT
    try:
        fields = poll(port_path)
    except Exception as e:
        sys.stderr.write(f"Error reading Eco-Worthy MPPT over Modbus: {e}\n")
        sys.exit(1)

    field_str = ",".join(f"{k}={v}" for k, v in fields.items())
    print(f"eco_worthy_mppt {field_str}")
