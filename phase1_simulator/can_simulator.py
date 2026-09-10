"""
CAN Bus Simulator - Phase 1
----------------------------
Pretends to be the EV's ECU (Electronic Control Unit).
Every 100ms it generates 5 sensor readings, packs each into a
CAN-style frame [ID, DLC, Data...], and writes it to a log file
that the Parser (Phase 2) will read.

Message spec (this is our "DBC"):
  0x101 Motor_RPM        16-bit, big-endian, scale 1,   range 0-MAX_RPM
  0x102 Vehicle_Speed    16-bit, big-endian, scale 0.1, range 0-99 km/h
  0x103 Battery_SOC       8-bit,             scale 1,   range 0-100 %
  0x104 Battery_Voltage  16-bit, big-endian, scale 0.1, range 48-64 V
  0x105 Motor_Temperature 8-bit,             scale 1,   range 25-135 C

Physics model: only RPM changes randomly (simulating the driver's foot on
the pedal). Every other value -- speed, SOC, voltage, temperature -- is
calculated DIRECTLY from RPM (or from SOC) each step. Nothing "chases" a
target or moves independently; everything is one clean formula away from
the current RPM.
"""

import struct
import time
import random
import os

# Always find sample_data relative to THIS file's own location on disk,
# so it works no matter which folder you run the script from.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(SCRIPT_DIR, "..", "sample_data", "can_bus.log")

# ---------------------------------------------------------------
# Bounded operating limits (derived, not guessed)
# ---------------------------------------------------------------
WHEEL_CIRCUMFERENCE_M = 0.3   # distance travelled per one wheel revolution
TOP_SPEED_KMH = 99            # hard vehicle speed limit

# km/h produced by 1 RPM: (circumference x 60 min/hr) / 1000 m/km
RPM_TO_KMH = WHEEL_CIRCUMFERENCE_M * 60 / 1000          # = 0.018

# Max RPM is whatever RPM produces exactly TOP_SPEED_KMH
MAX_RPM = round(TOP_SPEED_KMH / RPM_TO_KMH)              # = 5500

TOP_TEMP_C = 120              # motor over-temperature warning threshold
LOW_BATTERY_SOC = 20          # battery percentage that triggers a low-battery warning

V_MIN, V_MAX = 48.0, 64.0     # pack voltage at 0% SOC and 100% SOC
TEMP_BASE_C = 25              # motor temperature at idle (RPM = 0)
TEMP_RISE_C = 110             # extra degrees added at MAX_RPM (25 -> 135)
SOC_DRAIN_PER_STEP = 0.05     # max %-SOC lost in one step, at full RPM load


class CANMessage:
    """One CAN frame: an ID, a data length, and the raw payload bytes."""

    def __init__(self, can_id, dlc, data):
        self.can_id = can_id
        self.dlc = dlc
        self.data = data

    def __str__(self):
        # Human/parser-readable log line, e.g.:
        # ID: 0x101 DLC: 2 Data: [14 28]
        data_hex = " ".join(f"{b:02X}" for b in self.data)
        return f"ID: 0x{self.can_id:03X} DLC: {self.dlc} Data: [{data_hex}]"


