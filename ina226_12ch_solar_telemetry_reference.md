# 12-Channel Solar DC Telemetry System Reference Guide
**Sensor Target Hardware:** [Azuocn INA226 Voltage and Current Monitoring Module (6pc Pack)](https://www.amazon.com/Voltage-Current-Monitoring-Function-Bi-Directional/dp/B0DG5BKRLB)

This document provides complete technical specifications, physical wiring guidelines, and I2C address maps to build a compact, 12-channel plug-and-play voltage, current, and power telemetry system for a **12V Nominal DC Solar Distribution Fuse Box** running up to 20A per line.

---

## 1. Hardware Specifications (Azuocn ZY-125 / INA226)
* **Bus Voltage Sensing Range:** 0V to 36V DC (Bi-directional)
* **Max Continuous Current:** 20A (Dependent on PCB shunt configuration; layout native to 20A variants)
* **Interface Protocol:** I2C / SMBUS-compatible
* **Hardware Address Capacity:** Up to 16 unique programmable addresses
* **Precision Parameters:** 
  * 10 μV Maximum Offset Voltage
  * 0.1% Maximum Gain Error
  * 140 dB Common-Mode Rejection Ratio (CMRR)
* **Operating Voltage (Logic Side):** 2.7V to 5.5V DC
* **Quiescent Current Consumption:** 420 μA (Max); 2 μA in shutdown mode

---

## 2. High-Current Solar Wiring (12V Load Side)
To handle 20A continuous power safely without localized heat buildup, use **12 AWG stranded copper wire** for all main lines. Wire each of the 12 fuse box channels independently in series on the output side:

1. **Fuse Output to Sensor:** Route the heavy 12V wire from a specific **Fuse Output Terminal** to the **IN+** pin header/terminal block on that channel's INA226 board.
2. **Sensor to Appliance:** Route a matching heavy wire from the **IN-** pin on that same INA226 board to the positive (+) input terminal of your **12V DC Appliance** (e.g., inverter, fridge, lights).
3. **Voltage Reference Jump:** Bridge a thin wire (22 AWG is sufficient) from the **VBUS** pin on the board directly into the **IN+** pin terminal on that exact same board. This enables the chip's internal voltage multiplexer to sample the live line voltage.

---

## 3. Low-Current Logic Wiring (Microcontroller Bus Side)
All 12 modules share a single I2C communication bus. Wire all matching logic pins together in a parallel chain back to your microcontroller (e.g., ESP32, Raspberry Pi Pico) using thin **22 AWG hookup wire**:

* **VS (or VCC):** Connect all 12 pins together -> Connect to Microcontroller **3.3V or 5V** power out.
* **GND:** Connect all 12 pins together -> Connect to Microcontroller **GND**. *(Important: Your 12V solar battery negative bank must share a common ground reference with this microcontroller ground block).*
* **SCL:** Connect all 12 pins together -> Connect to Microcontroller Hardware **SCL** pin.
* **SDA:** Connect all 12 pins together -> Connect to Microcontroller Hardware **SDA** pin.

---

## 4. 12-Channel I2C Address Mapping Table
To prevent data collisions on the shared bus lines, change the hardware addresses of your 12 boards by bridging the onboard **A0** and **A1** solder pads to ground, power, or data lines:

| Board / Channel | Target Fuse Output | Wire/Bridge A0 to: | Wire/Bridge A1 to: | Hex I2C Address |
| :---: | :--- | :--- | :--- | :---: |
| **Board 1** | Fuse Channel 1 | **GND** | **GND** | `0x40` |
| **Board 2** | Fuse Channel 2 | **VS (VCC)** | **GND** | `0x41` |
| **Board 3** | Fuse Channel 3 | **SDA** | **GND** | `0x42` |
| **Board 4** | Fuse Channel 4 | **SCL** | **GND** | `0x43` |
| **Board 5** | Fuse Channel 5 | **GND** | **VS (VCC)** | `0x44` |
| **Board 6** | Fuse Channel 6 | **VS (VCC)** | **VS (VCC)** | `0x45` |
| **Board 7** | Fuse Channel 7 | **SDA** | **VS (VCC)** | `0x46` |
| **Board 8** | Fuse Channel 8 | **SCL** | **VS (VCC)** | `0x47` |
| **Board 9** | Fuse Channel 9 | **GND** | **SDA** | `0x48` |
| **Board 10** | Fuse Channel 10 | **VS (VCC)** | **SDA** | `0x49` |
| **Board 11** | Fuse Channel 11 | **SDA** | **SDA** | `0x4A` |
| **Board 12** | Fuse Channel 12 | **SCL** | **SDA** | `0x4B` |

---

## 5. Architectural Considerations
* **Grounding Loop Safety:** Ensure the wire gauge running from your negative DC distribution block to your microcontroller ground is robust to minimize ground shift interference during heavy current surges.
* **Digital Signal Integrity:** Keep the I2C chaining lines as short as physically possible inside the distribution box to avoid noise injection from nearby high-current lines. If lines extend beyond 30cm, consider adding **4.7kΩ pull-up resistors** to the SDA and SCL lines.

***
*Disclaimer: This is for informational purposes only. For medical advice or diagnosis, consult a professional. AI responses may include mistakes.*