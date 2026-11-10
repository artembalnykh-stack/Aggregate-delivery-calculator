# -*- coding: utf-8 -*-
"""
Сервисный слой: поиск папки данных, загрузка/перезагрузка прайса,
борьба бэкапов, добавление товара в Excel, скачивание прайса с Яндекс Диска.

Все данные хранятся локально в папке data/ рядом с программой.
"""
from __future__ import annotations

import logging
import shutil
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from src.parser.reader import (
    PriceData,
    PriceValidationError,
    load_price,
    to_decimal,
)


def app_dir() -> Path:
    """Папка, рядом с которой лежит data/.

    В разработке — корень проекта. В собранном PyInstaller-приложении —
    папка, СОДЕРЖАЩАЯ .app (данные отдельно от сборки, как требует ТЗ):
    .../КалькуляторДоставки.app/Contents/MacOS/main → .../ — та самая.
    """
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        # ищем вверх папку с .app или обычную папку с exe
        for parent in [exe.parent, *exe.parents]:
            if parent.suffix == ".app":
                return parent.parent
            if (parent / "data").exists():
                return parent
        return exe.parent
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    d = app_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def price_path() -> Path:
    return data_dir() / "прайс.xlsx"


def exports_dir() -> Path:
    p = data_dir() / "exports"
    p.mkdir(parents=True, exist_ok=True)
    return p


def settings_path() -> Path:
    return data_dir() / "settings.json"


class PriceService:
    """Держит загруженный прайс в памяти; reload() возвращает статус."""

    def __init__(self) -> None:
        self.data: PriceData | None = None
        self.last_error: list[str] = []
        self.last_loaded: datetime | None = None

    @property
    def loaded(self) -> bool:
        return self.data is not None

    def reload(self) -> tuple[bool, str]:
        try:
            self.data = load_price(price_path())
            self.last_error = []
            self.last_loaded = datetime.now()
            logging.getLogger(__name__).info(
                "reload: файл=%s, товаров=%d, машин=%d",
                self.data.path, len(self.data.products), len(self.data.vehicles))
            warn = f" Предупреждений: {len(self.data.warnings)}." if self.data.warnings else ""
            return True, (f"Прайс загружен: {len(self.data.products)} товаров, "
                          f"{len(self.data.vehicles)} машин.{warn}")
        except PriceValidationError as exc:
            # прошлые данные остаются в памяти, чтобы приложение работало
            self.last_error = exc.messages
            return False, "Файл прайса не применён: " + " ".join(exc.messages)

    def ensure_loaded(self) -> PriceData:
        if self.data is None:
            ok, msg = self.reload()
            if not ok or self.data is None:
                raise PriceValidationError([msg])
        return self.data


def to_string(value: Any) -> str:
    """Значение Excel-ячейки к строке для отображения."""
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value).strip()


def _https_opener():
    """Opener с корректным хранилищем сертификатов (macOS/бандл PyInstaller)."""
    import json
    import ssl
    import urllib.request
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 запасной вариант — системные корни
        ctx = ssl.create_default_context()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ctx))


def read_settings() -> dict:
    import json
    p = settings_path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        # ссылка Яндекс Диска «из коробки»
        for k, v in DEFAULT_SETTINGS.items():
            data.setdefault(k, v)
        return data
    write_settings(dict(DEFAULT_SETTINGS))
    return dict(DEFAULT_SETTINGS)


def write_settings(settings: dict) -> None:
    import json
    settings_path().write_text(json.dumps(settings, ensure_ascii=False, indent=2),
                               encoding="utf-8")


# ------------------------------------------------------------ добавление товара

def add_product(service: PriceService, item: dict) -> tuple[bool, str]:
    """Дописывает товар в конец листа «Товары» внешнего прайса.

    Прайс остаётся единым источником данных; перед записью создаётся
    резервная копия data/прайс.backup.xlsx.
    """
    from openpyxl import load_workbook

    errors: list[str] = []
    name = (item.get("name") or "").strip()
    if not name:
        errors.append("Не заполнено «Наименование».")
    unit = (item.get("unit") or "").strip() or "шт"
    price = to_decimal(item.get("price"))
    if price is None or price < 0:
        errors.append("Некорректная цена.")
    mass = to_decimal(item.get("mass_kg"))
    if mass is None or mass <= 0:
        errors.append("Некорректная масса единицы.")
    cargo = (item.get("cargo_type") or "Штучный").strip()
    vol = to_decimal(item.get("volume_m3"))
    category = (item.get("category") or "").strip() or "Прочие"
    if errors:
        return False, " ".join(errors)

    path = price_path()
    if service.loaded and service.data is not None:
        sku = service.data.next_sku()
    else:
        sku = f"NS{9999:04d}"

    backup = data_dir() / "прайс.backup.xlsx"
    if path.exists():
        shutil.copy2(path, backup)

    wb = load_workbook(path)
    sheet = None
    for ws in wb.worksheets:
        if "товар" in ws.title.lower() or "прайс" in ws.title.lower():
            sheet = ws
            break
    if sheet is None:
        return False, "В прайсе не найден лист «Товары»."
    # строка шапки: ищем «Артикул»
    header_row = None
    art_col = None
    for row_idx, row in enumerate(sheet.iter_rows(max_row=15), 1):
        for cell in row:
            if cell.value and "артикул" in str(cell.value).lower():
                header_row = row_idx
                art_col = cell.column
                break
        if header_row:
            break
    if not header_row:
        return False, "Не найдена строка шапки листа «Товары»."
    headers = { (cell.value or "").strip().lower(): cell.column
                for cell in sheet[header_row] if cell.value }
    row_num = sheet.max_row + 1
    values = {
        "артикул": sku, "наименование": name, "категория": category,
        "единица измерения": unit, "цена, руб": price,
        "масса единицы, кг": mass, "объём единицы, м³": vol if vol else None,
        "тип груза": cargo, "активен": "Да",
        "дата цены": datetime.now().date(),
    }
    matched = 0
    for key, col in headers.items():
        for val_key, val in values.items():
            if val_key in key or key in val_key:
                sheet.cell(row=row_num, column=col, value=val)
                matched += 1
                break
    if matched == 0:
        return False, "Не удалось сопоставить колонки листа «Товары»."
    wb.save(path)
    ok, msg = service.reload()
    return ok, (f"Товар добавлен (артикул {sku}). {msg}" if ok else msg)


