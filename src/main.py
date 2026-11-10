# -*- coding: utf-8 -*-
"""
Точка входа приложения.

Приложение живёт по схеме «трамплин»:
  • двойной клик по .app запускает короткий родительский процесс, который
    стартует сервер ОТСОЕДИНЁННЫМ фоновым процессом (--serve), открывает
    окно браузера и сразу завершается — macOS не считает такое приложение
    «не отвечающим», Dock-иконка не зависает;
  • процесс с флагом --serve держит локальный сервер (только 127.0.0.1)
    и живёт, пока его не завершит кнопка «⏻ Завершить приложение»;
  • повторный двойной клик: порт занят → просто ещё одно окно интерфейса.
"""
from __future__ import annotations

import logging
import json
import os
import shutil
import signal
import subprocess
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PORT = 8741
URL = f"http://127.0.0.1:{PORT}/"

SERVE_FLAG = "--serve"


def _web_dir_guess() -> str:
    """где лежит интерфейс: сборка → Resources бандла, разработка → src/web."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        return str(exe.parents[1] / "Resources" / "web")
    return str(Path(__file__).resolve().parent / "web")


def _server_record_path() -> Path:
    from src.server.service import data_dir
    return data_dir() / "server.json"


def write_server_record() -> None:
    """Фоновый сервер отмечается в data/server.json: pid + откуда запущен."""
    from src.server import service
    record = {
        "pid": os.getpid(),
        "port": PORT,
        "app_dir": str(service.app_dir()),
        "web_dir": _web_dir_guess(),
    }
    try:
        _server_record_path().write_text(json.dumps(record), encoding="utf-8")
        logging.info("Запись server.json: %s", record)
    except Exception:
        logging.exception("Не удалось записать server.json")


def tcp_holder_pids(port: int) -> list[int]:
    """PID процессов, слушающих порт (lsof)."""
    try:
        out = subprocess.run(["lsof", "-nP", f"-tiTCP:{port}", "-sTCP:LISTEN"],
                             capture_output=True, text=True, timeout=5)
        return [int(x) for x in out.stdout.split()]
    except Exception:  # noqa: BLE001
        return []


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, TypeError, ValueError):
        return False
    except PermissionError:
        return True


def occupier_is_valid() -> bool:
    """Держатель порта — живой СЕРВЕР ЭТОГО же приложения?

    Проверяем запись server.json: жив ли процесс и существует ли его
    папка с интерфейсом (могла быть удалена — тогда это «зомби»).
    """
    try:
        rec = json.loads(_server_record_path().read_text(encoding="utf-8"))
    except Exception:
        return False
    if not pid_alive(rec.get("pid", -1)):
        return False
    web_dir = rec.get("web_dir") or ""
    return bool(web_dir) and Path(web_dir).exists()


def _setup_logging() -> None:
    """В windowed-сборке stdout нет — журнал пишем в data/app.log."""
    import logging
    try:
        from src.server.service import data_dir
        log_path = data_dir() / "app.log"
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            handlers=[logging.FileHandler(log_path, encoding="utf-8")],
            force=True,
        )
    except Exception:
        logging.basicConfig(level=logging.INFO)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)


def port_in_use(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_for_server(port: int, timeout: float = 8.0) -> bool:
    """Ждём, пока локальный сервер реально начнёт принимать соединения."""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_in_use(port):
            return True
        time.sleep(0.2)
    return False


def open_in_window(url: str) -> None:
    """Открывает интерфейс отдельным окном: Chrome/Edge app-window, иначе браузер."""
    command = None
    if sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
        chrome = next((c for c in candidates if Path(c).exists()), None)
        command = [chrome, f"--app={url}", "--window-size=1280,860"] if chrome else None
    elif sys.platform == "win32":
        base = os.environ.get("PROGRAMFILES", r"C:\Program Files") \
            + r"\Google\Chrome\Application\chrome.exe"
        chrome = base if Path(base).exists() else shutil.which("chrome")
        command = [chrome, f"--app={url}", "--window-size=1280,860"] if chrome else None
    else:
        chrome = shutil.which("chromium") or shutil.which("google-chrome")
        command = [chrome, f"--app={url}", "--window-size=1280,860"] if chrome else None

    if command:
        try:
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:  # noqa: BLE001 открытие окна не должно падать приложением
            pass
    webbrowser.open_new(url)


def run_serve() -> None:
    """Роль --serve: держит сервер. Живёт до «Завершить приложение» или SIGTERM."""
    _setup_logging()
    write_server_record()
    logging.info("Сервер запущен (порт %s)", PORT)

    def serve() -> None:
        from src.server.app import run_server
        run_server(PORT)

    import threading
    threading.Thread(target=serve, daemon=True).start()
    try:
        wait_for_server(PORT)
        logging.info("Сервер принимает соединения на %s", URL)
    except Exception:
        logging.exception("Сервер не смог подняться")
    _sleep_forever()          # держим процесс живым независимо от потоков


def _sleep_forever() -> None:
    import time
    while True:
        time.sleep(3600)


def run_launch() -> None:
    """Роль без аргументов (двойной клик): трамплин."""
    _setup_logging()

    try:
        if port_in_use(PORT):
            holders = tcp_holder_pids(PORT)
            if occupier_is_valid():
                # живой сервер этого же приложения — просто окно
                logging.info("Порт %s занят живым приложением, открываем окно", PORT)
                open_in_window(URL)
                return
            # порт держит «зомби» или чужой сервер — гасим его и стартуем свой
            logging.warning("Порт %s держит недействительный сервер %s — гасим", PORT, holders)
            for pid in holders:
                try:
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    pass
            import time
            deadline = time.time() + 3
            while time.time() < deadline and port_in_use(PORT):
                time.sleep(0.2)

        logging.info("Запуск: стартуем фоновый сервер и открываем окно")
        # отсоединённый фоновый процесс с сервером: живёт после выхода родителя;
        # в сборке бинарник сам понимает --serve, в разработке — скрипт main.py
        if getattr(sys, "frozen", False):
            server_cmd = [sys.executable, SERVE_FLAG]
        else:
            server_cmd = [sys.executable, str(Path(__file__).resolve()), SERVE_FLAG]
        subprocess.Popen(
            server_cmd,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        if not wait_for_server(PORT):
            logging.error("Сервер не поднялся за отведённое время")
        open_in_window(URL)         # окно открываем при живом сервере
        logging.info("Родительский процесс завершён (сервер работает в фоне)")
    except Exception:
        logging.exception("Аварийное завершение")
        raise
    # родитель сразу выходит — macOS видит нормальный короткий запуск


if __name__ == "__main__":
    if SERVE_FLAG in sys.argv[1:]:
        run_serve()
    else:
        run_launch()
