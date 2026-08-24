"""RTX810 の CLI へ SSH で接続し、show コマンドの出力を取得する。

RTX810 の SSH サーバーは exec チャネル（`ssh host "コマンド"` の形）に対応していない
ため、対話シェルを開いてプロンプトを待ちながらコマンドを流し込む。

`show` 系は一般ユーザーモードで実行できるので、`administrator` へは昇格しない。
このツールがルーターの設定を変更できないことを、権限の側でも担保するため。
"""

from __future__ import annotations

import logging
import re
import socket
import time
from types import TracebackType

import paramiko

from .config import RouterConfig

logger = logging.getLogger("home_network_api_server.rtx")

# 一般ユーザーモードのプロンプトは `RTX810>`、管理者モードは `RTX810#`。
# ホスト名は `console prompt` で変えられるので、行末の > / # だけを手掛かりにする。
_PROMPT_RE = re.compile(r"(?:\A|\n)[^\n]{0,80}[>#] ?\Z")
_PROMPT_LINE_RE = re.compile(r"\A\S*[>#] ?\Z")

# `console lines infinity` が通れば出ないが、念のため残す。
# 実機では `---more---` の形で出た
_PAGER_RE = re.compile(r"---\s*(?:つづく|続く|more)\s*---", re.IGNORECASE)

# 行が折り返されるとレコードが分断されてパースできなくなるので、
# 端末幅を広げた上でページングを止める。どれも通らなくても致命的ではない。
_CONSOLE_COLUMNS = 200
# ページングを止めるのは `console lines infinity`。`console lines 0` は
# `Error: Parameter out of range` になる（実機で確認済み）。
_SESSION_SETUP = (
    "console character ascii",
    "console lines infinity",
    f"console columns {_CONSOLE_COLUMNS}",
)

_RECV_SIZE = 65536
_RECV_TIMEOUT = 0.5


class RtxError(RuntimeError):
    """RTX810 との通信に失敗した（再試行で直りうる）。"""


class RtxAuthError(RtxError):
    """SSH のログインに失敗した（再試行では直らない）。"""


class RtxSession:
    """SSH の対話シェル 1 本を表す。`with` で使うと必ずログアウトする。"""

    def __init__(self, config: RouterConfig) -> None:
        self._config = config
        self._client: paramiko.SSHClient | None = None
        self._chan: paramiko.Channel | None = None

    def __enter__(self) -> RtxSession:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def connect(self) -> None:
        client = paramiko.SSHClient()
        # known_hosts にエントリがあれば検証する（鍵が変わっていれば例外）。
        # 無ければ AutoAddPolicy で受け入れる — 自宅 LAN 内の相手であり、
        # 初回接続のために手で known_hosts を用意させるほどの脅威ではない。
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        target = f"{self._config.host}:{self._config.port}"
        try:
            client.connect(
                hostname=self._config.host,
                port=self._config.port,
                username=self._config.username,
                password=self._config.password,
                # ルーター相手に鍵認証を試す意味はなく、エージェントを覗く必要もない
                look_for_keys=False,
                allow_agent=False,
                timeout=self._config.timeout,
                banner_timeout=self._config.timeout,
                auth_timeout=self._config.timeout,
            )
        except paramiko.AuthenticationException as exc:
            client.close()
            raise RtxAuthError(f"{target} への SSH ログインに失敗しました") from exc
        except paramiko.SSHException as exc:
            client.close()
            raise RtxError(f"{target} との SSH 接続に失敗しました: {exc}") from exc
        except OSError as exc:
            client.close()
            raise RtxError(f"{target} へ接続できません: {exc}") from exc

        self._client = client
        self._chan = client.invoke_shell(width=_CONSOLE_COLUMNS, height=1000)
        self._chan.settimeout(_RECV_TIMEOUT)
        self._read_until_prompt()  # ログインバナーと最初のプロンプトを読み捨てる
        for command in _SESSION_SETUP:
            self.run(command)
        logger.debug("%s へログインしました", target)

    def run(self, command: str) -> str:
        """コマンドを 1 つ実行し、エコーとプロンプトを除いた出力を返す。"""
        chan = self._chan
        if chan is None:
            raise RtxError("接続していません")

        self._drain()
        try:
            # 端末の Enter と同じく CR だけを送る。LF も足すと空行が 1 回余計に
            # 入力され、プロンプトが 2 つ返ってきて以降の読み取りがずれる。
            chan.sendall(command.encode("ascii") + b"\r")
        except OSError as exc:
            raise RtxError(f"コマンドの送信に失敗しました: {exc}") from exc

        return _strip_echo_and_prompt(_decode(self._read_until_prompt()), command)

    def close(self) -> None:
        """ログアウトして接続を閉じる。

        RTX810 は同時ログイン数に上限があるので、失敗しても必ず切断まで進める。
        """
        if self._chan is not None:
            try:
                self._chan.sendall(b"exit\r")
            except OSError:
                logger.debug("exit の送信に失敗しました", exc_info=True)
            self._chan.close()
            self._chan = None
        if self._client is not None:
            self._client.close()
            self._client = None

    def _drain(self) -> None:
        """前のコマンドの取りこぼしを捨てて、読み取り位置を揃える。"""
        chan = self._chan
        while chan is not None and chan.recv_ready():
            chan.recv(_RECV_SIZE)

    def _read_until_prompt(self) -> bytes:
        """次のプロンプトが出るまで読む。

        マルチバイト文字が chunk の境界で割れるため、デコードは呼び出し側に任せて
        bytes のまま返す。プロンプトとページャの判定は ASCII なので、
        判定用にだけ緩いデコードをする。
        """
        chan = self._chan
        if chan is None:
            raise RtxError("接続していません")

        deadline = time.monotonic() + self._config.timeout
        out = bytearray()
        window = bytearray()
        while True:
            try:
                chunk = chan.recv(_RECV_SIZE)
            except socket.timeout:
                chunk = b""
            except OSError as exc:
                raise RtxError(f"読み取りに失敗しました: {exc}") from exc

            if chunk:
                window += chunk
                view = window.decode("utf-8", errors="replace")
                if _PAGER_RE.search(view):
                    # 続きを出させる。窓を空にして同じページャを二重に検出しない
                    chan.sendall(b" ")
                    out += window
                    window.clear()
                    continue
                if _PROMPT_RE.search(view):
                    return bytes(out + window)
            elif chan.eof_received or chan.closed:
                raise RtxError("接続が切断されました")

            if time.monotonic() > deadline:
                raise RtxError(
                    f"{self._config.timeout} 秒以内にプロンプトが返りませんでした"
                )


def _decode(raw: bytes) -> str:
    """RTX810 の出力を文字列にする。

    `console character ascii` が通っていれば UTF-8 として読めるが、
    通らなかった場合に備えて Shift_JIS も試す。
    """
    for encoding in ("utf-8", "cp932"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _strip_echo_and_prompt(text: str, command: str) -> str:
    """出力の先頭のエコー行と、末尾のプロンプト行を落とす。"""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and command in lines[0]:
        lines.pop(0)

    while lines and not lines[-1].strip():
        lines.pop()
    if lines and _PROMPT_LINE_RE.match(lines[-1].strip()):
        lines.pop()
    while lines and not lines[-1].strip():
        lines.pop()

    return "\n".join(lines)
