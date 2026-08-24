# ロードマップ

## フェーズ 0: プロジェクト基盤 ✅

- [x] uv でプロジェクト初期化（Python 3.13, `uv_build`）
- [x] `.gitignore`（`.env` と `clients.json` を除外）
- [x] `.env.example` で必要な環境変数を明示

**コミット:** `chore: uv でプロジェクトを初期化`

## フェーズ 1: 変換ロジックと保存 ✅

- [x] `config.py` — 環境変数の読み取り、JSON パスの共有
- [x] `models.py` — `Connection` を `type` / `band` / `guest` に分解、MAC 正規化
- [x] `storage.py` — `os.replace` による原子的な上書き保存
- [x] 単体テスト（実データ `output.txt` の値を使った変換テスト）

**コミット:** `feat: クライアント情報の変換と JSON 保存を実装`

## フェーズ 2: 定期取得（collector） ✅

- [x] `collector.py` — ログイン → `get_status()` → 保存 → ログアウト
- [x] 失敗時は非ゼロ終了し、既存 JSON を残す
- [x] 例外時も必ずログアウトする（セッション枯渇を防ぐ）
- [x] `TplinkRouterProvider` をスタブ化したテスト
- [x] ~~実機での動作確認（Archer A10）~~ — ルーターを RTX810 に入れ替えたため破棄

**コミット:** `feat: ルーターからクライアント一覧を取得するワンショット処理を追加`

## フェーズ 3: API サーバー ✅

- [x] FastAPI で `GET /api/clients` のみ実装
- [x] 未収集・破損時は 503
- [x] `TestClient` による E2E テスト
- [x] `api` は `tplinkrouterc6u` に依存しない構成

**コミット:** `feat: クライアント一覧を返す API サーバーを追加`

## フェーズ 4: systemd ✅

- [x] `home-network-collector.service`（oneshot）
- [x] `home-network-collector.timer`（5 分間隔）
- [x] `home-network-api.service`（常駐 + `Restart=on-failure`）
- [x] ハードニング設定
- [x] `install.sh` によるインストール手順の自動化

**コミット:** `feat: systemd の service / timer ファイルを追加`

## フェーズ 5: ドキュメント ✅

- [x] README（エンドポイント仕様、環境変数、インストール手順）
- [x] `docs/design.md`（設計判断と不採用案）
- [x] `docs/roadmap.md`（このファイル）

**コミット:** `docs: 設計ドキュメントとロードマップを追加`

## フェーズ 6: RTX810 への移行 ✅

ルーターを TP-Link Archer A10 から Yamaha RTX810 へ入れ替えたことによる書き換え。

- [x] `tplinkrouterc6u` を外し、`paramiko>=3.5,<5` へ差し替え（5 系は RTX810 と繋がらない）
- [x] `rtx.py` — SSH の対話シェルでコマンドを実行する層を新設
- [x] `models.py` — `show status dhcp` のパーサに書き換え
- [x] JSON スキーマを v2 へ（`type` / `band` / `guest` を落とし、`lease_expires` を追加）
- [x] `--raw` で生の出力を見られるようにする
- [x] `ROUTER_USERNAME` を必須化、`ROUTER_SSH_PORT` を追加
- [x] テスト・README・設計ドキュメントを RTX810 前提に更新
- [x] **実機（RTX810 Rev.11）の出力でパーサを確定** — 14 台・ホスト名 11 件・
      `lease_expires` 14 件が取れることを確認。テストのサンプルは実機の形式に
      合わせ、MAC とホスト名だけ架空の値にしてある
- [x] `console lines 0` が `Error: Parameter out of range` になるのを `infinity` へ修正
- [x] paramiko の INFO ログを抑制（5 分ごとに 2 行 journal に溜まるため）

**コミット:** `feat: 収集先を RTX810 の DHCP リースに変更`

## フェーズ 7: ラズパイへのデプロイ（次にやること）

- [ ] RTX810 側の準備（`sshd host key generate` / `sshd service on` / `login user`）
- [ ] 全端末を `dhcp scope bind` で MAC 固定する
- [ ] ラズパイに uv を導入
- [ ] ホーム配下へ clone し `./systemd/install.sh` を実行（`sudo` 不要）
- [ ] `~/.config/home-network-api-server/router.env` に SSH の認証情報を設定
- [ ] `uv run home-network-collector --raw` で出力形式を確認する
- [ ] `loginctl show-user $USER --property=Linger` が `yes` か確認
- [ ] timer の発火を `systemctl --user list-timers` で確認
- [ ] 一度再起動し、SSH ログイン無しでサービスが上がるか確認
- [ ] LAN の別ホストから `curl http://<ラズパイ>:8000/api/clients` を確認
- [ ] 一晩放置して journal にエラーが溜まっていないか確認

**コミット:** `docs: ラズパイでの実運用メモを追記`

---

## 今後の候補（要件が出たら着手）

優先度は「実際に困ってから」で判断する。現時点ではどれも不要と考えている。

### 運用が安定してから検討したいもの

| 項目 | 内容 | 判断材料 |
| --- | --- | --- |
| 端末名の別名辞書 | ホスト名を送ってこない端末を「自分の iPhone」等に読み替える YAML を用意 | 実際に一覧を見て、判別できない端末がいくつ残るか |
| 収集失敗の通知 | `OnFailure=` で失敗時に通知サービスを起動 | 何日か運用して失敗頻度を見てから |
| `updated_at` の鮮度チェック | 一定時間古ければ API が 503 を返す | 「古いデータでも返る」が実害になるか次第 |
| ログレベルの環境変数化 | `LOG_LEVEL` で journal の量を調整 | journal が煩いと感じたら |

### 要件が変わったら検討するもの

| 項目 | 内容 | 前提となる要件変更 |
| --- | --- | --- |
| 接続履歴の保存 | SQLite に時系列で記録し、在宅時間の集計に使う | 「履歴はいらない」が覆ったら |
| `show arp` の併用 | 実装済み。ARP の TTL を `arp` として返す | `active` へ統合せず、「最近通信したか」として扱う |
| 在宅判定エンドポイント | 特定 MAC の在/不在を返す | Home Assistant 等との連携が必要になったら |
| 認証 | APIキーまたは Basic 認証 | LAN 外からアクセスしたくなったら（先に VPN / Tailscale を検討すべき） |
| Prometheus 形式の出力 | `/metrics` で台数を expose | Grafana で可視化したくなったら |
| 複数ルーター対応 | メッシュ / AP 追加時に集約する | ネットワーク構成が変わったら |

### やらないと決めたもの

- **ルーターの設定変更 API**（再起動、WiFi のオンオフ等） — 読み取り専用に留める。
  書き込み系を足すと認証なしで公開できなくなる。
- **API からの直接取得** — 分離構成の利点を捨てることになる（`docs/design.md` 8 章）。
- **Archer A10 を収集元として再導入** — DHCP の母集団は RTX810 に固定し、Archer は
  接続種別を補う読み取り専用の情報源に限定しているため、旧来のルーター抽象化は復活させない。
