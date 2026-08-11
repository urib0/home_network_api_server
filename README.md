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
| `CLIENTS_JSON_PATH` | `/var/lib/home-network-api-server/clients.json` | 両方 | 収集結果 JSON のパス |
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

```bash
# uv を入れる（未導入なら）
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh

sudo git clone <このリポジトリ> /opt/home-network-api-server
cd /opt/home-network-api-server
sudo ./systemd/install.sh

sudo systemctl edit --full home-network-collector.service   # 必要なら間隔などを調整
sudoedit /etc/home-network-api-server/router.env            # パスワードを設定
sudo systemctl start home-network-collector.service         # 初回取得
```

確認:

```bash
systemctl list-timers home-network-collector.timer
journalctl -u home-network-collector -u home-network-api -f
curl -s http://localhost:8000/api/clients | jq
```

取得間隔は `systemd/home-network-collector.timer` の `OnUnitActiveSec` で調整する（既定 5 分）。
