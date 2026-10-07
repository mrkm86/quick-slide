"""launch トークン。平文は URL に付けて返すだけで DB には SHA-256 のみ保存する。
トークンは乱数（256 bit）なので塩は不要。"""

from __future__ import annotations

import hashlib
import secrets


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
