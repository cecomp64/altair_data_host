#!/usr/bin/env python3
import sys
import serial

def parse_vedirect(port_path):
    """
    Reads one complete frame from Victron VE.Direct text stream and outputs Influx Line Protocol.
    """
    fields = {}
    try:
        with serial.Serial(port_path, 19200, timeout=3) as ser:
            # Sync to frame start
            lines_read = 0
            while lines_read < 30:
                line = ser.readline().decode('ascii', errors='ignore').strip()
                lines_read += 1
                if '\t' in line:
                    key, val = line.split('\t', 1)

                    # Target critical VE.Direct fields
                    if key == 'V':      # Main Battery Voltage (mV)
                        fields['voltage'] = float(val) / 1000.0
                    elif key == 'I':    # Battery Current (mA)
                        fields['current'] = float(val) / 1000.0
                    elif key == 'P':    # Instantaneous Power (W)
                        fields['power'] = float(val)
                    elif key == 'SOC':  # State of Charge (‰ -> %)
                        fields['soc'] = float(val) / 10.0
                    elif key == 'TTG':  # Time To Go (minutes)
                        fields['time_to_go_min'] = float(val)
                    elif key == 'CE':   # Consumed Amp Hours (mAh)
                        fields['consumed_ah'] = float(val) / 1000.0

            if fields:
                # Format into Influx Line Protocol
                field_str = ",".join([f"{k}={v}" for k, v in fields.items()])
                print(f"victron_smartshunt {field_str}")
                sys.exit(0)
            else:
                sys.exit(1)

    except Exception as e:
        sys.stderr.write(f"Error reading VE.Direct stream: {e}\n")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.stderr.write("Usage: python3 read_vedirect.py <serial_port>\n")
        sys.exit(1)
    parse_vedirect(sys.argv[1])
