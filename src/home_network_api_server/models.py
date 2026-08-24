"""`show status dhcp` の出力を JSON 用の辞書へ変換する。

実機（RTX810 Rev.11）の出力は 1 リースが 3〜4 行のブロックになる。

    DHCP Scope number: 1
          Network address: 10.10.10.0
              Leased address: 10.10.10.2
            (type) Client ID: (01) b4 69 21 1f 7d ea
                   Host Name: mhf
             Remaining lease: 2days 16hours 3min. 50secs.

MAC は `(01) b4 69 ...`（クライアント ID）と `Client ethernet address: b4:69:...` の
両方の形で出てくる。同じ機種の `show status dhcp summary` は 1 行 1 リースで
`10.10.10.2:  b4:69:21:1f:7d:ea, mhf` という別の形になるが、どちらも読めるようにしてある
（桁位置には頼らず「MAC が出てきたらそこが 1 レコードの始まり」として拾う）。

MAC は JSON のキーになるので、これが取れない行は捨てる（スコープのヘッダや
末尾の集計行がそれに当たる）。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger("home_network_api_server.models")

_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# `00:a0:de:11:22:33` / `00-a0-de-11-22-33` 形式
_MAC_PATTERN = r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}"
_MAC_RE = re.compile(rf"(?<![0-9A-Fa-f]){_MAC_PATTERN}(?![0-9A-Fa-f])")
# RTX810 がクライアント ID を出すときの `(01) 00 a0 de 11 22 33` のような並び。
# 先頭の (01) は Ethernet を表す種別で、その後ろが MAC になる。
_HEX_RUN_RE = re.compile(
    r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{2}(?:[ \t]+[0-9A-Fa-f]{2})+(?![0-9A-Fa-f])"
)

# `show status dhcp` のブロック形式は `Host Name: mhf` とラベルを付ける
_HOSTNAME_RE = re.compile(
    r"(?:host\s*name|hostname|ホスト名)\s*[:=]\s*(\S+)", re.IGNORECASE
)
# `show status dhcp summary` はラベルを付けず、MAC の後ろにカンマ区切りで置く
#   1:        10.10.10.2:  b4:69:21:1f:7d:ea, mhf
_TRAILING_HOSTNAME_RE = re.compile(rf"{_MAC_PATTERN}\s*,\s*(\S+)")
# `Remaining lease: 2days 16hours 3min. 50secs.` — 絶対時刻ではなく残り時間で出る
_REMAINING_LABEL_RE = re.compile(r"remaining\s*lease|残り(?:時間|リース)", re.IGNORECASE)
_DURATION_RE = re.compile(r"(\d+)\s*(day|hour|min|sec)", re.IGNORECASE)
_UNIT_SECONDS = {"day": 86400, "hour": 3600, "min": 60, "sec": 1}

# `show arp` の実機出力:
# LAN1(port1)    10.10.10.2        b4:69:21:1f:7d:ea 1157
_ARP_RE = re.compile(
    rf"^(?P<interface>\S+)\s+(?P<ip>{_IPV4_RE.pattern})\s+"
    rf"(?P<mac>{_MAC_PATTERN})\s+(?P<ttl>\d+|permanent)$",
    re.IGNORECASE,
)

# ホスト名の末尾に付いてくる区切り文字
_HOSTNAME_TRAILING = ",、)）\"'"


def normalize_mac(raw: str) -> str:
    """MAC アドレスを大文字ハイフン区切りに揃える（JSON のキーになるため）。

    区切りはコロン・ハイフン・空白のいずれでもよい。
    """
    hexdigits = re.sub(r"[^0-9A-Fa-f]", "", raw)
    if len(hexdigits) != 12:
        raise ValueError(f"MAC アドレスとして読めません: {raw!r}")
    upper = hexdigits.upper()
    return "-".join(upper[i : i + 2] for i in range(0, 12, 2))


def find_mac(line: str) -> str | None:
    """行から MAC アドレスを 1 つ取り出す。無ければ None。"""
    match = _MAC_RE.search(line)
    if match:
        return normalize_mac(match.group())

    for run in _HEX_RUN_RE.finditer(line):
        octets = run.group().split()
        if len(octets) == 6:
            return normalize_mac("".join(octets))
        if len(octets) > 6:
            # DUID（`(ff) ...`）など 6 バイトでないクライアント ID。
            # 下位 6 バイトが MAC とは限らないので、推測せず捨てる。
            logger.warning(
                "MAC として解釈できないクライアント ID を無視します: %s", run.group()
            )
        # 6 バイト未満は日付や時刻がたまたま並んだだけなので黙って捨てる
    return None


def parse_remaining_lease(line: str) -> timedelta | None:
    """`Remaining lease: 2days 16hours 3min. 50secs.` を timedelta に直す。

    「残り時間」という語が無い行は対象外（数字の並びを誤って拾わないため）。
    リースが無期限の場合は数字が無いので None になる。
    """
    if not _REMAINING_LABEL_RE.search(line):
        return None
    seconds = 0
    found = False
    for value, unit in _DURATION_RE.findall(line):
        seconds += int(value) * _UNIT_SECONDS[unit.lower()]
        found = True
    if not found:
        logger.warning("残りリース時間として読めない行を無視します: %s", line)
        return None
    return timedelta(seconds=seconds)


def parse_hostname(line: str) -> str | None:
    """行からホスト名を取り出す。

    `Host Name: xxx` のラベル付きと、MAC の直後に `, xxx` と置く形の両方を見る。
    """
    match = _HOSTNAME_RE.search(line) or _TRAILING_HOSTNAME_RE.search(line)
    if not match:
        return None
    hostname = match.group(1).strip().strip(_HOSTNAME_TRAILING)
    return hostname or None


def parse_dhcp_status(text: str, now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """`show status dhcp` の出力を MAC をキーにした辞書へ変換する。

    `lease_expires` はルーターが残り時間で返すので、`now` を基準に絶対時刻へ直す。
    スナップショットの `updated_at` と同じ時刻を渡すこと（収集側がそうしている）。

    キーは MAC の昇順に並べ、差分を読みやすくする。同じ MAC が複数回現れた場合は
    後勝ち（スコープをまたいで同じ端末が出ることは通常ない）。
    """
    reference = now if now is not None else datetime.now().astimezone()
    clients: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    pending_ip: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        mac = find_mac(line)
        ip_match = _IPV4_RE.search(line)

        if mac is not None:
            # 同じ行に IP が無ければ、直前の行で拾っておいたものを使う（ブロック形式）
            ip = ip_match.group() if ip_match else pending_ip
            pending_ip = None
            if ip is None:
                logger.warning("IP アドレスの取れない行を無視します: %s", line)
                current = None
                continue
            current = {"ip": ip, "hostname": None, "lease_expires": None}
            clients[mac] = current
        elif ip_match is not None:
            # ブロック形式では IP とクライアント ID が別の行に出る。直前に見えた IP を
            # 覚えておき、MAC の行に IP が無ければそれを使う（後に出たものが勝つ）。
            # スコープのヘッダ行も拾ってしまうが、レコードの直前には必ずその
            # リース自身の IP 行が来るので上書きされる。
            pending_ip = ip_match.group()
            current = None

        if current is None:
            continue
        hostname = parse_hostname(line)
        if hostname is not None:
            current["hostname"] = hostname
        remaining = parse_remaining_lease(line)
        if remaining is not None:
            current["lease_expires"] = (reference + remaining).isoformat(timespec="seconds")

    return dict(sorted(clients.items()))


def parse_arp_table(text: str) -> dict[str, dict[str, Any]]:
    """`show arp` の出力を MAC をキーにした辞書へ変換する。"""
    entries: dict[str, dict[str, Any]] = {}
    for raw_line in text.splitlines():
        match = _ARP_RE.match(raw_line.strip())
        if not match:
            continue
        ttl = match.group("ttl").lower()
        entries[normalize_mac(match.group("mac"))] = {
            "interface": match.group("interface"),
            "ttl_seconds": int(ttl) if ttl.isdigit() else None,
            "entry_type": "static" if ttl == "permanent" else "dynamic",
        }
    return dict(sorted(entries.items()))


def merge_client_sources(
    dhcp_clients: dict[str, dict[str, Any]],
    arp_entries: dict[str, dict[str, Any]],
    archer_connections: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """DHCP を母集団として ARP と Archer の情報を付加する。"""
    clients: dict[str, dict[str, Any]] = {}
    for mac, client in dhcp_clients.items():
        merged = dict(client)
        arp = arp_entries.get(mac)
        merged["arp"] = {"present": False} if arp is None else {"present": True, **arp}
        merged["connection"] = archer_connections.get(mac)
        clients[mac] = merged
    return dict(sorted(clients.items()))
