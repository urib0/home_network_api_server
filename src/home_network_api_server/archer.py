"""Archer A10 の接続端末情報を読むための薄いアダプター。"""

from __future__ import annotations

import logging
from typing import Any

from .config import ArcherConfig
from .models import normalize_mac

logger = logging.getLogger("home_network_api_server.archer")


class ArcherError(RuntimeError):
    """Archer A10 の読み取りに失敗した。"""


def fetch_connections(config: ArcherConfig) -> dict[str, dict[str, Any]]:
    """接続中端末を MAC をキーにした接続情報として返す。"""
    try:
        from tplinkrouterc6u import TplinkRouterProvider

        router = TplinkRouterProvider.get_client(
            config.host,
            config.password,
            username=config.username,
            timeout=config.timeout,
            logger=logger,
        )
        router.authorize()
        try:
            devices = router.get_status().devices
        finally:
            router.logout()
    except Exception as exc:
        raise ArcherError(f"{config.host} から端末情報を取得できません: {exc}") from exc

    connections: dict[str, dict[str, Any]] = {}
    for device in devices:
        try:
            connections[normalize_mac(device.macaddr)] = _connection_from_device(device)
        except (AttributeError, ValueError) as exc:
            logger.warning("Archer の端末情報を無視します: %s", exc)
    return dict(sorted(connections.items()))


def _connection_from_device(device: Any) -> dict[str, Any]:
    kind = str(device.type).removeprefix("Connection.")
    if kind == "WIRED":
        connection: dict[str, Any] = {"medium": "wired"}
    elif kind.endswith(("2G", "5G")):
        band = "2.4ghz" if kind.endswith("2G") else "5ghz"
        connection = {"medium": "wifi", "band": band}
        if kind.startswith("GUEST_"):
            connection["guest"] = True
    else:
        connection = {"medium": "unknown"}
    for source, target in (("ssid", "ssid"), ("signal", "signal"), ("online_time", "online_time")):
        value = getattr(device, source, None)
        if value is not None:
            connection[target] = value
    return connection
