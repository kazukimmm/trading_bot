"""VAPID キーペア生成スクリプト（Web Push 用）。

Web Push（PWA のプッシュ通知）に必要な VAPID 公開鍵／秘密鍵を生成し、
そのまま .env に貼り付けられる形式で出力します。

実行方法:
    python scripts/generate_vapid_keys.py

出力された 3 行を .env に追記してください（鍵は絶対に Git にコミットしない）。
"""

from __future__ import annotations

import base64
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

# Windows のコンソール（cp932）でも絵文字を表示できるよう UTF-8 に切り替える。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass


def _b64url(data: bytes) -> str:
    """URL-safe Base64（パディング無し）にエンコードする。"""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_keys() -> tuple[str, str]:
    """VAPID 用の P-256 鍵ペアを生成して (public, private) を返す。

    Returns:
        ``(public_key, private_key)`` をいずれも URL-safe Base64 文字列で。
        - public_key: 65 バイトの非圧縮 EC ポイント（ブラウザに渡す applicationServerKey）。
        - private_key: 32 バイトの生の秘密鍵スカラ（pywebpush に渡す）。
    """
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key()

    # 公開鍵: 非圧縮ポイント（0x04 + X32 + Y32 = 65 バイト）。
    public_bytes = public.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )

    # 秘密鍵: 32 バイトのスカラ値。
    private_value = private.private_numbers().private_value
    private_bytes = private_value.to_bytes(32, byteorder="big")

    return _b64url(public_bytes), _b64url(private_bytes)


def main() -> None:
    """鍵を生成し、.env 貼り付け用に出力する。"""
    public_key, private_key = generate_keys()

    print("=" * 70)
    print("✅ VAPID キーペアを生成しました")
    print("=" * 70)
    print()
    print("以下の 3 行を .env に追記してください（VAPID_EMAIL は自分のメールに変更）:")
    print()
    print("# ★ プッシュ通知設定（Web Push / VAPID）")
    print(f"VAPID_PUBLIC_KEY={public_key}")
    print(f"VAPID_PRIVATE_KEY={private_key}")
    print("VAPID_EMAIL=mailto:you@example.com")
    print()
    print("=" * 70)
    print("⚠️  これらの鍵（特に VAPID_PRIVATE_KEY）は絶対に Git にコミットしないでください。")
    print("    .env は .gitignore 済みです。")
    print("=" * 70)


if __name__ == "__main__":
    main()