# ------------------------------------------------------------- Яндекс Диск

DEFAULT_SETTINGS = {
    # Ссылка на Яндекс Диск для обновления прайса — не публикуется в репозитории.
    # Менеджер вводит свою ссылку в разделе «Настройки» при первом запуске.
    "yandex_disk_url": "",
}

ARCHIVE_LIMIT = 10
def archive_dir() -> Path:
    p = data_dir() / "archive"
    p.mkdir(parents=True, exist_ok=True)
    return p


def archive_price() -> Path:
    """Сохраняет текущий прайс в архив (до 10 последних копий)."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = archive_dir() / f"прайс_{stamp}.xlsx"
    n = 1
    while dst.exists():                     # защита от коллизий в ту же секунду
        dst = archive_dir() / f"прайс_{stamp}_{n}.xlsx"
        n += 1
    if price_path().exists():
        shutil.copy2(price_path(), dst)
        files = sorted(archive_dir().glob("прайс_*.xlsx"), reverse=True)
        for old in files[ARCHIVE_LIMIT:]:
            old.unlink(missing_ok=True)
    return dst


def download_price_from_yandex_disk(public_url: str) -> tuple[bool, str]:
    """Кнопка «Обновить»: скачивает прайс по публичной ссылке Яндекс Диска
    и ЗАМЕНЯЕТ локальную базу. Текущий прайс перед заменой уходит в архив
    (хранятся 10 последних копий). Скачанный файл сначала проверяется
    парсером целиком — испорченный не применится никогда.

    Используется документированный публичный API Диска (без токенов).
    """
    import json
    import urllib.parse
    import urllib.request

    if not public_url.strip():
        return False, "Не указана публичная ссылка на прайс."
    opener = _https_opener()
    qkey = urllib.parse.quote(public_url.strip(), safe="")
    # метаданные файла на Диске: имя, дата, размер — попадут в тост,
    # чтобы было видно, какая именно версия скачалась
    yname = ydate = ""
    ysize = 0
    try:
        with opener.open("https://cloud-api.yandex.net/v1/disk/public/resources?public_key="
                         + qkey, timeout=20) as resp:
            meta = json.loads(resp.read().decode("utf-8"))
        yname = meta.get("name") or ""
        ysize = meta.get("size") or 0
        ydate = meta.get("modified") or ""
    except Exception:  # noqa: BLE001 мета-информация не критична для обновления
        pass
    api = ("https://cloud-api.yandex.net/v1/disk/public/resources/download"
           "?public_key=" + qkey)
    try:
        with opener.open(api, timeout=20) as resp:
            href = json.loads(resp.read().decode("utf-8")).get("href")
    except Exception as exc:  # noqa: BLE001
        return False, (f"Не удалось получить ссылку скачивания у Яндекс Диска: {exc}. "
                       "Проверьте публичную ссылку и подключение к сети.")
    if not href:
        return False, "Яндекс Диск не вернул прямую ссылку на скачивание."
    tmp = data_dir() / "прайс.tmp.xlsx"
    try:
        with opener.open(href, timeout=120) as resp, open(tmp, "wb") as fh:
            fh.write(resp.read())
    except Exception as exc:  # noqa: BLE001
        return False, f"Ошибка скачивания файла прайса: {exc}"
    # проверим валидность ДО замены
    try:
        load_price(tmp)
    except PriceValidationError as exc:
        tmp.unlink(missing_ok=True)
        return False, ("Скачанный файл не прошёл проверку и не применён, "
                       "текущий прайс не тронут: " + " ".join(exc.messages))
    archived = archive_price()
    shutil.move(tmp, price_path())
    import logging as _logging
    _logging.getLogger(__name__).info(
        "Обновление с Яндекс Диска: файл=%s (%d КБ), дата=%s → %s",
        yname, ysize // 1024, ydate, price_path())
    file_info = ""
    if yname:
        when = ydate
        if ydate:
            try:
                from datetime import timezone
                when = datetime.fromisoformat(ydate.replace("Z", "+00:00")).astimezone() \
                    .strftime("%d.%m.%Y %H:%M")
            except Exception:  # noqa: BLE001
                pass
        file_info = f" (файл «{yname}» от {when})"
    msg = (f"Прайс обновлён с Яндекс Диска{file_info}. "
           f"Прежняя база в архиве: {archived.name}")
    return True, msg


def list_archive() -> list[dict]:
    files = sorted(archive_dir().glob("прайс_*.xlsx"), reverse=True)
    return [{"name": f.name,
             "mtime": datetime.fromtimestamp(f.stat().st_mtime).strftime("%d.%m.%Y %H:%M")}
            for f in files[:ARCHIVE_LIMIT]]


def restore_from_archive(name: str) -> tuple[bool, str]:
    safe = Path(name).name
    src = archive_dir() / safe
    if not src.exists():
        return False, f"Файл архива не найден: {safe}"
    archive_price()                       # текущий тоже в архив, ничего не теряется
    shutil.copy2(src, price_path())
    return True, f"Прайс восстановлен из {safe}."
