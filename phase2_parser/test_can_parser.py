"""
CAN Parser Tests - Step 6
--------------------------
Runs the 4 official test cases from the task document (Section 8:
Testing Requirements) against can_parser.py, and prints PASS/FAIL for each.
"""

from can_parser import extract_signal, parse_log_line


def test_1_correct_parsing():
    """Input: 0x101 with data [0x13, 0x88] -> Expected: RPM = 5000"""
    result = extract_signal(0x101, bytes([0x13, 0x88]))
    assert result is not None, "Expected a decoded signal, got None"
    assert result["value"] == 5000, f"Expected RPM=5000, got {result['value']}"
    print("Test 1 (Correct Parsing) ... PASS")


def test_2_scaling():
    """Input: 0x104 with data [0x02, 0x71] -> Expected: Voltage = 62.5V"""
    result = extract_signal(0x104, bytes([0x02, 0x71]))
    assert result is not None, "Expected a decoded signal, got None"
    assert result["value"] == 62.5, f"Expected Voltage=62.5, got {result['value']}"
    print("Test 2 (Scaling) ... PASS")


def test_3_range_validation():
    """Input: 0x103 with data [0xFF] -> Expected: Warning (SOC > 100%)"""
    result = extract_signal(0x103, bytes([0xFF]))
    assert result is not None, "Expected a decoded signal, got None"
    assert result["value"] > result["max"], "Value should exceed the valid max"
    assert result["warning"] is not None, "Expected a range warning, got None"
    print("Test 3 (Range Validation) ... PASS")
    print(f"         -> {result['warning']}")


def test_4_invalid_messages():
    """Unknown CAN ID / incorrect DLC / malformed data should all be
    handled gracefully (return None), never crash the program."""

    # Unknown CAN ID
    result = extract_signal(0x199, bytes([0x00, 0x01]))
    assert result is None, "Unknown ID should return None"

    # Incorrect DLC (RPM needs 2 bytes, only 1 given)
    result = extract_signal(0x101, bytes([0x14]))
    assert result is None, "Too-short data should return None"

    # Malformed log line (garbled text, not real frame data)
    result = parse_log_line("this is not a valid CAN log line")
    assert result is None, "Malformed line should return None"

    print("Test 4 (Invalid Messages) ... PASS")


if __name__ == "__main__":
    print("Running CAN Parser test suite...\n")
    test_1_correct_parsing()
    test_2_scaling()
    test_3_range_validation()
    test_4_invalid_messages()
    print("\nAll 4 required test cases passed.")