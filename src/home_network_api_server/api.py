"""クライアント一覧 JSON を返すだけの API サーバー。

エンドポイントは GET /api/clients の 1 つのみ、認証なし（LAN 内前提）。
ルーターへは一切アクセスせず、収集側が書いた JSON を読むだけ。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from .config import ApiConfig, clients_json_path
from .storage import read_snapshot

logger = logging.getLogger("home_network_api_server.api")

app = FastAPI(
    title="Home Network API",
    description="自宅ルーター（RTX810）の DHCP リースにあるクライアント一覧を返す",
    version="0.1.0",
)


@app.get("/api/clients")
def get_clients() -> dict[str, Any]:
    """収集済みのクライアント一覧を返す。

    まだ収集されていない、または JSON が壊れている場合は 503 を返す。
    """
    path: Path = clients_json_path()
    try:
        return read_snapshot(path)
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


def main() -> None:
    import uvicorn

    config = ApiConfig.from_env()
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
