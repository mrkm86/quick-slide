"""画面が読む投影（**読み取りだけ**。状態は変えない）。

- ホーム / 一覧 / 検索のカード
- ごみ箱の一覧
- 削除のための asset 参照の和集合（参照の数は保存せず、全参照の和集合からその都度導く）

件数・状態は**実際に読んだ行**から出す。推測値・見積り（残り時間・進捗率）はここに置かない。
ページの枚数・先頭の画像は「見せる回」（`runs.shown_run`）から数える。
"""

from __future__ import annotations

from typing import Any

from ..enums import DECK_MODES, DECK_STATUSES, require
from ..errors import ValidationError
from .base import StoreBase

# 一覧の 1 ページ上限。ローカル用途なので大きめでよいが、無制限にはしない
MAX_LIST_LIMIT = 200


def _like_escape(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# assets への参照の和集合（削除時の専有 / 共有の判定に使う）。deck_id が NULL の行はデッキに属さない参照。
# 描いた回のページの画像はそのデッキの専有（P3 で持ち込みの画像の参照を足す）
_ASSET_REFS_CTE = (
    "WITH refs(asset_id, deck_id, source) AS ("
    " SELECT asset_id, NULL, 'brand_assets' FROM brand_assets"
    " UNION ALL SELECT rp.asset_id, r.deck_id, 'run_pages' FROM run_pages rp JOIN runs r ON r.id=rp.run_id"
    "  WHERE rp.asset_id IS NOT NULL"
    ")"
)


class DeckViewsMixin(StoreBase):
    # --- カード ---
    def deck_card(self, deck_row: dict[str, Any]) -> dict[str, Any]:
        """`decks` の 1 行に、画面のカードに要る派生値を足す。"""
        did = deck_row["id"]
        brand = self._one("SELECT b.id,b.name FROM brand_revisions br JOIN brands b ON b.id=br.brand_id WHERE br.id=?",
                          (deck_row["brand_revision_id"],))
        style = self._one("SELECT t.id,t.name FROM style_revisions tr JOIN styles t ON t.id=tr.style_id WHERE tr.id=?",
                          (deck_row["style_revision_id"],))
        pages = self.deck_page_summary(did)
        return {
            "id": did,
            "title": deck_row["title"],
            "subtitle": deck_row["subtitle"],
            "purpose": deck_row["purpose"],
            "audience": deck_row["audience"],
            "mode": deck_row["mode"],
            "status": deck_row["status"],
            "pinned": bool(deck_row["pinned"]),
            "archived": deck_row["archived_at"] is not None,
            "archived_at": deck_row["archived_at"],
            "deleted_at": deck_row["deleted_at"],
            "origin_deck_id": deck_row["origin_deck_id"],
            # 構成（台本の版＋採用した描いた回）。P2 は「見せる回」の id（P3 で採用が入る）
            "current_revision_id": pages["run_id"],
            "lock_version": deck_row["lock_version"],
            "created_at": deck_row["created_at"],
            "updated_at": deck_row["updated_at"],
            "tags": [t["name"] for t in self.deck_tags(did)],
            "brand": brand,
            "style": style,
            "page_count": pages["page_count"],
            "ready_count": pages["ready_count"],
            "thumbnail_asset_id": pages["thumbnail_asset_id"],
            "working": self.deck_working(did),
        }

    def home_decks(self, *, recent_limit: int = 10, mode: str | None = None) -> dict[str, Any]:
        """ホーム: ピン枠 + 最近（未アーカイブ・未削除・非ピン、更新日時降順 N 件）+ 一覧へ送る残り件数。

        `mode` を渡すとその形式だけ。`mode_counts` は形式ごとの未アーカイブ・未削除の件数（絞り込みの選択肢）。"""
        if mode is not None:
            require(mode, DECK_MODES, "mode")
        pinned = [self.deck_card(d) for d in self.pinned_decks() if mode is None or d["mode"] == mode]
        recent = [self.deck_card(d) for d in self.recent_decks(recent_limit, mode=mode)]
        live_unpinned = int(self._scalar(
            "SELECT COUNT(*) FROM decks WHERE deleted_at IS NULL AND archived_at IS NULL AND pinned=0"
            " AND (? IS NULL OR mode=?)", (mode, mode)))
        counts = {r["mode"]: int(r["n"]) for r in self._all(
            "SELECT mode,COUNT(*) AS n FROM decks WHERE deleted_at IS NULL AND archived_at IS NULL GROUP BY mode")}
        return {"pinned": pinned, "recent": recent, "recent_limit": recent_limit,
                "more_count": max(0, live_unpinned - len(recent)), "mode_counts": counts}

    def search_decks(
        self,
        *,
        q: str | None = None,
        status: str | None = None,
        mode: str | None = None,
        archived: bool | None = False,
        pinned: bool | None = None,
        brand_id: str | None = None,
        tag: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """一覧: 検索はタイトル / サブタイトル / 目的 / 読者 / タグ名。件数（total）も同じ条件で数える。
        `archived=None` はアーカイブの有無を問わない。"""
        if status is not None:
            require(status, DECK_STATUSES, "status")
        if mode is not None:
            require(mode, DECK_MODES, "mode")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > MAX_LIST_LIMIT:
            raise ValidationError(f"limit は 1〜{MAX_LIST_LIMIT}")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValidationError("offset は 0 以上")
        params: dict[str, Any] = {
            "archive": None if archived is None else (1 if archived else 0),
            "pinned": None if pinned is None else (1 if pinned else 0),
            "status": status,
            "mode": mode,
            "brand": brand_id,
            "q": _like_escape(q.strip()) if q and q.strip() else None,
            "tag": tag.strip() if tag and tag.strip() else None,
            "limit": limit,
            "offset": offset,
        }
        where = (
            " FROM decks d JOIN brand_revisions br ON br.id=d.brand_revision_id"
            " WHERE d.deleted_at IS NULL"
            " AND (:archive IS NULL OR (d.archived_at IS NOT NULL)=:archive)"
            " AND (:pinned IS NULL OR d.pinned=:pinned)"
            " AND (:status IS NULL OR d.status=:status)"
            " AND (:mode IS NULL OR d.mode=:mode)"
            " AND (:brand IS NULL OR br.brand_id=:brand)"
            " AND (:q IS NULL OR d.title LIKE :q ESCAPE '\\' OR d.subtitle LIKE :q ESCAPE '\\'"
            "      OR d.purpose LIKE :q ESCAPE '\\' OR d.audience LIKE :q ESCAPE '\\'"
            "      OR EXISTS(SELECT 1 FROM deck_tags dt JOIN tags t ON t.id=dt.tag_id WHERE dt.deck_id=d.id AND t.name LIKE :q ESCAPE '\\'))"
            " AND (:tag IS NULL OR EXISTS(SELECT 1 FROM deck_tags dt JOIN tags t ON t.id=dt.tag_id WHERE dt.deck_id=d.id AND t.name=:tag))"
        )
        total = int(self._scalar("SELECT COUNT(*)" + where, params))
        # 形式ごとの件数は、形式の絞り込みだけを外した同じ条件で数える（絞り込みの選択肢に件数を添えるため）
        counts = {r["mode"]: int(r["n"]) for r in self._all(
            "SELECT d.mode AS mode,COUNT(*) AS n" + where + " GROUP BY d.mode", {**params, "mode": None})}
        # 同着（updated_at が同値）は挿入順 = rowid で決める。乱数 id で決めると並びが実行ごとに変わる
        rows = self._all("SELECT d.*" + where + " ORDER BY d.updated_at DESC,d.rowid DESC LIMIT :limit OFFSET :offset", params)
        return {"items": [self.deck_card(d) for d in rows], "total": total, "limit": limit, "offset": offset,
                "mode_counts": counts}

    def all_tags(self) -> list[dict[str, Any]]:
        return self._all(
            "SELECT t.id,t.name,COUNT(dt.deck_id) AS decks FROM tags t"
            " LEFT JOIN deck_tags dt ON dt.tag_id=t.id"
            " LEFT JOIN decks d ON d.id=dt.deck_id AND d.deleted_at IS NULL"
            " GROUP BY t.id ORDER BY t.name")

    def last_event_seq(self, deck_id: str | None = None) -> int:
        if deck_id is None:
            return int(self._scalar("SELECT COALESCE(MAX(seq),0) FROM events"))
        return int(self._scalar("SELECT COALESCE(MAX(seq),0) FROM events WHERE deck_id=?", (deck_id,)))

    # --- 削除のための参照 ---
    def deck_asset_usage(self, deck_id: str) -> list[dict[str, Any]]:
        """このデッキが参照する asset の一覧と、**デッキの外からも参照されているか**（`shared`）。"""
        rows = self._all(
            _ASSET_REFS_CTE
            + " SELECT a.id AS asset_id,a.relative_path,a.media_type,a.bytes,a.sha256,a.state,"
            "  SUM(CASE WHEN r.deck_id=:d THEN 1 ELSE 0 END) AS own_refs,"
            "  SUM(CASE WHEN r.deck_id IS NULL OR r.deck_id!=:d THEN 1 ELSE 0 END) AS other_refs"
            " FROM refs r JOIN assets a ON a.id=r.asset_id"
            " WHERE a.id IN (SELECT asset_id FROM refs WHERE deck_id=:d)"
            " GROUP BY a.id ORDER BY a.relative_path", {"d": deck_id})
        for r in rows:
            r["own_refs"] = int(r["own_refs"])
            r["other_refs"] = int(r["other_refs"])
            r["shared"] = r["other_refs"] > 0
        return rows

    def unreferenced_asset_ids(self, asset_ids: list[str]) -> list[str]:
        """渡した asset のうち、**もうどこからも参照されていない**もの。

        完全削除が「この画像は消してよいか」を決める唯一の問い。デッキの行を消したあとに呼び、
        **消したあとの DB にもう一度訊く**（控えた値と実際の参照が食い違えば、他がまだ使っている画像を消してしまう）。
        """
        if not asset_ids:
            return []
        marks = ",".join("?" * len(asset_ids))
        rows = self._all(
            _ASSET_REFS_CTE + f" SELECT DISTINCT asset_id FROM refs WHERE asset_id IN ({marks})", tuple(asset_ids))
        still = {r["asset_id"] for r in rows}
        return [a for a in asset_ids if a not in still]

    def asset_references(self, asset_id: str) -> list[dict[str, Any]]:
        return self._all(_ASSET_REFS_CTE + " SELECT deck_id,source,COUNT(*) AS n FROM refs WHERE asset_id=? GROUP BY deck_id,source",
                         (asset_id,))

    # --- ごみ箱 ---
    def deletion_tasks_for_deck(self, deck_id: str) -> list[dict[str, Any]]:
        # 同着（created_at が同値）は挿入順 = rowid で決める。乱数 id で決めると「最新」が実行ごとに変わる
        return self._all("SELECT * FROM deletion_tasks WHERE deck_id=? ORDER BY created_at DESC,rowid DESC", (deck_id,))

    def trashed_decks(self) -> list[dict[str, Any]]:
        """ごみ箱の一覧: 削除済み（deleted_at あり）で、最新の削除タスクが trashed / failed / pending のデッキ。"""
        return self._all(
            "SELECT d.*, t.id AS deletion_task_id, t.state AS deletion_state, t.manifest_path, t.created_at AS deleted_task_at"
            " FROM decks d JOIN deletion_tasks t ON t.deck_id=d.id"
            " WHERE d.deleted_at IS NOT NULL AND t.id=("
            "   SELECT id FROM deletion_tasks WHERE deck_id=d.id"
            "    ORDER BY created_at DESC,deletion_tasks.rowid DESC LIMIT 1)"
            " AND t.state IN ('pending','running','trashed','failed')"
            " ORDER BY d.deleted_at DESC,d.id")
