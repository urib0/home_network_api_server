from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from tplinkrouterc6u.common.exception import AuthorizeError

from home_network_api_server import collector
from home_network_api_server.config import ConfigError, RouterConfig, clients_json_path
from home_network_api_server.storage import read_snapshot

from .conftest import FakeConnection, FakeDevice


class FakeRouter:
    """authorize -> get_status -> logout の呼び出し順を検証するためのスタブ。"""

    def __init__(self, devices, *, status_error: Exception | None = None):
        self._devices = devices
        self._status_error = status_error
        self.calls: list[str] = []

    def authorize(self):
        self.calls.append("authorize")

    def get_status(self):
        self.calls.append("get_status")
        if self._status_error:
            raise self._status_error
        return SimpleNamespace(
            devices=self._devices,
            clients_total=len(self._devices),
            wired_total=sum(1 for d in self._devices if d.type is FakeConnection.WIRED),
            wifi_clients_total=sum(1 for d in self._devices if d.type is not FakeConnection.WIRED),
        )

    def logout(self):
        self.calls.append("logout")


@pytest.fixture
def config() -> RouterConfig:
    return RouterConfig(host="http://192.168.0.1", username="admin", password="secret", timeout=10)


def _patch_provider(monkeypatch: pytest.MonkeyPatch, router: FakeRouter) -> None:
    monkeypatch.setattr(
        collector.TplinkRouterProvider, "get_client", staticmethod(lambda *a, **kw: router)
    )


def test_collect_once_がJSONを書く(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: RouterConfig):
    devices = [
        FakeDevice(FakeConnection.HOST_5G, "66-37-F6-1F-BF-8B", "192.168.0.102", "MacBookPro"),
        FakeDevice(FakeConnection.WIRED, "A0-66-10-0F-86-27", "192.168.0.54", "mhf"),
    ]
    router = FakeRouter(devices)
    _patch_provider(monkeypatch, router)

    path = tmp_path / "clients.json"
    assert collector.collect_once(config, path) == 2

    snapshot = read_snapshot(path)
    assert snapshot["count"] == 2
    assert snapshot["clients"]["A0-66-10-0F-86-27"]["type"] == "wired"
    assert router.calls == ["authorize", "get_status", "logout"]


def test_取得に失敗してもログアウトする(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: RouterConfig):
    router = FakeRouter([], status_error=RuntimeError("timeout"))
    _patch_provider(monkeypatch, router)

    with pytest.raises(RuntimeError):
        collector.collect_once(config, tmp_path / "clients.json")

    assert router.calls == ["authorize", "get_status", "logout"]


def test_取得に失敗しても既存JSONを壊さない(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: RouterConfig):
    path = tmp_path / "clients.json"
    _patch_provider(monkeypatch, FakeRouter([FakeDevice(FakeConnection.WIRED, "AA-BB-CC-DD-EE-FF", "192.168.0.9", "x")]))
    collector.collect_once(config, path)
    original = path.read_text(encoding="utf-8")

    _patch_provider(monkeypatch, FakeRouter([], status_error=RuntimeError("router down")))
    with pytest.raises(RuntimeError):
        collector.collect_once(config, path)

    assert path.read_text(encoding="utf-8") == original


def test_main_は失敗時に非ゼロを返す(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ROUTER_PASSWORD", "secret")
    monkeypatch.setenv("CLIENTS_JSON_PATH", str(tmp_path / "clients.json"))
    _patch_provider(monkeypatch, FakeRouter([], status_error=RuntimeError("router down")))

    assert collector.main() == 1


def test_main_は認証失敗なら2を返す(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ROUTER_PASSWORD", "wrong")
    monkeypatch.setenv("CLIENTS_JSON_PATH", str(tmp_path / "clients.json"))
    _patch_provider(monkeypatch, FakeRouter([], status_error=AuthorizeError()))

    # 再試行では直らない設定不備なので、一時的失敗 (1) と区別する
    assert collector.main() == 2


def test_main_はパスワード未設定なら2を返す(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ROUTER_PASSWORD", raising=False)
    assert collector.main() == 2


def test_ROUTER_HOST_はスキームを補う(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ROUTER_PASSWORD", "secret")
    monkeypatch.setenv("ROUTER_HOST", "192.168.0.1")
    assert RouterConfig.from_env().host == "http://192.168.0.1"


def test_ROUTER_TIMEOUT_が不正ならConfigError(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ROUTER_PASSWORD", "secret")
    monkeypatch.setenv("ROUTER_TIMEOUT", "fast")
    with pytest.raises(ConfigError):
        RouterConfig.from_env()


def test_CLIENTS_JSON_PATH_未設定時はXDG状態ディレクトリ配下(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CLIENTS_JSON_PATH", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", "/xdg/state")
    assert clients_json_path() == Path("/xdg/state/home-network-api-server/clients.json")


def test_XDG_STATE_HOME_未設定なら_local_state_を使う(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CLIENTS_JSON_PATH", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/pi")))
    assert clients_json_path() == Path(
        "/home/pi/.local/state/home-network-api-server/clients.json"
    )
