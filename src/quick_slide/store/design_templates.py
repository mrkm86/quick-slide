"""デザインテンプレート。版の番号（v1・v2）と、保存のたびに増える不変の改訂の行を分けて持つ。

- 利用者が見るのは `revision`（版の番号）。「いまの版を上書き」は、同じ版の新しい改訂の行を足す（UPDATE しない）。
  同じ版の行は `save_sequence` の順。版番号で引くと、その版の最後の保存（と、そのとき控えたブランドの版）を返す
- 内部の改訂の ID（`revision_id`）で引けば、上書きの前の行も一意にたどれる
- 保存の競合は `expected_revision_id`（呼び手が見ていた current の改訂の ID）と、いまの current の一致を
  保存と同じトランザクションの中で検査する（食い違えば ConflictError。API 層が 409 にする）
- 保存する文書は `design_templates.validate` を通した v3（id・brand_id・revision・based_on と、基本・構成・追加指示・リソース）だけ。
  内部の改訂の ID・会話の HMAC・保存の種類は文書に混ぜない
- 同じ中身の保存は何も作らない（`unchanged`）。比べるのは4項目だけを正規化したもののハッシュ
- 会話の識別子は `conversation_hash_for` で HMAC にしてからだけ持つ。生の値は残さない
- 削除は論理削除。改訂の行は残す。プリセット（付属）のブランドの下にも作れる
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any

from .. import design_templates as dt
from ..errors import ConflictError, NotFoundError, StateError, ValidationError
from .base import StoreBase, dump_json, load_json

# 差分・変更箇所の呼び名（画面のタブと同じ）
SECTION_BASIC, SECTION_STRUCTURE, SECTION_INSTRUCTIONS, SECTION_RESOURCES = "基本", "構成", "追加指示", "リソース"
SECTION_LABELS = (SECTION_BASIC, SECTION_STRUCTURE, SECTION_INSTRUCTIONS, SECTION_RESOURCES)

COPY_SUFFIX = "のコピー"


def normalized_sections(document: dict[str, Any]) -> dict[str, Any]:
    """4項目だけを、比べられる形にする。機械の ID・版・由来・添付の URL は除く。本文は要約も空白圧縮もしない。
    モードの集合と、順序に意味のないリソースの並びは並べ替える（Markdown の読み込みで行の ID が変わっても同じ形になる）。"""
    files = sorted(({k: r[k] for k in ("name", "kind", "path", "usage")} for r in document["resources"]), key=dump_json)
    attachments = sorted(({k: r[k] for k in ("name", "sha256", "usage")} for r in document["attachments"]), key=dump_json)
    return {
        SECTION_BASIC: {"name": document["name"], "description": document["description"], "modes": sorted(document["modes"])},
        SECTION_STRUCTURE: document["structure"],
        SECTION_INSTRUCTIONS: document["instructions"],
        SECTION_RESOURCES: {"files": files, "attachments": attachments},
    }


def content_sha256(document: dict[str, Any]) -> str:
    return hashlib.sha256(dump_json(normalized_sections(document)).encode("utf-8")).hexdigest()


def changed_sections(before: dict[str, Any] | None, after: dict[str, Any]) -> list[str]:
    """前の文書から変わった項目（基本・構成・追加指示・リソース）。前が無ければ空（最初の版）。"""
    if before is None:
        return []
    a, b = normalized_sections(before), normalized_sections(after)
    return [label for label in SECTION_LABELS if a[label] != b[label]]


def _validated(document: Any, template_id: str, brand_id: str, revision: int, based_on: int | None) -> dict[str, Any]:
    """入力の文書を v3 にして検証し、サーバが決める id・brand_id・revision・based_on を差し込んで、もう一度検証する。
    呼び手が送ってきた id・版・由来は使わない（由来の偽装を許さない）。"""
    if not isinstance(document, dict):
        raise ValidationError("テンプレートの保存形式を確認してください。")
    first = copy.deepcopy(document)
    first.update({"id": template_id, "brand_id": brand_id, "revision": 0, "based_on": None})
    try:
        d = dt.validate(first)
        d.update({"id": template_id, "brand_id": brand_id, "revision": revision, "based_on": based_on})
        return dt.validate(d)
    except ValueError as exc:
        raise ValidationError(str(exc)) from None


class DesignTemplatesMixin(StoreBase):
    # --- 保存（書き込み）---
    def create_design_template(
        self,
        brand_id: str,
        document: dict[str, Any],
        *,
        template_id: str | None = None,
        origin_runtime: str | None = None,
        conversation_key: str | None = None,
    ) -> dict[str, Any]:
        """新規。サーバが ID を作り v1・`based_on=null`。`template_id` は、取り込みで衝突しない正規 ID を保つときだけ渡す。"""
        runtime, chash = self._origin(origin_runtime, conversation_key)
        with self.transaction():
            brand = self._live_brand(brand_id)
            tid = template_id or self.new_id()
            if self._one("SELECT 1 FROM design_templates WHERE id=?", (tid,)) is not None:
                raise ConflictError("同じ ID のテンプレートが既にあります")
            doc = _validated(document, tid, brand_id, 1, None)
            now, rev_id = self.now(), self.new_id()
            self._exec(
                "INSERT INTO design_templates(id,brand_id,current_revision_id,created_at,updated_at) VALUES(?,?,?,?,?)",
                (tid, brand_id, rev_id, now, now),
            )
            self._insert_revision(tid, rev_id, 1, 1, doc, None, brand["current_revision_id"], runtime, chash, "create", now)
            self.append_event("design_template_saved", {"template_id": tid, "revision_id": rev_id, "revision": 1, "kind": "create"})
            return self._view(self._revision_row(tid, rev_id))

    def overwrite_design_template(
        self,
        template_id: str,
        document: dict[str, Any],
        *,
        expected_revision_id: str,
        origin_runtime: str | None = None,
        conversation_key: str | None = None,
    ) -> dict[str, Any]:
        """最新の版を上書きする（同じ版番号で新しい改訂の行を足す。由来は維持）。前の版は上書きできない。"""
        return self._save_existing(template_id, document, "overwrite", expected_revision_id, None,
                                   *self._origin(origin_runtime, conversation_key))

    def add_design_template_version(
        self,
        template_id: str,
        document: dict[str, Any],
        *,
        expected_revision_id: str,
        source_revision_id: str | None = None,
        origin_runtime: str | None = None,
        conversation_key: str | None = None,
    ) -> dict[str, Any]:
        """新しい版（`MAX(revision)+1`）として保存する。由来は `source_revision_id` の改訂（省略は期待した current）。
        文書の `based_on` には、その改訂の版番号をサーバが入れる。"""
        return self._save_existing(template_id, document, "new_version", expected_revision_id, source_revision_id,
                                   *self._origin(origin_runtime, conversation_key))

    def copy_design_template_version(
        self,
        template_id: str,
        source_revision_id: str,
        *,
        expected_revision_id: str,
        document: dict[str, Any] | None = None,
        origin_runtime: str | None = None,
        conversation_key: str | None = None,
    ) -> dict[str, Any]:
        """過去の版（読み取り専用）をコピーして、最新の次の版として保存する。`document` が無ければコピー元の文書そのまま。
        最新を上書きしない。由来はコピー元の改訂から計算する。"""
        runtime, chash = self._origin(origin_runtime, conversation_key)
        if document is None:
            with self.transaction():
                document = self._revision_doc(self._revision_row(template_id, source_revision_id))
        return self._save_existing(template_id, document, "new_version", expected_revision_id, source_revision_id,
                                   runtime, chash, kind="copy")

    def save_design_template_for_conversation(
        self,
        template_id: str,
        document: dict[str, Any],
        *,
        expected_revision_id: str,
        origin_runtime: str,
        conversation_key: str | None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """会話からの保存。既定は新しい版。いまの最新の版を作ったのが同じ会話で、そのあとの保存も同じ会話だけなら、
        その版へ上書きする。途中で別の会話・画面が保存していれば新しい版。識別子が無ければ新しい版。
        `overwrite=True` は最新の版の明示的な上書き（競合の検査は省かない）。戻りの `mode` は実際に使った方。"""
        runtime, chash = self._origin(origin_runtime, conversation_key)
        with self.transaction():
            _, cur = self._live_template(template_id, expected_revision_id)
            mode = "overwrite" if overwrite or self._may_overwrite_by(cur, chash) else "new_version"
            out = self._save_existing(template_id, document, mode, expected_revision_id, None, runtime, chash)
            out["mode"] = mode
            return out

    def duplicate_design_template(
        self,
        template_id: str,
        *,
        expected_revision_id: str,
        new_template_id: str | None = None,
        name: str | None = None,
        origin_runtime: str | None = None,
        conversation_key: str | None = None,
    ) -> dict[str, Any]:
        """別のテンプレートへ複製する。新 ID・名前に「のコピー」（`name` があればその名前）・v1・`based_on=null`。旧履歴は移さない。"""
        runtime, chash = self._origin(origin_runtime, conversation_key)
        with self.transaction():
            t, cur = self._live_template(template_id, expected_revision_id)
            brand = self._live_brand(t["brand_id"])
            tid = new_template_id or self.new_id()
            if self._one("SELECT 1 FROM design_templates WHERE id=?", (tid,)) is not None:
                raise ConflictError("同じ ID のテンプレートが既にあります")
            source = self._revision_doc(cur)
            source["name"] = source["name"] + COPY_SUFFIX if name is None else name
            doc = _validated(source, tid, t["brand_id"], 1, None)
            now, rev_id = self.now(), self.new_id()
            self._exec(
                "INSERT INTO design_templates(id,brand_id,current_revision_id,created_at,updated_at) VALUES(?,?,?,?,?)",
                (tid, t["brand_id"], rev_id, now, now),
            )
            self._insert_revision(tid, rev_id, 1, 1, doc, None, brand["current_revision_id"], runtime, chash, "duplicate", now)
            self.append_event("design_template_saved", {"template_id": tid, "revision_id": rev_id, "revision": 1,
                                                        "kind": "duplicate", "source_template_id": template_id})
            return self._view(self._revision_row(tid, rev_id))

    def delete_design_template(self, template_id: str, *, expected_revision_id: str) -> None:
        """論理削除。一覧・新規の選択から外し、改訂の行は残す。削除済みへの再削除は何もしない。"""
        with self.transaction():
            t = self._get("design_templates", template_id)
            self._check_expected(t, expected_revision_id)
            if t["deleted_at"] is not None:
                return
            now = self.now()
            self._exec("UPDATE design_templates SET deleted_at=?,updated_at=? WHERE id=?", (now, now, template_id))
            self.append_event("design_template_deleted", {"template_id": template_id})

    # --- 読み取り ---
    def list_design_templates(self, brand_id: str) -> list[dict[str, Any]]:
        """ブランドのテンプレート。削除済みのテンプレートと、削除済みのブランドのものは出さない。
        並びは `updated_at` の新しい順、同時刻は作った順の新しい方（rowid の降順）が先。"""
        rows = self._all(
            "SELECT t.id AS template_id, t.brand_id, t.created_at AS template_created_at, t.updated_at,"
            " r.id AS revision_id, r.revision, r.document_json, r.created_at AS saved_at"
            " FROM design_templates t"
            " JOIN design_template_revisions r ON r.template_id=t.id AND r.id=t.current_revision_id"
            " JOIN brands b ON b.id=t.brand_id"
            " WHERE t.brand_id=? AND t.deleted_at IS NULL AND b.deleted_at IS NULL"
            # 同着は挿入順（rowid）で決める。乱数 id では並びが実行ごとに変わる
            " ORDER BY t.updated_at DESC, t.rowid DESC",
            (brand_id,),
        )
        out = []
        for r in rows:
            d = load_json(r["document_json"])
            out.append({"template_id": r["template_id"], "brand_id": r["brand_id"], "name": d["name"],
                        "description": d["description"], "modes": d["modes"], "revision": r["revision"],
                        "revision_id": r["revision_id"], "created_at": r["template_created_at"],
                        "updated_at": r["updated_at"], "saved_at": r["saved_at"]})
        return out

    def get_design_template(self, template_id: str, *, revision: int | None = None,
                            revision_id: str | None = None) -> dict[str, Any]:
        """最新（既定）・指定の版の最後の保存・指定の内部改訂を返す。削除済みでも履歴は読める（`deleted_at` で分かる）。"""
        if revision is not None and revision_id is not None:
            raise ValidationError("revision と revision_id は同時に指定できません")
        t = self._get("design_templates", template_id)
        if revision_id is not None:
            row = self._revision_row(template_id, revision_id)
        elif revision is not None:
            row = self._one(
                "SELECT * FROM design_template_revisions WHERE template_id=? AND revision=? ORDER BY save_sequence DESC LIMIT 1",
                (template_id, revision),
            )
            if row is None:
                raise NotFoundError("その版は存在しません")
        else:
            row = self._revision_row(template_id, t["current_revision_id"])
        return self._view(row, template=t)

    def design_template_revisions(self, template_id: str) -> list[dict[str, Any]]:
        """版の一覧（新しい順）。版ごとに最後の保存と、保存日時・由来の版・前の版から変わった項目を添える。"""
        t = self._get("design_templates", template_id)
        rows = self._all("SELECT * FROM design_template_revisions WHERE template_id=? ORDER BY revision, save_sequence",
                         (template_id,))
        by_version: dict[int, list[dict[str, Any]]] = {}
        for r in rows:
            by_version.setdefault(r["revision"], []).append(r)
        latest = max(by_version)
        out, previous = [], None
        for version in sorted(by_version):
            group = by_version[version]
            last = group[-1]
            doc = self._revision_doc(last)
            out.append({
                "revision": version, "revision_id": last["id"], "saved_at": last["created_at"],
                "created_at": group[0]["created_at"], "based_on": doc["based_on"],
                "based_on_revision_id": group[0]["based_on_revision_id"],
                "changed": changed_sections(previous, doc), "save_kind": last["save_kind"], "save_count": len(group),
                "is_latest": version == latest, "is_current": last["id"] == t["current_revision_id"], "document": doc,
            })
            previous = doc
        return out[::-1]

    # --- 内部 ---
    def _origin(self, origin_runtime: str | None, conversation_key: str | None) -> tuple[str | None, str | None]:
        if conversation_key is None:
            return origin_runtime, None
        if origin_runtime is None:
            raise ValidationError("会話の識別子には origin_runtime が要る")
        return origin_runtime, self.conversation_hash_for(origin_runtime, conversation_key)

    def _live_brand(self, brand_id: str) -> dict[str, Any]:
        b = self._get("brands", brand_id)
        if b["deleted_at"] is not None:
            raise StateError("削除済みブランドにはテンプレートを保存できません")
        return b

    def _check_expected(self, template: dict[str, Any], expected_revision_id: Any) -> None:
        if not isinstance(expected_revision_id, str) or not expected_revision_id:
            raise ValidationError("expected_revision_id は必須")
        if template["current_revision_id"] != expected_revision_id:
            raise ConflictError("別の画面・会話で先に更新されています。読み直してから保存してください。")

    def _live_template(self, template_id: str, expected_revision_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """トランザクション内で、テンプレートと current の改訂を読み、競合と削除済みを断る。"""
        self._require_tx()
        t = self._get("design_templates", template_id)
        self._check_expected(t, expected_revision_id)
        if t["deleted_at"] is not None:
            raise StateError("削除済みのテンプレートには保存できません")
        return t, self._revision_row(template_id, t["current_revision_id"])

    def _revision_row(self, template_id: str, revision_id: str) -> dict[str, Any]:
        row = self._one("SELECT * FROM design_template_revisions WHERE template_id=? AND id=?", (template_id, revision_id))
        if row is None:
            raise NotFoundError("テンプレートの改訂が存在しません")
        return row

    @staticmethod
    def _revision_doc(row: dict[str, Any]) -> dict[str, Any]:
        return load_json(row["document_json"])

    def _may_overwrite_by(self, cur: dict[str, Any], chash: str | None) -> bool:
        """この版の保存が、すべてこの会話の保存か（途中で別の会話・画面・識別子なしの保存が1つでもあれば資格を失う。
        失った資格は、あとから同じ会話が上書きしても戻らない）。"""
        if chash is None or cur["conversation_hash"] != chash:
            return False
        others = self._scalar(
            "SELECT COUNT(*) FROM design_template_revisions WHERE template_id=? AND revision=?"
            " AND (conversation_hash IS NULL OR conversation_hash<>?)",
            (cur["template_id"], cur["revision"], chash),
        )
        return int(others) == 0

    def _save_existing(self, template_id: str, document: Any, mode: str, expected_revision_id: str,
                       source_revision_id: str | None, runtime: str | None, chash: str | None,
                       *, kind: str | None = None) -> dict[str, Any]:
        with self.transaction():
            t, cur = self._live_template(template_id, expected_revision_id)
            brand = self._live_brand(t["brand_id"])
            if mode == "overwrite":
                revision, based_on_rev_id, based_on = cur["revision"], cur["based_on_revision_id"], self._revision_doc(cur)["based_on"]
            else:
                source = cur if source_revision_id is None else self._revision_row(template_id, source_revision_id)
                revision = int(self._scalar("SELECT MAX(revision) FROM design_template_revisions WHERE template_id=?",
                                            (template_id,))) + 1
                based_on_rev_id, based_on = source["id"], source["revision"]
            doc = _validated(document, template_id, t["brand_id"], revision, based_on)
            if content_sha256(doc) == cur["content_sha256"]:
                view = self._view(cur, template=t)
                view["unchanged"] = True
                return view
            now, rev_id = self.now(), self.new_id()
            seq = int(self._scalar("SELECT MAX(save_sequence) FROM design_template_revisions WHERE template_id=?",
                                   (template_id,))) + 1
            kind = kind or mode
            self._insert_revision(template_id, rev_id, revision, seq, doc, based_on_rev_id, brand["current_revision_id"],
                                  runtime, chash, kind, now)
            self._exec("UPDATE design_templates SET current_revision_id=?,updated_at=? WHERE id=?", (rev_id, now, template_id))
            self.append_event("design_template_saved", {"template_id": template_id, "revision_id": rev_id,
                                                        "revision": revision, "kind": kind})
            return self._view(self._revision_row(template_id, rev_id))

    def _insert_revision(self, template_id: str, rev_id: str, revision: int, seq: int, doc: dict[str, Any],
                         based_on_rev_id: str | None, brand_revision_id: str, runtime: str | None, chash: str | None,
                         kind: str, now: str) -> None:
        if based_on_rev_id is not None:
            self._revision_row(template_id, based_on_rev_id)   # 由来は同じテンプレートの改訂だけ
        self._exec(
            "INSERT INTO design_template_revisions(id,template_id,revision,save_sequence,document_json,content_sha256,"
            "based_on_revision_id,brand_revision_id,origin_runtime,conversation_hash,save_kind,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (rev_id, template_id, revision, seq, dump_json(doc), content_sha256(doc), based_on_rev_id,
             brand_revision_id, runtime, chash, kind, now),
        )

    def _view(self, row: dict[str, Any], *, template: dict[str, Any] | None = None) -> dict[str, Any]:
        t = template or self._get("design_templates", row["template_id"])
        brand_rev = self._one("SELECT id,revision FROM brand_revisions WHERE id=?", (row["brand_revision_id"],))
        latest = int(self._scalar("SELECT MAX(revision) FROM design_template_revisions WHERE template_id=?", (t["id"],)))
        return {
            "template_id": t["id"], "brand_id": t["brand_id"], "deleted_at": t["deleted_at"],
            "revision": row["revision"], "revision_id": row["id"], "save_sequence": row["save_sequence"],
            "latest_revision": latest, "is_latest": row["revision"] == latest,
            "is_current": row["id"] == t["current_revision_id"], "current_revision_id": t["current_revision_id"],
            "document": self._revision_doc(row), "based_on_revision_id": row["based_on_revision_id"],
            "brand_revision_id": row["brand_revision_id"],
            "brand_revision": None if brand_rev is None else {"id": brand_rev["id"], "revision": brand_rev["revision"]},
            "save_kind": row["save_kind"], "saved_at": row["created_at"], "unchanged": False,
        }
