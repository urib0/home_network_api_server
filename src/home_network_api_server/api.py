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
from .device_names import (
    DeviceNameConflictError,
    create_device_name,
    delete_device_name,
    get_device_names,
    list_device_names,
    rename_device,
    set_device_name,
)
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
    mac: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if len(value.strip()) > 100:
            raise ValueError("端末名は100文字以内にしてください")
        return value


class DeviceNameCreate(BaseModel):
    """端末名の新規登録リクエスト。"""

    mac: str
    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("端末名を入力してください")
        if len(value.strip()) > 100:
            raise ValueError("端末名は100文字以内にしてください")
        return value


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    """人が閲覧するためのクライアント一覧画面を返す。"""
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/devices", include_in_schema=False)
def device_management() -> FileResponse:
    """端末名データベースを管理する画面を返す。"""
    return FileResponse(STATIC_DIR / "devices.html", media_type="text/html")


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
    if update.mac is None or update.mac == normalized_mac:
        name = set_device_name(device_names_db_path(), normalized_mac, update.name)
        return {"mac": normalized_mac, "name": name}
    try:
        new_mac, name = rename_device(device_names_db_path(), normalized_mac, update.mac, update.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except KeyError:
        raise HTTPException(status_code=404, detail="変更元のMACアドレスは登録されていません") from None
    except DeviceNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {"mac": new_mac, "name": name}


@app.get("/api/devices")
def get_devices() -> list[dict[str, str]]:
    """登録済みの端末名データベースを返す。"""
    return list_device_names(device_names_db_path())


@app.post("/api/devices", status_code=201)
def create_device(update: DeviceNameCreate) -> dict[str, str]:
    """MAC アドレスと表示名を新規登録する。"""
    try:
        mac, name = create_device_name(device_names_db_path(), update.mac, update.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except DeviceNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {"mac": mac, "name": name}


@app.delete("/api/devices/{mac}", status_code=204)
def delete_device(mac: str) -> None:
    """端末名の登録を削除する。"""
    try:
        deleted = delete_device_name(device_names_db_path(), mac)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if not deleted:
        raise HTTPException(status_code=404, detail="MACアドレスは登録されていません")


def main() -> None:
    import uvicorn

    config = ApiConfig.from_env()
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
