"""
CAN Bus Dashboard - Phase 3 (v2)
----------------------------------
This replaces dashboard_generator.py with a small Flask web server.

WHY THE CHANGE: dashboard_generator.py could only ever show data -- it had
no way to RECEIVE input from you (like typing in a test CAN ID). A plain
HTML page with <meta http-equiv="refresh"> can't listen for a form
submission; something has to be running to catch it. Flask is that
"something" -- but it's still just Python, and there is ZERO JavaScript
anywhere in this file. Every page is still built the exact same way as
before: an HTML string with {values} filled in.

Three pages:
  GET  /        -> the outer page shell: an embedded live cluster + the
                   diagnostics/testing panel
  GET  /cluster -> JUST the instrument cluster cards, on its own, refreshing
                   itself every 2 seconds
  POST /test    -> receives a submitted test (from a button or the manual
                   form), runs it through extract_signal(), remembers the
                   result, then sends you back to "/" to see it.

WHY TWO PAGES INSTEAD OF ONE: the outer page ("/") no longer auto-refreshes
at all. Only the small embedded cluster page ("/cluster") does, using an
<iframe> -- a plain HTML tag that shows one page inside another, like a
window. This fixes a real bug: with ONE auto-refreshing page, the whole
page (including whatever you were mid-typing into the test boxes) reloaded
every 2 seconds, wiping your input before you could click "Run test". Now
only the little cluster window refreshes; the boxes you're typing into sit
outside it and are left alone.

Run this file, then open http://127.0.0.1:5000 in your browser and leave
the tab open.
"""

import os
import sys
from datetime import datetime

from flask import Flask, request, redirect

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(SCRIPT_DIR, "..", "phase2_parser"))
from can_parser import parse_log_file, VehicleState, extract_signal, parse_log_line  # noqa: E402

app = Flask(__name__)

# ---------------------------------------------------------------
# Branding -- edit BRAND_COLOR once you know DAO EVTech's real hex.
# To show your actual logo: drop a file named exactly "logo.png" into a
# "static" folder next to this file (phase3_dashboard/static/logo.png).
# If it's there, it's shown automatically. If not, a plain text wordmark
# is shown instead -- so the dashboard works either way.
# ---------------------------------------------------------------
BRAND_COLOR = "#D60110"  # placeholder teal -- swap for DAO EVTech's real color
BRAND_NAME = "DAO EVTech"
LOGO_PATH = os.path.join(SCRIPT_DIR, "static", "logo.png")

# Holds the result of the most recent test, so it's still visible the next
# time the page auto-refreshes. A real multi-user app would need something
# fancier than a plain variable -- for a one-person local demo, this is fine.
LAST_RESULT = None


# ---------------------------------------------------------------
# Visual tokens -- one place to tune colors per signal
# ---------------------------------------------------------------
SIGNAL_COLORS = {
    "Motor_RPM": "#E8A33D",
    "Vehicle_Speed": "#4FB6C4",
    "Battery_SOC": "#7FB069",
    "Battery_Voltage": "#6D8FE0",
    "Motor_Temperature": "#E0793A",
}
SIGNAL_LABELS = {
    "Motor_RPM": "Motor speed",
    "Vehicle_Speed": "Vehicle speed",
    "Battery_SOC": "Battery charge",
    "Battery_Voltage": "Battery voltage",
    "Motor_Temperature": "Motor temperature",
}
# Display order for the cluster (dict order from the log isn't guaranteed
# to match this, since SOC/Temp arrive less often than RPM/Speed).
SIGNAL_ORDER = ["Motor_RPM", "Vehicle_Speed", "Battery_SOC", "Battery_Voltage", "Motor_Temperature"]

