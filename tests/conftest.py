"""RTX810 の DHCP リース表示のサンプル。

**形式は実機（RTX810 Rev.11）の出力そのまま**。MAC アドレスとホスト名だけは、
自宅の端末が分かってしまうので架空の値に置き換えている。
"""

from __future__ import annotations

# `show status dhcp` — collector が実際に叩くコマンド。
# 3 台目はホスト名を送ってこない端末（実機にも 14 台中 3 台あった）。
# MAC は「(01) から始まるクライアント ID」と「Client ethernet address」の
# 両方の形で出てくる。
DHCP_STATUS = """\
DHCP Scope number: 1
      Network address: 192.168.100.0
          Leased address: 192.168.100.2
        (type) Client ID: (01) 00 a0 de 11 22 33
               Host Name: nas
         Remaining lease: 2days 16hours 3min. 50secs.
          Leased address: 192.168.100.3
 Client ethernet address: 00:a0:de:44:55:66
               Host Name: raspberrypi
         Remaining lease: 1day 4hours 5min. 6secs.
          Leased address: 192.168.100.4
        (type) Client ID: (01) ac de 48 00 11 22
         Remaining lease: 16hours 30min. 0secs.
                  All: 509
               Except: 0
               Leased: 3
               Usable: 506
"""

# `show status dhcp summary` — 同じ機種の短い表示。残り時間が出ないので採用して
# いないが、パーサはこちらも読めるようにしてある（docs/design.md 3.2）。
DHCP_SUMMARY = """\
DHCP Scope number: 1
  1:      192.168.100.2:  00:a0:de:11:22:33, nas
  2:      192.168.100.3:  00:a0:de:44:55:66, raspberrypi
  3:      192.168.100.4:  ac:de:48:00:11:22
"""
