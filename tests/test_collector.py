from __future__ import annotations

from pathlib import Path

import pytest

from home_network_api_server import collector
from home_network_api_server.config import ConfigError, RouterConfig, clients_json_path
from home_network_api_server.rtx import RtxAuthError, RtxError
from home_network_api_server.storage import read_snapshot

from .conftest import DHCP_STATUS


class FakeSession:
    """RtxSession の代わり。呼び出し順とログアウトの有無を記録する。"""

    def __init__(self, output: str = "", *, error: Exception | None = None):
        self._output = output
        self._error = error
        self.calls: list[str] = []

    def __enter__(self) -> FakeSession:
        self.calls.append("connect")
        return self

    def __exit__(self, *exc_info) -> None:
        self.calls.append("close")

    def run(self, command: str) -> str:
        self.calls.append(command)
        if self._error:
            raise self._error
        return self._output


@pytest.fixture
def config() -> RouterConfig:
    return RouterConfig(
        host="192.168.100.1", port=22, username="hnapi", password="secret", timeout=10
    )


def _patch_session(monkeypatch: pytest.MonkeyPatch, session: FakeSession) -> FakeSession:
    monkeypatch.setattr(collector, "RtxSession", lambda config: session)
    return session


def test_collect_once_がJSONを書く(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: RouterConfig):
    session = _patch_session(monkeypatch, FakeSession(DHCP_STATUS))

    path = tmp_path / "clients.json"
    assert collector.collect_once(config, path) == 3

    snapshot = read_snapshot(path)
    assert snapshot["schema_version"] == 2
    assert snapshot["count"] == 3
    assert snapshot["clients"]["00-A0-DE-11-22-33"]["hostname"] == "nas"
    # 残り時間の基準は updated_at と同じ時刻を使う
    assert snapshot["clients"]["00-A0-DE-11-22-33"]["lease_expires"] > snapshot["updated_at"]
    assert session.calls == ["connect", collector.DHCP_STATUS_COMMAND, "close"]


def test_取得に失敗してもログアウトする(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: RouterConfig):
    session = _patch_session(monkeypatch, FakeSession(error=RtxError("timeout")))

    with pytest.raises(RtxError):
        collector.collect_once(config, tmp_path / "clients.json")

    assert session.calls[-1] == "close"


def test_取得に失敗しても既存JSONを壊さない(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: RouterConfig):
    path = tmp_path / "clients.json"
    _patch_session(monkeypatch, FakeSession(DHCP_STATUS))
    collector.collect_once(config, path)
    original = path.read_text(encoding="utf-8")

    _patch_session(monkeypatch, FakeSession(error=RtxError("router down")))
    with pytest.raises(RtxError):
        collector.collect_once(config, path)

    assert path.read_text(encoding="utf-8") == original


def test_リースが0件でも書き込むが警告する(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: RouterConfig, caplog
):
    # 形式が想定と違うと 0 件になりうるので、気付けるように警告を出す
    _patch_session(monkeypatch, FakeSession("DHCP Scope number: 1\n"))

    path = tmp_path / "clients.json"
    assert collector.collect_once(config, path) == 0
    assert read_snapshot(path)["count"] == 0
    assert "--raw" in caplog.text


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("ROUTER_USERNAME", "hnapi")
    monkeypatch.setenv("ROUTER_PASSWORD", "secret")
    return monkeypatch


def test_main_は成功したら0を返す(tmp_path: Path, env: pytest.MonkeyPatch):
    env.setenv("CLIENTS_JSON_PATH", str(tmp_path / "clients.json"))
    _patch_session(env, FakeSession(DHCP_STATUS))

    assert collector.main([]) == 0
    assert read_snapshot(tmp_path / "clients.json")["count"] == 3


def test_main_は失敗時に非ゼロを返す(tmp_path: Path, env: pytest.MonkeyPatch):
    env.setenv("CLIENTS_JSON_PATH", str(tmp_path / "clients.json"))
    _patch_session(env, FakeSession(error=RtxError("router down")))

    assert collector.main([]) == 1


def test_main_は認証失敗なら2を返す(tmp_path: Path, env: pytest.MonkeyPatch):
    env.setenv("CLIENTS_JSON_PATH", str(tmp_path / "clients.json"))
    _patch_session(env, FakeSession(error=RtxAuthError("ログインに失敗")))

    # 再試行では直らない設定不備なので、一時的失敗 (1) と区別する
    assert collector.main([]) == 2


def test_main_はパスワード未設定なら2を返す(env: pytest.MonkeyPatch):
    env.delenv("ROUTER_PASSWORD")
    assert collector.main([]) == 2


def test_main_はユーザー名未設定なら2を返す(env: pytest.MonkeyPatch):
    env.delenv("ROUTER_USERNAME")
    assert collector.main([]) == 2


def test_main_rawはJSONを書かずに出力を表示する(
    tmp_path: Path, env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    path = tmp_path / "clients.json"
    env.setenv("CLIENTS_JSON_PATH", str(path))
    _patch_session(env, FakeSession(DHCP_STATUS))

    assert collector.main(["--raw"]) == 0
    assert "192.168.100.2" in capsys.readouterr().out
    assert not path.exists()


def test_ROUTER_HOST_はスキームを落とす(env: pytest.MonkeyPatch):
    # HTTP 管理画面を叩いていた頃の設定ファイルをそのまま使えるようにする
    env.setenv("ROUTER_HOST", "http://192.168.0.1/")
    assert RouterConfig.from_env().host == "192.168.0.1"


def test_ROUTER_HOST_の既定値(env: pytest.MonkeyPatch):
    env.delenv("ROUTER_HOST", raising=False)
    assert RouterConfig.from_env().host == "192.168.100.1"


def test_ROUTER_SSH_PORT_を変えられる(env: pytest.MonkeyPatch):
    env.setenv("ROUTER_SSH_PORT", "2222")
    assert RouterConfig.from_env().port == 2222


def test_ROUTER_TIMEOUT_が不正ならConfigError(env: pytest.MonkeyPatch):
    env.setenv("ROUTER_TIMEOUT", "fast")
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
