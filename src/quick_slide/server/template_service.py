"""デザインテンプレートの保存サービス。画面・取り込み・計画（dry-run）の全部がここを通る。

順序: ブランドの生存 → 文書の検証 → 期待した改訂（競合）→ 同文の検出 → 保存。
競合・同文・版の採番は `store/design_templates.py` が同じトランザクションの中で決める。ここは入口の検査と、
返す形（`{document, current_revision_id, action, changed_sections, message, ...}`）を作るだけで、SQL を持たない。

計画（`plan`）は保存と同じ関数を呼び、最後に必ず巻き戻す。何も書かない（イベントも残らない）。
本文・パスはログにも例外にも出さない。
"""

from __future__ import annotations

import copy
from typing import Any

from .. import design_templates as dt
from ..enums import ORIGIN_RUNTIMES
from ..errors import NotFoundError, StateError, ValidationError
from ..store import Store
from ..store.design_templates import changed_sections

BROWSER = "browser"
MODES = ("overwrite", "new_version", "conversation")
IMPORT_SUFFIX = "（インポート）"


class _DryRun(Exception):
    """計画の終わり。外側のトランザクションを巻き戻すための合図（エラーではない）。"""


def check_document(document: Any) -> None:
    """文書の形を、保存の前に確かめる（旧い形式は読み込みの変換を通す。添付は新しく作れず、旧データの添付は
    リソースへ移した形で扱う）。絶対パスの検査は、残してよい旧データのパスを知っている保存の中（store）で行う。"""
    if not isinstance(document, dict):
        raise ValidationError("テンプレートの保存形式を確認してください。")
    probe = copy.deepcopy(document)
    probe.update({"id": "probe", "brand_id": "probe", "revision": 0, "based_on": None})
    try:
        dt.validate(probe, relative_paths=True)
    except ValueError as exc:
        raise ValidationError(str(exc)) from None


def _live_brand(store: Store, brand_id: Any) -> dict[str, Any]:
    if not isinstance(brand_id, str) or not brand_id:
        raise ValidationError("ブランドを選んでください。")
    brand = store.brand(brand_id)   # 無ければ NotFoundError
    if brand["deleted_at"] is not None:
        raise StateError("削除済みブランドにはテンプレートを保存できません")
    return brand


def _runtime(origin_runtime: Any) -> str:
    if origin_runtime is None:
        return BROWSER
    if origin_runtime not in ORIGIN_RUNTIMES:
        raise ValidationError("保存元の指定を確認してください。")
    return origin_runtime


def _sections_text(changed: list[str]) -> str:
    return "（変更：" + "・".join(changed) + "）" if changed else ""


def result(view: dict[str, Any], *, action: str, before: dict[str, Any] | None, source_revision: int | None = None) -> dict[str, Any]:
    """保存の結果を、画面・CLI が読む形にする。`before` は保存前の最新の文書（無ければ新規）。"""
    changed = [] if action == "unchanged" else changed_sections(before, view["document"])
    v = view["revision"]
    if action == "created":
        message = f"v{v} を作りました"
    elif action == "unchanged":
        message = f"内容が変わらないため保存しませんでした（v{v}）"
    elif action == "overwritten":
        message = f"v{v} を上書きしました" + _sections_text(changed)
    elif action == "copied" and source_revision is not None:
        message = f"v{source_revision} をもとに v{v} を作りました" + _sections_text(changed)
    elif action == "copied":
        message = f"「{view['document']['name']}」を作りました"
    else:
        message = f"v{v} を作りました" + _sections_text(changed)
    return {"template_id": view["template_id"], "brand_id": view["brand_id"], "document": view["document"],
            "revision": v, "revision_id": view["revision_id"], "current_revision_id": view["current_revision_id"],
            "brand_revision": view["brand_revision"], "action": action, "changed_sections": changed, "message": message}


def parse_markdown(markdown: Any, *, brand_id: str, template_id: str | None) -> dict[str, Any]:
    """交換ファイル（Markdown）を文書にする。ファイルに書かれた ID は使わない（ID はサーバが決める）。"""
    if not isinstance(markdown, str):
        raise ValidationError("テンプレートのファイルを確認してください。")
    try:
        return dt.from_markdown(markdown, brand_id=brand_id, template_id=template_id or "pending")
    except ValueError as exc:
        raise ValidationError(str(exc)) from None


def _one_of(document: Any, markdown: Any) -> None:
    if document is not None and markdown is not None:
        raise ValidationError("document と markdown は、どちらか 1 つだけ指定してください。")


def create(store: Store, brand_id: Any, document: Any, *, template_id: str | None = None,
           origin_runtime: Any = None, conversation_key: Any = None, markdown: Any = None) -> dict[str, Any]:
    _one_of(document, markdown)
    _live_brand(store, brand_id)
    if markdown is not None:
        document = parse_markdown(markdown, brand_id=brand_id, template_id=None)
    check_document(document)
    view = store.create_design_template(brand_id, document, template_id=template_id,
                                        origin_runtime=_runtime(origin_runtime), conversation_key=conversation_key)
    return result(view, action="created", before=None)


