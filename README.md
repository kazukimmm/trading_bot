# 米国株 自動売買システム（Dual Momentum / 資産保全特化）

> 「負けにくさ」を最優先に設計した、米国ETFの**フルオート売買ボット**。
> ルールベースのデュアルモメンタム戦略を、月1回のリバランスで淡々と実行します。
> バックテスト → ペーパートレード → クラウド常駐 → モバイル可視化 までを一気通貫で実装した個人開発プロジェクトです。

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Alpaca](https://img.shields.io/badge/Broker-Alpaca-FFD43B)
![Tests](https://img.shields.io/badge/tests-pytest-0A9EDC)
![License](https://img.shields.io/badge/license-MIT-green)

> ⚠️ **免責事項**: 本ソフトウェアは教育・研究目的です。投資は自己責任で行ってください。過去のバックテスト結果は将来の成績を保証しません。必ずペーパートレードで十分に検証してから運用してください。

---

## 📌 プロジェクト概要

「投資初心者でも、感情に左右されず・ほったらかしで・大きく負けない運用ができないか？」という課題から出発した自動売買システムです。

- **戦略はAI/機械学習ではなく、検証可能なルールベース**（Gary Antonacci の Dual Momentum）。再現性と説明可能性を重視。
- **取引・監視・通知・可視化を完全自動化**。人間の役割は「最終的な設定変更の判断」だけ。
- **段階的な安全設計**：バックテストで検証 → ペーパー（仮想資金）で運用 → 問題なければ少額の本番へ、という移行フローを前提に構築。

---

## ✨ 主な機能

| カテゴリ | 機能 |
| --- | --- |
| 🤖 **自動売買** | 月次リバランスをスケジューラで自動実行（米国東部時間ベース）。発注前バリデーション付き。 |
| 🛡️ **リスク管理** | 最大ドローダウン -15% で全ポジションを安全資産へ自動退避（毎営業日監視）。1銘柄あたり上限40%。 |
| 📊 **バックテスト基盤** | 2010年〜の長期検証。CAGR・最大DD・シャープレシオ・月次勝率・対SPYアルファを算出し、グラフ(PNG)とJSONを出力。 |
| 🔧 **パラメータチューニング** | ルックバック期間・保有銘柄数などをグリッドサーチし、過剰最適化を避けつつ頑健なパラメータを選定。 |
| 📱 **モバイルダッシュボード** | FastAPI製のAPI + PWA。スマホのホーム画面アプリとして総資産・保有・売買理由を確認できる。 |
| 💴 **円建て積立シミュレーション** | 「毎月N万円ずつ積み立てたら今いくらか」を円で表示（為替に依存しない設計）。 |
| 🔔 **通知** | Slack通知 + iPhoneへのWebプッシュ通知（VAPID）。リバランス完了・DD警告・月次レポートを配信。 |
| 💡 **AI戦略アドバイザー** | 成績悪化時にClaude APIで改善案を「理由付きで」提案。**自動適用はせず人間が最終判断**（安全のため）。 |
| 📝 **判断ログ** | 「なぜこの銘柄を買った/退避したか」を全件記録し、後から振り返り可能。 |
| ☁️ **クラウド常駐** | Railwayへデプロイし、PCを閉じても24時間365日稼働。永続ボリュームで状態を保持。 |

---

## 🧠 戦略ロジック（Dual Momentum）

```
                 ┌─────────────────────────────┐
                 │  毎月 第1営業日にリバランス  │
                 └──────────────┬──────────────┘
                                ▼
        ① 絶対モメンタム：SPYの過去9ヶ月リターンは＋か？
                 │                        │
            プラス（攻め）            マイナス（守り）
                 ▼                        ▼
   ② 相対モメンタム：監視ETFの         安全資産 BIL
      9ヶ月リターン上位3銘柄を         （短期米国債）へ
      均等保有（各 約33%）              全額退避
                 │                        │
                 └───────────┬────────────┘
                             ▼
        ③ 毎営業日：ピークから-15%下落で強制的に全退避
```

| 項目 | 設定値 |
| --- | --- |
| 監視ユニバース | SPY / QQQ / IWM / EFA / GLD / TLT（+安全資産 BIL） |
| ベンチマーク | SPY |
| モメンタム計測期間 | 9ヶ月 |
| 保有銘柄数 | 上位3銘柄を均等配分 |
| 最大ドローダウン制限 | -15%（超過で全退避） |
| 1銘柄あたり上限 | 40% |

---

## 📈 バックテスト結果（2010年〜・初期$1,000）

| 指標 | 本戦略 | SPY バイ&ホールド |
| --- | --- | --- |
| CAGR（年率） | **10.5%** | 14.7% |
| 最大ドローダウン | **-15.9%** | -33.7% |
| シャープレシオ | 0.91 | — |
| 月次勝率 | 64.5% | — |

> **狙いは「リターン最大化」ではなく「下落の浅さ」。** SPYに年率リターンでは劣るが、**最大ドローダウンを約半分に抑制**しており、"大きく負けない"という設計目標を満たしている。リスク調整後リターン（シャープ0.91）も良好。

---

## 🛠 使用技術

| 領域 | 技術 |
| --- | --- |
| 言語 | Python 3.11+ |
| データ取得 | yfinance（株価）/ Alpaca API（口座・市場状態） |
| 証券会社API | alpaca-py（ペーパー & ライブ両対応） |
| スケジューリング | APScheduler（cron トリガー） |
| バックテスト | pandas / numpy / matplotlib |
| Web / API | FastAPI / Uvicorn |
| フロント | バニラ JS + PWA（Service Worker / manifest）— 依存ライブラリ無しの軽量SPA |
| 通知 | Slack Incoming Webhook / Web Push（pywebpush, VAPID） |
| AI連携 | Anthropic Claude API（戦略提案・任意機能） |
| テスト | pytest |
| デプロイ | Railway（Nixpacks）/ Docker不要のワーカー常駐 |
| 設定管理 | python-dotenv（環境変数 + 型付き設定クラス） |

---

## 🏗 設計上のこだわり（エンジニアリング面）

このプロジェクトで意識した設計判断です。

- **純粋関数としての戦略・リスクロジック**
  `core/strategy.py` と `core/risk_manager.py` は副作用のない純粋関数として実装。価格データを入力に重み付けを返すだけなので、**バックテストと本番で同一コードを共有**でき、テストも容易。

- **グレースフルデグラデーション（機能の段階的縮退）**
  通知・ログ・AI提案・為替取得などの非クリティカル機能はすべて `try/except` で隔離。**これらが失敗しても取引本体は決して止まらない**設計。

- **関心の分離（レイヤー分割）**
  データ取得 / 戦略判断 / リスク管理 / 発注実行 / 通知 / 可視化 を独立モジュールに分離。各層は明確なインターフェースで疎結合。

- **堅牢性**
  API呼び出しは指数バックオフで自動リトライ。発注前に残高・上限・市場開場を多段バリデーション。ピーク資産は永続化し再起動後も継続。

- **セキュリティ**
  秘密情報（APIキー・VAPID秘密鍵）は `.env` に隔離し `.gitignore` 済み。リポジトリにはコードのみ。本番キーは段階移行を前提。

- **テスト**
  戦略・リスク・発注・データ取得を pytest でユニットテスト。`scripts/` に接続確認・少額発注テスト・通知テストの検証用CLIを用意。

---

## 📁 ディレクトリ構成

```
trading_bot/
├── main.py                  # 本番スケジューラ（APScheduler）
├── run_backtest.py          # バックテスト実行CLI
├── config/
│   └── settings.py          # 型付き設定（環境変数 + 既定値 + バリデーション）
├── core/
│   ├── data_fetcher.py      # 株価取得(yfinance) + 口座照会(Alpaca)
│   ├── strategy.py          # デュアルモメンタム（純粋関数）
│   ├── risk_manager.py      # ドローダウン判定・発注バリデーション（純粋関数）
│   ├── order_executor.py    # Alpaca 発注・ポジション管理
│   ├── strategy_advisor.py  # Claude APIによる戦略提案（任意）
│   ├── decision_logger.py   # 売買判断の記録
│   └── exceptions.py        # カスタム例外
├── backtesting/
│   ├── backtest_engine.py   # バックテストエンジン
│   ├── performance_metrics.py # CAGR/DD/シャープ等の指標計算
│   └── tuning.py            # パラメータのグリッドサーチ
├── dashboard/
│   ├── app.py               # FastAPI アプリ
│   ├── data_provider.py     # API用データ整形（ライブ overlay + 円建て積立）
│   ├── routes/              # portfolio / backtest / strategy / notifications
│   ├── push_sender.py       # Web Push 配信
│   └── static/              # PWA（index.html / sw.js / manifest.json）
├── utils/                   # logger / notifier(Slack) / retry / monthly_report
├── scripts/                 # 接続確認・発注テスト・VAPID鍵生成 等の検証CLI
├── tests/                   # pytest ユニットテスト
└── docs/                    # セットアップ・デプロイ手順・ワークフロー図
```

---

## 🚀 セットアップ

```bash
# 1. 依存関係のインストール（Python 3.11+ 推奨）
python -m pip install -r requirements.txt

# 2. 環境変数の設定
cp .env.example .env
#   .env を編集し、Alpaca のペーパートレード用 API Key / Secret を入力

# 3. 接続確認
python scripts/check_connection.py     # .env読込・キー形式・Alpaca接続を検証

# 4. バックテストで戦略を検証
python run_backtest.py                  # 指標を表示し logs/reports/ にグラフ出力

# 5. ペーパートレードで稼働
python main.py                          # スケジューラ起動（NY時間で自動運用）

# 6. ダッシュボードを起動（別ターミナル）
python -m uvicorn dashboard.app:app --host 0.0.0.0 --port 8000
#   → http://localhost:8000

# 7. テスト
python -m pytest -q
```

Alpacaの無料アカウント（ペーパートレード）は https://alpaca.markets で作成できます。
詳しいAPIキー取得手順は [`docs/`](docs/) を参照してください。

---

## ☁️ デプロイ（Railway）

1. リポジトリを GitHub に push（`.env` は `.gitignore` 済み）。
2. [Railway](https://railway.app) で New Project → Deploy from GitHub repo。
3. **Variables** に `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` / `ALPACA_BASE_URL` / `ENVIRONMENT` / `TZ=America/New_York` を設定。
4. **Volume** を `/app/logs` にマウント（再起動後も状態を保持）。
5. `railway.toml` の `startCommand = "python main.py"` でワーカーとして24時間常駐。

---

## 🔒 セキュリティ方針

- `.env`（APIキー・VAPID秘密鍵）は**絶対にコミットしない**（`.gitignore` 済み）。
- **ライブ取引のキーは最初から使わない**。ペーパーで十分検証後にのみ移行。
- AI提案は**提案のみ**で、設定変更は人間が手動で行う（暴走防止）。

---

## 📝 ライセンス

MIT License. 投資判断・運用結果に関する一切の責任は利用者に帰属します。
