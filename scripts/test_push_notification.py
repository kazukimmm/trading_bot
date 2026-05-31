"""プッシュ通知の送信テストスクリプト。

登録済みの全デバイス（``logs/push_subscriptions.json``）へテスト通知を送ります。
iPhone をホーム画面アプリとして追加し、通知を許可した後に実行してください。

実行方法:
    python scripts/test_push_notification.py
    python scripts/test_push_notification.py "タイトル" "本文" high
"""

from __future__ import annotations

import sys
from pathlib import Path

# プロジェクトルートを import パスに追加。
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

from dashboard.push_sender import PushSender  # noqa: E402


def main() -> None:
    """テスト通知を全デバイスにブロードキャストする。"""
    load_dotenv()

    title = sys.argv[1] if len(sys.argv) > 1 else "✅ テスト通知"
    body = sys.argv[2] if len(sys.argv) > 2 else "プッシュ通知が正しく届いています。"
    urgency = sys.argv[3] if len(sys.argv) > 3 else "normal"

    sender = PushSender()
    if not sender.enabled:
        print("❌ VAPID キーが未設定です。scripts/generate_vapid_keys.py を実行し .env に追加してください。")
        sys.exit(1)

    subs = sender.load_subscriptions()
    print(f"登録済みデバイス数: {len(subs)}")
    if not subs:
        print("⚠️ 購読がありません。iPhone のダッシュボードで「許可する」を押してください。")
        sys.exit(1)

    sent = sender.broadcast(title, body, urgency=urgency)
    print(f"✅ {sent}/{len(subs)} 台に送信しました（urgency={urgency}）。")


if __name__ == "__main__":
    main()
