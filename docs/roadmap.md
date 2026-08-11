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
- [ ] **実機での動作確認**（Archer A10 に対して実際に取得）

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

## フェーズ 6: ラズパイへのデプロイ（次にやること）

- [ ] ラズパイに uv を導入
- [ ] `/opt/home-network-api-server` へ配置し `install.sh` を実行
- [ ] `/etc/home-network-api-server/router.env` にパスワードを設定
- [ ] timer の発火を `systemctl list-timers` で確認
- [ ] LAN の別ホストから `curl http://<ラズパイ>:8000/api/clients` を確認
- [ ] 一晩放置して journal にエラーが溜まっていないか確認

**コミット:** `docs: ラズパイでの実運用メモを追記`

---

## 今後の候補（要件が出たら着手）

優先度は「実際に困ってから」で判断する。現時点ではどれも不要と考えている。

### 運用が安定してから検討したいもの

| 項目 | 内容 | 判断材料 |
| --- | --- | --- |
| 端末名の別名辞書 | `Unknown` や `66-37-F6-...` を「自分の iPhone」等に読み替える YAML を用意 | 実際に一覧を見て、判別できない端末がいくつ残るか |
| 収集失敗の通知 | `OnFailure=` で失敗時に通知サービスを起動 | 何日か運用して失敗頻度を見てから |
| `updated_at` の鮮度チェック | 一定時間古ければ API が 503 を返す | 「古いデータでも返る」が実害になるか次第 |
| ログレベルの環境変数化 | `LOG_LEVEL` で journal の量を調整 | journal が煩いと感じたら |

### 要件が変わったら検討するもの

| 項目 | 内容 | 前提となる要件変更 |
| --- | --- | --- |
| 接続履歴の保存 | SQLite に時系列で記録し、在宅時間の集計に使う | 「履歴はいらない」が覆ったら |
| 在宅判定エンドポイント | 特定 MAC の在/不在を返す | Home Assistant 等との連携が必要になったら |
| 認証 | APIキーまたは Basic 認証 | LAN 外からアクセスしたくなったら（先に VPN / Tailscale を検討すべき） |
| Prometheus 形式の出力 | `/metrics` で台数を expose | Grafana で可視化したくなったら |
| 複数ルーター対応 | メッシュ / AP 追加時に集約する | ネットワーク構成が変わったら |

### やらないと決めたもの

- **ルーターの設定変更 API**（再起動、WiFi のオンオフ等） — 読み取り専用に留める。
  書き込み系を足すと認証なしで公開できなくなる。
- **API からの直接取得** — 分離構成の利点を捨てることになる（`docs/design.md` 7 章）。
