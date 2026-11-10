#!/bin/bash
# Сборка macOS-приложения (.app) через PyInstaller.
# Результат: dist/КалькуляторДоставки.app + рядом должна лежать папка data/.
set -e
cd "$(dirname "$0")/.."
source .venv/bin/activate

pyinstaller \
  --name "КалькуляторДоставки" \
  --windowed \
  --noconfirm \
  --clean \
  src/main.py \
  --add-data "src/web:web" \
  --collect-submodules src \
  --collect-all certifi \
  --icon build/icon.icns

APP="dist/КалькуляторДоставки.app"
if [ ! -d "$APP" ]; then echo "ОШИБКА: бандл не собран"; exit 1; fi

# LSUIElement: приложение без иконки в Dock — стартует тихо, окно открывает
# в Chrome; Dock-иконка больше не «прыгает» при первом запуске
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$APP/Contents/Info.plist" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :LSUIElement true" "$APP/Contents/Info.plist"

# ad-hoc подпись всего бандла (после правки Info.plist) — предсказуемая
# проверка Gatekeeper на другой машине
codesign --deep --force --sign - "$APP" || echo "ВНИМАНИЕ: подпись не прошла"
codesign --verify --deep "$APP" || echo "ВНИМАНИЕ: подпись не прошла проверку"
# снять локальный карантин (у получателя он появляется при копировании)
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true

# pywebview не используем; данные — РЯДОМ с .app
if [ ! -d "dist/data" ]; then cp -R data dist/data; fi
echo "Готово: $APP"
echo "Данные: dist/data/ (правьте прайс.xlsx без пересборки)"
