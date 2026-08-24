# 設計

## 1. 目的とスコープ

自宅 LAN の端末一覧を、他のツール（ダッシュボード、在宅判定、Home Assistant など）から
HTTP で取れるようにする。

スコープ内:

- RTX810 の DHCP リースをクライアント母集団として取得し、ARP と Archer A10 の情報で補う
- MAC アドレスをキーにした JSON への上書き保存
- その JSON を返す読み取り専用 API

スコープ外（現時点）:

- 接続履歴の保存・時系列分析
- 認証・認可（LAN 内前提）
- ルーターの設定変更（本ツールは読み取りのみ）
- 有線 / 無線の区別（RTX810 は有線ルーターであり、Wi-Fi は別の AP がブリッジするので区別できない）

## 2. 全体構成

```
                 ┌───────────────────────────┐
                 │ RTX810 (192.168.100.1)    │
                 └────────────┬──────────────┘
                              │ SSH (paramiko) で対話シェルを開き
                              │ show status dhcp / show arp を実行 / 5 分ごと
        ┌─────────────────────▼──────────────────────┐
        │  home-network-collector.service (oneshot)  │
        │  ← home-network-collector.timer が起動      │
        └─────────────────────┬──────────────────────┘
                              │ os.replace による原子的な上書き
                 ┌────────────▼─────────────┐
                 │ ~/.local/state/home-     │
                 │   network-api-server/    │
                 │   clients.json           │
                 └────────────┬─────────────┘
                              │ リクエストごとに読む
        ┌─────────────────────▼──────────────────────┐
        │  home-network-api.service (常駐)            │
        │  FastAPI + uvicorn  :8000                  │
        └─────────────────────┬──────────────────────┘
                              │ GET /api/clients
                       LAN 内のクライアント
```

### なぜ 2 プロセスに分けるか

| 論点 | 分離した場合 |
| --- | --- |
| 応答速度 | API はローカルファイルを読むだけ。ルーターへの SSH ログイン（1〜数秒）を待たない |
| ルーターへの負荷 | API のリクエスト数に関係なく、ログインは 5 分に 1 回で一定 |
| 障害の切り分け | ルーターが落ちても API は最後の内容を返す。`updated_at` で鮮度が判断できる |
| 認証情報の範囲 | ルーターのパスワードは collector 側にしか渡らない。API サービスは `EnvironmentFile` を持たない |

## 3. データ設計

### 3.1 どのコマンドを使うか

RTX810 から「LAN に居る端末」を知る方法は主に 2 つあり、意味が違う。

| | `show arp` | DHCP リース |
| --- | --- | --- |
| 出てくる条件 | 直近にルーターと通信した（ARP エントリが生きている） | ルーターの DHCP からアドレスを借りている |
| 消える条件 | ARP エントリの寿命切れ（既定 1200 秒、`ip lan1 arp timer` 次第） | リース期限切れ（`dhcp scope` の `expire`、既定 72 時間） |
| 取れる情報 | IP / MAC / インターフェース | IP / MAC / **ホスト名** / リースの残り時間 |
| 差集合 | 固定 IP の端末はここにしか出ない | 電源を切った端末もここには残り続ける |

**端末の母集団は DHCP リース**。すべての端末を `dhcp scope bind` で MAC 固定する
運用を前提にしているため、DHCP リースをキーとし、ARP と Archer A10 の端末情報を
MAC で付加する。Archer にだけ現れる RTX810 自身は DHCP に無いため API には含めない。

代償として、この JSON は**「いま通信しているか」を表さない**。電源を切った端末も
リース期限（既定 72 時間）までは残る。

実測（2026-08-24、14 台の環境）での差:

| | 台数 |
| --- | --- |
| DHCP リースにある | 14 |
| `show arp` にある | 10 |
| DHCP にあって ARP に無い | 4（iPhone / Apple Watch / スリープ中の端末） |
| ARP にあって DHCP に無い | **0** |

逆方向が 0 なのは、すべての端末が `dhcp scope bind` で DHCP を通っているということ。
「DHCP リース＝端末一覧」という前提が実際に成立している。

一方 ARP を足しても在宅判定にはならない。両方にある 10 台の TTL は 928〜1193 秒で、
1200 秒から減っている途中だった。スリープ中の iPhone は 20 分で ARP から落ちるので、
ARP が表すのは「いま通信中か」であって「家に居るか」ではない。

