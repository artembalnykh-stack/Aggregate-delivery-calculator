# -*- coding: utf-8 -*-
"""
Автоматический подбор самосвалов: оптимальное сочетание машин под заказ.

Правила:
- кандидат допускается, только если оба статуса распознаны и равны «Да»
  («Вышел на линию» и «Исправна») и тип кузова совместим со всеми типами
  грузов заказа;
- рейсы машины = максимум из проверок по весу и по объёму (объём кузова ×
  коэф. наполнения для типа груза);
- у всех машин одинаковая стоимость рейса (общие тарифы), поэтому оптимум
  по стоимости = минимальное число рейсов; при равенстве — машина
  с меньшей грузоподъёмностью (минимум избыточности);
- если даже лучшей машине нужно больше рейсов, чем лимит смены, остаток
  груза хватается следующими машинами (жадная эвристика MVP, изменение
  критерия оптимума задаётся в таблице правил будущего развития).
"""
from __future__ import annotations

from decimal import Decimal

from src.core.models import Assignment
from src.parser.reader import Vehicle


def fill_coefficient(vehicle: Vehicle, cargo_type: str) -> Decimal:
    """Коэф. наполнения кузова для типа груза конкретной машины."""
    c = cargo_type.lower()
    if "штуч" in c:
        return vehicle.fill_unit
    return vehicle.fill_loose   # навалочные и миксерные (жидкий бетон)


def _ceil(x: Decimal) -> int:
    q, r = divmod(x, Decimal(1))
    return int(q) + (1 if r > 0 else 0)


def trips_for(vehicle: Vehicle, mass_t: Decimal, volume_m3: Decimal | None,
              cargo_type: str) -> tuple[int, int, int | None]:
    """(рейсы, по весу, по объёму). volume None → проверка по объёму чистится."""
    by_mass = _ceil(mass_t / vehicle.capacity_t) if mass_t > 0 else 0
    by_vol: int | None = None
    if volume_m3 is not None:
        cap_vol = vehicle.body_m3 * fill_coefficient(vehicle, cargo_type)
        by_vol = max(1, _ceil(volume_m3 / cap_vol)) if volume_m3 > 0 else 0
    return max(by_mass, by_vol or 0), by_mass, by_vol


def is_compatible(vehicle: Vehicle, cargo_types: set[str],
                  compat: dict[tuple[str, str], bool]) -> bool:
    for cargo in cargo_types:
        key = (cargo.lower(), vehicle.body_type.lower())
        if key not in compat or not compat[key]:
            return False
    return True


