"""収集結果 JSON の読み書き。

API サーバーが読んでいる最中に収集側が書き込んでも壊れた JSON を読まないよう、
書き込みは「同一ディレクトリの一時ファイル -> os.replace」で原子的に行う。
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def build_snapshot(clients: dict[str, dict[str, Any]], updated_at: datetime) -> dict[str, Any]:
    """JSON に書き出すトップレベルの構造を組み立てる。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": updated_at.isoformat(timespec="seconds"),
        "count": len(clients),
        "clients": clients,
    }


def write_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
    """スナップショットを原子的に上書き保存する（履歴は残さない）。"""
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        # mkstemp は 0600 で作るので、API サーバーが別ユーザーでも読めるようにする
        tmp_path.chmod(0o644)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    # ディレクトリエントリの差し替えも永続化させる（電源断対策）
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def read_snapshot(path: Path) -> dict[str, Any]:
    """スナップショットを読み込む。

    Raises:
        FileNotFoundError: まだ一度も収集されていない。
        json.JSONDecodeError: ファイルが壊れている。
    """
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)