### 3.1.1 `show status dhcp` と `show status dhcp summary`

DHCP リースを見るコマンドは 2 つあり、**残り時間が出る前者を採用した**。

| | `show status dhcp`（採用） | `show status dhcp summary` |
| --- | --- | --- |
| 1 リースあたり | 3〜4 行のブロック | 1 行 |
| 14 台での出力量 | 約 60 行 | 15 行 |
| ホスト名 | `Host Name: mhf` | MAC の後ろに `, mhf` |
| リースの残り時間 | `Remaining lease: 2days 16hours 3min. 50secs.` | **出ない** |

DHCP のみの構成では、残りリース時間が唯一の鮮度の手がかりになる。電源を切った端末は
更新されないので残り時間が減っていき、期限が来れば一覧から消える。出力量は 4 倍に
なるが、`console lines infinity` でページングを止めてあるので実害は無い。

パーサは両方の形式を読める（3.2）。出力量が問題になれば、collector の
`DHCP_STATUS_COMMAND` を `summary` 付きに変えるだけで切り替わる（`lease_expires` は
`null` になる）。

### 3.2 出力形式（実機で確認済み）

RTX810 Rev.11 の実機出力は次の形（MAC とホスト名は伏せてある）。

```
DHCP Scope number: 1
      Network address: 10.10.10.0
          Leased address: 10.10.10.2
        (type) Client ID: (01) 00 a0 de 11 22 33
               Host Name: nas
         Remaining lease: 2days 16hours 3min. 50secs.
          Leased address: 10.10.10.3
 Client ethernet address: 00:a0:de:44:55:66
               Host Name: raspberrypi
         Remaining lease: 1day 4hours 5min. 6secs.
                  All: 509
               Except: 0
               Leased: 14
               Usable: 495
```

読み取りで効いている点:

- **IP と MAC が別の行にある**。`Leased address:` の行に MAC は無い
- **MAC の表記が 2 通り**。`(01) 00 a0 de 11 22 33`（DHCP のクライアント ID。先頭の
  `(01)` は Ethernet を表す種別）と `Client ethernet address: 00:a0:de:44:55:66`
- **ホスト名を送ってこない端末がある**（実測で 14 台中 3 台）。その端末には
  `Host Name:` の行が出ない
- **期限は絶対時刻ではなく残り時間**
- 末尾にスコープの集計行（`All` / `Leased` など）が付く

そのため `models.parse_dhcp_status()` は桁位置に頼らず、次の規則で読む。

1. 行から MAC を探す。見つかったらそこが 1 レコードの始まり
2. IP は同じ行から取り、無ければ直前に見えた IP 行のものを使う
3. `Host Name:` と `Remaining lease:` は、レコードの開始行と以降の行から拾う
4. MAC が取れない行（スコープのヘッダ、集計行）は捨てる

MAC が JSON のキーなので、MAC の無いレコードは存在できない。ヘッダ行を弾く条件を
別に書かなくても、この規則だけで落ちる。

同じ機種の `show status dhcp summary` は 1 行 1 リースの別形式
（`10.10.10.2:  00:a0:de:11:22:33, nas`）だが、同じパーサで読める。
ホスト名がラベル無しで MAC の後ろに来る点だけを別に扱っている。

クライアント ID が 6 バイトでない場合（DUID など）は、下位 6 バイトが MAC とは
限らないので推測せず落とし、警告を journal に残す。

`uv run home-network-collector --raw` で生の出力を出せる。ファームウェアが変わって
形式が動いても、直すのは `parse_dhcp_status()` だけで済む。

### 3.3 JSON スキーマ

```json
{
  "schema_version": 3,
  "updated_at": "2026-08-24T01:40:00+09:00",
  "count": 13,
  "clients": {
    "<MAC>": {
      "ip": "192.168.100.2",
      "hostname": "nas" | null,
      "lease_expires": "2026-08-27T09:12:34+09:00" | null,
      "arp": {"present": true, "ip": "192.168.100.2", "interface": "LAN1(port1)", "ttl_seconds": 928, "entry_type": "dynamic"},
      "connection": {"medium": "wifi" | "wired", "band": "2.4ghz" | "5ghz"} | null
    }
  }
}
```

