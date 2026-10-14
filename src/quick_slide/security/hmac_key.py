"""会話識別子のハッシュ鍵。

- `config/hmac.key`: 32 バイト乱数、0600。無ければ生成する。読めなければ StartupError（終了コード 3）
- `conversation_hash = HMAC-SHA256(key, origin_runtime + "\\n" + conversation_key)` の小文字 hex 64 文字
- 生の conversation_key はこの関数の引数としてメモリ上に存在するだけで、返り値・例外・ログに含めない
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import stat
from pathlib import Path

from .. import oscompat
from ..errors import StartupError

KEY_BYTES = 32


def load_or_create_key(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    if not path.exists():
        _create(path)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            os.chmod(path, 0o600)
        key = path.read_bytes()
    except OSError as exc:
        raise StartupError(f"hmac.key を読めません（{exc.__class__.__name__}）") from exc
    if len(key) != KEY_BYTES:
        raise StartupError(f"hmac.key の長さが {KEY_BYTES} バイトではありません")
    return key


def _create(path: Path) -> None:
    key = secrets.token_bytes(KEY_BYTES)
    tmp = path.with_name(path.name + ".tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        try:
            os.write(fd, key)
            os.fsync(fd)
        finally:
            os.close(fd)
        oscompat.replace(tmp, path)
        os.chmod(path, 0o600)
    except FileExistsError:
        # 同時起動で別プロセスが作成中。相手の完成を待たずに、出来上がった本体を読む側に回る
        if tmp.exists() and not path.exists():
            raise StartupError("hmac.key を別プロセスが作成中です。再試行してください")
    except OSError as exc:
        raise StartupError(f"hmac.key を作成できません（{exc.__class__.__name__}）") from exc


def conversation_hash(key: bytes, origin_runtime: str, conversation_key: str) -> str:
    message = (origin_runtime + "\n" + conversation_key).encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()
