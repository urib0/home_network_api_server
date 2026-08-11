"""ルーターの Device を JSON 用の辞書へ変換する。

tplinkrouterc6u の Connection は host_2g / host_5g / wired / guest_2g … という
接続元ネットワークと周波数帯を混ぜた列挙値なので、
「有線か無線か」「どの帯域か」「ゲストネットワークか」に分解して持つ。
"""

from __future__ import annotations

from typing import Any, Protocol

WIRED = "wired"
WIRELESS = "wireless"
UNKNOWN = "unknown"

# Connection の値 -> (type, band, guest)
_CONNECTION_MAP: dict[str, tuple[str, str | None, bool]] = {
    "wired": (WIRED, None, False),
    "host_2g": (WIRELESS, "2.4G", False),
    "host_5g": (WIRELESS, "5G", False),
    "host_6g": (WIRELESS, "6G", False),
    "guest_2g": (WIRELESS, "2.4G", True),
    "guest_5g": (WIRELESS, "5G", True),
    "guest_6g": (WIRELESS, "6G", True),
    "iot_2g": (WIRELESS, "2.4G", False),
    "iot_5g": (WIRELESS, "5G", False),
    "iot_6g": (WIRELESS, "6G", False),
    "unknown": (UNKNOWN, None, False),
}


class DeviceLike(Protocol):
    """tplinkrouterc6u の Device のうち、ここで使う部分だけ。"""

    type: Any
    hostname: str

    @property
    def macaddr(self) -> str: ...

    @property
    def ipaddr(self) -> str: ...


def classify(connection: Any) -> tuple[str, str | None, bool]:
    """Connection 列挙値を (type, band, guest) に分解する。

    未知の値は type="unknown" として落とさずに残す。
    """
    value = getattr(connection, "value", connection)
    if not isinstance(value, str):
        return (UNKNOWN, None, False)
    return _CONNECTION_MAP.get(value.lower(), (UNKNOWN, None, False))


def normalize_mac(macaddr: str) -> str:
    """MAC アドレスを大文字ハイフン区切りに揃える（JSON のキーになるため）。"""
    return macaddr.strip().upper().replace(":", "-")


def device_to_entry(device: DeviceLike) -> dict[str, Any]:
    """Device 1 台分を JSON の値部分へ変換する。"""
    conn_type, band, guest = classify(device.type)
    hostname = device.hostname
    return {
        "type": conn_type,
        "band": band,
        "guest": guest,
        "ip": device.ipaddr,
        "hostname": hostname if hostname else None,
    }


def devices_to_clients(devices: list[DeviceLike]) -> dict[str, dict[str, Any]]:
    """Device のリストを MAC アドレスをキーにした辞書へ変換する。

    同じ MAC が複数回現れた場合は後勝ち（ルーターが重複を返すことは通常ない）。
    キーは MAC の昇順に並べ、差分を読みやすくする。
    """
    clients = {normalize_mac(d.macaddr): device_to_entry(d) for d in devices}
    return dict(sorted(clients.items()))