def select_vehicles(
    vehicles: list[Vehicle],
    cargo_types: list[str],
    mass_t: Decimal,
    volume_m3: Decimal | None,
    cost_per_trip: Decimal,
    max_trips: int,
    compat: dict[tuple[str, str], bool],
) -> tuple[list[Assignment], list[tuple[str, str]], list[str]]:
    """(назначения, исключения, предупреждения). cargo_types — уникальные типы грузов заказа.

    Если заказ состоит из грузов разных типов, для объёмной проверки
    берётся консервативный (минимальный) коэф. наполнения, а масса просто
    делится по рейсам каждой машины.
    """
    excluded: list[tuple[str, str]] = []
    warnings: list[str] = []
    cargo = list(cargo_types)

    for v in vehicles:
        label = f"{v.vid} {v.model}".strip()
        if v.online is None:
            excluded.append((label, "статус «Вышел на линию» не распознан — машина исключена"))
        elif v.online is not True:
            excluded.append((label, "не вышел на линию"))
            continue
        if v.serviceable is None:
            excluded.append((label, "статус «Исправна» не распознан — машина исключена"))
        elif v.serviceable is not True:
            excluded.append((label, "неисправна"))
            continue
        if not is_compatible(v, set(cargo), compat):
            types = ", ".join(cargo)
            excluded.append((label, f"кузов «{v.body_type}» не подходит для груза: {types}"))

    candidates: list[Vehicle] = []
    for v in vehicles:
        if v.online is True and v.serviceable is True and is_compatible(v, set(cargo), compat):
            candidates.append(v)

    if not candidates:
        return [], excluded, warnings

    # консервативный коэф. наполнения для смешанного заказа
    def worst_fill(v: Vehicle) -> Decimal:
        return min(fill_coefficient(v, c) for c in cargo)

    # (машина, рейсы_ solo, коэф)
    scored: list[tuple[Vehicle, int, Decimal]] = []
    manual_volume_check = False
    for v in candidates:
        if volume_m3 is None:
            # объём заказа неизвестен: только весовая проверка
            by_mass = _ceil(mass_t / v.capacity_t) if mass_t > 0 else 0
            scored.append((v, by_mass, Decimal("1")))
            manual_volume_check = True
            continue
        by_mass = _ceil(mass_t / v.capacity_t) if mass_t > 0 else 0
        by_vol = max(1, _ceil(volume_m3 / (v.body_m3 * worst_fill(v)))) if volume_m3 > 0 else 0
        scored.append((v, max(by_mass, by_vol), worst_fill(v)))

    if manual_volume_check:
        warnings.append("В partire товаров нет объёма — объёмная проверка недоступна, "
                        "проверка выполнена только по весу")

    scored.sort(key=lambda s: (s[1], s[0].capacity_t))
    best, best_trips, _ = scored[0]

    if best_trips <= max_trips:
        assignments = [Assignment(
            vehicle=best,
            trips=best_trips,
            cost=(cost_per_trip * best_trips).quantize(Decimal("0.01")),
            trips_by_mass=_ceil(mass_t / best.capacity_t) if mass_t > 0 else 0,
            trips_by_volume=(None if volume_m3 is None else
                             max(1, _ceil(volume_m3 / (best.body_m3 * worst_fill(best))))
                             if volume_m3 > 0 else 0),
        )]
        return assignments, excluded, warnings

    # Комбинация: остаток груза забирается следующими по вместительности машинами
    assignments: list[Assignment] = []
    rem_mass, rem_vol = mass_t, volume_m3 if volume_m3 is not None else Decimal(0)
    scored_greedy = sorted(scored, key=lambda s: (-s[0].capacity_t, s[1]))
    for v, _solo, fill in scored_greedy:
        if rem_mass <= 0 and rem_vol <= 0:
            break
        trips_i = 0
        if rem_mass > 0:
            trips_i = max(trips_i, _ceil(rem_mass / v.capacity_t))
        if volume_m3 is not None and rem_vol > 0:
            trips_i = max(trips_i, _ceil(rem_vol / (v.body_m3 * fill)))
        trips_i = min(trips_i, max_trips)
        if trips_i <= 0:
            continue
        carried_m = (v.capacity_t * trips_i).min(rem_mass) if rem_mass > 0 else rem_mass
        carried_v = (v.body_m3 * fill * trips_i).min(rem_vol) if volume_m3 is not None else rem_vol
        assignments.append(Assignment(
            vehicle=v,
            trips=trips_i,
            cost=(cost_per_trip * trips_i).quantize(Decimal("0.01")),
            trips_by_mass=_ceil(mass_t / v.capacity_t) if mass_t > 0 else 0,
            trips_by_volume=(None if volume_m3 is None else
                             max(1, _ceil(volume_m3 / (v.body_m3 * fill))) if volume_m3 > 0 else 0),
        ))
        rem_mass -= carried_m
        if volume_m3 is not None:
            rem_vol -= carried_v
        if len(assignments) == 1 and trips_i < _ceil(mass_t / v.capacity_t):
            warnings.append(f"Для {v.vid} {v.model} учтён лимит рейсов за смену ({max_trips})")
    if rem_mass > 0 or rem_vol > 0:
        # в демо-наборе 20-30 машин такого не встречается, но правило честное
        warnings.append("Доступных машин недостаточно, чтобы перевезти заказ за смену")

    return assignments, excluded, warnings
