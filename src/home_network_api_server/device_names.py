"""MAC アドレスごとの表示名を保存する SQLite ストア。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import normalize_mac


class DeviceNameConflictError(RuntimeError):
    """変更先の MAC アドレスがすでに登録されている。"""


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


def list_device_names(path: Path) -> list[dict[str, str]]:
    """登録済みの端末名を MAC アドレス順で返す。"""
    if not path.exists():
        return []
    with _connect(path) as connection:
        rows = connection.execute("SELECT mac, name FROM device_names ORDER BY mac")
        return [{"mac": mac, "name": name} for mac, name in rows]


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


def create_device_name(path: Path, mac: str, name: str) -> tuple[str, str]:
    """新しい MAC アドレスと表示名を登録する。重複は許可しない。"""
    normalized_mac = normalize_mac(mac)
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("端末名を入力してください")
    try:
        with _connect(path) as connection:
            connection.execute(
                "INSERT INTO device_names (mac, name) VALUES (?, ?)",
                (normalized_mac, normalized_name),
            )
    except sqlite3.IntegrityError as exc:
        raise DeviceNameConflictError(f"{normalized_mac} はすでに登録されています") from exc
    return normalized_mac, normalized_name


def rename_device(path: Path, old_mac: str, new_mac: str, name: str) -> tuple[str, str]:
    """登録済み端末の MAC アドレスと表示名を変更する。"""
    normalized_old_mac = normalize_mac(old_mac)
    normalized_new_mac = normalize_mac(new_mac)
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("端末名を入力してください")

    with _connect(path) as connection:
        if normalized_old_mac != normalized_new_mac:
            exists = connection.execute(
                "SELECT 1 FROM device_names WHERE mac = ?", (normalized_new_mac,)
            ).fetchone()
            if exists:
                raise DeviceNameConflictError(f"{normalized_new_mac} はすでに登録されています")
        result = connection.execute(
            """
            UPDATE device_names SET mac = ?, name = ?, updated_at = CURRENT_TIMESTAMP
            WHERE mac = ?
            """,
            (normalized_new_mac, normalized_name, normalized_old_mac),
        )
        if result.rowcount == 0:
            raise KeyError(normalized_old_mac)
    return normalized_new_mac, normalized_name


def delete_device_name(path: Path, mac: str) -> bool:
    """MAC アドレスの表示名登録を削除する。"""
    normalized_mac = normalize_mac(mac)
    with _connect(path) as connection:
        result = connection.execute("DELETE FROM device_names WHERE mac = ?", (normalized_mac,))
    return result.rowcount > 0
