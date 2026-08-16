# home-network-api-server

自宅の WiFi ルーター（TP-Link Archer A10）に接続中のクライアント一覧を定期的に取得し、
LAN 内向けの REST API で返すための小さなツール群。

- **collector** — ルーターへログインしてクライアント一覧を取得し、JSON へ上書き保存するワンショット処理（systemd timer で定期実行）
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
  "schema_version": 1,
  "updated_at": "2026-08-12T01:40:00+09:00",
  "count": 2,
  "clients": {
    "66-37-F6-1F-BF-8B": {
      "type": "wireless",
      "band": "5G",
      "guest": false,
      "ip": "192.168.0.102",
      "hostname": "MacBookPro"
    },
    "A0-66-10-0F-86-27": {
      "type": "wired",
      "band": null,
      "guest": false,
      "ip": "192.168.0.54",
      "hostname": "mhf"
    }
  }
}
```

| ステータス | 意味 |
| --- | --- |
| 200 | 正常。`updated_at` で鮮度を判断する |
| 503 | まだ一度も収集されていない、または JSON が壊れている |

認証は無し。LAN 内からのアクセスのみを想定しているため、ルーターのポート開放はしないこと。

## 環境変数

| 変数 | 既定値 | 使う側 | 説明 |
| --- | --- | --- | --- |
| `ROUTER_HOST` | `http://192.168.0.1` | collector | ルーターの URL。スキーム省略時は `http://` を補う |
| `ROUTER_USERNAME` | `admin` | collector | 管理ユーザー名 |
| `ROUTER_PASSWORD` | （必須） | collector | 管理パスワード。未設定なら終了コード 2 |
| `ROUTER_TIMEOUT` | `10` | collector | HTTP タイムアウト秒 |
| `CLIENTS_JSON_PATH` | `~/.local/state/home-network-api-server/clients.json` | 両方 | 収集結果 JSON のパス。未設定時のみ `$XDG_STATE_HOME` に従う（systemd ユニットは常に明示的に渡す） |
| `API_HOST` | `0.0.0.0` | api | 待ち受けアドレス |
| `API_PORT` | `8000` | api | 待ち受けポート |

## ローカルでの実行

```bash
uv sync
cp .env.example .env      # ROUTER_PASSWORD を記入する（.env は git 管理外）

# 1 回だけ取得
set -a && source .env && set +a
uv run home-network-collector

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

# 4. パスワードを設定して定期取得を開始
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
