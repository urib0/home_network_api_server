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
    return TestClient(app)


def test_収集済みならそのまま返す(client: TestClient, tmp_path: Path):
    clients = {
        "66-37-F6-1F-BF-8B": {
            "type": "wireless",
            "band": "5G",
            "guest": False,
            "ip": "192.168.0.102",
            "hostname": "MacBookPro",
        }
    }
    write_snapshot(tmp_path / "clients.json", build_snapshot(clients, datetime.now().astimezone()))

    response = client.get("/api/clients")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["clients"]["66-37-F6-1F-BF-8B"]["hostname"] == "MacBookPro"
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
    assert client.get("/").status_code == 404
