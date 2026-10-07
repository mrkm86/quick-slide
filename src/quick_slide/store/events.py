"""events 台帳。状態更新と同じトランザクションで追記する。"""

from __future__ import annotations

from typing import Any

from ..enums import EVENT_KINDS
from ..errors import ValidationError
from .base import StoreBase, assert_no_forbidden_keys, dump_json, load_json


class EventsMixin(StoreBase):
    def append_event(self, kind: str, payload: dict[str, Any], *, deck_id: str | None = None) -> int:
        """追記して seq を返す。トランザクション内であることを要求する（単独で呼ぶなら外側で開く）。"""
        self._require_tx()
        if kind not in EVENT_KINDS:
            raise ValidationError(f"未知のイベント種別: {kind}")
        if not isinstance(payload, dict):
            raise ValidationError("イベント payload はオブジェクト")
        assert_no_forbidden_keys(payload, "events.payload_json")
        cur = self.conn.execute(
            "INSERT INTO events(deck_id,kind,payload_json,created_at) VALUES(?,?,?,?)",
            (deck_id, kind, dump_json(payload), self.now()),
        )
        return int(cur.lastrowid)

    def events(
        self,
        *,
        deck_id: str | None = None,
        after_seq: int = 0,
        kinds: tuple[str, ...] | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        sql = "SELECT seq,deck_id,kind,payload_json,created_at FROM events WHERE seq>?"
        params: list[Any] = [after_seq]
        if deck_id is not None:
            sql += " AND deck_id=?"
            params.append(deck_id)
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            params.extend(kinds)
        sql += " ORDER BY seq LIMIT ?"
        params.append(limit)
        rows = self._all(sql, tuple(params))
        for r in rows:
            r["payload"] = load_json(r.pop("payload_json"))
        return rows
