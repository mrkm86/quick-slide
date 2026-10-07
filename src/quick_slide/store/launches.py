"""launch（ブラウザ起こし）。トークンは平文を保存せず、attach は同一 UPDATE の条件で 1 回だけ成功する。

会話から来たときの画面の開き方（スキルが URL を会話に出す → 画面が開いたら attach が届く → 来なければ
サーバが外部ブラウザで開く → それでも来なければ URL を案内する）の、サーバ側の記録。
"""

from __future__ import annotations

from typing import Any

from ..clock import add_seconds
from ..enums import ATTACHED_VIA, LAUNCH_REQUESTED_VIA, require
from ..errors import StateError, ValidationError
from ..security.tokens import hash_token, new_token
from .base import StoreBase, dump_json, load_json, reject_unknown_keys

CLIENT_KEYS = {"user_agent", "inner_width", "inner_height", "user_agent_data", "has_opener", "has_parent", "clipboard"}


class LaunchesMixin(StoreBase):
    def create_launch(self, *, requested_via: str, deck_id: str | None = None) -> dict[str, Any]:
        """戻り値の `token` は平文で、この戻り値以外に残らない（URL に付けて返すためだけ）。"""
        require(requested_via, LAUNCH_REQUESTED_VIA, "launches.requested_via")
        with self.transaction():
            if deck_id is not None:
                self._get("decks", deck_id)
            now = self.now()
            token = new_token()
            lid = self.new_id()
            self._exec(
                "INSERT INTO launches(id,deck_id,token_hash,requested_via,state,requested_at,expires_at) VALUES(?,?,?,?,?,?,?)",
                (lid, deck_id, hash_token(token), requested_via, "pending", now,
                 add_seconds(now, self.settings.launch.token_ttl_seconds)),
            )
            self.append_event("launch_created", {"launch_id": lid, "requested_via": requested_via}, deck_id=deck_id)
            row = self._get("launches", lid)
            row["token"] = token
            return row

    def attach_launch(self, token: str, *, attached_via: str = "unknown", client: dict[str, Any] | None = None) -> dict[str, Any]:
        """token_hash 一致・pending・期限内を同一 UPDATE の条件にして 1 回だけ成功させる。0 行なら拒否して events に記録。"""
        require(attached_via, ATTACHED_VIA, "launches.attached_via")
        client = client or {}
        if not isinstance(client, dict):
            raise ValidationError("client_json はオブジェクト")
        reject_unknown_keys(client, CLIENT_KEYS, "client_json")
        th = hash_token(token or "")
        with self.transaction():
            now = self.now()
            n = self._exec(
                "UPDATE launches SET state='attached',attached_at=?,attached_via=?,client_json=?"
                " WHERE token_hash=? AND state='pending' AND expires_at>?",
                (now, attached_via, dump_json(client), th, now),
            )
            if n == 1:
                row = self._one("SELECT * FROM launches WHERE token_hash=?", (th,))
                row["client"] = load_json(row.pop("client_json"))
                self.append_event("launch_attached", {"launch_id": row["id"], "attached_via": attached_via}, deck_id=row["deck_id"])
                return row
        # 拒否の記録は別トランザクションで commit してから例外を投げる（例外で rollback されると記録が消える）
        with self.transaction():
            now = self.now()
            existing = self._one("SELECT * FROM launches WHERE token_hash=?", (th,))
            if existing is None:
                reason, lid, did = "unknown_token", None, None
            elif existing["state"] == "attached":
                reason, lid, did = "already_consumed", existing["id"], existing["deck_id"]
            elif existing["state"] == "pending" and existing["expires_at"] <= now:
                self._exec("UPDATE launches SET state='expired' WHERE id=? AND state='pending'", (existing["id"],))
                reason, lid, did = "expired", existing["id"], existing["deck_id"]
            else:
                reason, lid, did = existing["state"], existing["id"], existing["deck_id"]
            self.append_event("launch_attach_rejected", {"launch_id": lid, "reason": reason}, deck_id=did)
        raise StateError(f"launch の attach を拒否しました（{reason}）")

    def expire_launches(self) -> list[str]:
        """期限切れの pending → expired。行は消さない。"""
        with self.transaction():
            now = self.now()
            rows = self._all("SELECT id,deck_id FROM launches WHERE state='pending' AND expires_at<=?", (now,))
            for r in rows:
                self._exec("UPDATE launches SET state='expired' WHERE id=? AND state='pending'", (r["id"],))
                self.append_event("launch_expired", {"launch_id": r["id"]}, deck_id=r["deck_id"])
            return [r["id"] for r in rows]

    def launch(self, launch_id: str) -> dict[str, Any]:
        row = self._get("launches", launch_id)
        row["client"] = load_json(row.pop("client_json"))
        return row

    def record_clipboard_copy(self, *, launch_id: str | None, method: str, ok: bool, error_name: str | None = None,
                              deck_id: str | None = None) -> None:
        require(method, ("api", "context_menu_hint", "external_browser", "save"), "clipboard_copy.method")
        with self.transaction():
            self.append_event("clipboard_copy", {"launch_id": launch_id, "method": method, "ok": bool(ok), "error_name": error_name},
                              deck_id=deck_id)