def save(store: Store, template_id: str, document: Any, *, mode: Any, expected_revision_id: Any,
         source_revision_id: Any = None, origin_runtime: Any = None, conversation_key: Any = None,
         overwrite: bool = False, markdown: Any = None) -> dict[str, Any]:
    _one_of(document, markdown)
    if mode not in MODES:
        raise ValidationError("保存のしかたを確認してください。")
    if type(overwrite) is not bool:   # "false" や [false] を真と読まない
        raise ValidationError("overwrite は true か false で指定してください。")
    if source_revision_id is not None and (not isinstance(source_revision_id, str) or mode != "new_version"):
        raise ValidationError("コピー元の指定は「新しい版として保存」でだけ使えます。")
    if not isinstance(expected_revision_id, str) or not expected_revision_id:
        raise ValidationError("expected_revision_id は必須")
    current = store.get_design_template(template_id)
    _live_brand(store, current["brand_id"])
    if current["deleted_at"] is not None:
        raise StateError("削除済みのテンプレートには保存できません")
    source = None
    if source_revision_id is not None:
        source = store.get_design_template(template_id, revision_id=source_revision_id)   # 他のテンプレートの改訂は NotFound
    if markdown is not None:
        document = parse_markdown(markdown, brand_id=current["brand_id"], template_id=template_id)
    if document is None and source is None:
        raise ValidationError("保存する内容がありません。")
    if document is not None:
        check_document(document)
    runtime = _runtime(origin_runtime)
    before = current["document"]
    if mode == "conversation":
        if conversation_key is not None and (not isinstance(conversation_key, str) or not conversation_key):
            raise ValidationError("会話の識別子を確認してください。")
        view = store.save_design_template_for_conversation(
            template_id, document, expected_revision_id=expected_revision_id, origin_runtime=runtime,
            conversation_key=conversation_key, overwrite=bool(overwrite))
        action = "overwritten" if view.get("mode") == "overwrite" else "new_version"
    elif mode == "overwrite":
        view = store.overwrite_design_template(template_id, document, expected_revision_id=expected_revision_id,
                                               origin_runtime=runtime)
        action = "overwritten"
    elif source is not None:
        view = store.copy_design_template_version(template_id, source_revision_id, expected_revision_id=expected_revision_id,
                                                  document=document, origin_runtime=runtime)
        action = "copied"
    else:
        view = store.add_design_template_version(template_id, document, expected_revision_id=expected_revision_id,
                                                 origin_runtime=runtime)
        action = "new_version"
    if view["unchanged"]:
        action = "unchanged"
    return result(view, action=action, before=before, source_revision=source["revision"] if source and action == "copied" else None)


def duplicate(store: Store, template_id: str, *, expected_revision_id: Any, name: Any = None) -> dict[str, Any]:
    current = store.get_design_template(template_id)
    _live_brand(store, current["brand_id"])
    if not isinstance(expected_revision_id, str) or not expected_revision_id:
        raise ValidationError("expected_revision_id は必須")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise ValidationError("名前を確認してください。")
    view = store.duplicate_design_template(template_id, expected_revision_id=expected_revision_id, name=name,
                                           origin_runtime=BROWSER)
    return result(view, action="copied", before=None)


def delete(store: Store, template_id: str, *, expected_revision_id: Any) -> dict[str, Any]:
    store.delete_design_template(template_id, expected_revision_id=expected_revision_id)
    view = store.get_design_template(template_id)
    return {"template_id": template_id, "brand_id": view["brand_id"], "document": view["document"],
            "revision": view["revision"], "revision_id": view["revision_id"],
            "current_revision_id": view["current_revision_id"], "action": "deleted", "changed_sections": [],
            "message": f"「{view['document']['name']}」を削除しました（履歴は残ります）"}


def import_mode(store: Store, brand_id: str, document: dict[str, Any], file_id: str | None) -> tuple[dict[str, Any] | None, list[str]]:
    """取り込む文書と同じ ID のテンプレートの状況 → (見せる既存 or None, 選べる道)。
    削除済み・別ブランドの同じ ID は、上書きも復活もさせず「別のテンプレートとして追加」だけにする。"""
    if not file_id:
        return None, ["create"]
    try:
        t = store.get_design_template(file_id)
    except NotFoundError:
        return None, ["create"]
    if t["deleted_at"] is not None or t["brand_id"] != brand_id:
        return None, ["add"]
    existing = {"template_id": t["template_id"], "name": t["document"]["name"], "revision": t["latest_revision"],
                "current_revision_id": t["current_revision_id"]}
    return existing, ["new_version", "add"]


def plan(store: Store, body: dict[str, Any]) -> dict[str, Any]:
    """保存の dry-run。保存と同じ関数を呼んで最後に巻き戻す。DB もイベントも変わらない。"""
    out: dict[str, Any] = {}
    try:
        with store.transaction():
            template_id = body.get("template_id")
            if template_id is None:
                out = create(store, body.get("brand_id"), body.get("document"), origin_runtime=body.get("origin_runtime"),
                             conversation_key=body.get("conversation_key"), markdown=body.get("markdown"))
            else:
                if not isinstance(template_id, str):
                    raise ValidationError("テンプレートを確認してください。")
                out = save(store, template_id, body.get("document"), mode=body.get("mode"),
                           expected_revision_id=body.get("expected_revision_id"),
                           source_revision_id=body.get("source_revision_id"), origin_runtime=body.get("origin_runtime"),
                           conversation_key=body.get("conversation_key"), overwrite=body.get("overwrite", False),
                           markdown=body.get("markdown"))
            raise _DryRun
    except _DryRun:
        pass
    return {"dry_run": True, "action": out["action"], "revision": out["revision"],
            "changed_sections": out["changed_sections"], "message": out["message"].replace("を作りました", "を作ります")
            .replace("を上書きしました", "を上書きします"), "current_revision_id": body.get("expected_revision_id")}
