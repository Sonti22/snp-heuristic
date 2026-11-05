"""Business rules and invariants for the single-case allocation operation."""

import csv
import itertools
import random
from pathlib import Path

import pytest

from allocator import Request, allocate


DATA = Path(__file__).resolve().parents[1] / "data"


def csv_case(case_id):
    with (DATA / "supply.csv").open(encoding="utf-8-sig", newline="") as stream:
        supply = next(row for row in csv.DictReader(stream) if row["case_id"] == case_id)
    with (DATA / "requests.csv").open(encoding="utf-8-sig", newline="") as stream:
        requests = [
            Request(
                request_id=row["request_id"],
                priority=int(row["priority"]),
                demand_qty=int(row["demand_qty"]),
                min_lot=int(row["min_lot"]),
                rounding_value=int(row["rounding_value"]),
            )
            for row in csv.DictReader(stream)
            if row["case_id"] == case_id
        ]
    return int(supply["available_qty"]), requests


@pytest.mark.parametrize(
    "case_id,expected,remaining",
    [
        ("CASE_01", {"R-101": 25, "R-102": 25, "R-103": 25}, 25),
        ("CASE_02", {"R-201": 40, "R-202": 30, "R-203": 30, "R-204": 0}, 0),
        ("CASE_04", {"R-401": 20, "R-402": 20, "R-403": 19, "R-404": 19, "R-405": 19}, 0),
        ("CASE_05", {"R-501": 25, "R-502": 30, "R-503": 5}, 440),
        ("CASE_06", {"R-601": 100, "R-602": 0, "R-603": 77}, 23),
        ("CASE_07", {"R-701": 100, "R-702": 0, "R-703": 0}, 0),
    ],
)
def test_supplied_valid_cases(case_id, expected, remaining):
    """Фиксирует планы из CSV: изменение правил незаметно изменит отгрузки и остатки."""
    available, requests = csv_case(case_id)
    actual = allocate(available, requests)
    assert actual == expected
    assert available - sum(actual.values()) == remaining


def test_supplied_invalid_case_reports_both_errors():
    """Отклоняет весь CASE_03 с обеими причинами: иначе ошибочные данные создадут частичный план."""
    available, requests = csv_case("CASE_03")
    with pytest.raises(ValueError) as error:
        allocate(available, requests)
    message = str(error.value)
    assert "R-302" in message and "demand_qty" in message
    assert "R-304" in message and "rounding_value" in message


def test_smaller_first_lots_maximize_recipient_count():
    """Сначала допускает меньшие партии: сортировка только по ID уменьшит охват получателей."""
    requests = [Request("A", 1, 6, 6), Request("B", 1, 10, 3), Request("C", 1, 10, 3)]
    assert allocate(6, requests) == {"A": 0, "B": 3, "C": 3}


@pytest.mark.parametrize("available,expected", [(49, 0), (50, 50), (74, 50)])
def test_minimum_is_rounded_up_to_a_valid_multiple(available, expected):
    """Учитывает min_lot вместе с кратностью: иначе первая отгрузка нарушит одно из ограничений."""
    assert allocate(available, [Request("A", 1, 100, 30, 25)]) == {"A": expected}


def test_lower_priority_receives_only_unusable_higher_priority_remainder():
    """Передаёт младшему приоритету неотгружаемый остаток: иначе теряется доступный сервис."""
    assert allocate(10, [Request("A", 1, 9, 0, 7), Request("B", 2, 10)]) == {"A": 7, "B": 3}


def test_priority_precedes_recipient_count():
    """Сохраняет строгий приоритет выше охвата: иначе маленькая младшая заявка вытеснит старшую."""
    assert allocate(10, [Request("A", 1, 10, 10), Request("B", 2, 1)]) == {"A": 10, "B": 0}


def test_unshippable_requests_receive_zero_without_invalidating_case():
    """Возвращает ноль при несовместимости спроса и партии: иначе появится перепоставка или ложная ошибка."""
    requests = [Request("A", 1, 6, 0, 7), Request("B", 1, 10, 11), Request("C", 1, 0)]
    assert allocate(100, requests) == {"A": 0, "B": 0, "C": 0}


def test_remaining_capacity_is_shared_proportionally_after_first_lots():
    """Делит остаток пропорционально доступному спросу: иначе ID превратится в главный вес заявки."""
    assert allocate(20, [Request("A", 1, 10), Request("B", 1, 30)]) == {"A": 5, "B": 15}


