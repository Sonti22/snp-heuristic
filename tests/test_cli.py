"""End-to-end checks for CSV validation, reporting, and process exit codes."""

import csv
import re
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SUPPLY_COLUMNS = "case_id,period,item,source_location,available_qty".split(",")
REQUEST_COLUMNS = (
    "case_id,request_id,dest_location,priority,demand_qty,min_lot,rounding_value"
).split(",")


def run_cli(*arguments, cwd=ROOT):
    return subprocess.run(
        [sys.executable, str(ROOT / "main.py"), *map(str, arguments)],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=10,
    )


def write_data(directory, supplies, requests):
    for filename, columns, rows in (
        ("supply.csv", SUPPLY_COLUMNS, supplies),
        ("requests.csv", REQUEST_COLUMNS, requests),
    ):
        with (directory / filename).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(columns)
            writer.writerows(rows)
    return directory


def test_supplied_cases_are_reported_from_another_directory(tmp_path):
    """All supplied cases run from any cwd; a bad case must not hide valid plans or return success."""
    result = run_cli(cwd=tmp_path)
    assert result.returncode == 1
    assert result.stderr == ""
    assert re.findall(r"^(CASE_\d+) \|", result.stdout, re.MULTILINE) == [
        f"CASE_{number:02}" for number in range(1, 8)
    ]
    expected = {
        "R-101": 25, "R-102": 25, "R-103": 25,
        "R-201": 40, "R-202": 30, "R-203": 30, "R-204": 0,
        "R-401": 20, "R-402": 20, "R-403": 19, "R-404": 19, "R-405": 19,
        "R-501": 25, "R-502": 30, "R-503": 5,
        "R-601": 100, "R-602": 0, "R-603": 77,
        "R-701": 100, "R-702": 0, "R-703": 0,
    }
    observed = {
        parts[0]: int(parts[-1])
        for line in result.stdout.splitlines()
        if (parts := line.split()) and re.fullmatch(r"R-\d+", parts[0])
    }
    assert observed == expected
    assert re.findall(r"allocated=(\d+); unallocated=(\d+)", result.stdout) == [
        ("75", "25"), ("100", "0"), ("97", "0"),
        ("60", "440"), ("177", "23"), ("100", "0"),
    ]
    assert "R-302: demand_qty" in result.stdout
    assert "R-304: rounding_value" in result.stdout
    assert "No allocation plan" in result.stdout
    assert "Cannot ship without oversupply: R-602" in result.stdout
    assert "Cases: 7; planned: 6; invalid: 1" in result.stdout


def test_valid_input_returns_success_and_remaining_quantity(tmp_path):
    """A valid external CSV directory returns zero and its balance; callers must distinguish it from rejected input."""
    data = write_data(tmp_path, [["C", "2026-01", "I", "P", 12]], [
        ["C", "A", "D1", 1, 7, 0, 5],
        ["C", "B", "D2", 2, 10, 0, 1],
    ])
    result = run_cli("--data-dir", data)
    assert result.returncode == 0
    assert result.stderr == ""
    assert re.search(r"A\s+D1\s+1\s+7\s+5", result.stdout)
    assert re.search(r"B\s+D2\s+2\s+10\s+7", result.stdout)
    assert "allocated=12; unallocated=0" in result.stdout
    assert "Cases: 1; planned: 1; invalid: 0" in result.stdout


@pytest.mark.parametrize("supplies, expected", [
    ([], "Cases: 0; planned: 0; invalid: 0"),
    ([["C", "2026-01", "I", "P", 12]], "allocated=0; unallocated=12"),
], ids=["no-cases", "case-without-requests"])
def test_empty_data_is_a_valid_plan(tmp_path, supplies, expected):
    """Header-only data and cases without requests succeed; empty workloads must not crash or consume stock."""
    data = write_data(tmp_path, supplies, [])
    result = run_cli("--data-dir", data)
    assert result.returncode == 0
    assert result.stderr == ""
    assert expected in result.stdout


@pytest.mark.parametrize("kind, diagnostic", [
    ("header", "expected columns"),
    ("missing-value", "missing/extra value"),
    ("extra-value", "missing/extra value"),
    ("unknown-case", "unknown case_id UNKNOWN"),
    ("duplicate-supply", "duplicate case_id C"),
    ("non-integer", "expected a base-10 integer"),
], ids=lambda value: value)
def test_ambiguous_input_is_rejected_before_any_plan(tmp_path, kind, diagnostic):
    """Structural errors return two before printing plans; silently accepting them could attach stock or requests incorrectly."""
    supplies = [["C", "2026-01", "I", "P", 12]]
    requests = [["C", "A", "D", 1, 10, 0, 1]]
    if kind == "missing-value":
        requests[0].pop()
    elif kind == "extra-value":
        requests[0].append("unexpected")
    elif kind == "unknown-case":
        requests[0][0] = "UNKNOWN"
    elif kind == "duplicate-supply":
        supplies.append(supplies[0].copy())
    elif kind == "non-integer":
        requests[0][4] = "2.5"
    data = write_data(tmp_path, supplies, requests)
    if kind == "header":
        (data / "supply.csv").write_text("case_id,available_qty\nC,12\n", encoding="utf-8")
    result = run_cli("--data-dir", data)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "INPUT ERROR:" in result.stderr
    assert diagnostic in result.stderr
    assert "Traceback" not in result.stderr


def test_missing_input_file_has_a_controlled_error(tmp_path):
    """A missing CSV returns two without a traceback; automation must not mistake an unread workload for success."""
    result = run_cli("--data-dir", tmp_path)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "INPUT ERROR:" in result.stderr
    assert "supply.csv" in result.stderr
    assert "Traceback" not in result.stderr
