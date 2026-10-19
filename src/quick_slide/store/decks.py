"""デッキ。モード（作るときに決め、あとから替えない）、ブランドとスタイルの選択、メタデータ、
ピン・アーカイブ・論理削除、タグ、lock_version。

ページ（台本の版・描いた回）は P2 で足す。それまでデッキはページを持たない。
"""

from __future__ import annotations

from typing import Any

from ..enums import DECK_MODES, DECK_STATUSES, require
from ..errors import ConflictError, NotFoundError, StateError, ValidationError
from .base import StoreBase

DECK_TEXT_FIELDS = ("title", "subtitle", "purpose", "audience")


class DecksMixin(StoreBase):
    # --- 作成 ---
    def create_deck(
        self,
        title: str,
        *,
        mode: str,
        brand_revision_id: str,
        style_revision_id: str,
        subtitle: str = "",
        purpose: str = "",
        audience: str = "",
        origin_deck_id: str | None = None,
        deck_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(title, str) or not title.strip():
            raise ValidationError("decks.title は空にできない")
        require(mode, DECK_MODES, "decks.mode")
        with self.transaction():
            self._require_live_brand_revision(brand_revision_id)
            self._require_live_style_revision(style_revision_id)
            if origin_deck_id is not None:
                self._get("decks", origin_deck_id)
            now = self.now()
            did = deck_id or self.new_id()
            self._exec(
                "INSERT INTO decks(id,title,subtitle,purpose,audience,mode,brand_revision_id,style_revision_id,"
                " status,origin_deck_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (did, title, subtitle, purpose, audience, mode, brand_revision_id, style_revision_id,
                 "draft", origin_deck_id, now, now),
            )
            self.append_event("deck_created", {"deck_id": did, "title": title, "mode": mode}, deck_id=did)
            return self.deck(did)

    # --- 参照 ---
    def deck(self, deck_id: str) -> dict[str, Any]:
        d = self._get("decks", deck_id)
        d["tags"] = self.deck_tags(deck_id)
        return d

    def _require_live_deck(self, deck_id: str, *, allow_archived: bool = False) -> dict[str, Any]:
        d = self._get("decks", deck_id)
        if d["deleted_at"] is not None:
            raise NotFoundError("削除済みデッキです")
        if d["archived_at"] is not None and not allow_archived:
            raise StateError("アーカイブ済みデッキには書き込めません")
        return d

    def recent_decks(self, limit: int = 10, *, mode: str | None = None) -> list[dict[str, Any]]:
        # 同着（updated_at が同値）は挿入順 = rowid で決める。乱数 id で決めると並びが実行ごとに変わる
        return self._all(
            "SELECT * FROM decks WHERE deleted_at IS NULL AND archived_at IS NULL AND pinned=0 AND (? IS NULL OR mode=?)"
            " ORDER BY updated_at DESC,rowid DESC LIMIT ?", (mode, mode, limit))

    def pinned_decks(self) -> list[dict[str, Any]]:
        return self._all(
            "SELECT * FROM decks WHERE deleted_at IS NULL AND archived_at IS NULL AND pinned=1 ORDER BY updated_at DESC,rowid DESC")

    # --- 更新（lock_version による競合検知） ---
    def _bump_deck(self, deck_id: str, expected_lock_version: int | None, set_sql: str, params: tuple) -> None:
        """`UPDATE decks SET <set_sql>, updated_at, lock_version+1 WHERE id AND lock_version=expected AND deleted_at IS NULL`。
        0 行なら ConflictError（呼び出し側のトランザクションごと rollback される）。"""
        self._require_tx()
        now = self.now()
        if expected_lock_version is None:
            n = self._exec(
                f"UPDATE decks SET {set_sql}, updated_at=?, lock_version=lock_version+1 WHERE id=? AND deleted_at IS NULL",
                (*params, now, deck_id),
            )
        else:
            n = self._exec(
                f"UPDATE decks SET {set_sql}, updated_at=?, lock_version=lock_version+1"
                " WHERE id=? AND lock_version=? AND deleted_at IS NULL",
                (*params, now, deck_id, expected_lock_version),
            )
        if n != 1:
            d = self._one("SELECT lock_version, deleted_at FROM decks WHERE id=?", (deck_id,))
            if d is None or d["deleted_at"] is not None:
                raise NotFoundError("デッキが存在しないか削除済みです")
            raise ConflictError("他のタブ/プロセスが先に更新しました（lock_version 不一致）。再読込してください")

    def update_deck_metadata(self, deck_id: str, *, expected_lock_version: int | None = None, **fields: Any) -> dict[str, Any]:
        """タイトル・副題・目的・読者・状態。**モードは変えられない**（替えたいときはデッキを作り直す）。"""
        allowed = set(DECK_TEXT_FIELDS) | {"status"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValidationError(f"更新できない項目: {', '.join(sorted(unknown))}")
        if not fields:
            raise ValidationError("更新項目がありません")
        with self.transaction():
            self._require_live_deck(deck_id)
            sets, params = [], []
            for k, v in fields.items():
                if k == "status":
                    require(v, DECK_STATUSES, "decks.status")
                    if v == "final":
                        # 確定は「全ページの絵ができている」ことが条件。ページは P2 で入るので、それまでは確定できない
                        raise StateError("ページが無いデッキは確定できません")
                elif k == "title":
                    if not isinstance(v, str) or not v.strip():
                        raise ValidationError("decks.title は空にできない")
                elif not isinstance(v, str):
                    raise ValidationError(f"decks.{k} は文字列")
                sets.append(f"{k}=?")
                params.append(v)
            self._bump_deck(deck_id, expected_lock_version, ", ".join(sets), tuple(params))
            self.append_event("deck_updated", {"deck_id": deck_id, "fields": sorted(fields)}, deck_id=deck_id)
            return self.deck(deck_id)

    def change_deck_axes(
        self,
        deck_id: str,
        *,
        expected_lock_version: int | None = None,
        brand_revision_id: str | None = None,
        style_revision_id: str | None = None,
    ) -> dict[str, Any]:
        """ブランド / スタイルの選択を変える（次に描く回から効く）。モードは変えない。"""
        with self.transaction():
            self._require_live_deck(deck_id)
            if self.deck_template_snapshot_ref(deck_id):   # type: ignore[attr-defined]
                raise StateError("デザインテンプレートで作った資料は、受け付けたときのブランドとテンプレートで固定します。"
                                 "別の設定を使うときは、新しい資料として頼み直してください。")
            sets, params = [], []
            if brand_revision_id is not None:
                self._require_live_brand_revision(brand_revision_id)
                sets.append("brand_revision_id=?")
                params.append(brand_revision_id)
            if style_revision_id is not None:
                self._require_live_style_revision(style_revision_id)
                sets.append("style_revision_id=?")
                params.append(style_revision_id)
            if not sets:
                raise ValidationError("更新項目がありません")
            self._bump_deck(deck_id, expected_lock_version, ", ".join(sets), tuple(params))
            self.append_event("deck_updated", {"deck_id": deck_id, "fields": ["axes"]}, deck_id=deck_id)
            return self.deck(deck_id)

    def follow_deck_settings(self, deck_id: str, *, brand_revision_id: str, style_revision_id: str) -> bool:
        """描く回を始めるとき、デッキが指すブランド・スタイルの改訂をいまの改訂へ進める（P4。設定を変えたら次に描く回から
        効く）。`lock_version` は上げない（利用者の編集と重ならない・画面の「別の画面で変わった」を出さない）。
        変えたら True。"""
        with self.transaction():
            d = self._get("decks", deck_id)
            if (d["brand_revision_id"], d["style_revision_id"]) == (brand_revision_id, style_revision_id):
                return False
            self._exec("UPDATE decks SET brand_revision_id=?,style_revision_id=? WHERE id=?",
                       (brand_revision_id, style_revision_id, deck_id))
            self.append_event("deck_updated", {"deck_id": deck_id, "fields": ["axes"], "by": "run"}, deck_id=deck_id)
            return True

    def set_pinned(self, deck_id: str, pinned: bool, *, expected_lock_version: int | None = None) -> None:
        with self.transaction():
            self._require_live_deck(deck_id)
            self._bump_deck(deck_id, expected_lock_version, "pinned=?", (1 if pinned else 0,))

    def set_archived(self, deck_id: str, archived: bool, *, expected_lock_version: int | None = None) -> None:
        with self.transaction():
            self._require_live_deck(deck_id, allow_archived=True)
            self._bump_deck(deck_id, expected_lock_version, "archived_at=?", (self.now() if archived else None,))

    def soft_delete_deck(self, deck_id: str, *, manifest_path: str, deletion_id: str | None = None) -> dict[str, Any]:
        """deleted_at と deletion_tasks 行を同時 commit。ファイル退避は `delete_deck_to_trash`（store/management.py）が行う。
        `deletion_id` は省略可（`.trash/<deletion-id>/` のパスを manifest_path に入れるため、先に決めた ID を渡せる）。"""
        with self.transaction():
            d = self._get("decks", deck_id)
            if d["deleted_at"] is not None:
                raise StateError("既に削除済みです")
            now = self.now()
            self._exec("UPDATE decks SET deleted_at=?,updated_at=?,lock_version=lock_version+1 WHERE id=?", (now, now, deck_id))
            tid = deletion_id or self.new_id()
            self._exec(
                "INSERT INTO deletion_tasks(id,deck_id,state,manifest_path,created_at) VALUES(?,?,?,?,?)",
                (tid, deck_id, "pending", manifest_path, now),
            )
            self.append_event("deck_updated", {"deck_id": deck_id, "fields": ["deleted_at"], "deletion_task_id": tid}, deck_id=deck_id)
            return self._get("deletion_tasks", tid)

    # --- タグ ---
    def add_tag(self, deck_id: str, name: str) -> dict[str, Any]:
        if not name or not name.strip():
            raise ValidationError("tags.name は空にできない")
        with self.transaction():
            self._require_live_deck(deck_id, allow_archived=True)
            tag = self._one("SELECT * FROM tags WHERE name=?", (name,))
            if tag is None:
                tag = {"id": self.new_id(), "name": name}
                self._exec("INSERT INTO tags(id,name) VALUES(?,?)", (tag["id"], tag["name"]))
            self._exec("INSERT OR IGNORE INTO deck_tags(deck_id,tag_id) VALUES(?,?)", (deck_id, tag["id"]))
            return tag

    def remove_tag(self, deck_id: str, tag_id: str) -> None:
        with self.transaction():
            self._exec("DELETE FROM deck_tags WHERE deck_id=? AND tag_id=?", (deck_id, tag_id))

    def deck_tags(self, deck_id: str) -> list[dict[str, Any]]:
        return self._all(
            "SELECT t.id,t.name FROM deck_tags dt JOIN tags t ON t.id=dt.tag_id WHERE dt.deck_id=? ORDER BY t.name", (deck_id,))
