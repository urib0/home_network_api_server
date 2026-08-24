from __future__ import annotations

import json
import stat
from datetime import datetime
from pathlib import Path

import pytest

from home_network_api_server.storage import build_snapshot, read_snapshot, write_snapshot


def _snapshot() -> dict:
    clients = {
        "00-A0-DE-11-22-33": {
            "ip": "192.168.100.2",
            "hostname": "nas",
            "lease_expires": "2026-08-27T09:12:34+09:00",
        }
    }
    return build_snapshot(clients, datetime(2026, 8, 12, 1, 40, 0).astimezone())


def test_build_snapshot_のトップレベル構造(tmp_path: Path):
    snapshot = _snapshot()
    assert snapshot["schema_version"] == 2
    assert snapshot["count"] == 1
    assert snapshot["updated_at"].startswith("2026-08-12T01:40:00")
    assert "00-A0-DE-11-22-33" in snapshot["clients"]


def test_write_snapshot_で読み書きできる(tmp_path: Path):
    path = tmp_path / "clients.json"
    write_snapshot(path, _snapshot())

    assert read_snapshot(path)["clients"]["00-A0-DE-11-22-33"]["ip"] == "192.168.100.2"
    assert json.loads(path.read_text(encoding="utf-8"))["count"] == 1


def test_write_snapshot_は親ディレクトリを作る(tmp_path: Path):
    path = tmp_path / "nested" / "dir" / "clients.json"
    write_snapshot(path, _snapshot())
    assert path.exists()


def test_write_snapshot_は上書きし一時ファイルを残さない(tmp_path: Path):
    path = tmp_path / "clients.json"
    write_snapshot(path, _snapshot())

    second = build_snapshot({}, datetime(2026, 8, 12, 2, 0, 0).astimezone())
    write_snapshot(path, second)

    assert read_snapshot(path)["count"] == 0
    assert list(tmp_path.iterdir()) == [path]


def test_write_snapshot_は他ユーザーが読める権限にする(tmp_path: Path):
    path = tmp_path / "clients.json"
    write_snapshot(path, _snapshot())
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_write_snapshot_の失敗時は既存ファイルを壊さない(tmp_path: Path):
    path = tmp_path / "clients.json"
    write_snapshot(path, _snapshot())
    original = path.read_text(encoding="utf-8")

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        write_snapshot(path, {"clients": Unserializable()})

    assert path.read_text(encoding="utf-8") == original
    assert list(tmp_path.iterdir()) == [path]


def test_read_snapshot_はファイルが無ければFileNotFoundError(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        read_snapshot(tmp_path / "missing.json")


def test_read_snapshot_は壊れたJSONでJSONDecodeError(tmp_path: Path):
    path = tmp_path / "clients.json"
    path.write_text("{ broken", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        read_snapshot(path)
