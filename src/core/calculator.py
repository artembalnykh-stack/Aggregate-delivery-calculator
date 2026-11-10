# -*- coding: utf-8 -*-
"""
Расчётное ядро калькулятора. Не знает ни про UI, ни про БД.

Модель тарификации (настольные правила из внешнего прайса):
  стоимость рейса = базовый тариф + тариф_за_км × расчётное_расстояние
  расчётное_расстояние = прямая_по_координатам × коэф. «по прямой»
                         × (2 если режим «Туда и обратно», иначе 1)
  доставка = Σ рейсов_всех_машин × стоимость_рейса,
             но не меньше минимальной стоимости доставки;
  если сумма заказа ≥ порога бесплатной доставки — доставка бесплатна.
Самовывоз: доставка = 0, транспорт не назначается.
"""
from __future__ import annotations

import math
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

from src.core import vehicle_selector as vs
from src.core.models import CalcRequest, CalcResult, OrderLine
from src.parser.reader import PriceData

D0 = Decimal(0)
TWO = Decimal("0.01")


def _ceil_ruble(x: Decimal) -> Decimal:
    return x.quantize(Decimal("1"), rounding=ROUND_CEILING)


def _round2(x: Decimal) -> Decimal:
    return x.quantize(TWO, rounding=ROUND_HALF_UP)


def haversine_km(lat1: Decimal, lon1: Decimal, lat2: Decimal, lon2: Decimal) -> Decimal:
    """Расстояние по прямым координатам (Земля как сфера), км."""
    phi1, phi2 = math.radians(float(lat1)), math.radians(float(lat2))
    dphi = math.radians(float(lat2 - lat1))
    dlmb = math.radians(float(lon2 - lon1))
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2)
    a = min(1.0, max(0.0, a))
    km = 2 * 6371.0088 * math.asin(math.sqrt(a))
    return Decimal(str(round(km, 3)))


def _validate(req: CalcRequest) -> str | None:
    if not req.lines:
        return "Заказ пуст: добавьте хотя бы один товар."
    for line in req.lines:
        if line.qty is None or line.qty <= 0:
            return f"Количество у товара «{line.product.name}» должно быть больше нуля."
        if line.product.price < 0:
            return f"Некорректная цена у товара «{line.product.name}»."
    if req.mode not in ("delivery", "pickup"):
        return "Неизвестный способ получения заказа."
    if req.mode == 'delivery':
        if req.warehouse is None:
            return "Для доставки выберите склад отгрузки."
        if req.destination is None:
            return "Для доставки выберите пункт назначения."
    return None


def calculate(data: PriceData, req: CalcRequest) -> CalcResult:
    res = CalcResult(ok=False, mode=req.mode)
    err = _validate(req)
    if err:
        res.error = err
        return res

    res.warehouse = req.warehouse
    res.destination = req.destination

    # ---- состав заказа
    goods_total = sum((line.qty * line.product.price for line in req.lines), D0)
    mass_t = sum((line.line_mass_t for line in req.lines), D0)
    res.goods_total = _round2(goods_total)
    res.mass_t = _round2(mass_t)

    if any(line.line_volume is None for line in req.lines):
        res.volume_known = False
        res.warnings.append("У части товаров нет объёма — общий объём заказа вычислить нельзя")
    else:
        res.volume_known = True
        res.volume_m3 = _round2(sum((line.line_volume for line in req.lines), D0))

    cargo_types = sorted({line.product.cargo_type for line in req.lines
                          if line.product.cargo_type})

    # ---------------- самовывоз
    if req.mode == "pickup":
        res.delivery_cost = D0
        res.delivery_cost_raw = D0
        res.total = _ruble_final(_round2(goods_total), data.rules.rounding if data.rules else "ruble")
        res.ok = True
        res.notes.append("Самовывоз: стоимость доставки не начисляется")
        return res

    # ---------------- доставка: расстояние
    rules = data.rules
    if rules is None:
        res.error = "Правила расчёта не загружены."
        return res

    straight = haversine_km(req.warehouse.lat, req.warehouse.lon,
                            req.destination.lat, req.destination.lon)
    res.distance_straight_km = _round2(straight)
    model = (_round2(straight * rules.distance_coefficient)
             * (Decimal(2) if rules.return_mode == "round" else Decimal(1)))
    res.distance_calc_km = _round2(model)
    res.distance_note = (f"Расстояние оценочное: по прямой {res.distance_straight_km} км "
                         f"× коэф. {rules.distance_coefficient} "
                         f"({rules.return_mode_label.lower()}). Это не фактический маршрут.")
    res.return_mode_label = rules.return_mode_label

    tariffs = data.tariffs
    if tariffs is None:
        res.error = "Тарифы не загружены."
        return res

    if res.distance_straight_km <= 0:
        res.error = ("Склад и пункт доставки совпадают или не заданы корректно; "
                     "расстояние нельзя принять нулевым.")
        return res

    # ---------------- стоимость рейса
    cost_trip = (tariffs.base_per_trip + tariffs.per_km * model).quantize(TWO)
    res.cost_per_trip = cost_trip

    # ---------------- подбор машин
    assignments, excluded, warnings = vs.select_vehicles(
        vehicles=data.vehicles,
        cargo_types=cargo_types,
        mass_t=mass_t,
        volume_m3=res.volume_m3 if res.volume_known else None,
        cost_per_trip=cost_trip,
        max_trips=tariffs.max_trips,
        compat=data.compatibility,
    )
    res.assignments = assignments
    res.excluded = excluded
    res.warnings.extend(warnings)

    if not assignments:
        res.ok = False
        res.error = ("Подходящий транспорт не назначен. Причины по каждой машине — "
                     "в детализации расчёта.")
        return res

    total_trips = sum(a.trips for a in assignments)
    delivery_raw = cost_trip * total_trips
    res.delivery_cost_raw = _round2(delivery_raw)

    # минимальный объём заказа
    if tariffs.min_volume is not None and res.volume_known \
            and res.volume_m3 < tariffs.min_volume:
        res.warnings.append(
            f"Объём заказа {res.volume_m3} м³ меньше минимального "
            f"{tariffs.min_volume} м³ — уточните условия отгрузки")

    # порог бесплатной доставки
    if tariffs.free_threshold is not None and goods_total >= tariffs.free_threshold:
        res.free_delivery_applied = True
        res.delivery_cost = D0
        res.notes.append(
            f"Бесплатная доставка: сумма заказа ≥ порога {tariffs.free_threshold} ₽")
    else:
        if delivery_raw < tariffs.min_delivery:
            res.min_tariff_applied = True
            res.notes.append(
                f"Применён минимальный тариф доставки {tariffs.min_delivery} ₽")
            delivery_raw = tariffs.min_delivery
        res.delivery_cost = _ruble_final(delivery_raw)

    res.total = _ruble_final(
        _round2(goods_total + (res.delivery_cost or D0))
    )
    res.ok = True
    return res


def _ruble_final(x: Decimal, rounding: str = "ruble") -> Decimal:
    """Финальные денежные значения — по правилам таблицы (округление вверх до рубля)."""
    if rounding == "ruble":
        return _ceil_ruble(x)
    return x.quantize(TWO, rounding=ROUND_HALF_UP)