# The 4 required test cases, taken directly from the task doc's Section 8.
PRESETS = {
    "test1": {
        "label": "Test 1: Correct parsing",
        "can_id": "0x101",
        "data_bytes": "13 88",
        "expect_name": "Motor_RPM",
        "expect_value": 5000,
    },
    "test2": {
        "label": "Test 2: Scaling",
        "can_id": "0x104",
        "data_bytes": "02 71",
        "expect_name": "Battery_Voltage",
        "expect_value": 62.5,
    },
    "test3": {
        "label": "Test 3: Range validation",
        "can_id": "0x103",
        "data_bytes": "FF",
        "expect_warning": True,
    },
}


# ---------------------------------------------------------------
# Small helpers: turn what a person types into real bytes
# ---------------------------------------------------------------
def parse_hex_id(text):
    """'0x101', '101', or '0X101' -> 257. Raises ValueError on bad input."""
    text = text.strip()
    if text.lower().startswith("0x"):
        text = text[2:]
    if not text:
        raise ValueError("empty CAN ID")
    return int(text, 16)


def parse_hex_bytes(text):
    """'13 88', '13,88', or '0x13 0x88' -> b'\\x13\\x88'."""
    text = text.strip()
    if not text:
        return b""
    pieces = text.replace(",", " ").split()
    values = []
    for piece in pieces:
        piece = piece.strip()
        if piece.lower().startswith("0x"):
            piece = piece[2:]
        values.append(int(piece, 16))
    return bytes(values)


# ---------------------------------------------------------------
# HTML building -- same f-string approach as before, just more of it
# ---------------------------------------------------------------
PAGE_STYLE = """
:root {
  --bg: #14171a;
  --panel: #1c2024;
  --panel-2: #1a1d1f;
  --border: #2a2f34;
  --text: #ece7dd;
  --text-muted: #8b9096;
  --danger: #d9605f;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 36px 40px 60px;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, "Segoe UI", sans-serif;
}
h1 { font-size: 21px; font-weight: 600; margin: 0 0 3px 0; letter-spacing: 0.2px; }
h2 { font-size: 15px; font-weight: 600; margin: 0 0 4px 0; color: var(--text); }
.timestamp { color: var(--text-muted); font-size: 13px; margin-bottom: 28px; }
.section-note { color: var(--text-muted); font-size: 13px; margin: 0 0 18px 0; max-width: 60ch; }

.brand-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 22px;
}
.brand-bar img { height: 36px; display: block; }
.brand-mark {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  background: var(--brand);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #0c0d0e;
  font-weight: 700;
  font-size: 15px;
  flex-shrink: 0;
}
.brand-name { font-size: 16px; font-weight: 600; line-height: 1.2; }
.brand-tagline { font-size: 12px; color: var(--text-muted); }

.cluster {
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  max-width: 960px;
  margin-bottom: 48px;
}
.card {
  background: linear-gradient(180deg, var(--panel) 0%, var(--panel-2) 100%);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 20px 22px;
  flex: 1 1 190px;
  position: relative;
  overflow: hidden;
}
.card::before {
  content: "";
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 3px;
  background: var(--accent);
}
.label { font-size: 13px; color: var(--text-muted); margin-bottom: 10px; }
.value {
  font-family: "SF Mono", Consolas, monospace;
  font-size: 34px;
  font-weight: 600;
  color: var(--accent);
}
.unit { font-size: 15px; color: var(--text-muted); margin-left: 6px; font-weight: 400; }
.bar-track {
  margin-top: 14px;
  height: 5px;
  background: #0f1113;
  border-radius: 3px;
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  background: var(--accent);
  border-radius: 3px;
  transition: width 0.6s ease;
}
.warning {
  margin-top: 12px;
  font-size: 12px;
  color: var(--danger);
  line-height: 1.4;
}
.empty-state {
  border: 1px dashed var(--border);
  border-radius: 10px;
  padding: 28px;
  color: var(--text-muted);
  font-size: 14px;
  max-width: 520px;
  margin-bottom: 48px;
}
.empty-state code {
  background: var(--panel-2);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: "SF Mono", Consolas, monospace;
}

/* --- Diagnostics panel: deliberately looks like a scan-tool, not a card --- */
.diagnostics {
  max-width: 720px;
  border: 1px solid var(--border);
  background: #16191c;
  padding: 26px 28px 28px;
}
.diagnostics-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  border-bottom: 1px solid var(--border);
  padding-bottom: 14px;
  margin-bottom: 20px;
}
.preset-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 22px;
}
button {
  font-family: -apple-system, "Segoe UI", sans-serif;
  font-size: 13px;
  background: #22262a;
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 9px 14px;
  cursor: pointer;
}
button:hover { border-color: #454b51; }
.run-button {
  background: #2a3d33;
  border-color: #3a5a49;
  color: #bfe3cd;
}
.manual-form {
  display: flex;
  gap: 10px;
  align-items: end;
  flex-wrap: wrap;
  border-top: 1px solid var(--border);
  padding-top: 20px;
}
.field { display: flex; flex-direction: column; gap: 6px; }
.field label { font-size: 12px; color: var(--text-muted); }
.field input {
  font-family: "SF Mono", Consolas, monospace;
  background: #0f1113;
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text);
  padding: 9px 10px;
  font-size: 13px;
  width: 170px;
}
.result {
  margin-top: 22px;
  padding: 16px 18px;
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.7;
}
.result.pass { background: #16241c; border: 1px solid #2c4a37; }
.result.fail { background: #26191a; border: 1px solid #4a2c2d; }
.result.info { background: #171b1e; border: 1px solid var(--border); }
.verdict { font-weight: 600; font-family: "SF Mono", Consolas, monospace; letter-spacing: 0.3px; }
.verdict.pass { color: #6fcf64; }
.verdict.fail { color: var(--danger); }
.result-row { color: var(--text-muted); }
.result-row b { color: var(--text); font-weight: 500; }

.cluster-frame {
  width: 100%;
  max-width: 960px;
  height: 400px;
  border: none;
  background: var(--bg);
  display: block;
  margin-bottom: 32px;
}
"""

