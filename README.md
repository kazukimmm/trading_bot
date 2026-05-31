# 米国株 保守的自動売買システム（デュアルモメンタム）

月1万円（約$65）から始められる、資産保全を最優先した米国ETFの自動売買ボットです。
Gary Antonacci の **Dual Momentum** 戦略をベースに、月1回のリバランスのみで運用します。

> ⚠️ **免責事項**: 本ソフトウェアは教育・研究目的で提供されます。投資は自己責任で行ってください。
> 過去のバックテスト結果は将来の成績を保証しません。作者・提供者はいかなる損失についても責任を負いません。
> 必ずペーパートレードで十分に検証してから少額で開始してください。

---

## 戦略概要

1. **絶対モメンタム（守りの判定）**: ベンチマーク（SPY）の過去12ヶ月リターンがプラスか確認。
   - プラス → 攻めモード（個別ETF選択へ）
   - マイナス → 守りモード（BIL: 短期米国債に全額退避）
2. **相対モメンタム（攻めの判定）**: リスク資産（SPY/QQQ/IWM/EFA/GLD/TLT）の過去12ヶ月リターン上位3銘柄を均等保有（各約33.3%）。
3. **リバランス**: 毎月第1営業日に1回だけ実行。

### リスク管理ルール

| ルール | 内容 |
| --- | --- |
| 最大ドローダウン制限 | 直近ピークから15%下落で全ポジションをクローズしBILへ退避。毎日15:55(NY)に監視 |
| 最大ポジションサイズ | 1リスク銘柄あたり最大40%（安全資産BILは除外） |
| 注文前バリデーション | 残高確認・注文額が残高の95%を超えない・市場が開いているか確認 |
| エラーハンドリング | API失敗時は3回・指数バックオフでリトライ。失敗時はスキップしてSlack通知 |

---

## ディレクトリ構成

```
trading_bot/
├── main.py                     # 本番スケジューラー
├── run_backtest.py             # バックテスト実行CLI
├── config/settings.py          # 全設定値（環境変数 + 型付き定数）
├── core/
│   ├── data_fetcher.py         # 株価取得（yfinance）＋口座照会（Alpaca）
│   ├── strategy.py             # デュアルモメンタム（純粋関数）
│   ├── risk_manager.py         # リスク判定（純粋関数）
│   ├── order_executor.py       # Alpaca注文実行
│   └── exceptions.py           # カスタム例外
├── utils/                      # logger / notifier / retry
├── backtesting/                # backtest_engine / performance_metrics
└── tests/                      # pytest ユニットテスト
```

---

## セットアップ

### 1. 依存関係のインストール
```bash
cd trading_bot
python -m pip install -r requirements.txt
```
（Python 3.11 以上を推奨）

### 2. 環境変数の設定
```bash
cp .env.example .env
# .env を編集して Alpaca のキー等を入力
```
Alpacaアカウント（無料）はこちらで作成: https://alpaca.markets
作成後、ペーパートレードの API Key / Secret を発行して `.env` に貼り付けてください。
詳しい手順は次の「🔑 APIキーの設定方法」を参照してください。

---

## 🔑 APIキーの設定方法

### STEP1: Alpacaの無料アカウントを作る
1. https://app.alpaca.markets にアクセスして「Sign Up」から登録（無料）。
2. ログイン後、画面が **Paper Trading（仮想資金）** になっていることを確認。
   - 上部または左メニューで「Live / Paper」を切り替えられます。**必ず最初は Paper**。

### STEP2: APIキーを発行する
1. 左メニューの **「Paper Trading」→「API Keys」** を開く。
2. **「Generate New Key」** をクリック。
3. 表示される 2 つの値をコピーする（Secret は**この時しか表示されません**）。
   - **API Key ID** … `PK` から始まる文字列
   - **Secret Key** … 長いランダム文字列

### STEP3: `.env` に貼り付ける
プロジェクト直下の `.env`（無ければ `.env.example` をコピー）を開き、次のように貼り付けます。

```env
ALPACA_API_KEY=PKXXXXXXXXXXXXXXXXXX
ALPACA_SECRET_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ALPACA_BASE_URL=https://paper-api.alpaca.markets
ENVIRONMENT=paper
```

⚠️ **貼り付け時の注意（よくある失敗）**
- `【】` などのプレースホルダ記号は必ず消す。
- キーの前後に**スペースを入れない**。
- キーを **クォート（" や '）で囲まない**。
- `=` の前後にスペースを入れない。

### STEP4: 接続できるか確認する
```bash
python scripts/check_connection.py
```
`.env` の読み込み・キーの形式・Alpaca接続・口座情報の取得を順番にチェックし、
成功すると `🎉 セットアップ完了！` と表示されます。
キーが未入力（プレースホルダのまま）の場合は、どこを直せばよいか案内が出ます。

### STEP5（任意）: 少額の注文テスト
ペーパー口座で SPY を $10 だけ買って即売却し、売買の流れを確認します。
```bash
python scripts/test_order.py
```

### Slack通知（任意）
通知が不要なら `SLACK_WEBHOOK_URL` は空のままで構いません。
設定する場合は https://api.slack.com/apps で Incoming Webhook を作成し、URL を `.env` に貼り付けます。

### 🔒 セキュリティ上の注意
- **`.env` を GitHub に push しない**（このリポジトリでは `.gitignore` 済み）。
- **Live Trading のAPIキーを最初から使わない**。十分な検証後にのみ切り替える。
- **Secret Key を他人に教えない / スクリーンショットを共有しない**。
- キーが漏れた疑いがあれば、Alpacaの管理画面で即座に再発行（revoke）する。

---

### 3. バックテストで戦略を検証
```bash
python run_backtest.py
```
- CAGR・最大ドローダウン・シャープレシオ・月次勝率・SPYとの比較を表示
- グラフは `logs/reports/` に PNG 出力されます（資産推移・ドローダウン・SPY比較）
- オプション: `--start 2015-01-01` `--capital 1000` `--no-plots`

### 4. ペーパートレードで稼働
```bash
python main.py
```
起動時に Alpaca への接続を確認し、以下のスケジュール（NY時間）で動作します:
- 毎月第1営業日 09:35 … リバランス
- 毎営業日 15:55 … ドローダウン監視
- 毎月最終営業日 16:10 … 月次サマリー通知

### 5. テスト
```bash
python -m pytest -q
```

---

## Railway へのデプロイ

1. このリポジトリを GitHub に push（`.env` は `.gitignore` 済み）。
2. [Railway](https://railway.app) で New Project → Deploy from GitHub repo。
3. **Variables** タブで以下を設定:
   `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_BASE_URL`, `SLACK_WEBHOOK_URL`, `ENVIRONMENT`, `LOG_LEVEL`
4. `railway.toml` の `startCommand = "python main.py"` でワーカーとして常駐します。
   スケジューラーはコンテナのタイムゾーンに関係なく内部で NY 時間に変換します。

---

## 本番（live）への切り替え

1. **最低1ヶ月以上**ペーパートレードで動作確認する。
2. `.env` の `ENVIRONMENT=live`、`ALPACA_BASE_URL=https://api.alpaca.markets` に変更。
3. 少額から開始し、Slack通知とログ（`logs/`）を必ず監視する。

---

## ライセンス / 注意

- `.env` は**絶対に**公開リポジトリへ push しないこと。
- 本番運用前にコードとリスク設定（`config/settings.py`）を必ず自分で確認すること。
