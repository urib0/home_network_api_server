"""SSH の対話シェルを読むところのテスト。

paramiko そのものは差し替えず、Channel だけを偽物にして
「プロンプトが出るまで読む」「エコーとプロンプトを落とす」部分を確かめる。
"""

from __future__ import annotations

import socket

import pytest

from home_network_api_server.config import RouterConfig
from home_network_api_server.rtx import RtxError, RtxSession, _decode, _strip_echo_and_prompt


class FakeChannel:
    """recv のたびに用意した chunk を 1 つずつ返す Channel もどき。"""

    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)
        self.sent = bytearray()
        self.closed = False
        self.eof_received = False

    def recv(self, size: int) -> bytes:
        if not self._chunks:
            raise socket.timeout
        return self._chunks.pop(0)

    def recv_ready(self) -> bool:
        # 送信前に溜まっているデータは無い、という前提 (_drain が読み捨てない)
        return False

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def settimeout(self, timeout: float) -> None:
        pass

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def config() -> RouterConfig:
    return RouterConfig(
        host="192.168.100.1", port=22, username="hnapi", password="secret", timeout=1
    )


def _session(config: RouterConfig, chunks: list[bytes]) -> tuple[RtxSession, FakeChannel]:
    session = RtxSession(config)
    chan = FakeChannel(chunks)
    session._chan = chan
    return session, chan


def test_run_はエコーとプロンプトを落とす(config: RouterConfig):
    session, chan = _session(
        config,
        [b"show status dhcp summary\r\n", b"192.168.100.2/24: 00:a0:de:11:22:33\r\n", b"RTX810> "],
    )

    assert session.run("show status dhcp summary") == "192.168.100.2/24: 00:a0:de:11:22:33"
    # 端末の Enter と同じく CR だけを送る (LF も送るとプロンプトが 2 回返る)
    assert chan.sent == b"show status dhcp summary\r"


def test_run_は分割されて届いても組み立てる(config: RouterConfig):
    session, _ = _session(config, [b"show arp\r\n192.168.10", b"0.2 00:a0:de:11", b":22:33\r\nRTX810#"])

    assert session.run("show arp") == "192.168.100.2 00:a0:de:11:22:33"


def test_run_はページャに空白を送って続きを読む(config: RouterConfig):
    session, chan = _session(
        config,
        ["show arp\r\n1 行目\r\n---つづく---".encode(), "\r\n2 行目\r\nRTX810> ".encode()],
    )

    assert session.run("show arp").splitlines()[-1] == "2 行目"
    assert chan.sent.endswith(b" ")


def test_プロンプトが返らなければタイムアウトする(config: RouterConfig):
    session, _ = _session(config, ["show arp\r\n途中まで\r\n".encode()])

    with pytest.raises(RtxError, match="プロンプト"):
        session.run("show arp")


def test_切断されたら例外(config: RouterConfig):
    session, chan = _session(config, [])
    chan.eof_received = True

    with pytest.raises(RtxError, match="切断"):
        session.run("show arp")


def test_decode_はShift_JISでも読める():
    assert _decode("ホスト名: nas".encode("cp932")) == "ホスト名: nas"
    assert _decode("ホスト名: nas".encode("utf-8")) == "ホスト名: nas"


def test_strip_echo_and_prompt_は出力が空でも壊れない():
    assert _strip_echo_and_prompt("console lines 0\r\nRTX810> ", "console lines 0") == ""
