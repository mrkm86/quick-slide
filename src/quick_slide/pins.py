"""版の切り替え（docs/EDIT-PLAN.md の C・docs/adr/0001-version-pins.md）: ページが指している版（`_pin`）を引く。

版を選ぶと、台本のそのページを前の版の中身に戻し、`_pin` にその版を描いた回の id を書く（`scriptdoc.with_restored`）。
指している回の絵・書き出しの部品・発表の区切りを、そのページの分として使い続ける（描き直さないので、新しい版はできない）。

印が効くのは、指している回が残っていて（同じデッキ・描き終えた回）、その回にそのページの絵があり、中身が台本の
いまの中身と同じときだけ。どれかが外れた印は無いものとして扱う（そのページはふつうに描く）。
どの関数もサーバの錠を取ってから呼ぶ。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import NotFoundError
from .scriptdoc import pins_of, slides_of

GONE_MESSAGE = "その版はもうありません。画面を読み込み直してください。"
NOT_RESTORABLE_MESSAGE = "その版には戻せません。"
NO_PAGE_MESSAGE = "その版にはこのページがありません。"
NO_PICTURE_MESSAGE = "この版には絵がありません。"


class PinRefused(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Pin:
    """効いている版の印。`row` は指している回のそのページの行（`store.run_pages` の形）。"""
    key: str
    page_id: str
    run: dict[str, Any]
    row: dict[str, Any]
    run_dir: Path

    @property
    def run_id(self) -> str:
        return self.run["id"]


def _ready(row: dict[str, Any] | None) -> bool:
    return bool(row and row["state"] == "ready" and row.get("asset_state") == "ready" and row.get("asset_id"))


def pin_source(store: Any, deck_id: str, run_id: Any, key: str) -> dict[str, Any]:
    """版を選んだとき: 戻す先の回を確かめ、その回の台本のそのページ（戻す中身）を返す。戻せなければ PinRefused。"""
    try:
        run = store.run(run_id) if isinstance(run_id, str) else None
    except NotFoundError:
        run = None
    if run is None:
        raise PinRefused(GONE_MESSAGE)
    if run["deck_id"] != deck_id or run["state"] != "done" or not run.get("script_id"):
        raise PinRefused(NOT_RESTORABLE_MESSAGE)
    old_script = store.load_script(store.script(run["script_id"]))
    old_page = next((s for s in slides_of(old_script) if s.get("id") == key), None)
    if old_page is None:
        raise PinRefused(NO_PAGE_MESSAGE)
    if not _ready(next((r for r in store.run_pages(run["id"]) if r.get("key") == key), None)):
        raise PinRefused(NO_PICTURE_MESSAGE)
    return old_page


class ScriptPins:
    """台本の版の印（台本の版のファイルは書き換えないので、版ごとに覚えておける）。"""

    _memo: dict[str, dict[str, str]] = {}

    @classmethod
    def of(cls, store: Any, script: dict[str, Any]) -> dict[str, str]:
        got = cls._memo.get(script["id"])
        if got is None:
            try:
                got = pins_of(store.load_script(script))
            except (OSError, ValueError):
                got = {}
            if len(cls._memo) > 2000:
                cls._memo.clear()
            cls._memo[script["id"]] = got
        return got


def resolve(store: Any, deck_id: str, pins: dict[str, str], pages: list[dict[str, Any]]) -> dict[str, Pin]:
    """効いている印（ページの id → Pin）。`pages` は台本のページ（`key` と中身の sha256。`script_pages` か
    `scriptdoc.screen_pages` の形）。"""
    out: dict[str, Pin] = {}
    if not pins:
        return out
    runs: dict[str, dict[str, Any] | None] = {}
    rows: dict[str, dict[str, dict[str, Any]]] = {}
    for p in pages:
        key = p["key"]
        rid = pins.get(key)
        if rid is None:
            continue
        if rid not in runs:
            try:
                runs[rid] = store.run(rid)
            except NotFoundError:
                runs[rid] = None
        run = runs[rid]
        if run is None or run["deck_id"] != deck_id or run["state"] != "done":
            continue
        if rid not in rows:
            rows[rid] = {r["key"]: r for r in store.run_pages(rid) if r.get("key")}
        row = rows[rid].get(key)
        content = p.get("content_sha256") or p.get("content_sha")
        if not _ready(row) or (row.get("content_sha256") and content and row["content_sha256"] != content):
            continue
        out[key] = Pin(key=key, page_id=row["page_id"], run=run, row=row, run_dir=store.root.run_dir(deck_id, rid))
    return out


def head_pins(store: Any, deck_id: str, head: dict[str, Any] | None,
              pages: list[dict[str, Any]] | None = None) -> dict[str, Pin]:
    """デッキのいまの台本で効いている印（ページの id（key）→ Pin）。画面・発表が読む。"""
    if head is None:
        return {}
    pins = ScriptPins.of(store, head)
    if not pins:
        return {}
    return resolve(store, deck_id, pins, pages if pages is not None else store.script_pages(head["id"]))
