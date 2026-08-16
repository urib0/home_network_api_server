#!/usr/bin/env bash
# ラズパイ (Debian 12) へのインストール手順をまとめたスクリプト。
# systemd のユーザーインスタンスに、実行ユーザー自身の権限で登録する。
# リポジトリをどこに置いても動く (配置場所はスクリプト自身の位置から決まる)。
#
#   ./systemd/install.sh          # sudo は不要
#
# 認証情報 (~/.config/home-network-api-server/router.env) は別途手で編集すること。
set -euo pipefail

# clone 先のディレクトリ名は環境によって変わる (リポジトリ名はアンダースコア区切り、
# ドキュメント上の既定はハイフン区切り) ため、固定せずスクリプトの位置から求める。
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
APP_DIR=$(dirname -- "$SCRIPT_DIR")

# ユニットファイルに書かれている既定パス。実際の APP_DIR へ置換して配置する。
# %h は systemd の指定子 (ユーザーのホーム) だが、clone 先は任意なので置換が必要。
UNIT_DEFAULT_DIR='%h/home-network-api-server'

XDG_CONFIG_HOME=${XDG_CONFIG_HOME:-$HOME/.config}
CONF_DIR="$XDG_CONFIG_HOME/home-network-api-server"
UNIT_DIR="$XDG_CONFIG_HOME/systemd/user"

# 収集結果の置き場。ユニット側の CLIENTS_JSON_PATH と一致させること。
# systemd の StateDirectory= / %S は使わない (user unit での解決先が
# systemd 252 では ~/.config、新しい版では ~/.local/state と食い違うため)。
STATE_DIR="$HOME/.local/state/home-network-api-server"

# root で実行すると root のホーム配下に入ってしまい、意図と食い違う
if [[ $EUID -eq 0 ]]; then
    echo "root では実行しないでください。サービスを動かしたいユーザー自身で実行します:" >&2
    echo "  ./systemd/install.sh" >&2
    exit 1
fi

if [[ ! -f $APP_DIR/pyproject.toml ]]; then
    echo "$APP_DIR がリポジトリのルートに見えません。" >&2
    echo "このスクリプトはリポジトリ内の systemd/ に置いたまま実行してください。" >&2
    exit 1
fi

# systemctl --user は XDG_RUNTIME_DIR / DBus 経由でユーザーインスタンスに繋ぐ。
# su で切り替えた直後などでは繋がらないことがあるので先に確かめる。
if ! systemctl --user show-environment >/dev/null 2>&1; then
    echo "systemd のユーザーインスタンスに接続できません。" >&2
    echo "そのユーザーで直接ログイン (ssh) してから実行してください。" >&2
    echo "  su で切り替えた場合は 'machinectl shell $USER@' などを使う。" >&2
    exit 1
fi

echo "==> 配置先: $APP_DIR"

# --- 依存関係の同期 ---
# 実行ユーザーと同じユーザーで sync するので、uv が入れる Python
# (~/.local/share/uv/python) をそのまま参照できる。置き場の調整は不要。
echo "==> 依存関係を同期"
if ! command -v uv >/dev/null 2>&1; then
    echo "uv が見つかりません。先に導入してください:" >&2
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
fi
uv sync --frozen --no-dev --directory "$APP_DIR"

# ローカル検証用の .env をうっかり置いていた場合に備えて閉じる
if [[ -f $APP_DIR/.env ]]; then
    chmod 600 "$APP_DIR/.env"
    echo "警告: $APP_DIR/.env があります。サービスは $CONF_DIR/router.env を読みます。" >&2
fi

# --- 認証情報 ---
mkdir -p "$CONF_DIR"
chmod 700 "$CONF_DIR"
if [[ ! -f $CONF_DIR/router.env ]]; then
    echo "==> $CONF_DIR/router.env を作成 (パスワードを編集してください)"
    install -m 0600 "$APP_DIR/systemd/router.env.example" "$CONF_DIR/router.env"
fi

# --- 状態ディレクトリ ---
# 収集時に storage.write_snapshot() も mkdir するが、先に作っておくと
# 初回収集前でも置き場が分かってよい。
mkdir -p "$STATE_DIR"

# 旧構成 (StateDirectory= を使っていた頃) の置き土産に気付けるようにする
if [[ -f $CONF_DIR/clients.json ]]; then
    echo "注意: $CONF_DIR/clients.json は旧構成の残骸です。" >&2
    echo "  現在の保存先は $STATE_DIR/clients.json なので削除して構いません。" >&2
fi

# --- systemd ユニット ---
# ユニット内の既定パスを実際の APP_DIR に置換して配置する
echo "==> systemd ユニットを配置"
mkdir -p "$UNIT_DIR"
for unit in home-network-collector.service home-network-collector.timer home-network-api.service; do
    sed "s|$UNIT_DEFAULT_DIR|$APP_DIR|g" "$APP_DIR/systemd/$unit" > "$UNIT_DIR/$unit"
    chmod 0644 "$UNIT_DIR/$unit"
done
systemctl --user daemon-reload

# --- ログアウト後もサービスを動かす ---
# linger が無いと、ログアウト時にユーザーインスタンスごと停止する。
if [[ $(loginctl show-user "$USER" --property=Linger --value 2>/dev/null) != yes ]]; then
    echo "==> linger を有効化 (ログアウト後も動かすため)"
    if ! loginctl enable-linger "$USER" 2>/dev/null; then
        echo "警告: linger を有効化できませんでした。手動で実行してください:" >&2
        echo "  sudo loginctl enable-linger $USER" >&2
        echo "  (これが無いと、ログアウト時にサービスが止まります)" >&2
    fi
fi

echo "==> 有効化"
systemctl --user enable --now home-network-api.service
systemctl --user enable home-network-collector.timer

# パスワードが雛形のままなら timer は起動しない (5 分ごとに失敗ログが出るだけなので)
if grep -q '^ROUTER_PASSWORD=ここに' "$CONF_DIR/router.env"; then
    cat <<EOF

インストール完了。ただしパスワードが未設定です。

  1. パスワードを設定:   \${EDITOR:-nano} $CONF_DIR/router.env
  2. 初回取得を手動実行: systemctl --user start home-network-collector.service
  3. 定期取得を開始:     systemctl --user start home-network-collector.timer
  4. 結果を確認:         curl http://localhost:8000/api/clients
  5. ログ:               journalctl --user -u home-network-collector -u home-network-api -f
EOF
else
    systemctl --user start home-network-collector.timer
    systemctl --user start home-network-collector.service
    cat <<EOF

インストール完了。

  結果を確認: curl http://localhost:8000/api/clients
  タイマー:   systemctl --user list-timers home-network-collector.timer
  ログ:       journalctl --user -u home-network-collector -u home-network-api -f
EOF
fi
