"""
CAN Bus Dashboard - Phase 3 (v3)
----------------------------------
Small Flask web server. Zero JavaScript anywhere in this file -- every
page is still an HTML string with {values} filled in, same as before.

Four pages:
  GET  /        -> the outer page shell: embedded live cluster + embedded
                   error panel + the diagnostics/testing panel
  GET  /cluster -> JUST the instrument cluster cards, refreshing itself
                   every 2 seconds
  GET  /errors  -> JUST the bus-error panel, refreshing itself every
                   2 seconds, completely separate from /cluster
  POST /test    -> receives a submitted test, runs it through
                   extract_signal(), remembers the result, sends you
                   back to "/" to see it.

WHY THREE SEPARATE LIVE PIECES INSTEAD OF ONE: each auto-refreshing region
sits in its own <iframe> (a plain HTML tag that shows one page inside
another). Earlier, the cluster cards AND the error panel were both jammed
into one iframe with a fixed height -- once the error panel grew past
that fixed height, it got clipped instead of shown, because the iframe
had scrolling turned off. Giving the error panel its OWN iframe, sized
for what it actually contains, fixes that at the root instead of just
making the box taller and hoping nothing overflows again.

Run this file, then open http://127.0.0.1:5000 and leave the tab open.
"""

import os
import sys
from datetime import datetime

from flask import Flask, request, redirect

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(SCRIPT_DIR, "..", "phase2_parser"))
from can_parser import parse_log_file, VehicleState, extract_signal, parse_log_line, error_summary  # noqa: E402

app = Flask(__name__)

BRAND_COLOR = "#D60110"  # sampled directly from DAO EVTech's real logo
BRAND_NAME = "DAO EVTech"
LOGO_PATH = os.path.join(SCRIPT_DIR, "static", "logo.png")

LAST_RESULT = None

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
SIGNAL_ORDER = ["Motor_RPM", "Vehicle_Speed", "Battery_SOC", "Battery_Voltage", "Motor_Temperature"]

ERROR_LABELS = {
    "unknown_id": "Unknown CAN ID",
    "dlc_mismatch": "Incorrect DLC (data too short)",
    "malformed_line": "Malformed log line",
}

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


def parse_hex_id(text):
    text = text.strip()
    if text.lower().startswith("0x"):
        text = text[2:]
    if not text:
        raise ValueError("empty CAN ID")
    return int(text, 16)


def parse_hex_bytes(text):
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