# Small separate style tag just for the brand color, kept apart from the
# big PAGE_STYLE string above so editing BRAND_COLOR can never break the
# rest of the CSS (plain strings vs. f-strings handle { } differently).
BRAND_STYLE = f":root {{ --brand: {BRAND_COLOR}; }}"

# Slimmer body padding for the page that lives INSIDE the iframe, so it
# doesn't get double spacing (the outer page already has its own padding).
CLUSTER_PAGE_STYLE = PAGE_STYLE + "\nbody { padding: 4px 4px 20px; }\n"


def build_card_html(name, signal):
    color = SIGNAL_COLORS.get(name, "#999999")
    label = SIGNAL_LABELS.get(name, name)
    has_warning = signal["warning"] is not None

    span = signal["max"] - signal["min"]
    pct = 0 if span <= 0 else max(0, min(100, (signal["value"] - signal["min"]) / span * 100))

    warning_html = f'<div class="warning">{signal["warning"]}</div>' if has_warning else ""

    return f"""
    <div class="card" style="--accent: {'#d9605f' if has_warning else color};">
      <div class="label">{label}</div>
      <div class="value">{signal['value']}<span class="unit">{signal['unit']}</span></div>
      <div class="bar-track"><div class="bar-fill" style="width: {pct:.0f}%;"></div></div>
      {warning_html}
    </div>"""


def build_cluster_html(snapshot):
    if not snapshot:
        return """
    <div class="empty-state">
      No signal data yet. Start the simulator in another terminal:<br><br>
      <code>cd phase1_simulator</code><br>
      <code>python can_simulator.py</code>
    </div>"""

    cards = "".join(
        build_card_html(name, snapshot[name]) for name in SIGNAL_ORDER if name in snapshot
    )
    return f'<div class="cluster">{cards}\n    </div>'


