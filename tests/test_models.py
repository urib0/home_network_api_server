"""`show status dhcp` の出力を変換するテスト。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from home_network_api_server.models import (
    find_mac,
    normalize_mac,
    merge_client_sources,
    parse_arp_table,
    parse_dhcp_status,
    parse_hostname,
    parse_remaining_lease,
)

from .conftest import ARP_TABLE, DHCP_STATUS, DHCP_SUMMARY

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 8, 24, 14, 0, 0, tzinfo=JST)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("00:a0:de:11:22:33", "00-A0-DE-11-22-33"),
        ("00-a0-de-11-22-33", "00-A0-DE-11-22-33"),
        ("00 a0 de 11 22 33", "00-A0-DE-11-22-33"),
        ("  AC-DE-48-00-11-22 ", "AC-DE-48-00-11-22"),
    ],
)
def test_normalize_mac(raw, expected):
    assert normalize_mac(raw) == expected


def test_normalize_mac_は桁数が違えばValueError():
    with pytest.raises(ValueError):
        normalize_mac("00:a0:de:11:22")


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (" Client ethernet address: 00:a0:de:11:22:33", "00-A0-DE-11-22-33"),
        ("        (type) Client ID: (01) 00 a0 de 11 22 33", "00-A0-DE-11-22-33"),
        ("  1:      192.168.100.2:  00:a0:de:11:22:33, nas", "00-A0-DE-11-22-33"),
        ("         Remaining lease: 2days 16hours 3min. 50secs.", None),
        ("          Leased address: 192.168.100.2", None),
        ("                  All: 509", None),
    ],
)
def test_find_mac(line, expected):
    assert find_mac(line) == expected


def test_find_mac_は長すぎるクライアントIDを捨てる(caplog: pytest.LogCaptureFixture):
    # DUID は下位 6 バイトが MAC とは限らないので、推測せずに落とす
    assert find_mac("(type) Client ID: (ff) 00 01 02 03 04 05 06 07") is None
    assert "MAC として解釈できない" in caplog.text


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Remaining lease: 2days 16hours 3min. 50secs.", timedelta(days=2, hours=16, minutes=3, seconds=50)),
        ("Remaining lease: 1day 4hours 5min. 6secs.", timedelta(days=1, hours=4, minutes=5, seconds=6)),
        ("Remaining lease: 16hours 30min. 0secs.", timedelta(hours=16, minutes=30)),
        # 「残り時間」の語が無ければ対象外（数字の並びを誤って拾わない）
        ("Leased address: 192.168.100.2", None),
        ("Host Name: 2days", None),
    ],
)
def test_parse_remaining_lease(line, expected):
    assert parse_remaining_lease(line) == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("               Host Name: nas", "nas"),
        ("ホスト名: nas", "nas"),
        # summary 形式は MAC の後ろにカンマ区切りでホスト名を置く
        ("  1:      192.168.100.2:  00:a0:de:11:22:33, nas", "nas"),
        ("  3:      192.168.100.4:  ac:de:48:00:11:22", None),
        ("                  All: 509", None),
    ],
)
def test_parse_hostname(line, expected):
    assert parse_hostname(line) == expected


def test_parse_dhcp_status():
    clients = parse_dhcp_status(DHCP_STATUS, NOW)

    assert list(clients) == ["00-A0-DE-11-22-33", "00-A0-DE-44-55-66", "AC-DE-48-00-11-22"]
    assert clients["00-A0-DE-11-22-33"] == {
        "ip": "192.168.100.2",
        "hostname": "nas",
        # 2026-08-24 14:00:00 + 2days 16h 3m 50s
        "lease_expires": "2026-08-27T06:03:50+09:00",
    }
    # Client ethernet address 形式でも同じように読める
    assert clients["00-A0-DE-44-55-66"]["hostname"] == "raspberrypi"
    # ホスト名を送ってこない端末
    assert clients["AC-DE-48-00-11-22"]["hostname"] is None
    assert clients["AC-DE-48-00-11-22"]["lease_expires"] == "2026-08-25T06:30:00+09:00"


def test_parse_dhcp_status_はIPをブロックの上の行から拾う():
    # 「Leased address:」の行に MAC は無い。直前に見えた IP を使う
    clients = parse_dhcp_status(DHCP_STATUS, NOW)
    assert clients["00-A0-DE-44-55-66"]["ip"] == "192.168.100.3"


def test_parse_dhcp_status_はヘッダと集計行を拾わない():
    # Network address / All / Leased などは MAC が無いので落ちる
    assert len(parse_dhcp_status(DHCP_STATUS, NOW)) == 3


def test_parse_dhcp_status_はsummary形式も読める():
    clients = parse_dhcp_status(DHCP_SUMMARY, NOW)

    assert list(clients) == ["00-A0-DE-11-22-33", "00-A0-DE-44-55-66", "AC-DE-48-00-11-22"]
    assert clients["00-A0-DE-11-22-33"]["ip"] == "192.168.100.2"
    assert clients["00-A0-DE-11-22-33"]["hostname"] == "nas"
    # summary は残り時間を出さない
    assert all(v["lease_expires"] is None for v in clients.values())


def test_parse_dhcp_status_はnow省略時に現在時刻を使う():
    clients = parse_dhcp_status(DHCP_STATUS)
    expires = datetime.fromisoformat(clients["00-A0-DE-11-22-33"]["lease_expires"])
    assert timedelta(days=2) < expires - datetime.now().astimezone() < timedelta(days=3)


def test_parse_dhcp_status_空でも壊れない():
    assert parse_dhcp_status("", NOW) == {}


def test_parse_arp_table():
    entries = parse_arp_table(ARP_TABLE)
    assert entries["00-A0-DE-11-22-33"] == {
        "ip": "192.168.100.2",
        "interface": "LAN1(port1)",
        "ttl_seconds": 1157,
        "entry_type": "dynamic",
    }
    assert entries["00-A0-DE-44-55-66"]["entry_type"] == "static"
    assert entries["00-A0-DE-44-55-66"]["ttl_seconds"] is None


def test_merge_client_sources_はDHCPを母集団にする():
    clients = merge_client_sources(
        {"00-A0-DE-11-22-33": {"ip": "192.168.100.2"}},
        {"00-A0-DE-11-22-33": {"ip": "192.168.100.9", "ttl_seconds": 10}},
        {
            "00-A0-DE-11-22-33": {"medium": "wifi", "band": "5ghz"},
            "AA-BB-CC-DD-EE-FF": {"medium": "wired"},
        },
    )
    assert list(clients) == ["00-A0-DE-11-22-33"]
    assert clients["00-A0-DE-11-22-33"]["ip"] == "192.168.100.9"
    assert clients["00-A0-DE-11-22-33"]["arp"] == {
        "present": True,
        "ip": "192.168.100.9",
        "ttl_seconds": 10,
    }
    assert clients["00-A0-DE-11-22-33"]["connection"] == {
        "medium": "wifi",
        "band": "5ghz",
    }
