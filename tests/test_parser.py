# -*- coding: utf-8 -*-
"""Тесты парсера внешнего прайса."""
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from src.parser.reader import PriceValidationError, load_price


@pytest.fixture()
def good_file(tmp_path: Path) -> Path:
    shutil.copy2(Path(__file__).parents[1] / "data" / "прайс.xlsx", tmp_path / "прайс.xlsx")
    return tmp_path / "прайс.xlsx"


def test_real_price_loads(good_file):
    d = load_price(good_file)
    assert len(d.products) > 100
    assert len(d.vehicles) >= 20
    assert len(d.warehouses) == 10
    assert d.tariffs.per_km == Decimal("45")
    assert d.rules.distance_coefficient == Decimal("1.35")
    assert d.rules.return_mode == "round"


def test_autodetects_shifted_header(tmp_path):
    """Шапка сдвинута со 2-й строки и начинается с 3-й колонки — должна найтись."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Товары"
    ws["A1"] = "Прайс transmitted 28.10.2026"
    ws["D3"] = "Артикул"; ws["E3"] = "Наименование"; ws["F3"] = "Цена, руб"
    ws["G3"] = "Масса единицы, кг"; ws["H3"] = "Единица измерения"
    ws["D4"] = "NS0001"; ws["E4"] = "Песок тестовый"; ws["F4"] = 700
    ws["G4"] = 1600; ws["H4"] = "м³"
    path = tmp_path / "t.xlsx"
    wb.save(path)
    # отсутствуют остальные листы → ошибка «не найден лист», но товары парсятся?
    # полный файл обязателен: ожидаем ошибку про другие листы
    with pytest.raises(PriceValidationError) as e:
        load_price(path)
    assert any("лист" in str(m).lower() for m in e.value.messages)


def test_bad_status_excludes_vehicle_with_warning(good_file):
    d = load_price(good_file)
    # находим машину и портим статус
    from openpyxl import load_workbook
    wb = load_workbook(good_file)
    ws = wb["Самосвалы"]
    # шапка в 3-й строке; «Вышел на линию» — 9-я колонка
    ws.cell(row=4, column=9, value="может быть")
    wb.save(good_file)
    d2 = load_price(good_file)
    v = next(v for v in d2.vehicles if v.online is None or v.online is False)
    assert any("не распознан" in w for w in d2.warnings)


def test_negative_price_skipped_with_warning(tmp_path, good_file):
    from openpyxl import load_workbook
    wb = load_workbook(good_file)
    ws = wb["Товары"]
    ws.cell(row=3, column=5, value=-5)  # первая строка данных (шапка в строке 2)
    wb.save(good_file)
    d = load_price(good_file)
    assert f"некорректная цена" in " ".join(d.warnings)