def build_result_html(result):
    if result is None:
        return ""

    if result["status"] == "error":
        return f"""
    <div class="result info">
      <div class="result-row">Couldn't read that input: <b>{result['message']}</b></div>
    </div>"""

    if result["status"] == "invalid_batch":
        rows = "".join(
            f'<div class="result-row">{"&#10003;" if ok else "&#10007;"} {desc}</div>'
            for desc, ok in result["checks"]
        )
        all_pass = all(ok for _, ok in result["checks"])
        verdict_class = "pass" if all_pass else "fail"
        return f"""
    <div class="result {verdict_class}">
      <div class="verdict {verdict_class}">{"PASS" if all_pass else "FAIL"}</div>
      {rows}
    </div>"""

    # Normal single decode result (preset 1/2/3 or manual entry)
    lines = [f'<div class="result-row">Sent: <b>ID 0x{result["can_id"]:03X}, data [{result["bytes_display"]}]</b></div>']

    if result["decoded"] is None:
        lines.append('<div class="result-row">Decoded: <b>nothing — unknown ID or too few data bytes</b></div>')
    else:
        d = result["decoded"]
        lines.append(f'<div class="result-row">Decoded: <b>{d["name"]} = {d["value"]}{d["unit"]}</b></div>')
        if d["warning"]:
            lines.append(f'<div class="result-row" style="color:#d9605f;">Warning: {d["warning"]}</div>')

    verdict_html = ""
    verdict_class = "info"
    if "expected_text" in result:
        verdict_class = "pass" if result["passed"] else "fail"
        lines.append(f'<div class="result-row">Expected: <b>{result["expected_text"]}</b></div>')
        verdict_html = f'<div class="verdict {verdict_class}">{"PASS" if result["passed"] else "FAIL"}</div>'

    return f'<div class="result {verdict_class}">{verdict_html}{"".join(lines)}</div>'


def build_brand_html():
    """Shows your real logo if static/logo.png exists, otherwise a plain
    text wordmark using BRAND_COLOR. Either way, works with no internet
    connection -- important for a live presentation."""
    if os.path.exists(LOGO_PATH):
        mark_html = f'<img src="/static/logo.png" alt="{BRAND_NAME} logo">'
    else:
        initial = BRAND_NAME[0]
        mark_html = f'<div class="brand-mark">{initial}</div>'

    return f"""
  <div class="brand-bar">
    {mark_html}
    <div>
      <div class="brand-name">{BRAND_NAME}</div>
      <div class="brand-tagline">EV instrument cluster</div>
    </div>
  </div>"""


def build_cluster_page(snapshot):
    """The small page that lives INSIDE the iframe. This is the only page
    that still auto-refreshes -- everything in here is safe to reload every
    2 seconds because there's nothing here for you to type into."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="2">
  <style>{CLUSTER_PAGE_STYLE}{BRAND_STYLE}</style>
</head>
<body>
  {build_brand_html()}
  <div class="timestamp">Last updated {timestamp}</div>
  {build_cluster_html(snapshot)}
</body>
</html>
"""


def build_shell_page(result_html):
    """The outer page: the iframe (live numbers) + the diagnostics panel
    (stable -- never reloads on its own, so typing here is never disturbed)."""

    preset_buttons = "".join(
        f"""<form method="POST" action="/test" style="margin:0;">
              <input type="hidden" name="mode" value="preset">
              <input type="hidden" name="preset_key" value="{key}">
              <button type="submit">{p['label']}</button>
            </form>"""
        for key, p in PRESETS.items()
    )
    preset_buttons += """<form method="POST" action="/test" style="margin:0;">
              <input type="hidden" name="mode" value="test4">
              <button type="submit">Test 4: Invalid messages</button>
            </form>"""

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>EV dashboard</title>
  <style>{PAGE_STYLE}{BRAND_STYLE}</style>
