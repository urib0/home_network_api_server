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

# Debian 12 の標準 Python は 3.11 なので、uv が 3.13 をダウンロードする。
# 既定の置き場 (~/.local/share/uv/python) は sudo 実行だと /root 配下になり、
# .venv/bin/python がそこへの symlink になる。hnapi は /root (0700) を辿れず、
# サービス側の ProtectHome=true でも遮断されるため、共有パスに置く。
export UV_PYTHON_INSTALL_DIR=/opt/uv-python

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

# --- 依存関係の同期 ---
echo "==> 依存関係を同期"
UV_BIN=$(command -v uv || true)
if [[ -z $UV_BIN ]]; then
    echo "uv が見つかりません。先に導入してください:" >&2
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh" >&2
    exit 1
fi
mkdir -p "$UV_PYTHON_INSTALL_DIR"
"$UV_BIN" sync --frozen --no-dev --directory "$APP_DIR"

# hnapi がインタプリタ本体を読めるようにする (.venv/bin/python がここを指す)
chmod -R a+rX "$UV_PYTHON_INSTALL_DIR"

chown -R root:"$SERVICE_USER" "$APP_DIR"
chmod -R o-rwx "$APP_DIR"

# 起動前に、サービスユーザーで実際に実行できるかを確かめる
echo "==> サービスユーザーでの実行可否を確認"
if ! runuser -u "$SERVICE_USER" -- "$APP_DIR/.venv/bin/python" -c \
        'import home_network_api_server' 2>/dev/null; then
    echo "エラー: $SERVICE_USER が .venv を実行できません。" >&2
    echo "  $APP_DIR/.venv/bin/python の symlink 先の権限を確認してください:" >&2
    readlink -f "$APP_DIR/.venv/bin/python" >&2
    exit 1
fi

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
systemctl enable --now home-network-api.service
systemctl enable home-network-collector.timer

# パスワードが雛形のままなら timer は起動しない (5 分ごとに失敗ログが出るだけなので)
if grep -q '^ROUTER_PASSWORD=ここに' "$CONF_DIR/router.env"; then
    cat <<EOF

インストール完了。ただしパスワードが未設定です。

  1. パスワードを設定:   sudoedit $CONF_DIR/router.env
  2. 初回取得を手動実行: sudo systemctl start home-network-collector.service
  3. 定期取得を開始:     sudo systemctl start home-network-collector.timer
  4. 結果を確認:         curl http://localhost:8000/api/clients
  5. ログ:               journalctl -u home-network-collector -u home-network-api -f
EOF
else
    systemctl start home-network-collector.timer
    systemctl start home-network-collector.service
    cat <<EOF

インストール完了。

  結果を確認: curl http://localhost:8000/api/clients
  タイマー:   systemctl list-timers home-network-collector.timer
  ログ:       journalctl -u home-network-collector -u home-network-api -f
EOF
fi
