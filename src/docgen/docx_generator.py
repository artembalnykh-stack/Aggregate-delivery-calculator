# -*- coding: utf-8 -*-
"""
Заказ-наряд в формате Word (.docx). Таблицы позиций, транспорта и итоги,
предупреждения о расчётном расстоянии. Полностью локальная генерация.
"""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


BRAND = RGBColor(0x1F, 0x4E, 0x79)
GREY = RGBColor(0x60, 0x60, 0x60)


def _style_table(table, widths: list[float] | None = None) -> None:
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    if not widths:
        return
    for row in table.rows:
        for cell, width in zip(row.cells, widths):
            cell.width = Cm(width)


def _header_row_fill(table) -> None:
    for cell in table.rows[0].cells:
        tcPr = cell._tc.get_or_add_tcPr()
        shd = tcPr.makeelement(qn("w:shd"), {qn("w:val"): "clear",
                                             qn("w:fill"): "1F4E79"})
        tcPr.append(shd)
        for p in cell.paragraphs:
            for run in p.runs:
                run.font.bold = True
                run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
                run.font.size = Pt(10)


def generate_order_sheet(snapshot: dict, out_path: Path) -> Path:
    """snapshot — структура, вернувшаяся из /api/calc (+ номер, дата)."""
    doc = Document()
    for section in doc.sections:
        section.left_margin = Cm(1.8)
        section.right_margin = Cm(1.8)
        section.top_margin = Cm(1.6)
        section.bottom_margin = Cm(1.6)

    # ----------- шапка документа
    title = doc.add_paragraph()
    run = title.add_run("НЕЙРОСТРОЙТРАНС")
    run.bold = True
    run.font.size = Pt(20)
    run.font.color.rgb = BRAND
    sub = doc.add_paragraph("Расчёт стоимости заказа с доставкой (демо-документ)")
    sub.runs[0].font.size = Pt(10)
    sub.runs[0].font.color.rgb = GREY

    head = doc.add_paragraph()
    head_run = head.add_run(
        f"Заказ-наряд № {snapshot.get('номер', '—')}  от {snapshot.get('дата', '—')}")
    head_run.bold = True
    head_run.font.size = Pt(14)

    mode_label = "Доставка" if snapshot.get("режим") == "delivery" else "Самовывоз"
    info = doc.add_paragraph()
    info.add_run(f"Способ получения: {mode_label}\n").bold = True
    if snapshot.get("режим") == "delivery":
        info.add_run(
            f"Склад отгрузки: {snapshot.get('склад', '—')}\n"
            f"Пункт доставки: {snapshot.get('пункт', '—')}\n")
    if snapshot.get("комментарий"):
        info.add_run(f"Комментарий: {snapshot['комментарий']}\n")

    # ----------- таблица позиций
    doc.add_paragraph("Состав заказа").runs[0].bold = True
    items = snapshot.get("позиции", [])
    t1 = doc.add_table(rows=1, cols=6)
    hdr = t1.rows[0].cells
    for i, name in enumerate(["Артикул", "Наименование", "Ед.",
                              "Кол-во", "Цена, ₽", "Сумма, ₽"]):
        hdr[i].text = name
    for it in items:
        row = t1.add_row().cells
        row[0].text = str(it.get("sku", ""))
        row[1].text = str(it.get("name", ""))
        row[2].text = str(it.get("unit", ""))
        row[3].text = al(it.get("qty", ""))
        row[4].text = al(it.get("price", ""))
        row[5].text = al(it.get("sum", ""))
    _style_table(t1, [2.2, 7.4, 1.4, 2.0, 2.6, 2.6])
    _header_row_fill(t1)

    # ----------- транспорт / расстояние
    if snapshot.get("режим") == "delivery":
        doc.add_paragraph("Транспорт и маршрут").runs[0].bold = True
        ass = snapshot.get("транспорт", [])
        if ass:
            t2 = doc.add_table(rows=1, cols=5)
            hdr = t2.rows[0].cells
            for i, name in enumerate(["Машина", "Госномер", "Рейсов",
                                      "По весу / по объёму", "Стоимость, ₽"]):
                hdr[i].text = name
            for a in ass:
                row = t2.add_row().cells
                row[0].text = f"{a.get('id','')} {a.get('model','')}"
                row[1].text = str(a.get("plate", ""))
                row[2].text = str(a.get("trips", ""))
                row[3].text = f"{a.get('trips_by_mass','—')} / {a.get('trips_by_volume','—')}"
                row[4].text = al(a.get("cost", ""))
            _style_table(t2, [5.0, 3.4, 2.0, 4.0, 3.4])
            _header_row_fill(t2)
        else:
            doc.add_paragraph("Транспорт не назначен.")
        p = doc.add_paragraph()
        p.add_run("Расстояние: ").bold = True
        p.add_run(
            f"по прямой {al(snapshot.get('прямая_км','—'))} км, "
            f"расчётное {al(snapshot.get('расчётная_км','—'))} км "
            f"({snapshot.get('режим_пути','')})")
        note = doc.add_paragraph()
        nrun = note.add_run(snapshot.get("примечание_расстояние", ""))
        nrun.italic = True
        nrun.font.size = Pt(9)
        nrun.font.color.rgb = GREY

    # ----------- итоги
    doc.add_paragraph("Итоги").runs[0].bold = True
    t3 = doc.add_table(rows=0, cols=2)
    sums = [
        ("Стоимость товаров", al(snapshot.get("товары_сумма", "—"))),
        ("Общая масса заказа", f"{al(snapshot.get('масса_т','—'))} т"),
        ("Общий объём заказа", (f"{al(snapshot.get('объём_м3','—'))} м³"
                                if snapshot.get("объём_известен") else "нет данных")),
        ("Доставка", (f"{al(snapshot.get('доставка','—'))} ₽"
                      + (" (бесплатно — превышен порог)"
                         if snapshot.get("бесплатная_доставка") else ""))),
    ]
    for label, value in sums:
        row = t3.add_row().cells
        row[0].text = label
        row[1].text = value
    total_row = t3.add_row().cells
    total_row[0].text = "ИТОГО"
    total_row[1].text = f"{al(snapshot.get('итог','—'))} ₽"
    _style_table(t3, [8.0, 8.0])
    for cell in t3.rows[-1].cells:
        for p in cell.paragraphs:
            for r in p.runs:
                r.bold = True

    # ----------- предупреждения
    warnings = snapshot.get("предупреждения", []) or []
    if warnings:
        doc.add_paragraph("Предупреждения").runs[0].bold = True
        for w in warnings:
            wp = doc.add_paragraph(style="List Bullet")
            wr = wp.add_run(str(w))
            wr.font.size = Pt(9.5)
            wr.font.color.rgb = GREY

    foot = doc.add_paragraph()
    fr = foot.add_run(
        "Сформировано локально, без передачи данных в интернет. "
        f"Данные прайса: {snapshot.get('данные_от', '—')}. "
        "Документ демонстрационный: значения прайса и тарифов примерные.")
    fr.font.size = Pt(8.5)
    fr.font.color.rgb = GREY

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)
    return out_path


def al(value) -> str:
    """Числа к строке с российской записью: 1 234,5."""
    from decimal import Decimal
    s = str(value).strip().replace(" ", "").replace("₽", "")
    if s.endswith("₽") or s.endswith("руб"):
        s = s[:-3]
    try:
        d = Decimal(s)
    except Exception:
        return str(value)
    sign = "-" if d < 0 else ""
    ab = d.copy_abs()
    int_part = int(ab // 1)
    digits = f"{int_part:,}".replace(",", " ")
    fract = f"{ab % 1:.2f}".split(".")[1].rstrip("0")
    if fract:
        return f"{sign}{digits},{fract}"
    return f"{sign}{digits}"
