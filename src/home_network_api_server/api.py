"""クライアント一覧 JSON と閲覧画面を提供する API サーバー。

閲覧画面は GET /、JSON API は GET /api/clients。認証なし（LAN 内前提）。
ルーターへは一切アクセスせず、収集側が書いた JSON を読むだけ。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator

from .config import ApiConfig, clients_json_path, device_names_db_path
from .device_names import get_device_names, set_device_name
from .models import normalize_mac
from .storage import read_snapshot

logger = logging.getLogger("home_network_api_server.api")

app = FastAPI(
    title="Home Network API",
    description="自宅ルーター（RTX810）の DHCP リースにあるクライアント一覧を返す",
    version="0.1.0",
)

STATIC_DIR = Path(__file__).parent / "static"


class DeviceNameUpdate(BaseModel):
    """端末表示名の更新リクエスト。空文字は登録を削除する。"""

    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if len(value.strip()) > 100:
            raise ValueError("端末名は100文字以内にしてください")
        return value


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    """人が閲覧するためのクライアント一覧画面を返す。"""
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/api/clients")
def get_clients() -> dict[str, Any]:
    """収集済みのクライアント一覧を返す。

    まだ収集されていない、または JSON が壊れている場合は 503 を返す。
    """
    path: Path = clients_json_path()
    try:
        snapshot = read_snapshot(path)
        names = get_device_names(device_names_db_path(), list(snapshot["clients"]))
        snapshot["clients"] = {
            mac: {**client, "name": names.get(mac)} for mac, client in snapshot["clients"].items()
        }
        return snapshot
    except FileNotFoundError:
        logger.warning("%s がまだ存在しません", path)
        raise HTTPException(
            status_code=503,
            detail="クライアント情報がまだ収集されていません",
        ) from None
    except json.JSONDecodeError as exc:
        logger.error("%s の読み込みに失敗しました: %s", path, exc)
        raise HTTPException(
            status_code=503,
            detail="クライアント情報の読み込みに失敗しました",
        ) from None


@app.put("/api/devices/{mac}")
def update_device_name(mac: str, update: DeviceNameUpdate) -> dict[str, str | None]:
    """MAC アドレスに紐づく、人が付ける表示名を保存する。"""
    try:
        normalized_mac = normalize_mac(mac)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    name = set_device_name(device_names_db_path(), normalized_mac, update.name)
    return {"mac": normalized_mac, "name": name}


def main() -> None:
    import uvicorn

    config = ApiConfig.from_env()
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
