"""デザインテンプレートで作る資料の「選び方」と「控え」（docs/design-template-generation.md §2）。

- 選び方（`resolve_design_template`）: 投入のトランザクションの中で、ブランド（既定または明示）→ そのブランドの生存テンプレート
  （ID が先、次に名前の完全一致）→ 版（省けば受付時の最新）を 1 つに決める。サーバが最終の判断をする
  （CLI が名前を先に引いていても、ここで引き直す）。曖昧・別ブランド・削除済み・無い版・使えないモードは
  `SelectionError`（code・候補つき）で断る
- 控え（`template_snapshots`・0012）: テンプレートの文書全文・内部改訂 ID・内容ハッシュと、**そのテンプレートの版を保存したときの**
  ブランドの改訂（確定した config・資産の目録）。最新のブランドで過去のテンプレートの版を組み直さない。
  不変の 1 行で、同じ内容は同じ行を共有する。描いた回は `runs.args.template_snapshot`（{snapshot_id, snapshot_sha256}）で指す
- 控えの元になったテンプレートを後から上書き・削除しても、ブランドを更新しても、この行は変わらない
"""

from __future__ import annotations

import hashlib
from typing import Any

from .. import design_templates as dt
from ..errors import NotFoundError, SelectionError, StateError
from .base import StoreBase, dump_json, load_json
from .design_templates import content_sha256

SNAPSHOT_SCHEMA_VERSION = 1
# 投入の属性（deck_attributes）に足す、テンプレートの選び方の 3 項目
SELECTION_ATTRIBUTES = ("design_template", "design_template_version", "design_template_expected_revision_id")
# 回の引数に残す、控えへの参照
SNAPSHOT_ARG = "template_snapshot"

MODE_LABELS = {"presentation": "プレゼン（HTML）", "pitch": "ピッチ（画像）"}


def _sha(obj: Any) -> str:
    return hashlib.sha256(dump_json(obj).encode("utf-8")).hexdigest()


