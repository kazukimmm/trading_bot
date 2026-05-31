# Railway デプロイ手順

このボットを 24 時間 365 日クラウドで動かすための手順です。
ローカル PC を起動しっぱなしにしなくても、Railway 上で自動運用されます。

> **この手順で常駐させるのは「ボット本体（`python main.py`）」だけです。**
> 取引と Slack 通知はこれだけで完全に動きます。ダッシュボードは見たいときに
> 手元の PC で `uvicorn dashboard.app:app` を起動して確認します（クラウドには載せません）。

## 0. 事前準備：GitHub にコードを上げる（最重要・最初にやる）

Railway は **GitHub のリポジトリ** を見てデプロイします。まだ Git 管理して
いない場合は、先にコードを GitHub に push してください。
`.env`（APIキー等の秘密情報）は `.gitignore` で除外済みなので push されません。

```bash
# プロジェクトフォルダで実行
git init                      # （初回のみ。済んでいれば不要）
git add -A
git commit -m "initial commit"

# GitHub で空のリポジトリを作成してから↓（URL は自分のものに置き換え）
git remote add origin https://github.com/＜あなた＞/trading_bot.git
git branch -M main
git push -u origin main
```

> ⚠️ push の前に `git status` で **`.env` が含まれていないこと** を必ず確認。
> （`OK: .env NOT staged` の状態ならOK）

## 1. Railwayアカウント作成

1. https://railway.app にアクセス
2. 「Start a New Project」→ GitHubでログイン

## 2. プロジェクト作成

1. 「New Project」→「Deploy from GitHub repo」
2. trading_botリポジトリを選択
3. 自動でデプロイが始まる

## 3. 環境変数の設定（最重要）

Railwayの管理画面で以下を設定する。
`.env`ファイルはGitHubにpushしないので、Railway側で直接入力が必要。

Settings → Variables に以下を追加：

| 変数名 | 値 |
|--------|-----|
| ALPACA_API_KEY | PKから始まるキー |
| ALPACA_SECRET_KEY | Secret Key |
| ALPACA_BASE_URL | https://paper-api.alpaca.markets |
| ENVIRONMENT | paper |
| SLACK_WEBHOOK_URL | SlackのWebhook URL |
| LOG_LEVEL | INFO |
| TZ | America/New_York |

⚠️ **TZ（タイムゾーン）は必ず設定すること。**
設定しないとスケジューラーがNY時間で動かない。

> 補足: 本ボットはスケジューラ内部でも `America/New_York` に変換しているため、
> 万一 TZ を入れ忘れても致命的ではありませんが、ログの時刻表示が分かりやすく
> なるので必ず設定してください。

## 3.5. 永続ディスク（Volume）の設定 ★1年運用では必須

Railway のコンテナは **再起動やデプロイのたびにファイルが初期化** されます。
このボットは以下を `logs/` フォルダに保存しているため、対策しないと再起動で消えます。

| ファイル | 役割 | 消えると… |
|----------|------|-----------|
| `logs/state.json` | ピーク資産（ドローダウン判定の基準） | −15%自動退避の基準がリセットされ、誤作動の恐れ |
| `logs/decision_history.json` | 売買の判断ログ | 「なぜ買ったか」の履歴が消える |
| `logs/monthly_snapshots.json` | 月次成績 | 月次レポート・リターン計算が途切れる |

### 設定方法

1. Railway のサービス画面 → **Variables / Settings → Volumes**
2. 「New Volume」を作成し、**Mount Path** に `/app/logs` を指定
   （Nixpacks の作業ディレクトリは `/app`。ボットは `/app/logs` に書き込む）
3. これで再起動してもピーク資産・判断ログ・月次成績が保持されます

> 1年間の検証データの整合性を保つため、**ここは必ず設定してください。**

## 4. デプロイ確認

1. Deployments タブでログを確認
2. 「Starting trading bot」「Scheduler configured」のログが出ればOK
3. Slackに起動通知が届くことを確認（Slack 設定時）

## 5. ログの確認方法

Railway管理画面 → Deployments → 最新のデプロイ → Logs

- リバランスや残高取得のログがここに流れます。
- エラーが出ていないか、デプロイ直後と毎月初めに確認してください。

## 6. 料金の目安

| プラン | 月額 | 用途 |
|--------|------|------|
| Trial | 無料（$5クレジット） | 最初のテスト |
| Hobby | $5/月 | 本番稼働におすすめ |

月 $5 で 24 時間 365 日ボットが動き続けます。

## トラブルシューティング

| 症状 | 原因 / 対処 |
|------|-------------|
| 起動直後にクラッシュを繰り返す | 環境変数（特に APIキー）の未設定。Variables を再確認 |
| 「Required environment variable ... is not set」 | ALPACA_API_KEY / ALPACA_SECRET_KEY が未入力 |
| 時刻がずれている | TZ=America/New_York を設定 |
| 取引が実行されない | 第1営業日 9:35 ET のみリバランス。平日・市場開場日か確認 |
