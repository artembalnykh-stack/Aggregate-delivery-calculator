# -*- coding: utf-8 -*-
"""Модели расчётного ядра (независимы от UI и БД)."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from src.parser.reader import Place, Product, Vehicle


@dataclass
class OrderLine:
    product: Product
    qty: Decimal

    @property
    def line_total(self) -> Decimal:
        return (self.product.price * self.qty).quantize(Decimal("0.01"))

    @property
    def line_mass_t(self) -> Decimal:
        return self.product.mass_kg * self.qty / Decimal(1000)

    @property
    def line_volume(self) -> Decimal | None:
        if self.product.volume_m3 is None:
            return None
        return self.product.volume_m3 * self.qty


@dataclass
class CalcRequest:
    warehouse: Place | None
    destination: Place | None
    lines: list[OrderLine]
    mode: str = "delivery"            # delivery | pickup


@dataclass
class Assignment:
    vehicle: Vehicle
    trips: int
    cost: Decimal                     # стоимость рейсов этой машины
    trips_by_mass: int
    trips_by_volume: int | None


@dataclass
class CalcResult:
    ok: bool
    error: str | None = None

    goods_total: Decimal | None = None
    mass_t: Decimal | None = None
    volume_m3: Decimal | None = None
    volume_known: bool = False

    mode: str = "delivery"
    warehouse: Place | None = None
    destination: Place | None = None

    distance_straight_km: Decimal | None = None
    distance_calc_km: Decimal | None = None
    distance_note: str | None = None
    return_mode_label: str | None = None

    assignments: list[Assignment] = field(default_factory=list)
    excluded: list[tuple[str, str]] = field(default_factory=list)  # (машина, причина)
    cost_per_trip: Decimal | None = None
    min_tariff_applied: bool = False
    free_delivery_applied: bool = False

    delivery_cost: Decimal | None = None
    delivery_cost_raw: Decimal | None = None
    total: Decimal | None = None

    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