設計上の決定:

- **`clients` でラップする** — トップレベルを MAC の辞書にすると `updated_at` のような
  メタ情報を足す場所が無くなる（MAC と衝突しない保証もない）。1 段ラップすることで、
  クライアント側が「収集が止まっていないか」を判断できる。
- **`schema_version: 3`** — DHCP の情報に ARP と Archer A10 の接続種別を加える。
  `arp` と `connection` は意味が異なるため統合した `active` フラグにはしない。
- **`updated_at` はローカルタイムゾーン付き ISO 8601** — ラズパイの JST がそのまま出る。
  オフセット付きなので曖昧さは無い。
- **`lease_expires` は計算値** — ルーターは絶対時刻ではなく残り時間
  （`2days 16hours 3min. 50secs.`）を返すので、`updated_at` と同じ時刻に足して
  絶対時刻へ直す。基準時刻を `parse_dhcp_status(text, now)` の引数で渡しているのは、
  `updated_at` と `lease_expires` がずれないようにするためと、テストを決定的にするため。
  残り時間が読めなければ `null`。
- **MAC は大文字ハイフン区切りに正規化** — ルーターの表記（コロン / 空白区切り）に
  引きずられないようにする。キーは昇順ソートし、`git diff` や目視での比較をしやすくする。
- **`hostname` が取れなければ `null`** — 固定 IP を端末側で設定している場合や、
  ホスト名を送ってこない端末がこれに当たる。
- **履歴は持たない** — 毎回全上書き。中間ファイルは保存せず、collector の 1 実行で
  DHCP / ARP / Archer を取得、マージしてから原子的に保存する。

### 3.4 書き込みの原子性

API が読んでいる最中に collector が書くと、途中まで書かれた JSON を読む可能性がある。
これを避けるため、書き込みは以下の手順で行う（`storage.write_snapshot`）:

1. 同一ディレクトリに一時ファイルを作る（`tempfile.mkstemp`）
2. JSON を書いて `fsync`
3. `chmod 0644`（`mkstemp` は 0600 で作るため、API 側ユーザーが読めるようにする）
4. `os.replace` で本番パスへ差し替え（同一ファイルシステム内なので atomic）
5. 親ディレクトリを `fsync`（電源断でリネームが失われないように）

途中で失敗した場合は一時ファイルを消し、既存の JSON はそのまま残す。
「取得に失敗したら古いデータを残す」ほうが「空を返す」より有用なため。

### 3.5 0 件だったときに上書きするか

パースの結果が 0 件でも、そのまま書き込む（`updated_at` だけが進む）。

リース期限が既定 72 時間もあるので、本当に 0 件になることはまず無い。0 件は
「出力形式が想定と違う」ことの現れである可能性のほうが高い。それでも古い JSON を
残さないのは、「JSON はルーターから最後に見えたものをそのまま映す」という
単純な約束を崩したくないから。代わりに WARNING を journal に出し、
`--raw` で確認するよう促す。

## 4. ルーターとの通信

### なぜ SSH か

| 案 | 判断 |
| --- | --- |
| **SSH（採用）** | パスワードが平文で流れない。RTX810 側は `sshd service on` と `login user` が要る |
| Telnet | ルーター側の設定は要らないが、パスワードが LAN 上を平文で流れる。Python 3.13 で `telnetlib` が削除されており、どのみち自前実装が要る |
| SNMP | ログインセッションを消費せず堅いが、`ipNetToMediaTable` から取れるのは ARP 相当で、ホスト名が一切取れない |
| HTTP 管理画面 | 画面の HTML をスクレイプすることになり、ファームウェア更新で壊れやすい |

### paramiko のバージョン固定

RTX810 の SSH サーバーは `diffie-hellman-group1-sha1` / `ssh-rsa`（SHA-1 署名）といった
古いアルゴリズムしか話さない。**paramiko 5 はこれらを実装ごと削除している**ため、
5 系ではハンドシェイクの時点で繋がらない。`pyproject.toml` で `paramiko>=3.5,<5` に固定している。

将来 paramiko 4 が保守されなくなった場合は、Telnet か SNMP への切り替えを検討する。
SHA-1 の危殆化そのものは、経路が自宅 LAN 内に閉じている以上ここでは問題にしない。

