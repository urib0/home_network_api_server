from types import SimpleNamespace

from home_network_api_server.archer import _connection_from_device


def test_無線端末の接続種別を変換する():
    device = SimpleNamespace(
        type="Connection.HOST_5G", ssid="home", signal=-48, online_time=120.0
    )
    assert _connection_from_device(device) == {
        "medium": "wifi",
        "band": "5ghz",
        "ssid": "home",
        "signal": -48,
        "online_time": 120.0,
    }


def test_有線端末の接続種別を変換する():
    device = SimpleNamespace(type="Connection.WIRED", ssid=None, signal=None, online_time=None)
    assert _connection_from_device(device) == {"medium": "wired"}


def test_未知の接続種別をwifiとして扱わない():
    device = SimpleNamespace(type="Connection.UNKNOWN", ssid=None, signal=None, online_time=None)
    assert _connection_from_device(device) == {"medium": "unknown"}
