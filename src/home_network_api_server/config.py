"""環境変数から設定を読む。

収集側と API 側で JSON のパスだけを共有するため、パス解決はここに集約する。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CLIENTS_JSON_PATH = Path("/var/lib/home-network-api-server/clients.json")


class ConfigError(RuntimeError):
    """必須の環境変数が無い、または値が不正。"""


def clients_json_path() -> Path:
    """収集結果 JSON のパス。収集側と API 側で同じ値を見る。"""
    raw = os.environ.get("CLIENTS_JSON_PATH")
    return Path(raw).expanduser() if raw else DEFAULT_CLIENTS_JSON_PATH


@dataclass(frozen=True, slots=True)
class RouterConfig:
    """ルーターへのログイン情報。"""

    host: str
    username: str
    password: str
    timeout: int

    @classmethod
    def from_env(cls) -> RouterConfig:
        password = os.environ.get("ROUTER_PASSWORD")
        if not password:
            raise ConfigError("環境変数 ROUTER_PASSWORD が設定されていません")

        host = os.environ.get("ROUTER_HOST", "http://192.168.0.1")
        # tplinkrouterc6u はスキーム付きの URL を期待する
        if "://" not in host:
            host = f"http://{host}"

        return cls(
            host=host,
            username=os.environ.get("ROUTER_USERNAME", "admin"),
            password=password,
            timeout=_int_env("ROUTER_TIMEOUT", 10),
        )


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
