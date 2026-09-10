"""
CAN Bus Parser - Phase 2
-------------------------
Reads can_bus.log (written by the Simulator) and decodes each raw frame
back into a real, human-readable sensor value using the DBC spec below.

This message_specs dictionary IS our DBC, just written as Python instead
of the Vector .dbc text format. One entry per CAN ID.
"""

import struct
import os

# Same trick as the simulator: find sample_data relative to THIS file's
# own location, so it doesn't matter which folder you run this script from.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(SCRIPT_DIR, "..", "sample_data", "can_bus.log")


# --- DBC spec: official ranges from the task document ---
# (Not the simulator's tighter operating bounds -- the parser must be able
#  to correctly validate ANY frame that matches this spec, from any sender.)
message_specs = {
    0x101: {
        "name": "Motor_RPM",
        "length_bytes": 2,
        "byte_order": "big",
        "scale": 1,
        "offset": 0,
        "unit": "RPM",
        "min": 0,
        "max": 10000,
    },
    0x102: {
        "name": "Vehicle_Speed",
        "length_bytes": 2,
        "byte_order": "big",
        "scale": 0.1,
        "offset": 0,
        "unit": "km/h",
        "min": 0,
        "max": 120,
    },
    0x103: {
        "name": "Battery_SOC",
        "length_bytes": 1,
        "byte_order": "big",
        "scale": 1,
        "offset": 0,
        "unit": "%",
        "min": 0,
        "max": 100,
    },
    0x104: {
        "name": "Battery_Voltage",
        "length_bytes": 2,
        "byte_order": "big",
        "scale": 0.1,
        "offset": 0,
        "unit": "V",
        "min": 0,
        "max": 100,
    },
    0x105: {
        "name": "Motor_Temperature",
        "length_bytes": 1,
        "byte_order": "big",
        "scale": 1,
        "offset": 0,
        "unit": "C",
        "min": 0,
        "max": 150,
    },
}


def extract_signal(can_id, data_bytes):
    """Decode one CAN frame's raw bytes into a real value, using the DBC spec.

    Returns a dict like {"name": "Motor_RPM", "value": 5160, "unit": "RPM"}
    or None if the ID is unknown / the data is too short (bad frame).
    """
    spec = message_specs.get(can_id)
    if spec is None:
        return None  # unknown CAN ID -- not in our DBC

    length = spec["length_bytes"]
    if len(data_bytes) < length:
        return None  # malformed frame -- fewer bytes than the spec expects

    relevant_bytes = data_bytes[:length]

    # Unpack the raw integer, matching byte width and byte order to the spec
    if length == 2:
        fmt = ">H" if spec["byte_order"] == "big" else "<H"
    else:
        fmt = "B"  # single byte, no order to worry about
    raw = struct.unpack(fmt, relevant_bytes)[0]

    # The core DBC formula: real value = raw * scale + offset
    # Rounded to 2 decimals to avoid floating point noise (e.g. 0.1 * 29 in
    # binary doesn't come out to exactly 2.9 -- this is just display cleanup).
    value = round(raw * spec["scale"] + spec["offset"], 2)

    warning = None
    if value < spec["min"] or value > spec["max"]:
        warning = (
            f"{spec['name']} out of range: {value}{spec['unit']} "
            f"(valid range {spec['min']}-{spec['max']}{spec['unit']})"
        )

    return {
        "name": spec["name"],
        "value": value,
        "unit": spec["unit"],
        "raw": raw,
        "min": spec["min"],
        "max": spec["max"],
        "warning": warning,
    }


def parse_log_line(line):
    """Turns one raw log line like:
        'ID: 0x101 DLC: 2 Data: [00 99]'
    into (can_id, data_bytes), e.g. (0x101, b'\\x00\\x99').
    Returns None for blank or badly formatted lines instead of crashing.
    """
    line = line.strip()
    if not line:
        return None
    try:
        id_text = line.split("ID:")[1].split("DLC:")[0].strip()
        can_id = int(id_text, 16)

        data_text = line.split("Data:")[1].strip().strip("[]")
        byte_values = [int(b, 16) for b in data_text.split()]
        data_bytes = bytes(byte_values)

        return can_id, data_bytes
    except (IndexError, ValueError):
        return None  # line didn't match the expected format


def parse_log_file(path=None):
    """Reads every line of the log file, decodes each into a real signal
    value, and returns a list of decoded signals in the order they appear."""
    if path is None:
        path = LOG_PATH

    decoded_signals = []
    with open(path) as f:
        for line in f:
            parsed = parse_log_line(line)
            if parsed is None:
                continue  # skip blank/bad lines
            can_id, data_bytes = parsed
            signal = extract_signal(can_id, data_bytes)
            if signal is not None:
                decoded_signals.append(signal)
    return decoded_signals


class VehicleState:
    """Keeps the most recently seen value for every signal.

    Needed because not every signal arrives on every log line -- RPM/Speed
    show up constantly, but SOC/Temp only every 10th tick (see the
    Simulator's priority-based send rates). Between updates, the dashboard
    should keep showing the last known value, not blank/zero.
    """

    def __init__(self):
        self.latest = {}  # signal name -> its most recent decoded signal dict

    def update(self, signal):
        self.latest[signal["name"]] = signal

    def snapshot(self):
        """A plain dict copy of {signal_name: decoded_signal} right now."""
        return dict(self.latest)


def track_latest_values(path=None):
    """Reads the whole log file and returns the FINAL last-known value of
    every signal -- i.e. 'what is the current state of the car?'"""
    state = VehicleState()
    for signal in parse_log_file(path):
        state.update(signal)
    return state.snapshot()


if __name__ == "__main__":
    print("--- Every decoded frame, in order ---")
    for signal in parse_log_file():
        print(f"{signal['name']}: {signal['value']} {signal['unit']}")
        if signal["warning"]:
            print(f"  [!] {signal['warning']}")

    print("\n--- Final known state of the vehicle ---")
    final_state = track_latest_values()
    for name, signal in final_state.items():
        print(f"{name}: {signal['value']} {signal['unit']}")