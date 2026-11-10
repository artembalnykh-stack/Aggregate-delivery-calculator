# -*- coding: utf-8 -*-
"""
История расчётов на SQLite. Каждый расчёт хранит ПОЛНЫЙ снимок
применённых цен и тарифов — ранее сохранённый расчёт не меняется
задним числом при обновлении прайса.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


class HistoryStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS calculations (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    number      TEXT    NOT NULL,
                    created_at  TEXT    NOT NULL,
                    mode        TEXT    NOT NULL,
                    summary     TEXT    NOT NULL,   -- краткая сводка для списка
                    snapshot    TEXT    NOT NULL    -- полный JSON расчёта
                )
            """)

    def save(self, snapshot: dict[str, Any]) -> int:
        now = datetime.now()
        number = now.strftime("%Z-%Y%m%d-%H%M%S-") + f"{now.microsecond // 1000:03d}"
        number = number.replace("Z-", "Р-")
        summary = {
            "госномер": None,
            "сумма": snapshot.get("итог"),
            "товаров": snapshot.get("позиций", 0),
            "склад": snapshot.get("склад", ""),
            "пункт": snapshot.get("пункт", ""),
            "режим": snapshot.get("режим", ""),
        }
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO calculations (number, created_at, mode, summary, snapshot) "
                "VALUES (?, ?, ?, ?, ?)",
                (number, now.isoformat(timespec="seconds"), snapshot.get("режим", ""),
                 json.dumps(summary, ensure_ascii=False), json.dumps(snapshot, ensure_ascii=False)))
            return int(cur.lastrowid)

    def list(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, number, created_at, mode, summary "
                "FROM calculations ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get(self, calc_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id, number, created_at, snapshot FROM calculations WHERE id = ?",
                (calc_id,)).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "number": row["number"],
                "created_at": row["created_at"], "snapshot": json.loads(row["snapshot"])}

    def delete(self, calc_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM calculations WHERE id = ?", (calc_id,))
            return cur.rowcount > 0

    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM calculations").fetchone()[0])
