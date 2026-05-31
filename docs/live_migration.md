# Live Trading 移行手順

ペーパートレード（仮想資金）で十分に検証してから、本物のお金での運用
（Live Trading）へ切り替えるための手順です。**焦らないことが最大のリスク管理です。**

## 移行条件チェックリスト

以下を全部満たしてから移行すること。

- [ ] ペーパートレードで1ヶ月以上正常稼働した
- [ ] バックテストと実際の動きの乖離が±5%以内
- [ ] ドローダウンが一度も15%を超えていない
- [ ] エラーログがない（または全て対処済み）
- [ ] Slack通知が毎回正常に届いている
- [ ] 投資する資金を失っても生活に支障がないことを確認した

## 移行手順

### ステップ1: Live Trading APIキーを取得

1. https://app.alpaca.markets にログイン
2. 左上のメニューから「Live Trading」に切り替え
3. 「API」→「Generate New Key」
4. KYC（本人確認）が必要な場合は完了させる

### ステップ2: Railwayの環境変数を変更

Railway管理画面 → Settings → Variables

変更する項目：

| 変数名 | 変更後の値 |
|--------|-----------|
| ALPACA_API_KEY | Live TradingのAPI Key（新しいキー） |
| ALPACA_SECRET_KEY | Live TradingのSecret Key（新しいキー） |
| ALPACA_BASE_URL | https://api.alpaca.markets ← paperを削除 |
| ENVIRONMENT | live |

> ⚠️ `ALPACA_BASE_URL` から **paper-** を消し忘れると、Live キーで paper URL に
> アクセスして接続エラーになります。URL も必ず変更してください。

### ステップ3: 変更後の確認

`python scripts/check_connection.py` を実行して
「ライブ（本物のお金）」と表示されることを確認する。

ローカルで確認する場合は、`.env` の以下 2 行も同様に変更します。

```
ALPACA_API_KEY=（Liveのキー）
ALPACA_SECRET_KEY=（Liveのキー）
ALPACA_BASE_URL=https://api.alpaca.markets
ENVIRONMENT=live
```

### ⚠️ 絶対に守ること

- 最初は少額（$100〜$200）から始める
- いきなり全額投入しない
- 最初の1ヶ月は毎日ログを確認する
- 入金額を増やすのは、Live で 1〜2 ヶ月問題なく回ってから

## 入金を増やしていく流れ

```
Live移行（$100〜）
  ↓ 1〜2ヶ月 問題なし
毎月コツコツ入金
  ↓
月次レポート（utils/monthly_report.py）で成績を確認
  ↓
収入が増えたら入金額を増やすだけ（戦略はそのまま）
```
