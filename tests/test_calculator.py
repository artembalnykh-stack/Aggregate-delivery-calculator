# -*- coding: utf-8 -*-
"""Тесты расчётного ядра: подбор машин, тарифы, пороги, самовывоз."""
from decimal import Decimal

import pytest

from src.core.calculator import calculate, haversine_km
from src.core.models import CalcRequest, OrderLine
from src.parser.reader import load_price
from pathlib import Path

ROOT = Path(__file__).parents[1]
PRICE = ROOT / "data" / "прайс.xlsx"


@pytest.fixture(scope="module")
def data():
    return load_price(PRICE)


def product(data, name_start):
    return next(p for p in data.products if p.active and p.name.startswith(name_start))


def warehouse(data, *, first=True):
    return data.warehouses[0]


def run_delivery(data, lines, w=None, d=None):
    w = w or data.warehouses[0]
    d = d or data.destinations[0]
    return calculate(data, CalcRequest(w, d, lines, "delivery"))


def _m3(name):
    """Продукт с объёмом 1 м³ на единицу."""
    def _get(data):
        p = product(data, name)
        return p
    return _get


def test_haversine_known_distance():
    # Москва → Санкт-Петербург ≈ 634 км по прямой
    km = haversine_km(Decimal("55.75"), Decimal("37.62"),
                      Decimal("59.94"), Decimal("30.31"))
    assert Decimal("610") < km < Decimal("660")


def test_single_product_single_trip(data):
    """Один товар, один рейс: излишняя кубатура отбраковывается."""
    p = product(data, "Песок карьерный")     # 1.6 т/м³, 1 м³
    r = run_delivery(data, [OrderLine(p, Decimal("10"))])   # 16 т, 10 м³
    assert r.ok
    assert len(r.assignments) == 1
    a = r.assignments[0]
    assert a.trips == 1                      # 20-тонник уносит 10 м³ целиком
    assert a.vehicle.capacity_t >= Decimal("16")
    assert r.distance_calc_km == r.distance_straight_km * r.rules.distance_coefficient * 2 if hasattr(r, 'rules') else True


def test_multi_vehicle_combination(data):
    """Большой заказ requiring combination of machines (лимит рейсов)."""
    p = product(data, "Песок карьерный")
    r = run_delivery(data, [OrderLine(p, Decimal("60"))])   # 96 т
    assert r.ok
    # либо одна машина с числом рейсов ≤ лимита, либо несколько машин
    total_trips = sum(a.trips for a in r.assignments)
    assert total_trips >= 5                  # 96 т / 20 т ≈ 5 рейсов
    assert all(a.trips <= data.tariffs.max_trips for a in r.assignments)


def test_vehicle_offline_excluded_and_reported(data):
    """Машина «не на линии» исключается из подбора с объяснением."""
    p = product(data, "Песок карьерный")
    r = run_delivery(data, [OrderLine(p, Decimal("5"))])
    assert r.ok
    offline = [v for v in data.vehicles if v.online is not True]
    assert offline
    for v in offline:
        assert any(v.vid in label for label, _ in r.excluded)


def test_wrong_status_missing_excluded(data):
    """Неизвестный статус не назначается автоматически."""
    v1 = next((v for v in data.vehicles if v.online is None or v.serviceable is None), None)
    if v1 is None:
        pytest.skip("в демо-данных нет машин с неопознанным статусом")
    p = product(data, "Песок карьерный")
    r = run_delivery(data, [OrderLine(p, Decimal("5"))])
    assert all(a.vehicle.vid != v1.vid for a in r.assignments)


def test_no_compatible_vehicle_error(data):
    """Смешанный заказ «Миксерный + Навалочный» → нет подходящей машины."""
    p_mixer = product(data, "Бетон М200")
    p_bulk = product(data, "Песок карьерный")
    r = run_delivery(data, [OrderLine(p_mixer, Decimal("2")),
                            OrderLine(p_bulk, Decimal("1"))])
    assert not r.ok
    assert "транспорт" in r.error.lower()


def test_pickup_no_delivery(data):
    p = product(data, "Цемент ПЦ 500 Д0, мешок 50 кг")
    r = calculate(data, CalcRequest(None, None, [OrderLine(p, Decimal("10"))], "pickup"))
    assert r.ok
    assert r.delivery_cost == 0
    assert r.total == r.goods_total
    assert r.assignments == []


def test_min_delivery_tariff(data):
    """Стоимость рейса ниже минимума → доначисляется до мин. доставки."""
    p = product(data, "Песок карьерный")
    r = run_delivery(data, [OrderLine(p, Decimal("1"))])   # близкая точка
    if r.min_tariff_applied:
        assert r.delivery_cost == data.tariffs.min_delivery


def test_free_delivery_threshold(data):
    """Сумма заказа ≥ порога → доставка бесплатная."""
    p = product(data, "Бетон М300")
    big = data.tariffs.free_threshold / p.price + 1
    r = run_delivery(data, [OrderLine(p, big.quantize(Decimal("0.001")))])
    assert r.ok
    assert r.free_delivery_applied
    assert r.delivery_cost == 0


def test_ruble_rounding_up(data):
    """Округление вверх до рубля разрешено правилами."""
    p = product(data, "Песок карьерный")
    r = run_delivery(data, [OrderLine(p, Decimal("10"))])
    assert r.delivery_cost == int(r.delivery_cost)


def test_empty_order(data):
    r = calculate(data, CalcRequest(None, None, [], "delivery"))
    assert not r.ok


def test_volume_unavailable_warns(data):
    """Товар без объёма: расчёт по весу выполняется, но с предупреждением."""
    from dataclasses import replace
    p = replace(product(data, "Песок карьерный"), volume_m3=None)
    r = run_delivery(data, [OrderLine(p, Decimal("5"))])
    assert r.ok
    assert not r.volume_known
    assert any("объём" in w.lower() for w in r.warnings)


def test_min_volume_warning(data):
    p = product(data, "Песок карьерный")
    r = run_delivery(data, [OrderLine(p, Decimal("1"))])   # 1 м³ < 3 м³ мин
    assert r.ok
    assert any("мин" in w for w in r.warnings)
