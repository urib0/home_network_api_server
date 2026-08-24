"""MAC アドレスごとの表示名を保存する SQLite ストア。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import normalize_mac


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS device_names (
            mac TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    return connection


def get_device_names(path: Path, macs: list[str]) -> dict[str, str]:
    """指定した MAC アドレスに登録済みの表示名を返す。"""
    if not macs or not path.exists():
        return {}
    normalized_macs = [normalize_mac(mac) for mac in macs]
    placeholders = ", ".join("?" for _ in normalized_macs)
    with _connect(path) as connection:
        rows = connection.execute(
            f"SELECT mac, name FROM device_names WHERE mac IN ({placeholders})", normalized_macs
        )
        return dict(rows)


def set_device_name(path: Path, mac: str, name: str | None) -> str | None:
    """表示名を保存する。空文字・None は登録を削除して DHCP 名へ戻す。"""
    normalized_mac = normalize_mac(mac)
    normalized_name = name.strip() if name else None
    with _connect(path) as connection:
        if normalized_name:
            connection.execute(
                """
                INSERT INTO device_names (mac, name, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(mac) DO UPDATE SET name = excluded.name, updated_at = CURRENT_TIMESTAMP
                """,
                (normalized_mac, normalized_name),
            )
        else:
            connection.execute("DELETE FROM device_names WHERE mac = ?", (normalized_mac,))
    return normalized_name
