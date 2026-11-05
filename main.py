"""CSV adapter and readable command-line output for independent cases."""

import argparse
import csv
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from allocator import Request, allocate


@dataclass
class Case:
    case_id: str
    period: str
    item: str
    source_location: str
    available_qty: int
    requests: list[Request] = field(default_factory=list)
    destinations: dict[str, str] = field(default_factory=dict)


def read_rows(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    """Reject ambiguous CSV structure before calculating any case."""
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        fields = reader.fieldnames or []
        if len(fields) != len(columns) or set(fields) != set(columns):
            raise ValueError(f"{path.name}: expected columns {', '.join(columns)}")
        rows = []
        for row in reader:
            if None in row or any(value is None or not value.strip()
                                  for value in row.values()):
                raise ValueError(f"{path.name}, line {reader.line_num}: missing/extra value")
            rows.append({key: value.strip() for key, value in row.items()})
        return rows


def integer(value: str, context: str) -> int:
    if not re.fullmatch(r"[+-]?[0-9]+", value):
        raise ValueError(f"{context}: expected a base-10 integer, got {value!r}")
    return int(value)


def load_cases(data_dir: Path) -> list[Case]:
    supplies = read_rows(data_dir / "supply.csv", (
        "case_id", "period", "item", "source_location", "available_qty"))
    requests = read_rows(data_dir / "requests.csv", (
        "case_id", "request_id", "dest_location", "priority", "demand_qty",
        "min_lot", "rounding_value"))
    cases: dict[str, Case] = {}
    for row in supplies:
        name = row["case_id"]
        if name in cases:
            raise ValueError(f"supply.csv: duplicate case_id {name}")
        cases[name] = Case(name, row["period"], row["item"], row["source_location"],
                           integer(row["available_qty"], f"{name}: available_qty"))
    for row in requests:
        name, request_id = row["case_id"], row["request_id"]
        if name not in cases:
            raise ValueError(f"requests.csv: unknown case_id {name}")
        values = {field: integer(row[field], f"{name}/{request_id}: {field}")
                  for field in ("priority", "demand_qty", "min_lot", "rounding_value")}
        cases[name].requests.append(Request(request_id, **values))
        cases[name].destinations[request_id] = row["dest_location"]
    return [cases[name] for name in sorted(cases)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path,
                        default=Path(__file__).resolve().parent / "data")
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.data_dir)
    except (OSError, UnicodeError, ValueError, csv.Error) as error:
        print(f"INPUT ERROR: {error}", file=sys.stderr)
        return 2
    invalid = 0
    for case in cases:
        print(f"\n{case.case_id} | {case.period} | {case.item} | "
              f"{case.source_location} | available={case.available_qty}")
        try:
            plan = allocate(case.available_qty, case.requests)
        except ValueError as error:
            invalid += 1
            print(f"  INVALID: {error}")
            print("  No allocation plan; the case is held for correction.")
            continue
        print(f"  {'request':<12} {'destination':<14} {'priority':>8} "
              f"{'demand':>10} {'allocated':>10}")
        for request in sorted(case.requests, key=lambda r: r.request_id):
            print(f"  {request.request_id:<12} "
                  f"{case.destinations[request.request_id]:<14} "
                  f"{request.priority:>8} {request.demand_qty:>10} "
                  f"{plan[request.request_id]:>10}")
        allocated = sum(plan.values())
        print(f"  allocated={allocated}; unallocated={case.available_qty - allocated}")
        unavailable = [r.request_id for r in case.requests if r.demand_qty > 0 and
                       r.demand_qty // r.rounding_value * r.rounding_value <
                       max(r.rounding_value, (r.min_lot + r.rounding_value - 1) //
                           r.rounding_value * r.rounding_value)]
        if unavailable:
            print("  Cannot ship without oversupply: " + ", ".join(sorted(unavailable)))
    print(f"\nCases: {len(cases)}; planned: {len(cases) - invalid}; invalid: {invalid}")
    return 1 if invalid else 0


if __name__ == "__main__":
    sys.exit(main())
