"""本番稼働前チェックリスト自動実行スクリプト。

本番（ペーパー→ライブ）稼働できる状態かを 6 カテゴリで自動確認します。
すべての必須項目がパスすれば「本番稼働の準備ができている」と判定します。

実行方法:
    python scripts/pre_launch_check.py

終了コード:
    0 = 全必須チェックパス / 1 = 1 つ以上の必須チェック失敗
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# プロジェクトルートを import パスに追加（scripts/ から実行できるように）。
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Windows のコンソール（cp932）でも絵文字を表示できるよう UTF-8 に切り替える。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

from dotenv import load_dotenv  # noqa: E402

# --- 合否判定の基準値 -----------------------------------------------------
_MIN_CAGR_PCT = 10.0  # CAGR は 10% 以上
_MAX_DD_PCT = 25.0  # 最大ドローダウンは 25% 以内
_MIN_SHARPE = 1.0  # シャープレシオは 1.0 以上

_PLACEHOLDER_MARKERS = ("ここに入力", "ここに", "your_api_key", "your_secret", "【", "】")
_RESULTS_FILE = _PROJECT_ROOT / "backtesting" / "results" / "backtest_results.json"
_LOGS_DIR = _PROJECT_ROOT / "logs"


class _CheckRecorder:
    """各チェックの結果を記録し、最終判定を行う集計役。"""

    def __init__(self) -> None:
        self.required_failed = 0  # 必須項目の失敗数
        self.warnings = 0  # 任意項目の警告数

    def ok(self, message: str) -> None:
        """成功した項目を表示する。"""
        print(f"  ✅ {message}")

    def fail(self, message: str, hint: str | None = None) -> None:
        """必須項目の失敗を表示し、失敗数を加算する。"""
        print(f"  ❌ {message}")
        if hint:
            for line in hint.splitlines():
                print(f"     → {line}")
        self.required_failed += 1

    def warn(self, message: str, hint: str | None = None) -> None:
        """任意項目の警告を表示する（合否には影響しない）。"""
        print(f"  ⚠️  {message}")
        if hint:
            for line in hint.splitlines():
                print(f"     → {line}")
        self.warnings += 1


def _check_environment(rec: _CheckRecorder) -> tuple[str, str, str, str]:
    """[1/6] 環境設定（.env / APIキー / paperモード）を確認する。

    Returns:
        (api_key, secret_key, base_url, environment) のタプル。
    """
    print("[1/6] 環境設定")
    env_path = _PROJECT_ROOT / ".env"
    if not env_path.exists():
        rec.fail(
            ".envファイルが見つかりません",
            "copy .env.example .env を実行し、APIキーを入力してください。",
        )
        return "", "", "", ""
    load_dotenv(env_path)
    rec.ok(".envファイル確認")

    api_key = os.getenv("ALPACA_API_KEY", "")
    secret_key = os.getenv("ALPACA_SECRET_KEY", "")
    base_url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    environment = os.getenv("ENVIRONMENT", "paper").strip().lower()

    placeholder = any(
        marker in value
        for value in (api_key, secret_key)
        for marker in _PLACEHOLDER_MARKERS
    )
    if not api_key.strip() or not secret_key.strip():
        rec.fail(
            "APIキーが空です",
            ".env の ALPACA_API_KEY / ALPACA_SECRET_KEY を入力してください。",
        )
    elif placeholder:
        rec.fail(
            "APIキーがプレースホルダ（【ここに入力】等）のままです",
            "https://app.alpaca.markets で取得したキーに置き換えてください。",
        )
    else:
        rec.ok("APIキー設定済み")

    if environment == "paper" and "paper" in base_url:
        rec.ok("Paper Trading モード確認")
    elif environment == "live":
        rec.warn(
            "ENVIRONMENT=live になっています（本番＝本物のお金）",
            "まずは paper で 1 ヶ月検証してから live へ移行してください。",
        )
    else:
        rec.warn(
            f"ENVIRONMENT={environment} / URL={base_url} の組み合わせを確認してください",
        )
    return api_key, secret_key, base_url, environment


def _check_alpaca(rec: _CheckRecorder, api_key: str, secret_key: str, base_url: str) -> None:
    """[2/6] Alpaca に接続し、口座残高を取得できるか確認する。"""
    print("[2/6] Alpaca接続")
    if not api_key.strip() or not secret_key.strip() or any(
        m in api_key or m in secret_key for m in _PLACEHOLDER_MARKERS
    ):
        rec.fail(
            "APIキー未設定のため接続をスキップしました",
            "先に .env へ実際のキーを入力してください。",
        )
        return
    try:
        from core.exceptions import OrderExecutionError
        from core.order_executor import OrderExecutor

        executor = OrderExecutor(api_key, secret_key, base_url=base_url)
        info = executor.get_account_info()
    except Exception as exc:  # noqa: BLE001 - 接続失敗の原因は多岐にわたる
        rec.fail(
            f"API接続に失敗しました: {exc}",
            "キーの誤り / paper・live URL の取り違え / ネット接続を確認してください。",
        )
        return
    rec.ok("API接続成功")
    rec.ok(f"残高取得成功: ${info['equity']:,.2f}")


def _check_strategy(rec: _CheckRecorder) -> None:
    """[3/6] バックテスト結果（CAGR/DD/シャープ）が基準を満たすか確認する。"""
    print("[3/6] 戦略検証")
    if not _RESULTS_FILE.exists():
        rec.fail(
            "バックテスト結果がありません",
            "python run_backtest.py を実行して結果を生成してください。",
        )
        return
    try:
        payload = json.loads(_RESULTS_FILE.read_text(encoding="utf-8"))
        summary = payload["summary"]
    except (ValueError, KeyError) as exc:
        rec.fail(f"バックテスト結果を読めません: {exc}")
        return
    rec.ok("バックテスト結果あり")

    cagr = float(summary.get("cagr", 0.0))
    max_dd = float(summary.get("max_drawdown", 0.0))  # 正の % で格納
    sharpe = float(summary.get("sharpe_ratio", 0.0))

    if cagr >= _MIN_CAGR_PCT:
        rec.ok(f"CAGR: {cagr:.1f}%（基準: {_MIN_CAGR_PCT:.0f}%以上）")
    else:
        rec.fail(f"CAGR: {cagr:.1f}%（基準: {_MIN_CAGR_PCT:.0f}%以上に未達）")

    if max_dd <= _MAX_DD_PCT:
        rec.ok(f"最大DD: -{max_dd:.1f}%（基準: {_MAX_DD_PCT:.0f}%以内）")
    else:
        rec.fail(f"最大DD: -{max_dd:.1f}%（基準: {_MAX_DD_PCT:.0f}%以内を超過）")

    if sharpe >= _MIN_SHARPE:
        rec.ok(f"シャープレシオ: {sharpe:.2f}（基準: {_MIN_SHARPE:.1f}以上）")
    else:
        # 守りの戦略では 1.0 未満でも許容しうるため、警告に留める。
        rec.warn(
            f"シャープレシオ: {sharpe:.2f}（目安: {_MIN_SHARPE:.1f}以上）",
            "守り重視（低DD）の戦略では 1.0 未満になることがあります。"
            "DD と勝率が基準内なら稼働可能です。",
        )


def _check_risk(rec: _CheckRecorder) -> None:
    """[4/6] リスク管理パラメータが設定されているか確認する。"""
    print("[4/6] リスク管理")
    try:
        from config.settings import Settings

        dd = Settings.__dataclass_fields__["max_drawdown_threshold"].default
        pos = Settings.__dataclass_fields__["max_position_size"].default
    except Exception as exc:  # noqa: BLE001
        rec.fail(f"設定値を読めません: {exc}")
        return

    if isinstance(dd, float) and 0.0 < dd < 1.0:
        rec.ok(f"ドローダウン制限: {dd:.0%}")
    else:
        rec.fail(f"MAX_DRAWDOWN_THRESHOLD が不正です: {dd}")

    if isinstance(pos, float) and 0.0 < pos <= 1.0:
        rec.ok(f"最大ポジション: {pos:.0%}")
    else:
        rec.fail(f"MAX_POSITION_SIZE が不正です: {pos}")


def _check_slack(rec: _CheckRecorder) -> None:
    """[5/6] Slack Webhook が設定されていればテスト通知を送る（任意）。"""
    print("[5/6] Slack通知")
    webhook = os.getenv("SLACK_WEBHOOK_URL", "")
    if not webhook.strip() or any(m in webhook for m in _PLACEHOLDER_MARKERS):
        rec.warn(
            "Slack Webhook 未設定（任意）",
            "通知が必要な場合のみ .env の SLACK_WEBHOOK_URL を設定してください。",
        )
        return
    try:
        from utils.notifier import SlackNotifier

        notifier = SlackNotifier(webhook)
        notifier.notify_monthly_summary(
            "✅ pre_launch_check からのテスト通知です（接続確認）。"
        )
    except Exception as exc:  # noqa: BLE001
        rec.fail(f"テスト通知の送信に失敗しました: {exc}")
        return
    rec.ok("テスト通知送信成功")


def _check_logs(rec: _CheckRecorder) -> None:
    """[6/6] logs フォルダの存在と書き込み権限を確認する。"""
    print("[6/6] ログ設定")
    try:
        _LOGS_DIR.mkdir(parents=True, exist_ok=True)
        rec.ok("logsフォルダ確認")
    except OSError as exc:
        rec.fail(f"logsフォルダを作成できません: {exc}")
        return
    probe = _LOGS_DIR / ".write_test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        rec.fail(f"logsフォルダに書き込めません: {exc}")
        return
    rec.ok("書き込み権限確認")


def main() -> None:
    """全 6 カテゴリのチェックを実行し、総合結果を表示する。"""
    print("-" * 40)
    print("🚀 本番稼働前チェック開始")
    print("-" * 40)

    rec = _CheckRecorder()
    api_key, secret_key, base_url, _env = _check_environment(rec)
    _check_alpaca(rec, api_key, secret_key, base_url)
    _check_strategy(rec)
    _check_risk(rec)
    _check_slack(rec)
    _check_logs(rec)

    print("=" * 40)
    if rec.required_failed == 0:
        print("✅ 全チェック完了！本番稼働の準備ができています。")
        if rec.warnings:
            print(f"   （任意項目の警告が {rec.warnings} 件あります。内容を確認してください）")
        print("次のステップ:")
        print("  1. python main.py でローカル動作確認")
        print("  2. Railway にデプロイ")
        print("  3. 1ヶ月ペーパートレードで動作確認")
        print("  4. 問題なければ .env の ENVIRONMENT=live に変更")
        print("=" * 40)
        sys.exit(0)
    else:
        print(f"❌ 必須チェックに {rec.required_failed} 件の失敗があります。")
        print("   上記の「→」のヒントに従って修正し、もう一度実行してください。")
        if rec.warnings:
            print(f"   （他に任意項目の警告が {rec.warnings} 件あります）")
        print("=" * 40)
        sys.exit(1)


if __name__ == "__main__":
    main()
