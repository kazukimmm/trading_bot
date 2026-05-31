"""Web Push 通知の送信（VAPID + pywebpush）。

購読情報（subscription）はローカル JSON（``logs/push_subscriptions.json``）に
保存します。元の仕様では Firestore を想定していますが、本プロジェクトの他の
状態ファイルと同様にローカル JSON を採用し、追加の認証情報なしでローカルでも
Railway でも動作するようにしています。

通知失敗は握りつぶしてログに残すだけで、呼び出し元（取引ループ）を止めません。
410/404（購読が無効）の場合は、その購読を自動削除します。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from utils.logger import get_logger

_logger = get_logger(__name__)

_SUBSCRIPTIONS_FILE = (
    Path(__file__).resolve().parent.parent / "logs" / "push_subscriptions.json"
)


def _endpoint_of(sub: dict[str, Any]) -> str:
    """購読情報からエンドポイント URL（一意キー）を取り出す。"""
    return str(sub.get("endpoint", ""))


class PushSender:
    """登録済みデバイスへ Web Push 通知を送るクラス。"""

    def __init__(
        self,
        vapid_private_key: str | None = None,
        vapid_public_key: str | None = None,
        vapid_email: str | None = None,
        subscriptions_file: Path = _SUBSCRIPTIONS_FILE,
    ) -> None:
        """送信器を初期化する。

        Args:
            vapid_private_key: VAPID 秘密鍵。未指定なら環境変数から読む。
            vapid_public_key: VAPID 公開鍵。未指定なら環境変数から読む。
            vapid_email: ``mailto:you@example.com`` 形式の連絡先。
            subscriptions_file: 購読情報を保存する JSON ファイル。
        """
        self.vapid_private_key = vapid_private_key or os.getenv("VAPID_PRIVATE_KEY", "")
        self.vapid_public_key = vapid_public_key or os.getenv("VAPID_PUBLIC_KEY", "")
        self.vapid_email = vapid_email or os.getenv("VAPID_EMAIL", "mailto:admin@example.com")
        self.subscriptions_file = subscriptions_file
        self.enabled = bool(self.vapid_private_key and self.vapid_public_key)

    # ------------------------------------------------------------------
    # 購読の永続化
    # ------------------------------------------------------------------
    def load_subscriptions(self) -> list[dict[str, Any]]:
        """保存済みの購読情報をすべて読み込む。"""
        if not self.subscriptions_file.exists():
            return []
        try:
            with self.subscriptions_file.open(encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError) as exc:
            _logger.error("Failed to read push subscriptions: %s", exc)
        return []

    def _write_subscriptions(self, subs: list[dict[str, Any]]) -> None:
        """購読情報リストをファイルに書き込む。"""
        try:
            self.subscriptions_file.parent.mkdir(parents=True, exist_ok=True)
            with self.subscriptions_file.open("w", encoding="utf-8") as fh:
                json.dump(subs, fh, ensure_ascii=False, indent=2)
        except OSError as exc:
            _logger.error("Failed to write push subscriptions: %s", exc)

    def save_subscription(self, subscription: dict[str, Any]) -> None:
        """購読情報を追加保存する（同じエンドポイントは上書き）。"""
        endpoint = _endpoint_of(subscription)
        if not endpoint:
            _logger.warning("Subscription without endpoint ignored.")
            return
        subs = self.load_subscriptions()
        subs = [s for s in subs if _endpoint_of(s) != endpoint]
        subs.append(subscription)
        self._write_subscriptions(subs)
        _logger.info("Saved push subscription (total=%d).", len(subs))

    def _remove_subscription(self, endpoint: str) -> None:
        """無効になった購読を削除する。"""
        subs = self.load_subscriptions()
        remaining = [s for s in subs if _endpoint_of(s) != endpoint]
        if len(remaining) != len(subs):
            self._write_subscriptions(remaining)
            _logger.info("Removed invalid push subscription.")

    # ------------------------------------------------------------------
    # 送信
    # ------------------------------------------------------------------
    def send_notification(
        self,
        subscription_info: dict[str, Any],
        title: str,
        body: str,
        urgency: str = "normal",
        url: str = "/",
    ) -> bool:
        """1 件の購読へ通知を送る。

        Args:
            subscription_info: ブラウザの PushSubscription（endpoint/keys を含む）。
            title: 通知タイトル。
            body: 通知本文。
            urgency: ``"high"`` / ``"medium"`` / ``"low"`` / ``"normal"``。
            url: タップ時に開く URL。

        Returns:
            送信成功なら ``True``。失敗（無効購読の削除を含む）なら ``False``。
        """
        if not self.enabled:
            _logger.debug("Push disabled (no VAPID keys); message suppressed: %s", title)
            return False

        try:
            from pywebpush import WebPushException, webpush
        except ImportError:
            _logger.error("pywebpush not installed; cannot send push notification.")
            return False

        payload = json.dumps(
            {"title": title, "body": body, "urgency": urgency, "url": url},
            ensure_ascii=False,
        )
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=self.vapid_private_key,
                vapid_claims={"sub": self.vapid_email},
            )
            return True
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                self._remove_subscription(_endpoint_of(subscription_info))
            else:
                _logger.error("Push send failed (status=%s): %s", status, exc)
            return False
        except Exception as exc:  # noqa: BLE001 - 通知失敗で取引を止めない
            _logger.error("Unexpected push send error: %s", exc)
            return False

    def broadcast(self, title: str, body: str, urgency: str = "normal", url: str = "/") -> int:
        """登録済みの全デバイスへ通知を送る。

        Returns:
            送信に成功したデバイス数。
        """
        subs = self.load_subscriptions()
        if not subs:
            _logger.debug("No push subscriptions; broadcast skipped: %s", title)
            return 0
        sent = 0
        for sub in list(subs):
            if self.send_notification(sub, title, body, urgency=urgency, url=url):
                sent += 1
        _logger.info("Push broadcast '%s' delivered to %d/%d devices.", title, sent, len(subs))
        return sent
