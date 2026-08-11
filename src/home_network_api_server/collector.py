"""ワンショットでルーターからクライアント一覧を取得し、JSON へ上書き保存する。

systemd の oneshot サービス（timer から定期起動）として実行される想定。
失敗時は非ゼロ終了し、既存の JSON はそのまま残す。
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

from tplinkrouterc6u import TplinkRouterProvider
from tplinkrouterc6u.common.exception import AuthorizeError

from .config import ConfigError, RouterConfig, clients_json_path
from .models import devices_to_clients
from .storage import build_snapshot, write_snapshot

logger = logging.getLogger("home_network_api_server.collector")


def fetch_clients(config: RouterConfig) -> dict[str, dict]:
    """ルーターへログインしてクライアント一覧を取得する。"""
    router = TplinkRouterProvider.get_client(
        config.host,
        config.password,
        username=config.username,
        timeout=config.timeout,
        logger=logger,
    )
    logger.info("router client: %s (%s)", type(router).__name__, config.host)

    router.authorize()
    try:
        status = router.get_status()
    finally:
        # ルーターの同時ログインセッション数には上限があるため、必ずログアウトする
        try:
            router.logout()
        except Exception:
            logger.warning("logout に失敗しました", exc_info=True)

    logger.info(
        "取得: 合計 %s 台 (有線 %s / 無線 %s)",
        status.clients_total,
        status.wired_total,
        status.wifi_clients_total,
    )
    return devices_to_clients(status.devices)


def collect_once(config: RouterConfig, output_path: Path) -> int:
    """1 回分の取得と保存。書き込んだクライアント数を返す。"""
    clients = fetch_clients(config)
    snapshot = build_snapshot(clients, datetime.now().astimezone())
    write_snapshot(output_path, snapshot)
    logger.info("%s に %s 件を書き込みました", output_path, len(clients))
    return len(clients)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        config = RouterConfig.from_env()
        output_path = clients_json_path()
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2

    try:
        collect_once(config, output_path)
    except AuthorizeError:
        # 再試行しても直らないので、トレースバックは出さず設定不備として扱う
        logger.error(
            "ルーターへのログインに失敗しました。ROUTER_USERNAME / ROUTER_PASSWORD を確認してください"
        )
        return 2
    except Exception as exc:
        # ルーター再起動中など、一時的な失敗も想定される。次回の timer 発火に任せる
        logger.error("取得に失敗しました: %s: %s", type(exc).__name__, exc, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
