"""環境変数から設定を読む。

収集側と API 側で JSON のパスだけを共有するため、パス解決はここに集約する。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

class ConfigError(RuntimeError):
    """必須の環境変数が無い、または値が不正。"""


def default_clients_json_path() -> Path:
    """CLIENTS_JSON_PATH 未設定時の保存先。

    サービスはユーザー権限で動くので、システム全体の /var/lib ではなく
    XDG の状態ディレクトリ（既定で ~/.local/state）配下に置く。

    なお systemd ユニットは CLIENTS_JSON_PATH を明示的に渡すため、この既定値は
    使われない。ユニット側は %h/.local/state/... と直に書いており
    XDG_STATE_HOME を見ない（systemd の %S がバージョンで解決先を変えるのを
    避けるため）ので、XDG_STATE_HOME を変えている環境では両者がずれる。
    """
    state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return base / "home-network-api-server" / "clients.json"


def clients_json_path() -> Path:
    """収集結果 JSON のパス。収集側と API 側で同じ値を見る。"""
    raw = os.environ.get("CLIENTS_JSON_PATH")
    return Path(raw).expanduser() if raw else default_clients_json_path()


@dataclass(frozen=True, slots=True)
class RouterConfig:
    """RTX810 へ SSH でログインするための設定。"""

    host: str
    port: int
    username: str
    password: str
    timeout: int

    @classmethod
    def from_env(cls) -> RouterConfig:
        # RTX810 の SSH は必ずユーザー名を要求する (login user で作ったもの)。
        # 既定値を置くと、間違ったユーザー名での認証失敗として現れて分かりにくい。
        username = os.environ.get("ROUTER_USERNAME")
        if not username:
            raise ConfigError("環境変数 ROUTER_USERNAME が設定されていません")

        password = os.environ.get("ROUTER_PASSWORD")
        if not password:
            raise ConfigError("環境変数 ROUTER_PASSWORD が設定されていません")

        return cls(
            host=_normalize_host(os.environ.get("ROUTER_HOST", "192.168.100.1")),
            port=_int_env("ROUTER_SSH_PORT", 22),
            username=username,
            password=password,
            timeout=_int_env("ROUTER_TIMEOUT", 10),
        )


def _normalize_host(raw: str) -> str:
    """ROUTER_HOST をホスト名 / IP だけにする。

    HTTP 管理画面を叩いていた頃の設定ファイルには `http://192.168.0.1` のような
    値が残っているので、スキームと末尾のスラッシュを落として受け付ける。
    """
    host = raw.strip()
    if "://" in host:
        host = host.split("://", 1)[1]
    return host.rstrip("/")


@dataclass(frozen=True, slots=True)
class ApiConfig:
    """API サーバーの待ち受け設定。"""

    host: str
    port: int

    @classmethod
    def from_env(cls) -> ApiConfig:
        return cls(
            host=os.environ.get("API_HOST", "0.0.0.0"),
            port=_int_env("API_PORT", 8000),
        )


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"環境変数 {name} は整数である必要があります: {raw!r}") from exc
