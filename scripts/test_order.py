"""ペーパー口座での少額・往復注文テスト。

SPY を $10 だけ買い、保有を確認してから全部売却し、ポジションが
無くなるところまでを実際に発注して確認します。**ペーパー（仮想資金）
専用** です。ライブ口座が設定されている場合は安全のため中止します。

実行方法:
    python scripts/test_order.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

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

_TEST_SYMBOL = "SPY"
_TEST_NOTIONAL = 10.0


def _fail(message: str) -> None:
    print(message)
    sys.exit(1)


def _wait_for_fill(executor: OrderExecutor, symbol: str, *, expect_open: bool) -> None:
    """ポジションが期待状態（建つ/無くなる）になるまで少し待つ。"""
    for _ in range(10):
        positions = executor.get_current_positions()
        has_position = symbol in positions and abs(positions[symbol]["qty"]) > 0
        if has_position == expect_open:
            return
        time.sleep(1.0)


def main() -> None:
    """少額の買い→確認→売り→確認を実行する。"""
    print("=" * 50)
    print("  ペーパー注文テスト（SPY $10 往復）")
    print("=" * 50)

    env_path = _PROJECT_ROOT / ".env"
    if not env_path.exists():
        _fail("❌ .envファイルが見つかりません。先に scripts/check_connection.py を実行してください。")
    load_dotenv(env_path)

    api_key = os.getenv("ALPACA_API_KEY", "")
    secret_key = os.getenv("ALPACA_SECRET_KEY", "")
    base_url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

    try:
        validate_api_keys(api_key, secret_key)
    except ValueError as exc:
        _fail(f"❌ {exc}\n   先に scripts/check_connection.py で接続を確認してください。")

    if "paper" not in base_url:
        _fail(
            "❌ ライブ口座が設定されています。注文テストはペーパー口座専用です。\n"
            "   .env の ALPACA_BASE_URL を https://paper-api.alpaca.markets に戻してください。"
        )

    try:
        executor = OrderExecutor(api_key, secret_key, base_url=base_url)
    except OrderExecutionError as exc:
        _fail(f"❌ 接続に失敗しました: {exc}")

    if not executor.is_market_open():
        print("⚠️  現在マーケットは閉まっています。")
        print("   注文は送信できますが、約定は次の取引時間まで保留されます。")
        print("   約定価格・取得株数が表示されない場合があります。\n")

    # --- ① 買い注文 ----------------------------------------------------
    print(f"① {_TEST_SYMBOL} ${_TEST_NOTIONAL:.0f} 買い注文送信...")
    try:
        buy = executor.submit_order(_TEST_SYMBOL, _TEST_NOTIONAL, side="buy")
    except OrderExecutionError as exc:
        _fail(f"❌ 買い注文に失敗しました: {exc}")
    print(f"   → 注文ID    : {buy['id']}")
    print(f"   → ステータス: {buy['status']}")
    if buy["filled_avg_price"] is not None:
        print(f"   → 約定価格  : ${buy['filled_avg_price']:.2f}")
    if buy["filled_qty"] is not None:
        print(f"   → 取得株数  : {buy['filled_qty']:.5f} 株")

    _wait_for_fill(executor, _TEST_SYMBOL, expect_open=True)

    # --- ② 保有確認 ----------------------------------------------------
    print("\n② 保有ポジション確認...")
    positions = executor.get_current_positions()
    if _TEST_SYMBOL in positions:
        pos = positions[_TEST_SYMBOL]
        print(f"   → {_TEST_SYMBOL}: {pos['qty']:.5f} 株 / 評価額 ${pos['market_value']:.2f}")
    else:
        print(f"   → {_TEST_SYMBOL} のポジションはまだ反映されていません（マーケット時間外の可能性）。")

    # --- ③ 売り注文（全決済）------------------------------------------
    print("\n③ 全ポジション決済...")
    try:
        executor.close_all_positions()
    except OrderExecutionError as exc:
        _fail(f"❌ 決済に失敗しました: {exc}")
    print("   → 決済リクエスト送信完了")

    _wait_for_fill(executor, _TEST_SYMBOL, expect_open=False)

    # --- ④ 決済確認 ----------------------------------------------------
    print("\n④ 決済確認...")
    positions = executor.get_current_positions()
    if _TEST_SYMBOL in positions and abs(positions[_TEST_SYMBOL]["qty"]) > 0:
        print(f"   → ⚠️ まだ {_TEST_SYMBOL} を保有しています（時間外で決済保留の可能性）。")
    else:
        print(f"   → ✅ {_TEST_SYMBOL} のポジションはありません。")

    print("\n🎉 注文テスト完了！ 売買・決済の一連の流れが確認できました。")


if __name__ == "__main__":
    main()