### 対話シェルを使う理由

RTX810 の SSH は exec チャネル（`ssh host "コマンド"` の形）に対応しない。
`invoke_shell()` で対話シェルを開き、プロンプト（行末の `>` / `#`）が返るまで読む、という
実装になっている（`rtx.RtxSession`）。付随して以下を行う。

- セッション開始時に `console character ascii` / `console lines infinity` /
  `console columns 200` を送る。行の折り返しとページングでレコードが分断されるのを防ぐ。
  通らなくても致命的ではないので失敗は無視する。
  **`console lines 0` は `Error: Parameter out of range` で弾かれる**（実機で確認）。
  ページングを止める値は `infinity`
- それでもページャが出た場合に備えて、`---more---` を見たら空白を送る
  （`console lines` が通らない環境向けの保険。実機では `---more---` の形で出た）
- コマンドの送信は CR のみ。CR+LF だと空行がもう 1 回入力され、プロンプトが 2 つ返って
  以降の読み取りがずれる
- 出力は UTF-8 → Shift_JIS の順にデコードを試す（`console character` が通らなかった場合の保険）

### 管理者モードへ昇格しない

`show` 系は一般ユーザーモードで実行できるので、`administrator` は打たない。
このツールがルーターの設定を変更できないことを、コードだけでなく権限の側でも担保するため。
`router.env` が漏れても、そのユーザーでできるのは参照だけになる。

### ホスト鍵の扱い

`load_system_host_keys()` で `~/.ssh/known_hosts` を読み、未知の鍵は `AutoAddPolicy` で
受け入れる。既知の鍵と食い違えば例外になる。初回接続のために手で known_hosts を
用意させるほどの脅威ではない、という判断（相手は自宅 LAN 内の固定 IP）。

## 5. コンポーネント

```
src/home_network_api_server/
├── config.py     環境変数の読み取り。JSON パスの解決を collector / api で共有
├── rtx.py        RTX810 への SSH 接続とコマンド実行（paramiko 依存をここに閉じ込める）
├── models.py     show status dhcp の出力 -> JSON エントリの変換
├── storage.py    JSON の原子的な読み書き
├── collector.py  ワンショット処理のエントリポイント
└── api.py        FastAPI アプリとエントリポイント
```

依存の向き: `collector` → `rtx` / `models` / `storage` / `config`、`api` → `storage` / `config`。
**`api` は `paramiko` を import しない**ので、SSH 側が壊れても API は動く。

`models.py` は文字列を受け取って辞書を返すだけで、ネットワークに触らない。
実機の出力を貼れば、そのままテストになる。

### collector の終了コード

| コード | 意味 | systemd での扱い |
| --- | --- | --- |
| 0 | 成功 | 正常終了 |
| 1 | 一時的な失敗（ルーター無応答、タイムアウトなど） | `failed` になる。次の timer 発火で再試行 |
| 2 | 設定不備（`ROUTER_USERNAME` / `ROUTER_PASSWORD` 未設定、認証失敗） | `failed`。人間が直すまで直らない |

認証失敗（`RtxAuthError`）は再試行しても直らないので 1 ではなく 2 に分類し、
トレースバックも出さずに 1 行のエラーメッセージだけを journal に残す。

ルーターの再起動中など一時的な失敗は日常的に起きるので、collector 側では再試行しない。
5 分後の次回実行に任せる。

### `--raw`

`uv run home-network-collector --raw` は JSON に保存せず、`show status dhcp` の出力を
そのまま標準出力へ出す。ファームウェアで表示形式が変わったときに、パースを直す材料を
取るためのもの（3.2 参照）。systemd からは引数なしで起動されるので影響しない。

### API のエラー応答

ファイルが無い / 壊れている場合は **503 Service Unavailable**（404 ではない）。
「そのリソースは存在しない」のではなく「まだ準備できていない」状態であり、
クライアントは時間をおいて再試行すべきだから。

## 6. デプロイ設計

### ユーザーと配置

systemd の**ユーザーインスタンス**（`systemctl --user`）に登録し、
利用者自身の権限で動かす。専用のサービスユーザーは作らない。