class CANSimulator:
    """Generates EV sensor values where RPM drives everything else directly."""

    def __init__(self):
        self.motor_rpm = 0
        # Everything else starts as whatever RPM=0 computes:
        self.vehicle_speed = 0.0
        self.battery_soc = 100.0
        self.battery_voltage = V_MAX
        self.motor_temp = TEMP_BASE_C

    def step_physics(self):
        """One tick: RPM takes a random step (the only randomness in the
        whole simulator), then speed / SOC / voltage / temperature are all
        recalculated directly from the new RPM (and SOC)."""

        # 1) RPM: gradually increases, capped at MAX_RPM (the only randomness)
        if self.motor_rpm < MAX_RPM:
            self.motor_rpm += random.randint(10, 20)
        self.motor_rpm = min(self.motor_rpm, MAX_RPM)
        load_fraction = self.motor_rpm / MAX_RPM   # 0.0 (idle) to 1.0 (max load)

        # 2) Speed: direct function of RPM via wheel circumference
        self.vehicle_speed = self.motor_rpm * RPM_TO_KMH   # already <= 99 by construction

        # 3) SOC: drains based on how hard the motor is working right now.
        #    Each call is one time-step, so drain accumulates over time too.
        self.battery_soc = max(0.0, self.battery_soc - SOC_DRAIN_PER_STEP * load_fraction)

        # 4) Voltage: direct function of SOC (linear interpolation)
        self.battery_voltage = V_MIN + (self.battery_soc / 100) * (V_MAX - V_MIN)

        # 5) Temperature: direct function of RPM load
        self.motor_temp = TEMP_BASE_C + load_fraction * TEMP_RISE_C

    def check_warnings(self):
        """Returns a list of any bound violations for the current state."""
        warnings = []
        if self.vehicle_speed >= TOP_SPEED_KMH:
            warnings.append(f"TOP SPEED REACHED ({self.vehicle_speed:.0f} km/h)")
        if self.motor_temp >= TOP_TEMP_C:
            warnings.append(f"OVER TEMP ({self.motor_temp:.0f} C)")
        if self.battery_soc <= LOW_BATTERY_SOC:
            warnings.append(f"LOW BATTERY ({self.battery_soc:.0f}%)")
        return warnings

    # --- Each method below packs one signal into its CAN frame ---

    def frame_rpm(self):
        data = struct.pack(">H", int(self.motor_rpm))  # >H = big-endian uint16
        return CANMessage(0x101, 2, data)

    def frame_speed(self):
        raw = round(self.vehicle_speed * 10)  # undo the 0.1 scale before sending
        data = struct.pack(">H", raw)
        return CANMessage(0x102, 2, data)

    def frame_soc(self):
        data = struct.pack("B", int(self.battery_soc))  # B = uint8
        return CANMessage(0x103, 1, data)

    def frame_voltage(self):
        raw = round(self.battery_voltage * 10)
        data = struct.pack(">H", raw)
        return CANMessage(0x104, 2, data)

    def frame_temp(self):
        data = struct.pack("B", int(self.motor_temp))
        return CANMessage(0x105, 1, data)

    def run(self, output_file=None, cycles=None):
        """Main loop: advance physics every 100ms tick, but only send each
        message at its own priority rate:
          - RPM, Speed     -> every tick     (100ms)  [high priority]
          - Voltage        -> every 5 ticks  (500ms)  [medium priority]
          - SOC, Temp      -> every 10 ticks (1000ms) [low priority]
        `cycles=None` runs forever (Ctrl+C to stop); pass a number for a demo."""
        if output_file is None:
            output_file = LOG_PATH
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        with open(output_file, "w") as f:
            print(f"CAN simulator started (MAX_RPM={MAX_RPM}), logging to {output_file}")
            count = 0
            try:
                while cycles is None or count < cycles:
                    self.step_physics()

                    messages = [self.frame_rpm(), self.frame_speed()]      # 0x101, 0x102 every 100ms
                    if count % 10 == 0:
                        messages.append(self.frame_soc())                  # 0x103 every 1000ms
                    if count % 5 == 0:
                        messages.append(self.frame_voltage())              # 0x104 every 500ms
                    if count % 10 == 0:
                        messages.append(self.frame_temp())                 # 0x105 every 1000ms

                    for msg in messages:
                        print(msg)
                        f.write(str(msg) + "\n")
                    for w in self.check_warnings():
                        print(f"  [!] {w}")
                    f.flush()
                    time.sleep(0.1)
                    count += 1
            except KeyboardInterrupt:
                print("\nSimulation stopped.")


if __name__ == "__main__":
    CANSimulator().run()