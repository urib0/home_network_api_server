from __future__ import annotations

from pathlib import Path

from home_network_api_server.device_names import get_device_names, set_device_name


def test_表示名をMACアドレスで保存して取得する(tmp_path: Path):
    path = tmp_path / "names.sqlite3"

    set_device_name(path, "00:a0:de:11:22:33", "NAS")

    assert get_device_names(path, ["00-A0-DE-11-22-33", "AA-BB-CC-DD-EE-FF"]) == {
        "00-A0-DE-11-22-33": "NAS"
    }


def test_空文字の保存は表示名を削除する(tmp_path: Path):
    path = tmp_path / "names.sqlite3"
    mac = "00-A0-DE-11-22-33"
    set_device_name(path, mac, "NAS")

    assert set_device_name(path, mac, "") is None
    assert get_device_names(path, [mac]) == {}