| 項目 | 値 |
| --- | --- |
| 実行ユーザー | ログインユーザー本人（`sudo` 不要） |
| アプリ配置先 | ホーム配下の任意の場所（`install.sh` の位置から決まる） |
| ユニット | `~/.config/systemd/user/`（`%h/...` を実パスへ置換して配置） |
| 認証情報 | `~/.config/home-network-api-server/router.env`（`0600`） |
| 状態ファイル | `~/.local/state/home-network-api-server/clients.json` |

ユニット内では `%h`（ホーム）/ `%E`（`$XDG_CONFIG_HOME`）の指定子を使い、
ユーザー名をハードコードしない。`%h` だけは clone 先が任意なので
`install.sh` が実パスへ置換する。

**`StateDirectory=` / `%S` を使わない理由** — user unit におけるこれらの基点は
systemd のバージョンで食い違う。Debian 12（systemd 252）では `$XDG_CONFIG_HOME`
（`~/.config`）に解決され、`$XDG_STATE_HOME`（`~/.local/state`）になるのは
それより新しい版。使うと OS 更新で保存先が黙って移動しうるので、
`%h/.local/state/...` と直に書いている。ディレクトリは
`storage.write_snapshot()` が `mkdir(parents=True)` で作るため、
systemd に作らせる必要はない（`install.sh` も先回りして作る）。

**専用ユーザー（`hnapi`）+ `/opt` をやめた理由** — 当初はシステムユニットとして
`--system --no-create-home` のサービスユーザーで動かしていた。より堅い構成ではあるが、

- `ProtectHome=true` と共存できず、アプリをホーム配下に置けない
- サービスユーザーが他ユーザーのホーム（`0700`）を辿れない
- uv が入れる Python が `sudo` 実行だと `/root` 配下に落ち、`.venv/bin/python` の
  symlink 先を辿れなくなる（`UV_PYTHON_INSTALL_DIR=/opt/uv-python` での回避が必要だった）

と、インストール手順の複雑さが実際の脅威に見合わない。攻撃者が自宅 LAN 内に
到達している時点で他に守るものが多く、この 1 プロセスの分離で得られる差は小さい、
という判断で単純さを取った。

### linger

ユーザーインスタンスは既定でログアウト時に停止する。`loginctl enable-linger` で
ブート時起動・ログアウト後も継続するようにする。`install.sh` が自動で試み、
polkit に弾かれた場合のみ `sudo loginctl enable-linger $USER` を案内する。

### systemd ユニット

| ユニット | 種別 | 役割 |
| --- | --- | --- |
| `home-network-collector.service` | `Type=oneshot` | 1 回取得して終わる。timer 起動なので `[Install]` は持たない |
| `home-network-collector.timer` | timer | `OnStartupSec=1min`, `OnUnitActiveSec=5min` |
| `home-network-api.service` | `Type=exec` | uvicorn 常駐。`Restart=on-failure` |

**timer + oneshot を選んだ理由** — Python 内で `while True: sleep(300)` を回すより、
間隔の変更が `systemctl edit` で完結し、手動実行（`systemctl start ...service`）も自然にでき、
1 回の実行ごとに journal のログが区切られて追いやすい。プロセスが常駐しないぶんメモリも使わない。

**`Persistent=false`** — ラズパイが止まっていた間の実行を起動時にまとめて消化する必要はない。
欲しいのは常に「最新の 1 回」だけ。

**取得間隔 5 分** — RTX810 の同時ログインセッション数には上限があり、短すぎる間隔は
他の管理操作を妨げる。DHCP のリース期限（既定 72 時間）と比べれば十分に細かく、
実質的には「もっと粗くてもよい」側の余裕がある。

**`network-online.target` を使わない** — ユーザーインスタンスにこの target は存在しない。
API は `0.0.0.0` への bind なのでネットワーク未接続でも起動でき、collector は
初回に失敗しても 5 分後の発火で回復するため、順序付けは不要と判断した。

### ハードニング

seccomp / prctl ベースのものだけを両サービスに設定する
（`NoNewPrivileges` / `RestrictAddressFamilies` / `RestrictNamespaces` /
`RestrictRealtime` / `LockPersonality` / `MemoryDenyWriteExecute` /
`SystemCallArchitectures`）。これらは追加の準備なしにユーザーインスタンスでも効く。

