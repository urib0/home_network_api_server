# 設計

## 1. 目的とスコープ

自宅 LAN の「いま誰が繋がっているか」を、他のツール（ダッシュボード、在宅判定、Home Assistant など）から
HTTP で取れるようにする。

スコープ内:

- TP-Link Archer A10 からのクライアント一覧取得
- MAC アドレスをキーにした JSON への上書き保存
- その JSON を返す読み取り専用 API

スコープ外（現時点）:

- 接続履歴の保存・時系列分析
- 認証・認可（LAN 内前提）
- ルーターの設定変更（本ツールは読み取りのみ）
- 複数ルーター / メッシュ構成

## 2. 全体構成

```
                 ┌──────────────────────────┐
                 │  Archer A10 (192.168.0.1)│
                 └────────────┬─────────────┘
                              │ HTTP (tplinkrouterc6u)
                              │ 5 分ごと
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
| 応答速度 | API はローカルファイルを読むだけ。ルーターへの HTTP 往復（数百 ms〜数秒）を待たない |
| ルーターへの負荷 | API のリクエスト数に関係なく、取得は 5 分に 1 回で一定 |
| 障害の切り分け | ルーターが落ちても API は最後の内容を返す。`updated_at` で鮮度が判断できる |
| 認証情報の範囲 | ルーターのパスワードは collector 側にしか渡らない。API サービスは `EnvironmentFile` を持たない |

## 3. データ設計

### 3.1 ルーターから取れるもの

`tplinkrouterc6u` の `router.get_status()` は `Status` を返し、`status.devices` に `Device` のリストが入る。
Archer A10 では `TPLinkVR400v2Client` が選択され、実測で 13 台・以下のフィールドが有効だった。

| Device のフィールド | Archer A10 での実測 |
| --- | --- |
| `type` | `Connection.HOST_2G` / `HOST_5G` / `WIRED` が出現 |
| `macaddr` | `66-37-F6-1F-BF-8B` 形式（大文字ハイフン区切り） |
| `ipaddr` | `192.168.0.102` |
| `hostname` | `MacBookPro` など。不明な端末は `Unknown` |
| `packets_sent` / `packets_received` | 無線は有効、有線は `None` のことがある |
| `signal` / `tx_rate` / `rx_rate` / `online_time` / `ssid` | すべて `None`（この機種では取れない） |

`signal` や `ssid` が取れないため、電波強度ベースの機能は将来的にも作れない。

### 3.2 type の分解

`Connection` は「接続元ネットワーク」と「周波数帯」を 1 つの値に混ぜている
（`host_2g`, `host_5g`, `host_6g`, `guest_2g`, …, `iot_2g`, …, `wired`, `unknown`）。
要件は「有線 / 無線」なので、これを 3 つのフィールドに分解して保持する。

| Connection | `type` | `band` | `guest` |
| --- | --- | --- | --- |
| `wired` | `wired` | `null` | `false` |
| `host_2g` / `iot_2g` | `wireless` | `2.4G` | `false` |
| `host_5g` / `iot_5g` | `wireless` | `5G` | `false` |
| `host_6g` / `iot_6g` | `wireless` | `6G` | `false` |
| `guest_2g` | `wireless` | `2.4G` | `true` |
| `guest_5g` | `wireless` | `5G` | `true` |
| `guest_6g` | `wireless` | `6G` | `true` |
| 上記以外 / `unknown` | `unknown` | `null` | `false` |

未知の値でも例外にせず `unknown` として残す。ライブラリ側に新しい `Connection` が増えても
収集全体が落ちないようにするため。

### 3.3 JSON スキーマ

```json
{
  "schema_version": 1,
  "updated_at": "2026-08-12T01:40:00+09:00",
  "count": 13,
  "clients": {
    "<MAC>": {
      "type": "wired" | "wireless" | "unknown",
      "band": "2.4G" | "5G" | "6G" | null,
      "guest": true | false,
      "ip": "192.168.0.102",
      "hostname": "MacBookPro" | null
    }
  }
}
```

設計上の決定:

- **`clients` でラップする** — 要件は「MAC をキーにした辞書」だが、トップレベルを MAC の辞書にすると
  `updated_at` のようなメタ情報を足す場所が無くなる（MAC と衝突しない保証もない）。
  1 段ラップすることで、クライアント側が「収集が止まっていないか」を判断できる。
- **`schema_version`** — 将来フィールドを増やしたときに、古い読み手が気づけるようにする。
- **`updated_at` はローカルタイムゾーン付き ISO 8601** — ラズパイの JST がそのまま出る。オフセット付きなので曖昧さは無い。
- **MAC は大文字ハイフン区切りに正規化** — キーの表記揺れで別端末に見えるのを防ぐ。キーは昇順ソートし、
  `git diff` や目視での比較をしやすくする。
- **`hostname` が空文字なら `null`** — 「取れなかった」ことを型で表す。ルーターが返す `"Unknown"` は
  ルーター側の表現なのでそのまま残す（実際に `Unknown` という名前の端末と区別できないため加工しない）。
- **履歴は持たない** — 要件どおり毎回全上書き。`active` が `false` の端末も含め、
  `get_status()` が返したものをそのまま反映する。

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

## 4. コンポーネント

```
src/home_network_api_server/
├── config.py     環境変数の読み取り。JSON パスの解決を collector / api で共有
├── models.py     Device -> JSON エントリの変換（ルーター依存を閉じ込める）
├── storage.py    JSON の原子的な読み書き
├── collector.py  ワンショット処理のエントリポイント
└── api.py        FastAPI アプリとエントリポイント
```

依存の向き: `collector` → `models` / `storage` / `config`、`api` → `storage` / `config`。
**`api` は `tplinkrouterc6u` を import しない** ので、ルーター側のライブラリが壊れても API は動く。

### collector の終了コード

| コード | 意味 | systemd での扱い |
| --- | --- | --- |
| 0 | 成功 | 正常終了 |
| 1 | 一時的な失敗（ルーター無応答、タイムアウトなど） | `failed` になる。次の timer 発火で再試行 |
| 2 | 設定不備（`ROUTER_PASSWORD` 未設定、認証失敗） | `failed`。人間が直すまで直らない |

認証失敗（`AuthorizeError`）は再試行しても直らないので 1 ではなく 2 に分類し、
トレースバックも出さずに 1 行のエラーメッセージだけを journal に残す。

ルーターの再起動中など一時的な失敗は日常的に起きるので、collector 側では再試行しない。
5 分後の次回実行に任せる。

### API のエラー応答

ファイルが無い / 壊れている場合は **503 Service Unavailable**（404 ではない）。
「そのリソースは存在しない」のではなく「まだ準備できていない」状態であり、
クライアントは時間をおいて再試行すべきだから。

## 5. デプロイ設計

### ユーザーと配置

systemd の**ユーザーインスタンス**（`systemctl --user`）に登録し、
利用者自身の権限で動かす。専用のサービスユーザーは作らない。

| 項目 | 値 |
| --- | --- |
| 実行ユーザー | ログインユーザー本人（`sudo` 不要） |
| アプリ配置先 | ホーム配下の任意の場所（`install.sh` の位置から決まる） |
| ユニット | `~/.config/systemd/user/`（`%h/...` を実パスへ置換して配置） |
| 認証情報 | `~/.config/home-network-api-server/router.env`（`0600`） |
| 状態ファイル | `~/.local/state/home-network-api-server/clients.json`（`StateDirectory=` で systemd が作成） |

ユニット内では `%h`（ホーム）/ `%E`（`~/.config`）/ `%S`（`~/.local/state`）の
指定子を使い、ユーザー名をハードコードしない。`%h` だけは clone 先が任意なので
`install.sh` が実パスへ置換する。

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

**取得間隔 5 分** — Archer A10 の管理画面へのログインは同時セッション数に上限があり、
短すぎる間隔は他の管理操作を妨げる。DHCP リース時間（実測で 2 時間前後）と比べても十分に細かい。

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

## 6. セキュリティ上の判断

- **認証なし・LAN 内限定** — 要件どおり。ただしクライアント一覧は「誰がいつ家にいるか」を示す情報なので、
  ルーターのポート開放は行わない。外部公開が必要になった場合は 7 章を参照。
- **`API_HOST=0.0.0.0`** — LAN の他ホストから引くための既定値。ラズパイ内からしか使わないなら
  `127.0.0.1` に変更する。
- **パスワードの置き場** — `EnvironmentFile` で collector にのみ渡す。リポジトリには
  `.env` / `clients.json` を含めない（`.gitignore` 済み）。
- **`systemctl --user show` でのパスワード露出** — `EnvironmentFile` の内容は展開後に見える。
  ユーザーインスタンスなので他の一般ユーザーからは覗けないが、root からは見える。
- **アプリを実行ユーザー自身が書き換えられる** — システムユニット時代は `/opt` を
  root 所有にできたが、いまはサービスを動かすユーザー自身がコードを差し替えられる。
  同じユーザーでログインできる者はどのみち任意のコードを実行できるので、実質的な差はない。

## 7. 検討したが採用しなかった案

| 案 | 不採用の理由 |
| --- | --- |
| API がリクエストのたびにルーターへ問い合わせる | 応答が遅く、ルーターのセッション上限を圧迫する。要件でも分離が指定されている |
| SQLite に保存 | 履歴が不要なら JSON で足り、`jq` で直接読めるほうが運用が楽 |
| 収集を常駐サービスの内部ループにする | 間隔変更に再デプロイが要る。timer のほうが systemd に寄せられる |
| API に `/health` や `/api/clients/{mac}` を追加 | 要件は「エンドポイント 1 つ」。鮮度は `updated_at`、絞り込みはクライアント側でできる |
| ゲスト / IoT を `type` に含める | 「有線 / 無線」という要件から外れる。`band` と `guest` に分けて情報は保持している |
| DHCP リース情報（`get_ipv4_dhcp_leases()`）も混ぜる | 現在接続中でない端末も含まれ、「接続中の一覧」の意味がぼやける |

## 8. 既知の制約

- Archer A10 は `signal` / `ssid` / `online_time` を返さないため、電波強度や接続先 SSID は取得できない。
- ファームウェア更新でルーターの API が変わると `tplinkrouterc6u` 側の対応待ちになる。
  その間 collector は失敗し続けるが、API は最後の JSON を返し続ける（`updated_at` が古くなる）。
- 同じ端末が Wi-Fi と有線を行き来すると MAC が変わるため、別クライアントとして数えられる。
  iOS のプライベート Wi-Fi アドレスも同様（`66-37-F6-...` のようなローカル管理 MAC がそれ）。
