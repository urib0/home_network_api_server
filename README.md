# home-network-api-server

自宅のルーター（Yamaha RTX810）の DHCP リースを母集団に、ARP と Archer A10 の
接続端末情報を補ってクライアント一覧を定期的に取得し、
LAN 内向けの REST API で返すための小さなツール群。

- **collector** — RTX810 へ SSH でログインして `show status dhcp` を実行し、JSON へ上書き保存するワンショット処理（systemd timer で定期実行）
- **api** — その JSON を読んで返すだけの HTTP サーバー（FastAPI + uvicorn）

2 つはプロセスとして完全に分離しており、共有するのは JSON ファイル 1 つだけ。
ルーターが落ちていても API は最後に取れた内容を返し続ける。

詳細は [docs/design.md](docs/design.md)、今後の予定は [docs/roadmap.md](docs/roadmap.md) を参照。

## エンドポイント

```
GET /api/clients
```

```json
{
  "schema_version": 3,
  "updated_at": "2026-08-24T01:40:00+09:00",
  "count": 2,
  "clients": {
    "00-A0-DE-11-22-33": {
      "ip": "192.168.100.2",
      "hostname": "nas",
      "lease_expires": "2026-08-27T09:12:34+09:00",
      "arp": {"present": true, "interface": "LAN1(port1)", "ttl_seconds": 928, "entry_type": "dynamic"},
      "connection": {"medium": "wifi", "band": "5ghz", "ssid": "home", "signal": -48}
    },
    "AC-DE-48-00-11-22": {
      "ip": "192.168.100.4",
      "hostname": "iPhone",
      "lease_expires": "2026-08-26T23:59:59+09:00"
    }
  },
  "sources": {"dhcp": {"status": "ok"}, "arp": {"status": "ok"}, "archer": {"status": "ok"}}
}
```

| ステータス | 意味 |
| --- | --- |
| 200 | 正常。`updated_at` で鮮度を判断する |
| 503 | まだ一度も収集されていない、または JSON が壊れている |

`hostname` は端末が DHCP でホスト名を送ってこない場合 `null` になる（実測では 14 台中 3 台）。
`lease_expires` はルーターが返す残りリース時間を `updated_at` に足した絶対時刻。
`arp` は RTX810 が保持する ARP エントリ、`connection` は Archer A10 で現在見えている
接続種別を表す。ARP は「最近の通信」、Archer は「現在の Wi-Fi / 有線接続」を示すため、
どちらも在宅判定そのものではない。Archer の設定が無い、または取得に失敗した場合は
`connection` が `null` となり、`sources.archer` に状態を記録する。

認証は無し。LAN 内からのアクセスのみを想定しているため、ルーターのポート開放はしないこと。

### 一覧に出てくる端末・出てこない端末

**RTX810 の DHCP サーバーがリースを持っている端末だけ**が並ぶ。「いま通信しているか」は見ていない。

- 端末側で固定 IP を設定した端末は、DHCP を使わないので**出てこない**
- 電源を切った端末も、リース期限（既定 72 時間）までは**出続ける**

すべての端末をルーター側の `dhcp scope bind` で固定する運用を前提にしている。
在宅判定のように「いま繋がっているか」が要るなら `show arp` を併用する必要がある
（[docs/design.md](docs/design.md) 3 章）。

## RTX810 側の設定

collector は `show` コマンドしか実行しないので、**管理者権限は不要**。
一般ユーザーでログインできれば足りる。

```
# コンソール（telnet / シリアル）から
administrator
sshd host key generate         # 初回のみ。数十秒かかる
sshd service on
login user hnapi <パスワード>   # collector 用のユーザーを作る
save
```

クライアント一覧を DHCP から取るので、各端末には MAC で IP を予約しておく:

```
dhcp scope bind 1 192.168.100.2 00:a0:de:11:22:33
save
```

確認:

```bash
ssh hnapi@192.168.100.1
# ログインしたら show status dhcp と打つ。
# RTX810 の SSH は exec チャネル（ssh host "コマンド" の形）に対応しないので、
# コマンドを引数で渡すことはできない。collector も対話シェルを開いて流し込んでいる。
```

RTX810 の SSH は古い暗号（`diffie-hellman-group1-sha1` / `ssh-rsa`）しか話さないため、
OpenSSH から手で繋ぐ場合はオプションが要ることがある:

```bash
ssh -oKexAlgorithms=+diffie-hellman-group1-sha1 -oHostKeyAlgorithms=+ssh-rsa hnapi@192.168.100.1
```

同じ理由で、collector が使う paramiko は 4 系までに固定している（5 系はこれらを削除済み）。

## 環境変数

