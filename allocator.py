"""Deterministic shortage allocation with strict priority and no oversupply."""

from dataclasses import dataclass
from itertools import groupby
from typing import Iterable


@dataclass(frozen=True)
class Request:
    request_id: str
    priority: int
    demand_qty: int
    min_lot: int = 0
    rounding_value: int = 1


def _validate(available_qty: int, requests: list[Request]) -> None:
    errors = []
    if type(available_qty) is not int or available_qty < 0:
        errors.append("available_qty must be a non-negative integer")
    seen = set()
    for index, request in enumerate(requests, 1):
        if not isinstance(request, Request):
            errors.append(f"request #{index} must be a Request")
            continue
        name = request.request_id
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            errors.append(f"request #{index}: request_id must be non-empty and trimmed")
        elif name in seen:
            errors.append(f"duplicate request_id: {name}")
        else:
            seen.add(name)
        for field, minimum in (("priority", 1), ("demand_qty", 0),
                               ("min_lot", 0), ("rounding_value", 1)):
            value = getattr(request, field)
            if type(value) is not int or value < minimum:
                errors.append(f"{name}: {field} must be an integer >= {minimum}")
    if errors:
        raise ValueError("; ".join(errors))


def allocate(available_qty: int, requests: Iterable[Request]) -> dict[str, int]:
    """Reserve lots, share headroom proportionally, then use feasible residue.

    Invalid input rejects the whole case. Equal choices use request_id, never
    input order. The number of iterations does not depend on stock quantities.
    """
    rows = list(requests)
    _validate(available_qty, rows)
    plan = {r.request_id: 0 for r in sorted(rows, key=lambda r: r.request_id)}
    remaining = available_qty
    rows.sort(key=lambda r: (r.priority, r.request_id))
    for _, group in groupby(rows, key=lambda r: r.priority):
        candidates = []
        for request in group:
            step = request.rounding_value
            cap = request.demand_qty // step * step
            first = max(step, (request.min_lot + step - 1) // step * step)
            if cap >= first:
                candidates.append((first, request.request_id, step, cap))

        # Smallest feasible first lots maximize recipient count in this tier.
        admitted = []
        for first, name, step, cap in sorted(candidates):
            if first <= remaining:
                plan[name] = first
                remaining -= first
                admitted.append((name, step, cap))

        budget = remaining
        headroom = sum(cap - plan[name] for name, _, cap in admitted)
        if not budget or not headroom:
            continue
        remainders = []
        for name, step, cap in admitted:
            numerator = budget * (cap - plan[name])
            # If supply exceeds all headroom, simply fill every admitted cap.
            extra = min(cap - plan[name], numerator // headroom // step * step)
            plan[name] += extra
            remaining -= extra
            remainders.append((-(numerator - extra * headroom), name, step, cap))

        order = sorted(remainders)
        # One quantum per request preserves largest-remainder behavior for
        # equal increments; integer numerators avoid floating-point ties.
        for _, name, step, cap in order:
            if plan[name] + step <= cap and step <= remaining:
                plan[name] += step
                remaining -= step

        # Mixed increments may leave more than one small quantum. A bulk pass
        # uses it without a quantity-sized loop; this is a greedy heuristic.
        for _, name, step, cap in order:
            extra = min(cap - plan[name], remaining // step * step)
            plan[name] += extra
            remaining -= extra
    return plan
