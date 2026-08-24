"""ワンショットで RTX810 から DHCP リース一覧を取得し、JSON へ上書き保存する。

systemd の oneshot サービス（timer から定期起動）として実行される想定。
失敗時は非ゼロ終了し、既存の JSON はそのまま残す。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from .config import ConfigError, RouterConfig, clients_json_path
from .models import parse_dhcp_status
from .rtx import RtxAuthError, RtxError, RtxSession
from .storage import build_snapshot, write_snapshot

logger = logging.getLogger("home_network_api_server.collector")

# 一般ユーザーモードで実行できる読み取り専用コマンド。
# `show status dhcp summary` は 1 行 1 リースで短いが、リースの残り時間を出さない。
DHCP_STATUS_COMMAND = "show status dhcp"


def fetch_raw(config: RouterConfig) -> str:
    """RTX810 へログインし、DHCP リース一覧の生の出力を返す。"""
    with RtxSession(config) as session:
        return session.run(DHCP_STATUS_COMMAND)


def fetch_clients(config: RouterConfig, now: datetime) -> dict[str, dict]:
    """RTX810 から取得したクライアント一覧を返す。

    `now` はリースの残り時間を絶対時刻に直す基準。スナップショットの `updated_at`
    と同じ値を渡し、両者がずれないようにする。
    """
    clients = parse_dhcp_status(fetch_raw(config), now)
    if not clients:
        # リース期限は既定 72 時間あるので、本当に 0 件になることはまず無い。
        # 出力形式が想定と違う可能性のほうが高いので、確認方法を添えて警告する。
        # PATH には入らない venv 内のスクリプトなので、実際に叩けるパスを出す
        logger.warning(
            "DHCP リースが 1 件も読み取れませんでした。"
            "`%s --raw` で実際の出力を確認してください",
            sys.argv[0],
        )
    logger.info("取得: %s 台", len(clients))
    return clients


def collect_once(config: RouterConfig, output_path: Path) -> int:
    """1 回分の取得と保存。書き込んだクライアント数を返す。"""
    now = datetime.now().astimezone()
    clients = fetch_clients(config, now)
    snapshot = build_snapshot(clients, now)
    write_snapshot(output_path, snapshot)
    logger.info("%s に %s 件を書き込みました", output_path, len(clients))
    return len(clients)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="home-network-collector",
        description="RTX810 の DHCP リース一覧を取得して JSON に保存する",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help=f"JSON に保存せず、`{DHCP_STATUS_COMMAND}` の出力をそのまま表示する",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # paramiko は接続と認証の成功を INFO で出す。5 分ごとに 2 行ずつ journal に
    # 溜まっても読み取れる情報は無いので、警告以上だけにする
    logging.getLogger("paramiko").setLevel(logging.WARNING)
    try:
        config = RouterConfig.from_env()
        output_path = clients_json_path()
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2

    try:
        if args.raw:
            print(fetch_raw(config))
        else:
            collect_once(config, output_path)
    except RtxAuthError as exc:
        # 再試行しても直らないので、トレースバックは出さず設定不備として扱う
        logger.error(
            "%s。ROUTER_USERNAME / ROUTER_PASSWORD と、"
            "RTX810 側の `login user` の設定を確認してください",
            exc,
        )
        return 2
    except RtxError as exc:
        # ルーター再起動中など、一時的な失敗も想定される。次回の timer 発火に任せる
        logger.error("取得に失敗しました: %s", exc)
        return 1
    except Exception as exc:
        logger.error("取得に失敗しました: %s: %s", type(exc).__name__, exc, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
