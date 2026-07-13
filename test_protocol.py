#!/usr/bin/env python3
"""Offline invariants for the 176248 remix protocol."""

import json
from pathlib import Path

import collector


def main() -> None:
    assert collector.TARGET_KEY == "176248"
    assert len(collector.CONTROL_KEYS) == 127
    assert len(set(collector.CONTROL_KEYS)) == 127
    assert collector.TARGET_KEY not in collector.CONTROL_KEYS

    packet_a = bytes(range(256)) * 3
    packet_b = bytes(reversed(range(256))) * 3
    first = collector.compute_detection(packet_a, packet_b)
    second = collector.compute_detection(packet_a, packet_b)
    assert first == second
    assert 1 <= first["target"]["rank_combined"] <= 128

    numbers, evidence = collector.decode_numbers(packet_a, packet_b)
    assert len(numbers) == 7
    assert len(set(numbers)) == 7
    assert numbers == sorted(numbers)
    assert all(1 <= number <= 37 for number in numbers)
    assert len(evidence) == 7

    collector.write_protocol_file()
    protocol = json.loads(Path("protocol.json").read_text(encoding="utf-8"))
    assert protocol["target_key"] == "176248"
    assert protocol["control_keys"] == collector.CONTROL_KEYS
    print("protocol tests: OK")


if __name__ == "__main__":
    main()
