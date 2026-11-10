# -*- coding: utf-8 -*-
"""
Локальный сервер приложения. Слушает только 127.0.0.1, наружу ничего
не отправляется; никаких данных не уходит в интернет (кроме явной
команды «обновить прайс с Яндекс Диска» в настройках).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
import logging
import sys
import threading

from flask import Flask, jsonify, request, send_from_directory

from src.docgen.docx_generator import generate_order_sheet
from src.parser.reader import PriceValidationError
from src.server import service
from src.storage.history import HistoryStore

db_history = HistoryStore(service.exports_dir().parent / "history.sqlite")
price_service = service.PriceService()

app = Flask(__name__, static_folder=None)
# интерфейс никогда не кешируется браузером: правки UI и «Обновить» прайса
# всегда доезжают до окна без ручной перезагрузки с очисткой кеша
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


@app.after_request
def _no_cache_ui(resp):
    if not request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def _find_web_dir() -> Path:
    """Ищет папку интерфейса: разработка → src/web; сборка → ресурсы бандла."""
    here = Path(__file__).resolve()
    candidates = [
        here.parents[1] / "web",
        Path(getattr(sys, "_MEIPASS", "/")) / "web",
        Path(getattr(sys, "_MEIPASS", "/")) / ".." / "Resources" / "web",
        here.parent / "web",
    ]
    for c in candidates:
        if (c / "index.html").exists():
            return c
    return candidates[0]


WEB_DIR = _find_web_dir()


def _s(v: Any) -> str:
    """Decimal/None к строке: ноль — это '0', а не пустая строка."""
    return "" if v is None else str(v)


# ------------------------------------------------------------------ helpers

def _to_decimal(value: Any) -> Decimal | None:
    return service.to_decimal(value)


def _serialize_calc(snapshot: dict[str, Any]) -> dict[str, Any]:
    return snapshot


def _product_json(p) -> dict:
    return {
        "sku": p.sku, "name": p.name, "category": p.category,
        "unit": p.unit, "price": str(p.price), "mass_kg": str(p.mass_kg),
        "volume_m3": str(p.volume_m3) if p.volume_m3 is not None else None,
        "cargo_type": p.cargo_type, "active": p.active,
    }


def _vehicle_json(v) -> dict:
    return {
        "id": v.vid, "plate": v.plate, "model": v.model,
        "body_type": v.body_type, "capacity_t": str(v.capacity_t),
        "body_m3": str(v.body_m3),
        "fill_loose": str(v.fill_loose), "fill_unit": str(v.fill_unit),
        "online": v.online, "serviceable": v.serviceable,
        "online_raw": service.to_string(v.online_raw),
        "serviceable_raw": service.to_string(v.ok_raw),
    }


def _place_json(p) -> dict:
    return {
        "id": p.pid, "name": p.name, "lat": str(p.lat), "lon": str(p.lon),
        "address": p.address, "city": getattr(p, "city", ""),
    }


def _price_meta() -> dict:
    d = price_service.data
    return {
        "loaded": price_service.loaded,
        "loaded_at": price_service.last_loaded.isoformat(timespec="seconds")
            if price_service.last_loaded else None,
        "source_file": str(d.path) if d else None,
        "warnings": list(price_service.last_error or (d.warnings if d else [])),
        "counts": ({"products": len(d.products), "vehicles": len(d.vehicles),
                    "warehouses": len(d.warehouses),
                    "destinations": len(d.destinations)} if d else {}),
    }


# ---------------------------------------------------------------- API

@app.get("/api/state")
def api_state():
    try:
        d = price_service.ensure_loaded()
    except PriceValidationError as exc:
        return jsonify({"ok": False, "error": "; ".join(exc.messages),
                        "meta": _price_meta()}), 200
    compat = [{"cargo": c, "body": b, "allowed": ok}
              for (c, b), ok in d.compatibility.items()]
    return jsonify({
        "ok": True,
        "products": [_product_json(p) for p in d.products],
        "vehicles": [_vehicle_json(v) for v in d.vehicles],
        "warehouses": [_place_json(p) for p in d.warehouses],
        "destinations": [_place_json(p) for p in d.destinations],
        "tariffs": {
            "base_per_trip": str(d.tariffs.base_per_trip),
            "per_km": str(d.tariffs.per_km),
            "min_delivery": str(d.tariffs.min_delivery),
            "free_threshold": str(d.tariffs.free_threshold) if d.tariffs.free_threshold else None,
            "max_trips": d.tariffs.max_trips,
            "min_volume": str(d.tariffs.min_volume) if d.tariffs.min_volume else None,
        },
        "rules": {
            "distance_coefficient": str(d.rules.distance_coefficient),
            "return_mode": d.rules.return_mode,
            "return_mode_label": d.rules.return_mode_label,
        },
        "compatibility": compat,
        "meta": _price_meta(),
        "settings": {"yandex_disk_url": service.read_settings().get("yandex_disk_url", "")},
    })


@app.post("/api/reload")
def api_reload():
    ok, msg = price_service.reload()
    return jsonify({"ok": ok, "message": msg, "meta": _price_meta()})


@app.post("/api/calc")
def api_calc():
    from src.core.calculator import calculate
    from src.core.models import CalcRequest, OrderLine

    payload = request.get_json(silent=True) or {}
    try:
        data = price_service.ensure_loaded()
    except PriceValidationError as exc:
        return jsonify({"ok": False, "error": "; ".join(exc.messages)}), 200

    products = {p.sku: p for p in data.products}
    warehouses = {w.pid: w for w in data.warehouses}
    destinations = {p.pid: p for p in data.destinations}

    mode = payload.get("mode") if payload.get("mode") in ("delivery", "pickup") else "delivery"
    lines: list[OrderLine] = []
    for item in payload.get("lines", []):
        sku = str(item.get("sku", ""))
        product = products.get(sku)
        if product is None:
            return jsonify({"ok": False, "error": f"Товар {sku} не найден в прайсе."}), 200
        qty = _to_decimal(item.get("qty"))
        lines.append(OrderLine(product=product, qty=qty if qty else Decimal(0)))

    wh = warehouses.get(payload.get("warehouse")) if mode == "delivery" else None
    dest = destinations.get(payload.get("destination")) if mode == "delivery" else None
    req = CalcRequest(warehouse=wh, destination=dest, lines=lines, mode=mode)
    result = calculate(data, req)

    snapshot: dict[str, Any] = {
        "номер": payload.get("number") or "",
        "дата": payload.get("created_at") or "",
        "режим": mode,
        "склад": wh.name if wh else "",
        "пункт": dest.name if dest else "",
        "комментарий": payload.get("comment") or "",
        "позиций": len(lines),
        "позиции": [{
            "sku": l.product.sku, "name": l.product.name, "unit": l.product.unit,
            "qty": str(l.qty), "price": str(l.product.price),
            "sum": str(l.line_total),
        } for l in lines],
        "товары_сумма": _s(result.goods_total),
        "масса_т": _s(result.mass_t),
        "объём_м3": _s(result.volume_m3),
        "объём_известен": result.volume_known,
        "прямая_км": _s(result.distance_straight_km),
        "расчётная_км": _s(result.distance_calc_km),
        "режим_пути": result.return_mode_label or "",
        "примечание_расстояние": result.distance_note or "",
        "транспорт": [{
            "id": a.vehicle.vid, "model": a.vehicle.model,
            "plate": a.vehicle.plate, "body_type": a.vehicle.body_type,
            "trips": a.trips,
            "trips_by_mass": a.trips_by_mass,
            "trips_by_volume": a.trips_by_volume,
            "cost": str(a.cost),
        } for a in result.assignments],
        "исключены": [{"машина": label, "причина": reason}
                      for label, reason in result.excluded],
        "рейс_стоимость": _s(result.cost_per_trip),
        "мин_тариф": result.min_tariff_applied,
        "бесплатная_доставка": result.free_delivery_applied,
        "доставка": _s(result.delivery_cost),
        "итог": _s(result.total),
        "предупреждения": result.warnings,
        "примечания": result.notes,
        "данные_от": (price_service.last_loaded.isoformat(timespec="seconds")
                      if price_service.last_loaded else ""),
    }

    if result.ok:
        try:
            calc_id = db_history.save(snapshot)
            saved = db_history.get(calc_id)
            snapshot["номер"] = saved["number"] if saved else ""
        except Exception as exc:  # noqa: BLE001  история не должна ломать расчёт
            snapshot["ошибка_истории"] = str(exc)

    resp = {
        "ok": result.ok,
        "error": result.error or None,
        "snapshot": _serialize_calc(snapshot),
    }
    status = 200
    return jsonify(resp), status


@app.get("/api/history")
def api_history():
    return jsonify({"ok": True, "items": db_history.list()})


@app.get("/api/history/<int:calc_id>")
def api_history_item(calc_id: int):
    item = db_history.get(calc_id)
    if item is None:
        return jsonify({"ok": False, "error": "Расчёт не найден."}), 404
    return jsonify({"ok": True, "item": item})


@app.delete("/api/history/<int:calc_id>")
def api_history_delete(calc_id: int):
    return jsonify({"ok": db_history.delete(calc_id)})


@app.post("/api/export-docx")
def api_export_docx():
    payload = request.get_json(silent=True) or {}
    snapshot = payload.get("snapshot")
    if not snapshot:
        return jsonify({"ok": False, "error": "Нет расчёта для документа."}), 200
    out = service.exports_dir() / (
        f"Заказ-наряд {snapshot.get('номер', 'без-номера').replace(' ', '_')}.docx")
    try:
        path = generate_order_sheet(snapshot, out)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"Не удалось создать Word-документ: {exc}"}), 200
    return jsonify({"ok": True, "path": str(path), "name": out.name})


@app.get("/api/exports")
def api_exports():
    items = []
    for f in sorted(service.exports_dir().glob("*.docx"), reverse=True):
        import os
        items.append({
            "name": f.name,
            "size": f.stat().st_size,
            "mtime": datetime.fromtimestamp(f.stat().st_mtime).strftime("%d.%m.%Y %H:%M"),
        })
    return jsonify({"ok": True, "items": items})


@app.get("/api/exports/download")
def api_exports_download():
    name = request.args.get("name", "")
    safe = Path(name).name
    if not safe.endswith(".docx") or safe.startswith("."):
        return jsonify({"ok": False, "error": "Некорректное имя файла."}), 200
    path = service.exports_dir() / safe
    if not path.exists():
        return jsonify({"ok": False, "error": "Документ не найден."}), 404
    return send_from_directory(service.exports_dir(), safe, as_attachment=True)


@app.delete("/api/exports")
def api_exports_delete():
    payload = request.get_json(silent=True) or {}
    safe = Path(payload.get("name", "")).name
    if not safe.endswith(".docx"):
        return jsonify({"ok": False, "error": "Некорректное имя файла."}), 200
    path = service.exports_dir() / safe
    if path.exists():
        path.unlink()
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Документ не найден."}), 404


@app.post("/api/shutdown")
def api_shutdown():
    """Полное завершение приложения (актуально при LSUIElement — без Dock-иконки)."""
    def _quit() -> None:
        import os
        import signal
        import time as _t
        _t.sleep(0.3)
        logging.info("Получена команда завершения приложения")
        try:
            (service.exports_dir().parent / "server.json").unlink(missing_ok=True)
        except Exception:
            pass
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Thread(target=_quit, daemon=True).start()
    return jsonify({"ok": True})


@app.get("/api/archive")
def api_archive():
    return jsonify({"ok": True, "items": service.list_archive()})


@app.post("/api/archive/restore")
def api_archive_restore():
    name = (request.get_json(silent=True) or {}).get("name", "")
    ok, msg = service.restore_from_archive(name)
    if ok:
        ok2, msg2 = price_service.reload()
        msg = f"{msg} {msg2}"
    return jsonify({"ok": ok, "message": msg, "meta": _price_meta()})


@app.post("/api/add-product")
def api_add_product():
    item = (request.get_json(silent=True) or {})
    ok, msg = service.add_product(price_service, item)
    return jsonify({"ok": ok, "message": msg})


@app.post("/api/download-price")
def api_download_price():
    url = (request.get_json(silent=True) or {}).get("url", "")
    settings = service.read_settings()
    if (request.get_json(silent=True) or {}).get("save_url", True) and url:
        settings["yandex_disk_url"] = url
        service.write_settings(settings)
    ok, msg = service.download_price_from_yandex_disk(url)
    if ok:
        ok2, msg2 = price_service.reload()
        msg = f"{msg}. {msg2}"
    stuff = {"ok": ok, "message": msg, "meta": _price_meta()}
    return jsonify(stuff)


@app.post("/api/settings")
def api_settings():
    item = request.get_json(silent=True) or {}
    settings = service.read_settings()
    if "yandex_disk_url" in item:
        settings["yandex_disk_url"] = item.get("yandex_disk_url", "")
    service.write_settings(settings)
    return jsonify({"ok": True, "settings": settings})


# ---------------------------------------------------------------- static UI

@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/<path:path>")
def static_files(path: str):
    return send_from_directory(WEB_DIR, path)


def run_server(port: int = 8741) -> None:
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
