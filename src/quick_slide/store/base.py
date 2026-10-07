"""Store の基底。接続・時計・ID・JSON・行変換・トランザクションを持ち、各ミックスインが使う。

生の SQL はこの `store/` パッケージの中に閉じる。上位は dict / dataclass しか受け取らない。
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import Any

from ..clock import Clock
from ..enums import ORIGIN_RUNTIMES, require
from ..db.connection import Transaction
from ..errors import NotFoundError, StateError, ValidationError
from ..ids import new_id
from ..paths import DataRoot
from ..security.hmac_key import conversation_hash
from ..settings import Settings

log = logging.getLogger("quick_slide.store")

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# events.payload_json など、永続化する JSON に決して入れてはいけないキー
FORBIDDEN_PERSISTED_KEYS = frozenset({"conversation_key", "transcript_excerpt", "token", "manuscript", "hmac_key"})


def dump_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_json(text: str | None) -> Any:
    return None if text is None else json.loads(text)


def assert_no_forbidden_keys(obj: Any, where: str) -> None:
    """再帰的に辞書キーを走査し、秘匿キーが混ざっていたら拒否する（プログラミング上の事故を止める最後の網）。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in FORBIDDEN_PERSISTED_KEYS:
                raise ValidationError(f"{where} に永続化禁止のキー '{k}' が含まれています")
            assert_no_forbidden_keys(v, where)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            assert_no_forbidden_keys(v, where)


def require_str(obj: dict, key: str, where: str, *, allow_empty: bool = False, optional: bool = False) -> str | None:
    if key not in obj or obj[key] is None:
        if optional:
            return None
        raise ValidationError(f"{where}.{key} は必須")
    v = obj[key]
    if not isinstance(v, str):
        raise ValidationError(f"{where}.{key} は文字列")
    if not allow_empty and not v.strip():
        raise ValidationError(f"{where}.{key} は空にできない")
    return v


def require_number(obj: dict, key: str, where: str, *, minimum: float | None = None, maximum: float | None = None,
                   exclusive_min: bool = False, optional: bool = False) -> float | None:
    if key not in obj or obj[key] is None:
        if optional:
            return None
        raise ValidationError(f"{where}.{key} は必須")
    v = obj[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
        raise ValidationError(f"{where}.{key} は有限の数値")
    if minimum is not None and (v <= minimum if exclusive_min else v < minimum):
        raise ValidationError(f"{where}.{key} が下限を下回る")
    if maximum is not None and v > maximum:
        raise ValidationError(f"{where}.{key} が上限を超える")
    return v


def require_hex_color(value: Any, where: str) -> str:
    if not isinstance(value, str) or not HEX_COLOR_RE.match(value):
        raise ValidationError(f"{where} は #RRGGBB の 6 桁 hex")
    return value


def reject_unknown_keys(obj: dict, allowed: set[str], where: str) -> None:
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise ValidationError(f"{where} に未知のキーがあります: {', '.join(unknown)}")


class StoreBase:
    def __init__(
        self,
        root: DataRoot,
        conn: sqlite3.Connection,
        *,
        clock: Clock,
        settings: Settings,
        hmac_key: bytes,
    ) -> None:
        self.root = root
        self.conn = conn
        self.clock = clock
        self.settings = settings
        self._hmac_key = hmac_key

    # --- 基本部品 ---
    def now(self) -> str:
        return self.clock.now()

    @staticmethod
    def new_id() -> str:
        return new_id()

    def transaction(self) -> Transaction:
        return Transaction(self.conn)

    def _require_tx(self) -> None:
        if not self.conn.in_transaction:
            raise StateError("この操作はトランザクション内で呼ぶ（store.transaction() を使う）")

    def close(self) -> None:
        self.conn.close()

    def conversation_hash_for(self, origin_runtime: str, conversation_key: str) -> str:
        """会話識別子を HMAC 化する。**生値を受け取るのはここまで**で、戻るのは 64 桁の 16 進数だけ。"""
        require(origin_runtime, ORIGIN_RUNTIMES, "origin.runtime")
        return conversation_hash(self._hmac_key, origin_runtime, conversation_key)

    # --- SQL の薄い包み。上位には出さない ---
    def _one(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        row = self.conn.execute(sql, params).fetchone()
        return None if row is None else dict(row)

    def _all(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def _exec(self, sql: str, params: tuple | dict = ()) -> int:
        return self.conn.execute(sql, params).rowcount

    def _scalar(self, sql: str, params: tuple | dict = ()) -> Any:
        row = self.conn.execute(sql, params).fetchone()
        return None if row is None else row[0]

    def _get(self, table: str, id_value: str, *, id_column: str = "id") -> dict[str, Any]:
        row = self._one(f"SELECT * FROM {table} WHERE {id_column}=?", (id_value,))
        if row is None:
            raise NotFoundError(f"{table} に該当がありません")
        return row
