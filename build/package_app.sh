#!/bin/bash
# Пакетирование приложения для передачи на другие Mac.
# Результат в dist/:
#   КалькуляторДоставки_для_Mac.zip   (основной способ: распаковал → работает)
#   КалькуляторДоставки_установщик.pkg (вариант с установкой в /Applications)
set -e
cd "$(dirname "$0")/.."

./build/build_app.sh

STAGE="dist/Калькулятор доставки"
rm -rf "$STAGE"
mkdir -p "$STAGE/data"

# приложение (симлинки внутри .app обязательны — копируем ditto)
ditto "dist/КалькуляторДоставки.app" "$STAGE/КалькуляторДоставки.app"
# база данных: свежая копия data/
ditto data "$STAGE/data"
# инструкция установщика
cp build/УСТАНОВКА.txt "$STAGE/УСТАНОВКА.txt"

# ZIP: ditto сохраняет симлинки и метаданные macOS
rm -f "dist/КалькуляторДоставки_для_Mac.zip"
ditto -c -k --sequesterRsrc --keepParent "$STAGE" "dist/КалькуляторДоставки_для_Mac.zip"

# PKG: установка в /Applications/КалькуляторДоставки/
PKGROOT="dist/.pkg-root"
rm -rf "$PKGROOT"
mkdir -p "$PKGROOT/Applications/КалькуляторДоставки"
ditto "$STAGE/КалькуляторДоставки.app" "$PKGROOT/Applications/КалькуляторДоставки/КалькуляторДоставки.app"
ditto "$STAGE/data" "$PKGROOT/Applications/КалькуляторДоставки/data"
cp build/УСТАНОВКА.txt "$PKGROOT/Applications/КалькуляторДоставки/УСТАНОВКА.txt"
rm -f "dist/КалькуляторДоставки_установщик.pkg"
pkgbuild --root "$PKGROOT" \
         --identifier "ru.neurostroytrans.calculator" \
         --version "1.0" \
         "dist/КалькуляторДоставки_установщик.pkg" >/dev/null
rm -rf "$PKGROOT"

rm -f "$STAGE/.DS_Store"
echo
echo "Пакеты готовы в dist/:"
ls -lh dist/*.zip dist/*.pkg | awk '{print " ", $NF, "("$5")"}'
echo "Содержимое пакета: dist/Калькулятор доставки/"
