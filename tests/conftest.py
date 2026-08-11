from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FakeConnection(Enum):
    """tplinkrouterc6u.Connection と同じ value を持つスタブ。"""

    HOST_2G = "host_2g"
    HOST_5G = "host_5g"
    HOST_6G = "host_6g"
    GUEST_2G = "guest_2g"
    GUEST_5G = "guest_5g"
    GUEST_6G = "guest_6g"
    IOT_2G = "iot_2g"
    IOT_5G = "iot_5g"
    IOT_6G = "iot_6g"
    WIRED = "wired"
    UNKNOWN = "unknown"


@dataclass
class FakeDevice:
    """Device の macaddr / ipaddr プロパティを模したスタブ。"""

    type: FakeConnection
    macaddr: str
    ipaddr: str
    hostname: str
