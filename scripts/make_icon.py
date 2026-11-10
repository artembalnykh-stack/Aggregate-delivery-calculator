#!/usr/bin/env python3
"""Одноразовый генератор иконки приложения (скрипт, не часть рантайма).

Рисует логотип «Нейростройтранс» (самосвал на сине-фиолетовом градиенте)
и сохраняет:
  - src/web/favicon.png  и src/web/favicon.ico  (иконка вкладки браузера)
  - build/icon.iconset/*.png + build/icon.icns (иконка .app для macOS)

Запуск: .venv/bin/python scripts/make_icon.py
"""
from __future__ import annotations

from pathlib import Path
from subprocess import run

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ICONSET = ROOT / "build" / "icon.iconset"
WEB = ROOT / "src" / "web"

S = 1024  # базовый размер


def draw_icon() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # фон — вертикальный градиент от синего к фиолетовому, скруглённый квадрат
    top, bottom = (52, 105, 244), (123, 88, 240)
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / S
        gd.line([(0, y), (S, y)],
                fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S, S], radius=210, fill=255)
    img.paste(grad, (0, 0), mask)

    white = (255, 255, 255, 255)
    amber = (255, 193, 64, 255)
    dark = (30, 35, 60, 255)

    # платформа самосвала — белый цельный силуэт
    d.rounded_rectangle([170, 430, 760, 640], radius=36, fill=white)
    # кабина
    d.rounded_rectangle([640, 280, 860, 640], radius=48, fill=white)
    # окно кабины
    d.rounded_rectangle([680, 330, 808, 470], radius=30, fill=dark)
    # кузов «с грузом»: янтарная загрузка над платформой
    d.rounded_rectangle([205, 330, 640, 470], radius=30, fill=amber)

    # колёса
    for cx in (300, 680):
        d.ellipse([cx - 78, 560, cx + 78, 716], fill=dark)
        d.ellipse([cx - 36, 618, cx + 36, 690], fill=white)

    # штрих скорости: три линии слева
    for i, y in enumerate((480, 560, 640)):
        d.rounded_rectangle([92, y - 16, 150 + i * 6, y + 16], radius=16, fill=white)

    return img


def main() -> None:
    base = draw_icon()

    # favicon для веб-страницы
    WEB.mkdir(exist_ok=True)
    fav = base.resize((256, 256), Image.LANCZOS)
    fav.save(WEB / "favicon.png")
    fav.resize((32, 32), Image.LANCZOS).save(WEB / "favicon.ico", sizes=[(16, 16), (32, 32)])

    # iconset → icns
    ICONSET.mkdir(parents=True, exist_ok=True)
    for size, tag in [(16, "16"), (32, "16@2x"), (32, "32"), (64, "32@2x"),
                      (128, "128"), (256, "128@2x"), (256, "256"),
                      (512, "256@2x"), (512, "512"), (1024, "512@2x")]:
        img = base.resize((size, size), Image.LANCZOS)
        img.save(ICONSET / f"icon_{tag}.png")
    run(["iconutil", "-c", "icns", str(ICONSET), "-o", str(ROOT / "build" / "icon.icns")],
        check=True)
    print("OK: favicon.png / favicon.ico в src/web, build/icon.icns")


if __name__ == "__main__":
    main()