| 変数 | 既定値 | 使う側 | 説明 |
| --- | --- | --- | --- |
| `ROUTER_HOST` | `192.168.100.1` | collector | RTX810 の IP。`http://` が付いていても落として使う |
| `ROUTER_SSH_PORT` | `22` | collector | SSH のポート |
| `ROUTER_USERNAME` | （必須） | collector | `login user` で作ったユーザー名。未設定なら終了コード 2 |
| `ROUTER_PASSWORD` | （必須） | collector | そのユーザーのパスワード。未設定なら終了コード 2 |
| `ROUTER_TIMEOUT` | `10` | collector | 接続とコマンド応答のタイムアウト秒 |
| `ARCHER_HOST` / `ARCHER_USERNAME` / `ARCHER_PASSWORD` | （任意） | collector | Archer A10 の管理画面。3 つすべてを設定すると Wi-Fi / 有線の接続種別を補う |
| `ARCHER_TIMEOUT` | `10` | collector | Archer A10 の応答タイムアウト秒 |
| `CLIENTS_JSON_PATH` | `~/.local/state/home-network-api-server/clients.json` | 両方 | 収集結果 JSON のパス。未設定時のみ `$XDG_STATE_HOME` に従う（systemd ユニットは常に明示的に渡す） |
| `API_HOST` | `0.0.0.0` | api | 待ち受けアドレス |
| `API_PORT` | `8000` | api | 待ち受けポート |

## ローカルでの実行

```bash
uv sync
cp .env.example .env      # ROUTER_USERNAME / ROUTER_PASSWORD を記入する（.env は git 管理外）

# 1 回だけ取得
set -a && source .env && set +a
uv run home-network-collector

# ルーターの生の出力を見る（パースがおかしいときの確認用）
uv run home-network-collector --raw

# API サーバー
uv run home-network-api
curl -s http://localhost:8000/api/clients | jq
```

テスト:

```bash
uv run pytest
```

## ラズパイ（Debian 12）へのインストール

systemd の**ユーザーインスタンス**に、自分のユーザー権限で登録する。`sudo` は使わない。

```bash
# 1. uv を入れる（未導入なら）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. リポジトリを配置する（場所は任意。ホーム配下ならどこでもよい）
git clone <このリポジトリ> ~/home-network-api-server
cd ~/home-network-api-server

# 3. インストール（依存同期・ユニット配置・linger 有効化・起動）
./systemd/install.sh

# 4. 認証情報を設定して定期取得を開始
${EDITOR:-nano} ~/.config/home-network-api-server/router.env
systemctl --user start home-network-collector.service   # 初回取得
systemctl --user start home-network-collector.timer     # 定期取得
```

確認:

```bash
systemctl --user list-timers home-network-collector.timer
journalctl --user -u home-network-collector -u home-network-api -f
curl -s http://localhost:8000/api/clients | jq
```

| 何 | どこ |
| --- | --- |
| アプリ本体 | clone した場所（任意） |
| ユニット | `~/.config/systemd/user/` |
| 認証情報 | `~/.config/home-network-api-server/router.env`（`0600`） |
| 収集結果 JSON | `~/.local/state/home-network-api-server/clients.json` |

配置先は `install.sh` 自身の位置から決まるので、どこに clone しても動く。
ユニット内の `%h/home-network-api-server` が実際の配置先に置換される。
所有権をいじらないので、そのまま `git pull` して `systemctl --user restart` すればよい。

paramiko は `cryptography` に依存する。64bit の Raspberry Pi OS（aarch64）には wheel が
あるのでそのまま入るが、32bit（armv7l）だと Rust でのビルドが走る。その場合は
`sudo apt install python3-dev libssl-dev pkg-config` と Rust ツールチェーンが要る。

### linger（ログアウトしても動かす）

systemd のユーザーインスタンスは既定でログアウト時に停止する。`install.sh` は
`loginctl enable-linger` を試みるが、polkit に弾かれた場合は手動で:

```bash
sudo loginctl enable-linger $USER
```

これが唯一 `sudo` を要する箇所（環境によっては不要）。有効になっていれば
ブート時に SSH ログイン無しでサービスが立ち上がる。

### root で動かさないことの影響

一般ユーザー権限で動かすため、`ProtectSystem` / `ProtectHome` / `PrivateTmp` /
`ReadOnlyPaths` といった mount namespace ベースのハードニングは外してある
（ユーザーインスタンスでは環境次第で起動失敗するリスクがあり、得られるものが小さい）。
`NoNewPrivileges` や `RestrictAddressFamilies` など seccomp / prctl ベースのものは残している。

副作用として、collector と api が同一ユーザーで動くので、API 側から JSON への
書き込みを権限で禁じることはできない（コード上は読むだけ）。

取得間隔は `systemd/home-network-collector.timer` の `OnUnitActiveSec` で調整する（既定 5 分）。

## うまく取れないとき

```bash
uv run home-network-collector --raw
```

`show status dhcp` の出力がそのまま出る。RTX810 Rev.11 の実機で確認した形式に
合わせてあるが、ファームウェアで変わりうるので、見慣れない形をしていたら
`src/home_network_api_server/models.py` の `parse_dhcp_status()` を直す。
パースの入口はこの関数 1 つだけに閉じてある。

| 症状 | 見るところ |
| --- | --- |
| `SSH ログインに失敗しました` | `login user` の設定と `ROUTER_USERNAME` / `ROUTER_PASSWORD` |
| `SSH 接続に失敗しました: ... kex` | paramiko が 5 系になっていないか（`uv sync` し直す） |
| `プロンプトが返りませんでした` | `ROUTER_TIMEOUT` を伸ばす。RTX810 の同時ログイン数の上限にかかっていないか |
| `DHCP リースが 1 件も読み取れませんでした` | `--raw` で出力を確認する |
