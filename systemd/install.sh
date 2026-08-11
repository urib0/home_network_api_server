#!/usr/bin/env bash
# ラズパイ (Debian 12) へのインストール手順をまとめたスクリプト。
# リポジトリを /opt/home-network-api-server に配置した状態で、リポジトリルートから実行する。
#
#   sudo ./systemd/install.sh
#
# 認証情報 (/etc/home-network-api-server/router.env) は別途手で編集すること。
set -euo pipefail

APP_DIR=/opt/home-network-api-server
CONF_DIR=/etc/home-network-api-server
SERVICE_USER=hnapi

if [[ $EUID -ne 0 ]]; then
    echo "root で実行してください: sudo $0" >&2
    exit 1
fi

if [[ ! -d $APP_DIR ]]; then
    echo "$APP_DIR がありません。リポジトリをここに配置してください。" >&2
    exit 1
fi

# --- サービス用ユーザー (ログイン不可・ホーム無し) ---
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    echo "==> ユーザー $SERVICE_USER を作成"
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

# --- 依存関係の同期 (uv は事前に入れておく: curl -LsSf https://astral.sh/uv/install.sh | sh) ---
echo "==> 依存関係を同期"
UV_BIN=$(command -v uv || echo /usr/local/bin/uv)
"$UV_BIN" sync --frozen --no-dev --directory "$APP_DIR"
chown -R root:"$SERVICE_USER" "$APP_DIR"
chmod -R o-rwx "$APP_DIR"

# --- 認証情報 ---
mkdir -p "$CONF_DIR"
if [[ ! -f $CONF_DIR/router.env ]]; then
    echo "==> $CONF_DIR/router.env を作成 (パスワードを編集してください)"
    install -o root -g "$SERVICE_USER" -m 0640 \
        "$APP_DIR/systemd/router.env.example" "$CONF_DIR/router.env"
fi

# --- systemd ユニット ---
echo "==> systemd ユニットを配置"
install -m 0644 "$APP_DIR"/systemd/home-network-collector.service /etc/systemd/system/
install -m 0644 "$APP_DIR"/systemd/home-network-collector.timer /etc/systemd/system/
install -m 0644 "$APP_DIR"/systemd/home-network-api.service /etc/systemd/system/
systemctl daemon-reload

echo "==> 有効化"
systemctl enable --now home-network-collector.timer
systemctl enable --now home-network-api.service

cat <<EOF

インストール完了。

  1. パスワードを設定:   sudoedit $CONF_DIR/router.env
  2. 初回取得を手動実行: sudo systemctl start home-network-collector.service
  3. 結果を確認:         curl http://localhost:8000/api/clients
  4. ログ:               journalctl -u home-network-collector -u home-network-api -f
EOF