</head>
<body>
  <iframe class="cluster-frame" src="/cluster" scrolling="no"></iframe>

  <div class="diagnostics">
    <div class="diagnostics-header">
      <h2>Signal diagnostics</h2>
      <span class="label">manual + preset frame testing</span>
    </div>
    <p class="section-note">
      Run the 4 required test cases from the spec, or type your own CAN ID and
      data bytes to see exactly how the parser decodes them.
    </p>

    <div class="preset-row">{preset_buttons}</div>

    <form class="manual-form" method="POST" action="/test">
      <input type="hidden" name="mode" value="manual">
      <div class="field">
        <label for="can_id">CAN ID (hex)</label>
        <input type="text" id="can_id" name="can_id" placeholder="0x101" required>
      </div>
      <div class="field">
        <label for="data_bytes">Data bytes (hex)</label>
        <input type="text" id="data_bytes" name="data_bytes" placeholder="13 88" required>
      </div>
      <div class="field">
        <label for="expected">Expected value (optional)</label>
        <input type="text" id="expected" name="expected" placeholder="e.g. 5000">
      </div>
      <button class="run-button" type="submit">Run test</button>
    </form>

    {result_html}
  </div>
</body>
</html>
"""


# ---------------------------------------------------------------
# Routes
# ---------------------------------------------------------------
@app.route("/")
def dashboard():
    return build_shell_page(build_result_html(LAST_RESULT))


@app.route("/cluster")
def cluster():
    signals = parse_log_file()
    state = VehicleState()
    for s in signals:
        state.update(s)
    return build_cluster_page(state.snapshot())


@app.route("/test", methods=["POST"])
def run_test():
    global LAST_RESULT
    mode = request.form.get("mode")

    if mode == "preset":
        key = request.form.get("preset_key")
        preset = PRESETS[key]
        can_id = parse_hex_id(preset["can_id"])
        data_bytes = parse_hex_bytes(preset["data_bytes"])
        decoded = extract_signal(can_id, data_bytes)

        if "expect_value" in preset:
            passed = decoded is not None and decoded["value"] == preset["expect_value"]
            expected_text = f"{preset['expect_name']} = {preset['expect_value']}"
        else:
            passed = decoded is not None and decoded["warning"] is not None
            expected_text = "a range warning"

        LAST_RESULT = {
            "status": "ok",
            "can_id": can_id,
            "bytes_display": data_bytes.hex(" ").upper(),
            "decoded": decoded,
            "expected_text": expected_text,
            "passed": passed,
        }

    elif mode == "test4":
        checks = []
        r1 = extract_signal(0x199, bytes([0x00, 0x01]))
        checks.append(("Unknown CAN ID (0x199) returns nothing", r1 is None))
        r2 = extract_signal(0x101, bytes([0x14]))
        checks.append(("Incorrect DLC (0x101 with 1 byte) returns nothing", r2 is None))
        r3 = parse_log_line("this is not a valid CAN log line")
        checks.append(("Malformed log line returns nothing", r3 is None))
        LAST_RESULT = {"status": "invalid_batch", "checks": checks}

    else:  # manual entry
        try:
            can_id = parse_hex_id(request.form.get("can_id", ""))
            data_bytes = parse_hex_bytes(request.form.get("data_bytes", ""))
            decoded = extract_signal(can_id, data_bytes)
            LAST_RESULT = {
                "status": "ok",
                "can_id": can_id,
                "bytes_display": data_bytes.hex(" ").upper(),
                "decoded": decoded,
            }

            expected_text = request.form.get("expected", "").strip()
            if expected_text:
                try:
                    expected_num = float(expected_text)
                    passed = decoded is not None and decoded["value"] == expected_num
                    LAST_RESULT["expected_text"] = expected_text
                    LAST_RESULT["passed"] = passed
                except ValueError:
                    pass  # not a number -- just skip the pass/fail comparison
        except ValueError:
            LAST_RESULT = {
                "status": "error",
                "message": "not valid hex — try a CAN ID like 0x101 and bytes like 13 88",
            }

    return redirect("/")


if __name__ == "__main__":
    print("Dashboard running. Open this in your browser:")
    print("http://127.0.0.1:5000")
    app.run(debug=False)