"""Alpaca接続チェックスクリプト（セットアップ確認用）。

.env に入力した API キーが正しく読み込めるか、Alpaca に接続できるか、
口座情報を取得できるかを順番に確認します。

実行方法:
    python scripts/check_connection.py
"""

from __future__ import annotations

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

from core.exceptions import OrderExecutionError  # noqa: E402
from core.order_executor import OrderExecutor, validate_api_keys  # noqa: E402


def _fail(message: str) -> None:
    """エラーメッセージを表示して終了する。"""
    print(message)
    sys.exit(1)


def main() -> None:
    """セットアップ状況を順番にチェックして結果を表示する。"""
    print("=" * 50)
    print("  Alpaca 接続チェック")
    print("=" * 50)

    # --- STEP1: .env の読み込み -----------------------------------------
    env_path = _PROJECT_ROOT / ".env"
    if not env_path.exists():
        _fail(
            "❌ .envファイルが見つかりません。\n"
            "   .env.example をコピーして .env を作成し、APIキーを入力してください。\n"
            "   例) copy .env.example .env"
        )
    load_dotenv(env_path)
    print("✅ .envファイル読み込み成功")

    api_key = os.getenv("ALPACA_API_KEY", "")
    secret_key = os.getenv("ALPACA_SECRET_KEY", "")
    base_url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    environment = os.getenv("ENVIRONMENT", "paper")

    # --- STEP2: APIキーの中身チェック（空 / プレースホルダ）-------------
    try:
        validate_api_keys(api_key, secret_key)
    except ValueError as exc:
        _fail(
            f"❌ {exc}\n"
            "   .envファイルの ALPACA_API_KEY と ALPACA_SECRET_KEY を\n"
            "   実際のキーに置き換えてください（【】やスペースは残さない）。\n"
            "   取得場所: https://app.alpaca.markets → Paper Trading → API Keys"
        )
    print("✅ APIキーの形式チェック成功")

    is_paper = "paper" in base_url
    mode_label = "ペーパー（仮想資金）" if is_paper else "ライブ（本物のお金）"
    print(f"🌐 接続先: {mode_label}  [{base_url}]")
    print(f"🧪 ENVIRONMENT: {environment}")

    # --- STEP3: Alpaca へ接続して口座情報を取得 -------------------------
    print("\n⏳ Alpaca に接続しています...")
    try:
        executor = OrderExecutor(api_key, secret_key, base_url=base_url)
        info = executor.get_account_info()
    except OrderExecutionError as exc:
        _fail(
            f"❌ Alpaca への接続に失敗しました: {exc}\n"
            "   考えられる原因:\n"
            "   ・APIキー / Secretキーが間違っている\n"
            "   ・Paper用のキーで Live URL（またはその逆）を指定している\n"
            "   ・ネットワーク接続の問題\n"
            "   もう一度 .env のキーと URL を確認してください。"
        )
    except Exception as exc:  # noqa: BLE001
        _fail(f"❌ 予期しないエラーが発生しました: {exc}")

    print("✅ Alpaca 接続成功\n")
    print("📊 口座情報")
    print("-" * 50)
    print(f"  口座タイプ      : {'ペーパー' if info['is_paper'] else 'ライブ'}")
    print(f"  総資産（equity）: ${info['equity']:,.2f}")
    print(f"  買付余力        : ${info['buying_power']:,.2f}")
    print(f"  現金            : ${info['cash']:,.2f}")
    print(f"  保有銘柄数      : {info['position_count']} 銘柄")
    print("-" * 50)

    print("\n🎉 セットアップ完了！")
    print("   次は注文テスト（scripts/test_order.py）を実行できます。")


if __name__ == "__main__":
    main()