class TemplateSnapshotsMixin(StoreBase):
    # --- 選び方 ---
    def resolve_design_template(self, attrs: dict[str, Any]) -> dict[str, Any] | None:
        """`deck_attributes` の選び方を 1 つの確定した選択にする（何も書かない）。選んでいなければ None。
        戻り値: {snapshot, snapshot_sha256, identity（同一判定に使う正規化した選択）, brand_revision_id, summary}。
        呼ぶ側のトランザクションの中で呼ぶ（読みと投入の書き込みの間に、テンプレートやブランドが変わらないように）。"""
        wanted = attrs.get("design_template")
        if wanted is None:
            return None
        self._require_tx()
        mode = attrs["mode"]
        explicit_brand = attrs.get("brand_id")
        brand = self._selection_brand(explicit_brand)
        template = self._selection_template(wanted, brand, explicit=explicit_brand is not None)
        version = attrs.get("design_template_version")
        try:
            view = self.get_design_template(template["id"], revision=version)
        except NotFoundError:
            latest = self._scalar("SELECT MAX(revision) FROM design_template_revisions WHERE template_id=?", (template["id"],))
            raise SelectionError("template_version_not_found",
                                 f"テンプレートに v{version} はありません（最新は v{latest}）。版を選び直してください。",
                                 details={"template_id": template["id"], "latest_version": latest}) from None
        doc = view["document"]
        if mode not in doc["modes"]:
            usable = "・".join(MODE_LABELS.get(m, m) for m in doc["modes"])
            raise SelectionError("template_mode_unsupported",
                                 f"このテンプレートは {usable} で使えます。今回の形式（{MODE_LABELS.get(mode, mode)}）では使えません。",
                                 details={"template_id": template["id"], "modes": doc["modes"], "mode": mode})
        expected = attrs.get("design_template_expected_revision_id")
        if expected is not None and expected != view["revision_id"]:
            raise SelectionError("template_revision_changed",
                                 "選んだあとにテンプレートが保存し直されました。内容を確かめてから、もう一度頼んでください。",
                                 details={"template_id": template["id"], "revision_id": view["revision_id"]}, status=409)
        brand_rev = self.brand_revision(view["brand_revision_id"])
        if brand_rev["brand_id"] != brand["id"]:
            raise SelectionError("template_brand_mismatch", "テンプレートを保存したときのブランドを確認できません。"
                                 "テンプレートを開いて保存し直してください。", details={"template_id": template["id"]})
        brand_snapshot = {
            "id": brand["id"], "revision_id": brand_rev["id"], "revision": brand_rev["revision"],
            "config": brand_rev["config"],
            "assets": [{k: a[k] for k in ("asset_id", "role", "label", "relative_path", "sha256", "width", "height")}
                       for a in brand_rev["assets"]],
        }
        brand_sha = _sha({"config": brand_snapshot["config"], "assets": brand_snapshot["assets"]})
        overrides = {k: attrs[k] for k in ("image_look", "style_note") if attrs.get(k)}
        if attrs.get("style_said"):
            overrides["style"] = attrs["style"]
        tpl_sha = content_sha256(doc)
        snapshot = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION, "compiler_version": dt.COMPILER_VERSION,
            "template": {"id": template["id"], "revision": view["revision"], "revision_id": view["revision_id"],
                         "document": doc, "content_sha256": tpl_sha},
            "brand": {**brand_snapshot, "sha256": brand_sha},
            "mode": mode, "explicit_overrides": overrides,
        }
        identity = {"template_id": template["id"], "version": view["revision"], "revision_id": view["revision_id"],
                    "template_sha256": tpl_sha, "brand_id": brand["id"], "brand_revision_id": brand_rev["id"],
                    "brand_sha256": brand_sha, "explicit_overrides": overrides}
        return {"snapshot": snapshot, "snapshot_sha256": _sha(snapshot), "identity": identity,
                "brand_revision_id": brand_rev["id"]}

    def _selection_brand(self, brand_id: str | None) -> dict[str, Any]:
        """明示のブランド、無ければアプリの既定。既定が使えないときは別のブランドへ移らずに止まる。"""
        if brand_id is None:
            configured = self.settings.default_brand_id
            if configured and self._one("SELECT 1 FROM brands WHERE id=? AND deleted_at IS NULL", (configured,)) is None:
                raise SelectionError("brand_not_found", "既定のブランドが見つかりません。ブランドを指定してください（設定で既定を選び直せます）。")
            brand_id = self.default_brand_id()
        row = self._one("SELECT id,name FROM brands WHERE id=? AND deleted_at IS NULL", (brand_id,))
        if row is None:
            raise SelectionError("brand_not_found", "そのブランドが見つかりません（削除された可能性があります）。ブランドを選び直してください。")
        return row

    def _selection_template(self, wanted: str, brand: dict[str, Any], *, explicit: bool) -> dict[str, Any]:
        """ブランドの中で、ID の一致を先に、次に名前の完全一致で 1 つに決める。"""
        by_id = self._one("SELECT id,brand_id,deleted_at FROM design_templates WHERE id=?", (wanted,))
        if by_id is not None:
            if by_id["brand_id"] != brand["id"]:
                if not explicit:
                    raise SelectionError("template_other_brand", "その ID のテンプレートは、既定のブランドのものではありません。"
                                         "そのテンプレートのブランドを指定してください。", details={"template_id": wanted})
                raise SelectionError("template_not_found", "そのブランドにその ID のテンプレートがありません。")
            if by_id["deleted_at"] is not None:
                raise SelectionError("template_deleted", "このテンプレートは削除済みです。", details={"template_id": wanted})
            return by_id
        rows = self.list_design_templates(brand["id"])
        matches = [r for r in rows if r["name"] == wanted]
        if len(matches) > 1:
            raise SelectionError("template_ambiguous",
                                 f"同じ名前のテンプレートが {len(matches)} 件あります。ID を指定してください。",
                                 details={"candidates": [{"id": r["template_id"], "name": r["name"], "version": r["revision"]}
                                                         for r in matches]})
        if not matches:
            raise SelectionError("template_not_found", f"ブランド「{brand['name']}」にその名前のテンプレートがありません。",
                                 details={"brand_id": brand["id"],
                                          "candidates": [{"id": r["template_id"], "name": r["name"]} for r in rows]})
        return {"id": matches[0]["template_id"]}

    # --- 控え ---
    def save_template_snapshot(self, snapshot: dict[str, Any], snapshot_sha256: str) -> dict[str, str]:
        """控えを残す（同じ内容があれば、その行を使う）。回の引数に入れる参照を返す。投入のトランザクションの中で呼ぶ。"""
        self._require_tx()
        row = self._one("SELECT id FROM template_snapshots WHERE snapshot_sha256=?", (snapshot_sha256,))
        if row is None:
            sid = self.new_id()
            self._exec("INSERT INTO template_snapshots(id,snapshot_sha256,snapshot_json,created_at) VALUES(?,?,?,?)",
                       (sid, snapshot_sha256, dump_json(snapshot), self.now()))
        else:
            sid = row["id"]
        return {"snapshot_id": sid, "snapshot_sha256": snapshot_sha256}

    def template_snapshot(self, ref: dict[str, Any]) -> dict[str, Any]:
        """回の引数の参照から控えの中身を読む。中身のハッシュが参照と違えば StateError（黙って別のものを使わない）。"""
        row = self._one("SELECT snapshot_json,snapshot_sha256 FROM template_snapshots WHERE id=?", (ref.get("snapshot_id"),))
        if row is None:
            raise StateError("デザインテンプレートの控えが見つかりません。")
        data = load_json(row["snapshot_json"])
        if row["snapshot_sha256"] != ref.get("snapshot_sha256") or _sha(data) != row["snapshot_sha256"]:
            raise StateError("デザインテンプレートの控えが、受け付けたときと違っています。")
        return data

    @staticmethod
    def template_summary(snapshot: dict[str, Any], ref: dict[str, Any]) -> dict[str, Any]:
        """応答・画面に返す、控えの要約（本文を含めない）。"""
        t = snapshot["template"]
        return {"template_id": t["id"], "name": t["document"]["name"], "version": t["revision"],
                "revision_id": t["revision_id"], "brand_id": snapshot["brand"]["id"],
                "brand_revision_id": snapshot["brand"]["revision_id"], "snapshot_id": ref["snapshot_id"]}

    def deck_template_snapshot_ref(self, deck_id: str) -> dict[str, Any] | None:
        """そのデッキの控えへの参照（最初の回が持つ。無ければテンプレートを使わないデッキ）。"""
        row = self._one("SELECT args_json FROM runs WHERE deck_id=? ORDER BY number LIMIT 1", (deck_id,))
        ref = (load_json(row["args_json"]) or {}).get(SNAPSHOT_ARG) if row else None
        return ref if isinstance(ref, dict) else None
