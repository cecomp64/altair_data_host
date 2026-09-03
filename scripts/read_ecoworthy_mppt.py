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
"""
import struct
import sys

import serial

PORT = "/dev/ttyUSB_ECOWORTHY"
BAUD = 9600
SLAVE_ID = 1
START_ADDR = 0x0100
REGISTER_COUNT = 6
TIMEOUT_S = 2

# (field name, scale, signed)
# controller_temp is signed and scaled by 0.1 - not 1.0 as telegraf.conf's
# now-unused inputs.modbus block had it. A raw reading of 144 decoded as
# 144 degC (implausible) but decodes as 14.4 degC at scale 0.1, consistent
# with every other register on this controller being tenths-scaled, and with
# a plausible nighttime ambient reading. Confirmed against a live register
# dump during initial bring-up; worth re-verifying against a known temperature
# if this ever looks wrong again.
FIELDS = [
    ("pv_voltage", 0.1, False),
    ("pv_current", 0.01, False),
    ("pv_power", 1.0, False),
    ("controller_temp", 0.1, True),
    ("battery_voltage", 0.1, False),
    ("charging_current", 0.01, False),
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


def poll(port_path):
    request = build_read_holding_request(SLAVE_ID, START_ADDR, REGISTER_COUNT)
    with serial.Serial(
        port=port_path,
        baudrate=BAUD,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=TIMEOUT_S,
    ) as ser:
        ser.reset_input_buffer()
        ser.write(request)
        data = read_response(ser)

    if len(data) != REGISTER_COUNT * 2:
        raise ValueError(f"expected {REGISTER_COUNT * 2} data bytes, got {len(data)}")

    fields = {}
    for i, (name, scale, signed) in enumerate(FIELDS):
        raw = struct.unpack_from(">h" if signed else ">H", data, i * 2)[0]
        fields[name] = round(raw * scale, 3)

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
