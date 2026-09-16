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
from datetime import datetime

# Same trick as the simulator: find sample_data relative to THIS file's
# own location, so it doesn't matter which folder you run this script from.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(SCRIPT_DIR, "..", "sample_data", "can_bus.log")
ERROR_LOG_PATH = os.path.join(SCRIPT_DIR, "..", "sample_data", "error_log.txt")


# --- DBC spec: official ranges from the task document ---
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
    spec = message_specs.get(can_id)
    if spec is None:
        return None

    length = spec["length_bytes"]
    if len(data_bytes) < length:
        return None

    relevant_bytes = data_bytes[:length]

    if length == 2:
        fmt = ">H" if spec["byte_order"] == "big" else "<H"
    else:
        fmt = "B"
    raw = struct.unpack(fmt, relevant_bytes)[0]

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
        return None


def classify_error(line):
    """Figures out WHY a line can't be decoded, instead of just silently
    giving up. Returns None if the line is actually fine, or a short
    reason string otherwise:
      'malformed_line' -- couldn't even be split into ID/bytes
      'unknown_id'     -- ID isn't in our DBC at all
      'dlc_mismatch'   -- fewer data bytes than the spec expects
    """
    parsed = parse_log_line(line)
    if parsed is None:
        return "malformed_line"

    can_id, data_bytes = parsed
    spec = message_specs.get(can_id)
    if spec is None:
        return "unknown_id"
    if len(data_bytes) < spec["length_bytes"]:
        return "dlc_mismatch"
    return None


def log_error(reason, line):
    """Optional: appends one line to error_log.txt as a running audit
    trail. NOT used for counting anymore (see error_summary below) --
    kept only if you want a persistent history of bad frames to look
    through later. Safe to call as often as you like; it just grows."""
    os.makedirs(os.path.dirname(ERROR_LOG_PATH), exist_ok=True)
    timestamp = datetime.now().strftime("%H:%M:%S")
    with open(ERROR_LOG_PATH, "a") as f:
        f.write(f"[{timestamp}] {reason}: {line.strip()}\n")


def parse_log_file(path=None):
    """Reads every line of the log file, decodes each into a real signal
    value, and returns a list of decoded signals in the order they appear.
    Bad lines are simply skipped here -- error COUNTING is handled
    separately by error_summary(), which re-scans the log fresh on every
    call instead of accumulating across repeated calls (see its
    docstring for why that distinction matters)."""
    if path is None:
        path = LOG_PATH

    decoded_signals = []
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue  # skip truly blank lines

            if classify_error(line) is not None:
                continue  # bad frame -- counted separately by error_summary()

            can_id, data_bytes = parse_log_line(line)
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


def error_summary(path=None):
    """Counts bus errors currently present in the log file, RIGHT NOW --
    freshly re-scanning can_bus.log on every call instead of reading from
    a file that keeps growing across calls.

    WHY THIS MATTERS: this used to read from error_log.txt, which
    parse_log_file() appended to every time it ran. Since the dashboard
    re-parses the whole log every 2 seconds (to keep the live view
    updating), it kept re-detecting the SAME bad frames and appending
    them AGAIN each time -- so the count grew forever, even with the
    simulator stopped and the log completely unchanged. Counting fresh
    from the log itself fixes that: the number always reflects what's
    actually in can_bus.log at this moment, not how many times this
    function has been called since the dashboard started."""
    if path is None:
        path = LOG_PATH
    counts = {}
    if not os.path.exists(path):
        return counts
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            reason = classify_error(line)
            if reason is not None:
                counts[reason] = counts.get(reason, 0) + 1
    return counts


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

    print("\n--- Error summary ---")
    errors = error_summary()
    if not errors:
        print("No errors detected.")
    else:
        for reason, count in errors.items():
            print(f"{reason}: {count}")