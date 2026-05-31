"""プッシュ通知（Web Push）の購読 API ルート。"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Body

from dashboard.push_sender import PushSender

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

_sender = PushSender()


@router.get("/vapid-key")
def get_vapid_key() -> dict[str, str]:
    """ブラウザの購読登録に必要な VAPID 公開鍵を返す。"""
    return {"public_key": os.getenv("VAPID_PUBLIC_KEY", "")}


@router.post("/subscribe")
def subscribe(subscription: dict[str, Any] = Body(...)) -> dict[str, str]:
    """ブラウザから送られた PushSubscription を保存する。"""
    _sender.save_subscription(subscription)
    return {"status": "ok"}
