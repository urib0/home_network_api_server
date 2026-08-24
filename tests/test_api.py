from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from home_network_api_server.api import app
from home_network_api_server.storage import build_snapshot, write_snapshot


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("CLIENTS_JSON_PATH", str(tmp_path / "clients.json"))
    monkeypatch.setenv("DEVICE_NAMES_DB_PATH", str(tmp_path / "device_names.sqlite3"))
    return TestClient(app)


def test_収集済みならそのまま返す(client: TestClient, tmp_path: Path):
    clients = {
        "00-A0-DE-11-22-33": {
            "ip": "192.168.100.2",
            "hostname": "nas",
            "lease_expires": "2026-08-27T09:12:34+09:00",
        }
    }
    write_snapshot(tmp_path / "clients.json", build_snapshot(clients, datetime.now().astimezone()))

    response = client.get("/api/clients")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["clients"]["00-A0-DE-11-22-33"]["hostname"] == "nas"
    assert "updated_at" in body


def test_未収集なら503(client: TestClient):
    assert client.get("/api/clients").status_code == 503


def test_壊れたJSONなら503(client: TestClient, tmp_path: Path):
    (tmp_path / "clients.json").write_text("{ broken", encoding="utf-8")
    assert client.get("/api/clients").status_code == 503


def test_更新後は最新の内容を返す(client: TestClient, tmp_path: Path):
    path = tmp_path / "clients.json"
    write_snapshot(path, build_snapshot({}, datetime.now().astimezone()))
    assert client.get("/api/clients").json()["count"] == 0

    write_snapshot(path, build_snapshot({"AA-BB-CC-DD-EE-FF": {}}, datetime.now().astimezone()))
    assert client.get("/api/clients").json()["count"] == 1


def test_他のエンドポイントは404(client: TestClient):
    assert client.get("/api/cluents").status_code == 404


def test_トップページは閲覧画面を返す(client: TestClient):
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "ネットワークの端末一覧" in response.text


def test_登録済みの表示名をクライアント一覧へ重ねる(client: TestClient, tmp_path: Path):
    mac = "00-A0-DE-11-22-33"
    write_snapshot(tmp_path / "clients.json", build_snapshot({mac: {}}, datetime.now().astimezone()))
    client.put(f"/api/devices/{mac}", json={"name": "NAS"})

    response = client.get("/api/clients")

    assert response.status_code == 200
    assert response.json()["clients"][mac]["name"] == "NAS"


def test_端末名を保存して空文字で削除できる(client: TestClient):
    mac = "00-A0-DE-11-22-33"

    saved = client.put("/api/devices/00:a0:de:11:22:33", json={"name": "  NAS  "})
    deleted = client.put(f"/api/devices/{mac}", json={"name": ""})

    assert saved.status_code == 200
    assert saved.json() == {"mac": mac, "name": "NAS"}
    assert deleted.status_code == 200
    assert deleted.json() == {"mac": mac, "name": None}


def test_端末名のMACが不正なら422(client: TestClient):
    response = client.put("/api/devices/not-a-mac", json={"name": "NAS"})

    assert response.status_code == 422
