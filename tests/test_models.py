"""output.txt の実データを基にした変換テスト。"""

from __future__ import annotations

import pytest

from home_network_api_server.models import classify, device_to_entry, devices_to_clients, normalize_mac

from .conftest import FakeConnection, FakeDevice


@pytest.mark.parametrize(
    ("connection", "expected"),
    [
        (FakeConnection.WIRED, ("wired", None, False)),
        (FakeConnection.HOST_2G, ("wireless", "2.4G", False)),
        (FakeConnection.HOST_5G, ("wireless", "5G", False)),
        (FakeConnection.HOST_6G, ("wireless", "6G", False)),
        (FakeConnection.GUEST_2G, ("wireless", "2.4G", True)),
        (FakeConnection.GUEST_5G, ("wireless", "5G", True)),
        (FakeConnection.IOT_5G, ("wireless", "5G", False)),
        (FakeConnection.UNKNOWN, ("unknown", None, False)),
    ],
)
def test_classify(connection, expected):
    assert classify(connection) == expected


def test_classify_未知の値は落とさずunknownにする():
    assert classify("host_7g") == ("unknown", None, False)
    assert classify(None) == ("unknown", None, False)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("66-37-f6-1f-bf-8b", "66-37-F6-1F-BF-8B"),
        ("66:37:F6:1F:BF:8B", "66-37-F6-1F-BF-8B"),
        ("  A0-66-10-0F-86-27 ", "A0-66-10-0F-86-27"),
    ],
)
def test_normalize_mac(raw, expected):
    assert normalize_mac(raw) == expected


def test_device_to_entry_無線():
    device = FakeDevice(FakeConnection.HOST_5G, "66-37-F6-1F-BF-8B", "192.168.0.102", "MacBookPro")
    assert device_to_entry(device) == {
        "type": "wireless",
        "band": "5G",
        "guest": False,
        "ip": "192.168.0.102",
        "hostname": "MacBookPro",
    }


def test_device_to_entry_有線():
    device = FakeDevice(FakeConnection.WIRED, "A0-66-10-0F-86-27", "192.168.0.54", "mhf")
    assert device_to_entry(device) == {
        "type": "wired",
        "band": None,
        "guest": False,
        "ip": "192.168.0.54",
        "hostname": "mhf",
    }


def test_device_to_entry_ホスト名が空ならNone():
    device = FakeDevice(FakeConnection.WIRED, "74-FE-CE-6D-77-2D", "192.168.0.11", "")
    assert device_to_entry(device)["hostname"] is None


def test_devices_to_clients_はMACをキーにしてソートする():
    devices = [
        FakeDevice(FakeConnection.HOST_5G, "66-37-F6-1F-BF-8B", "192.168.0.102", "MacBookPro"),
        FakeDevice(FakeConnection.WIRED, "00-A0-DE-A9-4D-02", "192.168.0.2", "Unknown"),
        FakeDevice(FakeConnection.HOST_2G, "A8-48-FA-EC-5D-AC", "192.168.0.22", "SwitchBot-HubMini"),
    ]
    clients = devices_to_clients(devices)

    assert list(clients) == ["00-A0-DE-A9-4D-02", "66-37-F6-1F-BF-8B", "A8-48-FA-EC-5D-AC"]
    assert clients["A8-48-FA-EC-5D-AC"]["band"] == "2.4G"


def test_devices_to_clients_空でも壊れない():
    assert devices_to_clients([]) == {}