一方 mount namespace ベースのもの（`ProtectSystem` / `ProtectHome` / `PrivateTmp` /
`PrivateDevices` / `ProtectKernel*` / `ReadOnlyPaths`）は外した。非特権ユーザーでは
unprivileged userns の可否など環境に依存して起動失敗を招きうるのに対し、
そもそも一般ユーザー権限では `/usr` への書き込みも他ユーザーのホームの読み取りも
できないため、上積みが小さい。

この結果、API サービスの JSON 読み取り専用化（旧 `ReadOnlyPaths`）は権限では
強制できなくなった。コード上は読むだけであり、書き込み経路を持たない。

## 7. セキュリティ上の判断

- **認証なし・LAN 内限定** — 要件どおり。ただしクライアント一覧は「誰がいつ家にいるか」を
  示しうる情報なので、ルーターのポート開放は行わない。
- **`API_HOST=0.0.0.0`** — LAN の他ホストから引くための既定値。ラズパイ内からしか使わないなら
  `127.0.0.1` に変更する。
- **ルーターのアカウントは一般ユーザー** — collector は `administrator` に昇格しないので、
  `router.env` が漏れてもルーターの設定は変えられない（4 章）。
- **パスワードの置き場** — `EnvironmentFile` で collector にのみ渡す。リポジトリには
  `.env` / `clients.json` を含めない（`.gitignore` 済み）。
- **`systemctl --user show` でのパスワード露出** — `EnvironmentFile` の内容は展開後に見える。
  ユーザーインスタンスなので他の一般ユーザーからは覗けないが、root からは見える。
- **アプリを実行ユーザー自身が書き換えられる** — 同じユーザーでログインできる者は
  どのみち任意のコードを実行できるので、実質的な差はない。

## 8. 検討したが採用しなかった案

| 案 | 不採用の理由 |
| --- | --- |
| `show arp` を併用して「いま通信中か」を出す | 今回の運用（全端末を `dhcp scope bind` で固定）では DHCP リースが端末一覧そのものになる。在宅判定が要件になったら足す |
| `show status dhcp summary` を使う | 出力は 4 分の 1 で済むが、リースの残り時間が出ない（3.1.1）。パーサは両対応にしてあるので、必要になれば 1 行で切り替わる |
| ARP の寿命切れによる出入りを収集側で吸収する | 前回の JSON を読んで猶予を持たせることになり、「履歴を持たない」設計から外れる。そもそも DHCP のみなら起きない |
| API がリクエストのたびにルーターへ問い合わせる | 応答が遅く、ルーターのセッション上限を圧迫する |
| SQLite に保存 | 履歴が不要なら JSON で足り、`jq` で直接読めるほうが運用が楽 |
| 収集を常駐サービスの内部ループにする | 間隔変更に再デプロイが要る。timer のほうが systemd に寄せられる |
| API に `/health` や `/api/clients/{mac}` を追加 | エンドポイントは 1 つに保つ。鮮度は `updated_at`、絞り込みはクライアント側でできる |
| Archer A10 と RTX810 の両対応（`ROUTER_KIND` で分岐） | ルーターを入れ替えるだけで、両方を並行運用する予定が無い。抽象化の維持費が見合わない |

## 9. 既知の制約

- **「接続中」ではなく「リースを持っている」一覧**。電源を切った端末も既定 72 時間は残る（3.1）。
- **端末側で固定 IP を設定した端末は出てこない**。ルーターの `dhcp scope bind` で予約すること。
- **有線 / 無線の区別ができない**。RTX810 は有線ルーターで、Wi-Fi の AP はブリッジとして
  ぶら下がっているため、ルーターからは全部 LAN 側の端末に見える。
- **`hostname` は端末が送ってこなければ `null`**（実測で 14 台中 3 台）。
  端末名の別名辞書を作るなら、ここを埋めることになる。
- **`lease_expires` は残り時間からの計算値**（3.3）。収集時刻を基準にしているので、
  収集の間隔ぶんの誤差がある。
- **paramiko 5 では動かない**（4 章）。
- iOS のプライベート Wi-Fi アドレスを使う端末は、MAC が変わると別クライアントとして
  数えられる。`dhcp scope bind` で固定する運用なら、その端末側で
  「プライベートアドレスを使わない」設定にしておく必要がある。