def test_bulk_cleanup_uses_residue_that_cannot_fit_a_large_step():
    """Добирает остаток малыми кратностями: без завершающего прохода останется отгружаемый запас."""
    requests = [Request("A", 1, 102, 0, 2), Request("B", 1, 1100, 0, 100)]
    assert allocate(200, requests) == {"A": 100, "B": 100}


def test_all_input_permutations_have_identical_values_and_order():
    """Разрешает ничьи независимо от порядка Iterable: иначе перестановка CSV изменит план."""
    requests = [Request("C", 1, 10), Request("A", 1, 10), Request("B", 1, 10)]
    expected = list(allocate(5, requests).items())
    assert dict(expected) == {"A": 2, "B": 2, "C": 1}
    for permutation in itertools.permutations(requests):
        assert list(allocate(5, permutation).items()) == expected


def test_generator_is_consumed_once():
    """Поддерживает одноразовый Iterable: повторное чтение генератора потеряет заявки."""
    assert allocate(5, (Request(name, 1, 5) for name in ("B", "A"))) == {"A": 3, "B": 2}


def test_large_integers_keep_exact_ties_without_unit_loops():
    """Считает огромные объёмы точно: float потеряет единицу, а цикл по штукам не завершится."""
    unit = 10**50
    requests = [Request("A", 1, 2 * unit), Request("B", 1, 2 * unit)]
    assert allocate(2 * unit + 1, requests) == {"A": unit + 1, "B": unit}


def test_empty_requests_and_zero_supply():
    """Обрабатывает отсутствие заявок и запаса: иначе пустой расчёт вызовет ошибку или отгрузку."""
    assert allocate(100, []) == {}
    assert allocate(0, [Request("A", 1, 10)]) == {"A": 0}


@pytest.mark.parametrize("available", [-1, True, False, 1.5, "10", None])
def test_invalid_supply_is_rejected_even_without_requests(available):
    """Проверяет запас до расчёта: отрицательные или нецелые значения нарушат баланс."""
    with pytest.raises(ValueError):
        allocate(available, [])


@pytest.mark.parametrize(
    "field,value",
    [
        ("priority", 0), ("priority", -1), ("priority", True), ("priority", 1.0),
        ("demand_qty", -1), ("demand_qty", False), ("demand_qty", 1.5),
        ("min_lot", -1), ("min_lot", True), ("min_lot", "2"),
        ("rounding_value", 0), ("rounding_value", -1), ("rounding_value", True),
        ("rounding_value", 2.0), ("request_id", ""), ("request_id", "  "),
        ("request_id", None),
    ],
)
def test_invalid_request_fields_are_rejected_before_allocation(field, value):
    """Проверяет каждое поле даже при нулевом запасе: иначе повреждённые данные останутся незамеченными."""
    fields = dict(request_id="A", priority=1, demand_qty=10, min_lot=0, rounding_value=1)
    fields[field] = value
    with pytest.raises(ValueError):
        allocate(0, [Request(**fields)])


def test_duplicate_ids_are_rejected():
    """Отклоняет повтор ID: иначе dict затрёт одну заявку и исказит баланс."""
    with pytest.raises(ValueError):
        allocate(10, [Request("A", 1, 10), Request("A", 2, 10)])


def test_seeded_random_cases_preserve_physical_constraints_and_order_independence():
    """Проверяет баланс, партии, спрос и порядок на разных данных: иначе редкие сочетания дадут неверный план."""
    rng = random.Random(20261105)
    for _ in range(300):
        available = rng.randrange(0, 800)
        requests = [
            Request(f"R-{i:02}", rng.randrange(1, 5), rng.randrange(0, 200), rng.randrange(0, 240), rng.randrange(1, 25))
            for i in range(rng.randrange(0, 15))
        ]
        result = allocate(available, requests)
        assert set(result) == {r.request_id for r in requests}
        assert sum(result.values()) <= available
        remainder = available - sum(result.values())
        for request in requests:
            amount = result[request.request_id]
            assert type(amount) is int
            assert 0 <= amount <= request.demand_qty
            assert amount == 0 or amount >= request.min_lot
            assert amount % request.rounding_value == 0
            next_amount = (
                amount + request.rounding_value
                if amount
                else max(request.rounding_value, ((request.min_lot + request.rounding_value - 1) // request.rounding_value) * request.rounding_value)
            )
            assert next_amount > request.demand_qty or next_amount - amount > remainder
        rng.shuffle(requests)
        assert list(allocate(available, requests).items()) == list(result.items())
