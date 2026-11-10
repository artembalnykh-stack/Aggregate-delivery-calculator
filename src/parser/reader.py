# -*- coding: utf-8 -*-
"""
Парсер внешнего прайса-файла Excel.

Программа сама находит листы и строку шапки, сопоставляет колонки по
названиям (толерантно к написанию), проверяет типы и статусы.
Файл применяется ЦЕЛИКОМ: при критических ошибках данные не применяются.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import load_workbook


# ------------------------------------------------------------------ модели

@dataclass
class Product:
    sku: str
    name: str
    category: str
    unit: str
    price: Decimal
    mass_kg: Decimal
    volume_m3: Decimal | None
    cargo_type: str
    active: bool


@dataclass
class Vehicle:
    vid: str
    plate: str
    model: str
    body_type: str
    capacity_t: Decimal
    body_m3: Decimal
    fill_loose: Decimal
    fill_unit: Decimal
    online_raw: Any
    ok_raw: Any
    # распознанные статусы; unknown → всегда исключается из подбора
    online: bool | None
    serviceable: bool | None


@dataclass
class Place:
    pid: str
    name: str
    lat: Decimal
    lon: Decimal
    address: str = ""
    city: str = ""


@dataclass
class Tariffs:
    base_per_trip: Decimal
    per_km: Decimal
    min_delivery: Decimal
    free_threshold: Decimal | None
    max_trips: int
    min_volume: Decimal | None


@dataclass
class Rules:
    distance_coefficient: Decimal
    return_mode: str                      # "round" | "oneway"
    return_mode_label: str
    rounding: str                         # "ruble"


@dataclass
class PriceData:
    path: Path
    parsed_at: datetime
    products: list[Product] = field(default_factory=list)
    vehicles: list[Vehicle] = field(default_factory=list)
    warehouses: list[Place] = field(default_factory=list)
    destinations: list[Place] = field(default_factory=list)
    tariffs: Tariffs | None = None
    rules: Rules | None = None
    compatibility: dict[tuple[str, str], bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def next_sku(self) -> str:
        """Очередной артикул для нового товара пользователя."""
        maxn = 0
        for p in self.products:
            if p.sku.startswith("NS") and p.sku[2:].isdigit():
                maxn = max(maxn, int(p.sku[2:]))
        return f"NS{maxn + 1:04d}"


# ------------------------------------------------------------- ошибки импорта

class PriceValidationError(Exception):
    """Файл не применён целиком; messages — понятные причины."""

    def __init__(self, messages: list[str]):
        self.messages = messages
        super().__init__(messages[0] if messages else "Ошибка загрузки данных")


# ------------------------------------------------------------------ утилиты

_NUM_SHEET_ALIASES: dict[str, list[str]] = {
    "products": ["товар", "прайс"],
    "vehicles": ["самосвал", "машин", "автопарк", "авто"],
    "warehouses": ["склад"],
    "destinations": ["пункт", "доставк"],   # «Пункты доставки»
    "tariffs": ["тариф"],
    "rules": ["правил"],
    "compatibility": ["совместим"],
}

# ключи шапки → канонические поля (по сущностям)
_FIELDS: dict[str, dict[str, list[str]]] = {
    "products": {
        "sku": ["артикул"],
        "name": ["наименование", "название", "товар"],
        "category": ["категория", "группа"],
        "unit": ["единица измерения", "ед", "единица"],
        "price": ["цена"],
        "mass": ["масса", "вес"],
        "volume": ["объём", "объем"],
        "cargo_type": ["тип груза", "груз"],
        "active": ["активен", "статус"],
    },
    "vehicles": {
        "vid": ["id", "код"],
        "plate": ["госномер", "номер"],
        "model": ["модель", "марка", "наименование", "название"],
        "body_type": ["тип кузова", "кузов", "тип"],
        "capacity": ["грузоподъемность", "грузоподъёмность"],
        "body_m3": ["объём кузова", "объем кузова", "объём", "объем"],
        "fill_loose": ["наполнения, навалочные", "наполнения навалочные",
                       "коэф. навал", "навалочные"],
        "fill_unit": ["наполнения, штучные", "наполнения штучные",
                      "коэф. штуч", "штучные"],
        "online": ["вышел на линию", "на линии"],
        "serviceable": ["исправна", "исправен"],
    },
    "warehouses": {
        "pid": ["id", "код"],
        "name": ["название", "наименование"],
        "city": ["город"],
        "address": ["адрес"],
        "lat": ["широта"],
        "lon": ["долгота"],
    },
    "destinations": {
        "pid": ["id", "код"],
        "name": ["название", "наименование", "пункт", "город"],
        "lat": ["широта"],
        "lon": ["долгота"],
    },
    "tariffs": {"key": ["параметр"], "value": ["значение"]},
    "rules": {"key": ["параметр"], "value": ["значение"]},
    "compatibility": {
        "cargo": ["тип груза"],
        "body": ["тип кузова"],
        "allowed": ["допустимо"],
    },
}

_STATUS_YES = "да"
_STATUS_NO = "нет"


def _norm(text: Any) -> str:
    if text is None:
        return ""
    s = str(text).lower().strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _norm_key(text: Any) -> str:
    """Нормализация для сопоставления колонок: без пробелов и пунктуации."""
    if text is None:
        return ""
    s = str(text).lower()
    s = re.sub(r"[^\w]+", "", s, flags=re.UNICODE)
    return s


def to_decimal(value: Any) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    s = str(value).replace(" ", "").replace("₽", "").replace("руб", "")
    s = s.replace(",", ".").strip()
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def to_bool(value: Any) -> bool | None:
    """Да/Нет/1/0; неизвестное → None (машина считается недоступной)."""
    if isinstance(value, bool):
        return value
    if value in (1, 0):
        return bool(value)
    s = _norm(value)
    if s in (_STATUS_YES, "да", "yes", "вышел", "1", "истина", "исправна", "исправен"):
        return True
    if s in (_STATUS_NO, "no", "нет", "не вышл", "0", "ложь"):
        return False
    return None


def _matches_header(header_value: Any, aliases: list[str]) -> bool:
    """header «Цена, руб» должен совпасть с ключом «цена» (толерантно)."""
    h = _norm_key(header_value)
    if not h:
        return False
    for alias in aliases:
        a = _norm_key(alias)
        if a and (a in h or h in a):
            return True
    return False


def _find_header(ws, field_aliases: dict[str, list[str]]) -> tuple[int, dict[str, int]] | None:
    """Ищет строку шапки: максимум совпавших ключевых колонок."""
    best_row, best_map, best_score = None, None, 0
    for row_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=15, max_col=30), 1):
        mapped: dict[str, int] = {}
        for cell in row:
            if cell.value is None:
                continue
            for f, aliases in field_aliases.items():
                if f in mapped:
                    continue
                if _matches_header(cell.value, aliases):
                    mapped[f] = cell.column
        if len(mapped) > best_score:
            best_row, best_map, best_score = row_idx, mapped, len(mapped)
    if best_score >= min(3, len(field_aliases)):
        return best_row, best_map
    return None


def _rows_iter(ws, header_row: int):
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in row):
            continue
        yield row


class _SheetParser:
    def __init__(self, entity: str, errors: list[str], warnings: list[str]):
        self.entity = entity
        self.errors = errors
        self.warnings = warnings

    def parse(self, wb, sheet_names: dict[str, str]) -> list[dict]:
        """Возвращает строки-словари канонических полей сущности."""
        sheet = sheet_names.get(self.entity)
        if sheet is None:
            self.errors.append(f"Не найден лист с данными сущности «{self.entity}»")
            return []
        ws = wb[sheet]
        fspec = _FIELDS[self.entity]
        found = _find_header(ws, fspec)
        if not found:
            self.errors.append(
                f"На листе «{sheet}» не найдена строка шапки с обязательными колонками")
            return []
        header_row, colmap = found
        missing = [f for f in fspec if f not in colmap]
        if missing:
            self.errors.append(
                f"На листе «{sheet}» нет обязательных колонок: "
                + ", ".join(missing))
            return []
        rows = []
        for r in _rows_iter(ws, header_row):
            item = {}
            for f, col in colmap.items():
                item[f] = r[col - 1] if col - 1 < len(r) else None
            if all(v is None for v in item.values()):
                continue
            rows.append(item)
        return rows


def _classify_sheets(wb) -> dict[str, str]:
    """Сопоставляет листы книги сущностям по названию листа."""
    result = {}
    for entity, aliases in _NUM_SHEET_ALIASES.items():
        for ws in wb.worksheets:
            t = _norm(ws.title)
            if any(a in t for a in aliases) and entity not in result:
                result[entity] = ws.title
                break
    return result


TARIFF_KEYS = {
    "base_per_trip": "базовый тариф за рейс",
    "per_km": "тариф за километр",
    "min_delivery": "минимальная стоимость доставки",
    "free_threshold": "порог бесплатной доставки",
    "max_trips": "макс. рейсов на машину",
    "min_volume": "минимальный объём доставки",
}

RULE_KEYS = {
    "distance_coefficient": "коэффициент расстояния",
    "return_mode": "режим обратного пути",
    "rounding": "округление стоимости",
}


def _parse_kv(wb, sheet: str, keys: dict[str, str]) -> dict[str, Any]:
    ws = wb[sheet]
    found = _find_header(ws, {"key": ["параметр"], "value": ["значение"]})
    out: dict[str, Any] = {}
    if not found:
        return out
    _, colmap = found
    for r in _rows_iter(ws, found[0]):
        key_raw = r[colmap.get("key", 1) - 1] if colmap.get("key", 1) - 1 < len(r) else None
        val_raw = r[colmap.get("value", 2) - 1] if colmap.get("value", 2) - 1 < len(r) else None
        k = _norm(key_raw)
        for field_name, keyphrase in keys.items():
            kp = _norm_key(keyphrase)
            if kp and kp in _norm_key(k):
                out[field_name] = val_raw
    return out


def load_price(path: Path) -> PriceData:
    """Загружает и валидирует прайс; при ошибке — PriceValidationError."""
    errors: list[str] = []
    warnings: list[str] = []
    if not Path(path).exists():
        raise PriceValidationError([f"Файл прайса не найден: {path}"])
    try:
        wb = load_workbook(path, data_only=True, read_only=True)
    except Exception as exc:  # noqa: BLE001
        raise PriceValidationError([f"Не удалось открыть Excel-файл: {exc}"]) from exc

    sheets = _classify_sheets(wb)
    for req in ("products", "vehicles", "warehouses", "destinations",
                "tariffs", "rules", "compatibility"):
        if req not in sheets:
            errors.append(f"В файле не найден лист с данными «{req}».")

    if errors:
        raise PriceValidationError(errors)

    products: list[Product] = []
    seen_sku: set[str] = set()
    ps = _SheetParser("products", errors, warnings).parse(wb, sheets)
    for i, row in enumerate(ps, 2):
        sku = str(row.get("sku") or "").strip()
        name = str(row.get("name") or "").strip()
        price = to_decimal(row.get("price"))
        mass = to_decimal(row.get("mass"))
        vol = to_decimal(row.get("volume"))
        active = to_bool(row.get("active"))
        if not sku or not name:
            warnings.append(f"Товары, строка {i}: пропущена — нет артикула или названия")
            continue
        if price is None or price < 0:
            warnings.append(f"Товары, строка {i} ({sku}): некорректная цена — товар пропущен")
            continue
        if mass is None or mass <= 0:
            warnings.append(f"Товары, строка {i} ({sku}): некорректная масса — товар пропущен")
            continue
        if sku in seen_sku:
            warnings.append(f"Товары, строка {i}: дублируется артикул {sku}")
            continue
        seen_sku.add(sku)
        products.append(Product(
            sku=sku,
            name=name,
            category=str(row.get("category") or "").strip(),
            unit=str(row.get("unit") or "шт").strip(),
            price=price,
            mass_kg=mass,
            volume_m3=vol,
            cargo_type=str(row.get("cargo_type") or "Штучный").strip(),
            active=True if active is None else active,
        ))

    vehicles: list[Vehicle] = []
    vs = _SheetParser("vehicles", errors, warnings).parse(wb, sheets)
    for i, row in enumerate(vs, 2):
        vid = str(row.get("vid") or "").strip() or f"V{i:03d}"
        cap = to_decimal(row.get("capacity"))
        bm3 = to_decimal(row.get("body_m3"))
        model = str(row.get("model") or "").strip() or vid
        body_type = str(row.get("body_type") or "").strip()
        online = to_bool(row.get("online"))
        serviceable = to_bool(row.get("serviceable"))
        if not body_type:
            warnings.append(f"Самосвалы, строка {i}: не указан тип кузова — машина исключена")
        if online is None:
            warnings.append(
                f"Самосвалы, строка {i} ({model}): статус «Вышел на линию» не распознан — машина исключена")
        if serviceable is None:
            warnings.append(
                f"Самосвалы, строка {i} ({model}): статус «Исправна» не распознан — машина исключена")
        if cap is None or cap <= 0 or bm3 is None or bm3 <= 0:
            warnings.append(f"Самосвалы, строка {i} ({model}): некорректные характеристики — машина исключена")
            continue
        vehicles.append(Vehicle(
            vid=vid,
            plate=str(row.get("plate") or "").strip(),
            model=model,
            body_type=body_type,
            capacity_t=cap,
            body_m3=bm3,
            fill_loose=to_decimal(row.get("fill_loose")) or Decimal("1"),
            fill_unit=to_decimal(row.get("fill_unit")) or Decimal("1"),
            online_raw=row.get("online"),
            ok_raw=row.get("serviceable"),
            online=online,
            serviceable=serviceable,
        ))

    def _parse_places(entity: str) -> list[Place]:
        out: list[Place] = []
        sp = _SheetParser(entity, errors, warnings).parse(wb, sheets)
        for i, row in enumerate(sp, 2):
            pid = str(row.get("pid") or f"X{i}").strip()
            name = str(row.get("name") or "").strip()
            lat = to_decimal(row.get("lat"))
            lon = to_decimal(row.get("lon"))
            if not name:
                warnings.append(f"{entity}, строка {i}: нет названия — пропущено")
                continue
            if lat is None or lon is None or abs(float(lat)) > 90 or abs(float(lon)) > 180:
                errors.append(
                    f"«{'Склады' if entity == 'warehouses' else 'Пункты доставки'}», строка {i}: "
                    f"некорректные координаты у «{name}»")
                continue
            city_field = row.get("city")
            out.append(Place(pid=pid, name=name, lat=lat, lon=lon,
                             address=str(row.get("address") or "").strip(),
                             city=str(city_field or "").strip()))
        return out

    warehouses = _parse_places("warehouses")
    destinations = _parse_places("destinations")

    tv = _parse_kv(wb, sheets["tariffs"], TARIFF_KEYS)
    rv = _parse_kv(wb, sheets["rules"], RULE_KEYS)

    base = to_decimal(tv.get("base_per_trip"))
    per_km = to_decimal(tv.get("per_km"))
    min_delivery = to_decimal(tv.get("min_delivery"))
    free_th = to_decimal(tv.get("free_threshold"))
    max_trips = to_decimal(tv.get("max_trips"))
    min_volume = to_decimal(tv.get("min_volume"))
    if base is None or base < 0:
        errors.append("Тарифы: не найден или некорректен «Базовый тариф за рейс»")
    if per_km is None or per_km < 0:
        errors.append("Тарифы: не найден или некорректен «Тариф за километр»")
    if min_delivery is None or min_delivery < 0:
        errors.append("Тарифы: не найдена или некорректна «Минимальная стоимость доставки»")
    max_trips_i = int(max_trips) if max_trips else 3
    if max_trips_i < 1:
        max_trips_i = 3

    mode_raw = _norm(rv.get("return_mode"))
    if "обратно" in mode_raw or "туда и" in mode_raw:
        return_mode, mode_label = "round", "Туда и обратно"
    elif mode_raw in ("только туда", "туда", "один конец"):
        return_mode, mode_label = "oneway", "Только туда"
    else:
        return_mode, mode_label = "round", "Туда и обратно (по умолчанию)"
        warnings.append("Правила: «Режим обратного пути» не распознан — применён «Туда и обратно»")

    coef = to_decimal(rv.get("distance_coefficient")) or Decimal("1.3")
    if coef <= 0:
        errors.append("Правила: коэффициент расстояния должен быть положительным")

    compatibility: dict[tuple[str, str], bool] = {}
    cs = _SheetParser("compatibility", errors, warnings).parse(wb, sheets)
    for row in cs:
        cargo = str(row.get("cargo") or "").strip()
        body = str(row.get("body") or "").strip()
        allowed = to_bool(row.get("allowed"))
        if cargo and body and allowed is not None:
            compatibility[(cargo.lower(), body.lower())] = allowed

    if errors:
        raise PriceValidationError(errors)

    data = PriceData(
        path=Path(path),
        parsed_at=datetime.now(),
        products=products,
        vehicles=vehicles,
        warehouses=warehouses,
        destinations=destinations,
        tariffs=Tariffs(
            base_per_trip=base,
            per_km=per_km,
            min_delivery=min_delivery,
            free_threshold=free_th if (free_th is not None and free_th > 0) else None,
            max_trips=max_trips_i,
            min_volume=min_volume if (min_volume is not None and min_volume > 0) else None,
        ),
        rules=Rules(
            distance_coefficient=coef,
            return_mode=return_mode,
            return_mode_label=mode_label,
            rounding="ruble",
        ),
        compatibility=compatibility,
        warnings=warnings,
    )
    wb.close()
    return data