PAGE_STYLE = """
:root {
  --bg: #14171a;
  --panel: #1c2024;
  --panel-2: #1a1d1f;
  --border: #2a2f34;
  --text: #ece7dd;
  --text-muted: #8b9096;
  --danger: #d9605f;
  --ok: #7fb069;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 32px 40px 50px;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, "Segoe UI", sans-serif;
}
h1 { font-size: 21px; font-weight: 600; margin: 0 0 3px 0; letter-spacing: 0.2px; }
h2 { font-size: 15px; font-weight: 600; margin: 0 0 4px 0; color: var(--text); }
.timestamp { color: var(--text-muted); font-size: 12px; margin-bottom: 22px; }
.section-note { color: var(--text-muted); font-size: 13px; margin: 0 0 18px 0; max-width: 60ch; }

.brand-bar {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 18px;
  padding-bottom: 16px;
  border-bottom: 2px solid var(--brand);
}
.logo-chip {
  background: #ffffff;
  border-radius: 8px;
  padding: 6px 12px;
  display: flex;
  align-items: center;
  box-shadow: 0 1px 3px rgba(0,0,0,0.4);
}
.logo-chip img { height: 34px; display: block; }
.brand-mark {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: var(--brand);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-weight: 700;
  font-size: 16px;
  flex-shrink: 0;
}
.brand-name { font-size: 17px; font-weight: 700; line-height: 1.2; letter-spacing: 0.2px; }
.brand-tagline { font-size: 12px; color: var(--text-muted); margin-top: 1px; }

.cluster {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  max-width: 920px;
}
.card {
  background: linear-gradient(180deg, var(--panel) 0%, var(--panel-2) 100%);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 18px 20px;
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
.label { font-size: 12px; color: var(--text-muted); margin-bottom: 9px; }
.value {
  font-family: "SF Mono", Consolas, monospace;
  font-size: 32px;
  font-weight: 600;
  color: var(--accent);
  line-height: 1;
}
.unit { font-size: 14px; color: var(--text-muted); margin-left: 6px; font-weight: 400; }
.bar-track {
  margin-top: 13px;
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
  margin-top: 11px;
  font-size: 11px;
  color: var(--danger);
  line-height: 1.4;
}
.empty-state {
  border: 1px dashed var(--border);
  border-radius: 10px;
  padding: 26px;
  color: var(--text-muted);
  font-size: 14px;
  max-width: 500px;
}
.empty-state code {
  background: var(--panel-2);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: "SF Mono", Consolas, monospace;
}

.error-panel {
  border-radius: 10px;
  padding: 14px 20px;
  font-size: 13px;
  height: 100%;
  overflow-y: auto;
}
.error-panel.clean {
  border: 1px solid #2c4a37;
  background: #16211b;
}
.error-summary-row {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-muted);
}
.error-panel.has-errors {
  border: 1px solid #4a2c2d;
  background: #1c1516;
}
.error-total { color: var(--danger); font-weight: 600; }
.error-row {
  display: flex;
  justify-content: space-between;
  color: var(--text-muted);
  padding: 5px 0;
  border-top: 1px solid rgba(255,255,255,0.05);
}
.error-row:first-of-type { border-top: none; margin-top: 8px; }
.error-row b { color: var(--danger); font-family: "SF Mono", Consolas, monospace; font-weight: 600; }

.diagnostics {
  max-width: 720px;
  border: 1px solid var(--border);
  background: #16191c;
  padding: 24px 26px 26px;
}
.diagnostics-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  border-bottom: 1px solid var(--border);
  padding-bottom: 13px;
  margin-bottom: 18px;
}
.preset-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 20px;
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
  padding-top: 18px;
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
  margin-top: 20px;
  padding: 15px 18px;
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.7;
}
.result.pass { background: #16241c; border: 1px solid #2c4a37; }
.result.fail { background: #26191a; border: 1px solid #4a2c2d; }
.result.info { background: #171b1e; border: 1px solid var(--border); }
.verdict { font-weight: 600; font-family: "SF Mono", Consolas, monospace; letter-spacing: 0.3px; }
.verdict.pass { color: var(--ok); }
.verdict.fail { color: var(--danger); }
.result-row { color: var(--text-muted); }
.result-row b { color: var(--text); font-weight: 500; }

.live-row {
  display: flex;
  gap: 16px;
  max-width: 920px;
  align-items: stretch;
  margin-bottom: 28px;
}
.cluster-frame {
  flex: 1 1 660px;
  min-width: 0;
  height: 380px;
  border: none;
  background: var(--bg);
}
.error-frame {
  flex: 1 1 240px;
  min-width: 220px;
  height: 380px;
  border: none;
  background: var(--bg);
}
"""

BRAND_STYLE = f":root {{ --brand: {BRAND_COLOR}; }}"

FRAME_PAGE_STYLE = PAGE_STYLE + "\nbody { padding: 6px 4px; }\n"


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


def build_error_panel_html():
    counts = error_summary()

    if not counts:
        return """
    <div class="error-panel clean">
      <div class="error-summary-row">
        <b style="color:var(--ok); font-family:'SF Mono',Consolas,monospace;">0 bus errors</b>
      </div>
      <div class="error-summary-row" style="margin-top:6px;">
        No unknown IDs, DLC mismatches, or malformed frames detected.
      </div>
    </div>"""

    rows = "".join(
        f'<div class="error-row"><span>{ERROR_LABELS.get(reason, reason)}</span><b>{count}</b></div>'
        for reason, count in counts.items()
    )
    total = sum(counts.values())
    return f"""
    <div class="error-panel has-errors">
      <div class="error-summary-row"><span class="error-total">{total} bus error(s) detected</span></div>
      {rows}
    </div>"""


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
    if os.path.exists(LOGO_PATH):
        mark_html = f'<div class="logo-chip"><img src="/static/logo.png" alt="{BRAND_NAME} logo"></div>'
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
    timestamp = datetime.now().strftime("%H:%M:%S")
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="2">
  <style>{FRAME_PAGE_STYLE}{BRAND_STYLE}</style>
</head>
<body>
  {build_brand_html()}
  <div class="timestamp">Last updated {timestamp}</div>
  {build_cluster_html(snapshot)}
</body>
</html>
"""


def build_errors_page():
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="2">
  <style>{FRAME_PAGE_STYLE}{BRAND_STYLE}</style>
</head>
<body style="padding: 10px;">
  <div class="label" style="margin-bottom: 10px;">Bus health</div>
  {build_error_panel_html()}
</body>
</html>
"""


def build_shell_page(result_html):
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
  <div class="live-row">
    <iframe class="cluster-frame" src="/cluster" scrolling="no"></iframe>
    <iframe class="error-frame" src="/errors" scrolling="no"></iframe>
  </div>

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


@app.route("/errors")
def errors():
    return build_errors_page()


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
                    pass
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