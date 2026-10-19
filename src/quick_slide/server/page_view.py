"""スライドの画面のページ（P3）。`deck_routes.deck_view` がここを呼ぶ。

**ページの並びはデッキのいまの台本**（`deck_heads`。非表示のページも入る）。**ページの絵は、そのページが指す版の
もの**: 版を切り替えたページ（台本の `_pin`。`pins.py`）はその版を描いた回の絵、ほかは**見せる回**（描き終えた
一番新しい回）の絵。描かない直し（非表示・並べ替え・版の切り替え）はその場で台本に当たり、書き出しと発表用の HTML は
裏の組み直し（tidy）が追いかけて揃える。直している間は、新しい並びに前の絵を出し、直している途中のページに
「生成中」「待機中」を付ける（描き終えると絵が替わる）。

**版スタック** は、そのページの描き終えた回をまたいだ「姿」の並び。姿は（台本のページの中身の sha256・
絵の sha256）で決め、同じ姿の回は 1 つにまとめる。番号（v1, v2 …）は姿が初めて出た順。前の版を押すと
その版に切り替わる（`POST /adopt`。描き直さないので新しい版はできない）。使用中の版は、印のあるページは印の回の姿、
ほかは見せる回の姿。候補を作って選ぶ v1 の形は無い（`candidates` は空）。
"""

from __future__ import annotations

from typing import Any

from ..engines import engine_for
from ..pins import head_pins
from ..render import workspace as W
from ..scriptdoc import one_page, screen_pages
from ..store.edits import TEMPLATE_RESTORE_REASON
from .later import NOT_READY_REASON
from .vocabulary import EDIT_MODE_LABELS, PIN_EXPORT_REDRAW_NOTE, SOURCE_KIND_LABELS, page_display, run_page_display

NO_PICTURE_REASON = "このページには直せる絵がありません。指示は文字・構成の直しになります。"
NO_REVISE_REASON = "モーションには絵だけを直す操作がありません。指示は場面の中身（文字・図・動き）の直しになります。"
INFOGRAPHIC_PAGES_REASON = "1 枚にまとめるスタイル（infographic）では、ページを足す・並べ替える・隠すことはできません。"
NOT_ADOPTABLE_REASON = "この版には絵がありません。"
NOT_PRESENTABLE_REASON = "見せられるページがまだありません。ページができてから開いてください。"


def asset_url(asset_id: str | None) -> str | None:
    return None if asset_id is None else f"/api/assets/{asset_id}/file"


def resolve_states(rows: list[dict[str, Any]], legacy_content: Any = None) -> list[tuple[str, str]]:
    """各行（新しい回から）の「姿」の鍵（中身の sha256・絵の sha256）。

    P2 の回（移行 0003 の前）の行には中身も絵の sha256 も無い。そのままでは、変わっていないページでも別の姿に
    見えて版が 2 つになる（2026-10-07 の本物の通しで見つかった）。だから:
    - 中身は、その回の台本の版のファイルから求める（`legacy_content(row)`。台本の版は書き換えないので確か）
    - 絵は、すぐ新しい回の同じページと**ページの画像のバイトが同じなら同じ絵**とみなす（同じ台本・同じ設定なら
      描き直してもバイトまで同じことは確かめてある。docs/P3-NOTES.md）。違えば、その画像のバイトを鍵にする
    - 中身が求められないとき（台本のファイルが無い）も、画像のバイトが同じなら新しい回の中身を引き継ぐ
    """
    out: list[tuple[str, str]] = []
    newer: dict[str, Any] | None = None
    newer_key: tuple[str, str] | None = None
    for row in rows:                       # 新しい回から
        asset = "asset:" + str(row.get("asset_sha256"))
        same_png = newer is not None and row.get("asset_sha256") and row.get("asset_sha256") == newer.get("asset_sha256")
        content = row.get("content_sha256") or (legacy_content(row) if legacy_content else None)
        if content is None:
            content = newer_key[0] if same_png and newer_key else asset
        picture = row.get("picture_sha256")
        if picture is None:
            picture = newer_key[1] if same_png and newer_key else asset
        newer, newer_key = row, (content, picture)
        out.append(newer_key)
    return out


def _made_here(made: dict[str, Any], page_id: str | None, first_of_page: bool) -> bool:
    """この姿を作った頼みごとが、このページに向けたものか（指示の文・直し方はそのページの版にだけ出す）。"""
    op = made.get("op")
    if op == "revise":
        return page_id in (made.get("pages") or []) or first_of_page   # 分けて増えたページも
    if op == "insert":
        return first_of_page
    return False


def page_versions(rows: list[dict[str, Any]], shown_run_id: str | None, requests: dict[str, dict[str, Any]],
                  *, page_id: str | None = None, legacy_content: Any = None
                  ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """そのページの版（新しい姿から）と、見せる回の姿の版。`rows` は `page_history` のそのページの行（新しい回から）。"""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row, key in zip(rows, resolve_states(rows, legacy_content)):   # 新しい回から
        g = groups.get(key)
        if g is None:
            groups[key] = g = {"newest": row, "oldest": row, "runs": []}
        g["oldest"] = row
        g["runs"].append(row["run_id"])
    ordered = sorted(groups.values(), key=lambda g: g["oldest"]["number"])
    for n, g in enumerate(ordered, 1):
        g["ordinal"] = n
    active = next((g for g in ordered if shown_run_id in g["runs"]), None)
    out = []
    for g in sorted(ordered, key=lambda g: -g["newest"]["number"]):
        rep = g["newest"]
        made = requests.get(g["oldest"]["run_id"]) or {}
        if not _made_here(made, page_id, g["ordinal"] == 1):
            # 別のページの直しが同時に走ると、このページの直しが、ほかの直しの回で一緒に描かれることがある
            # （docs/adr/0012）。同じ姿を描いた回のうち、このページを直した頼みごとを探す（古い回から）
            made = next((m for rid in reversed(g["runs"]) if (m := requests.get(rid) or {})
                         and m.get("op") in ("revise", "insert") and _made_here(m, page_id, False)), {})
        has_image = rep.get("asset_state") == "ready" and rep.get("asset_id") is not None
        mode = made.get("edit_mode") if made.get("op") == "revise" else None
        out.append({
            "id": rep["run_id"], "version": g["ordinal"], "state": "ready", "display": page_display("ready"),
            "item": None, "source_kind": "generated", "source_kind_label": SOURCE_KIND_LABELS["generated"],
            "active": g is active, "created_at": rep["created_at"],
            "image_url": asset_url(rep.get("asset_id")) if has_image else None,
            "image_missing": not has_image, "not_adoptable_reason": None if has_image else NOT_ADOPTABLE_REASON,
            "revision_note": (made.get("text") or None) if made.get("op") in ("revise", "insert") else None,
            "edit_mode": mode, "edit_mode_label": EDIT_MODE_LABELS.get(mode) if mode else None,
            "prompt_head": None, "origin_version_id": None,
        })
    return out, (None if active is None else next(v for v in out if v["active"]))


class LegacyContent:
    """P2 の回のページの中身の sha256 を、その回の台本の版のファイルから求める（台本の版は書き換えないので覚えておける）。"""

    _memo: dict[str, dict[str, str]] = {}

    def __init__(self, store: Any, args: dict[str, Any], keys: dict[str, str]) -> None:
        self.store, self.args, self.keys = store, args, keys

    def __call__(self, row: dict[str, Any]) -> str | None:
        sid, key = row.get("script_id"), self.keys.get(row.get("page_id"))
        if not sid or not key:
            return None
        table = self._memo.get(sid)
        if table is None:
            try:
                data = self.store.load_script(self.store.script(sid))
                table = {p["key"]: p["content_sha"] for p in screen_pages(self.args, data)}
            except Exception:  # noqa: BLE001 - 読めない台本は求めない（画像のバイトで決める）
                table = {}
            if len(self._memo) > 2000:
                self._memo.clear()
            self._memo[sid] = table
        return table.get(key)


def _minor(mode: str, has_picture: bool) -> dict[str, Any]:
    if not engine_for(mode).can_revise:
        # 画像の概念が無い（`hidden`: 画面は「元の画像を使わずに生成し直す」の選択肢と画像の添付を出さない）
        return {"available": False, "label": EDIT_MODE_LABELS["minor"], "reason": NO_REVISE_REASON, "hidden": True}
    if has_picture:
        return {"available": True, "label": EDIT_MODE_LABELS["minor"], "reason": None}
    return {"available": False, "label": EDIT_MODE_LABELS["minor"], "reason": NO_PICTURE_REASON}


def _generated_pages(store: Any, deck_id: str, shown: dict[str, Any]) -> set[str]:
    """見せる回で、描いた生成の絵があるページの id（プレゼン。微修正の土台があるか）。"""
    folder = store.root.run_dir(deck_id, shown["id"])
    index = W.snapshot_index(folder)
    out: set[str] = set()
    if index.get("pages"):
        for key, entry in index["pages"].items():
            if any(s.get("file") for s in entry.get("slots") or []):
                out.add(key)
        return out
    rows = {r["page_no"]: r.get("key") for r in store.run_pages(shown["id"])}
    for x in W.engine_record("presentation", folder).get("images") or []:
        if isinstance(x, dict) and x.get("origin") == "generated" and x.get("exists") and rows.get(x.get("slide")):
            out.add(rows[x["slide"]])
    return out


def _drawn_before(rows: list[dict[str, Any]], content: str | None) -> bool:
    """前の回（新しいものから）で、いまの中身（sha256）のまま描いた絵があるか。中身が分からない行は数えない。"""
    return content is not None and any(r.get("content_sha256") == content and r.get("asset_id") for r in rows)


def job_targets(jobs: list[dict[str, Any]]) -> dict[str, str]:
    """直している途中のページ（page_ids の id）→ running / queued。"""
    out: dict[str, str] = {}
    for j in jobs:
        req = j.get("request") or {}
        ids = list(req.get("pages") or []) + ([req["page"]] if req.get("page") else [])
        for pid in ids:
            if out.get(pid) != "running":
                out[pid] = j["state"]
    return out


def pages_view(store: Any, deck_id: str, *, shown: dict[str, Any] | None, latest: dict[str, Any] | None,
               jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    head = store.head_script(deck_id)
    if shown is None or head is None:
        return []   # 描き終えた回がまだ無い（最初の 1 冊を作っている途中）
    mode = shown["mode"]
    head_pages = store.script_pages(head["id"])
    shown_rows = {r["page_id"]: r for r in store.run_pages(shown["id"])}
    history: dict[str, list[dict[str, Any]]] = {}
    for row in store.page_history(deck_id):
        history.setdefault(row["page_id"], []).append(row)
    requests = store.run_requests(deck_id)
    legacy = LegacyContent(store, shown["args"], {p["page_id"]: p["key"] for p in head_pages})
    targets = job_targets(jobs)
    working = bool(jobs)
    generated = _generated_pages(store, deck_id, shown) if mode == "presentation" else set()
    failed_last = latest is not None and latest["state"] == "failed"
    pins = head_pins(store, deck_id, head, head_pages)
    previews = store.page_previews(deck_id)
    out = []
    for p in head_pages:
        pid = p["page_id"]
        row = shown_rows.get(pid)
        pin = pins.get(p["key"])
        versions, active = page_versions(history.get(pid, []), pin.run_id if pin else shown["id"], requests,
                                         page_id=pid, legacy_content=legacy)
        if pin is not None:
            row = pin.row   # 版を切り替えたページ: その版を描いた回の絵（組み直しの前から）
        has_image = bool(row and row["state"] == "ready" and row.get("asset_id") and row.get("asset_state") == "ready")
        image_url = asset_url(row["asset_id"]) if has_image else None
        same = bool(row) and (row.get("content_sha256") is None or p.get("content_sha256") is None
                              or row["content_sha256"] == p["content_sha256"])
        if not has_image and versions:   # 非表示・まだ描いていないページは、前に描いた絵を出す
            image_url = next((v["image_url"] for v in versions if v["image_url"]), None)
        if p["hidden"] and not image_url:
            # 非表示のページの下見の絵（描く回の最後に別に描く。中身が同じときだけ・docs/adr/0012-hidden-page-previews.md）
            pv = previews.get(pid)
            if pv and pv["asset_state"] == "ready" and pv["content_sha256"] == p.get("content_sha256"):
                image_url = asset_url(pv["asset_id"])
        if pid in targets:
            display = page_display("editing" if targets[pid] == "running" else "edit_queued")
        elif pin is not None:
            display = page_display("ready")
        elif row is not None and same:
            display = run_page_display(row["state"], shown["state"], has_image=has_image)
            if row["warnings"] and display["key"] == "failed":
                display = {**display, "reason": row["warnings"][0]}
        elif p["hidden"]:
            display = page_display("ready" if image_url else "unstarted")
        elif row is None and image_url and _drawn_before(history.get(pid, []), p.get("content_sha256")):
            # 見せる回では描いていない（非表示だった）が、いまの中身のまま前の回で描いたページ。
            # 表示に戻したばかり（描かない直し）。組み直しを待たずに、その絵を出す
            display = page_display("ready")
        elif working:
            display = page_display("generating")
        elif failed_last:
            display = page_display("failed", reason=latest.get("message") or None)
        else:
            display = page_display("unstarted")
        has_picture = (has_image if mode == "pitch" else p["key"] in generated) and not p["hidden"]
        if one_page(shown["args"]):
            has_picture = has_image
        version_no = active["version"] if active else (versions[0]["version"] if versions else 0)
        out.append({
            "can_minor": _minor(mode, has_picture),
            "position": p["page_no"],
            "hidden": p["hidden"],
            "slide_id": pid,
            "key": p["key"],
            "version_id": active["id"] if active else shown["id"],
            "version": version_no,
            "title": p["title"],
            "type": p["type"],
            "intent": None,
            "source_text": None,
            "body": p["body"],
            "prompt_text": None,
            "warnings": row["warnings"] if row else [],
            "source_kind": "generated",
            "source_kind_label": SOURCE_KIND_LABELS["generated"],
            "version_state": row["state"] if row else None,
            "image_url": image_url,
            "display": display,
            "item": None,
            "attempts": [],
            "versions": versions,
            # 版を切り替えたページか（台本の `_pin` が効いている）。切り替えた版を描き直さずに使っている
            "pinned": pin is not None,
            # 書き出しについての短い注（無ければ None）。部品を残す前の回の版に切り替えたページは、書き出しだけいまの作りで描き直す
            "export_note": PIN_EXPORT_REDRAW_NOTE if pin is not None and engine_for(mode).keeps_parts
                           and not W.has_parts(pin.run_dir, p["key"]) else None,
        })
    return out


def pending_inserts(store: Any, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """待っている・走っている差し込み（画面は隙間に「考えています…」を出す）。"""
    out = []
    for j in jobs:
        req = j.get("request") or {}
        if req.get("op") == "insert":
            out.append({"task_id": j["id"], "after_slide_id": req.get("after"), "state": j["state"]})
    return out


def export_formats(mode: str) -> list[dict[str, Any]]:
    """書き出しのメニューに出す形式。**そのモードで書き出せる形式だけを並べる**（モーションは HTML だけ、プレゼン・
    ピッチは PDF・PPTX。書き出せない形式は、押せなくして理由を出すのでなく、出さない。2026-10-14 利用者の指摘）。
    口（`POST /exports`）は、これまでどおり書き出せない形式を理由つきで断る。ページの有無による可否は `capabilities.export`。"""
    from .export_routes import format_refusal
    from .vocabulary import EXPORT_FORMAT_LABELS

    engine = engine_for(mode)
    out = []
    for fmt in ("pdf", "pptx", "html"):
        if fmt == "html" and fmt not in engine.exports:
            continue
        if format_refusal(mode, fmt) is not None:
            continue
        out.append({"format": fmt, "label": EXPORT_FORMAT_LABELS[fmt], "available": True, "reason": None})
    return out


def edit_capabilities(row: dict[str, Any], pages: list[dict[str, Any]], args: dict[str, Any],
                      *, labels: dict[str, str]) -> dict[str, Any]:
    """直す操作の可否（スライドの画面）。ページがあれば使える。仕事が走っていても受け付ける（順に並ぶ）。"""
    from .deck_routes import NO_PAGES_REASON

    has = bool(pages)
    pages_reason = None if has else NO_PAGES_REASON

    def cap(key: str, ok: bool, reason: str | None) -> dict[str, Any]:
        return {"available": ok, "label": labels[key], "reason": None if ok else reason}

    presentable = any(p.get("image_url") and not p["hidden"] for p in pages)
    structural = has and not one_page(args)
    struct_reason = pages_reason if not has else INFOGRAPHIC_PAGES_REASON
    templated = bool(args.get("template_snapshot"))   # デザインテンプレートの回: 前の版への切り替えは断る
    caps = {
        "page_instruction": cap("page_instruction", has, pages_reason),
        "pages_instruction": cap("pages_instruction", has, pages_reason),
        "deck_instruction": cap("deck_instruction", has, pages_reason),
        "regenerate": cap("regenerate", has, pages_reason),
        "adopt_version": cap("adopt_version", has, pages_reason),
        "insert_page": cap("insert_page", structural, struct_reason),
        "reorder_pages": cap("reorder_pages", structural, struct_reason),
        "duplicate_page": cap("duplicate_page", structural, struct_reason),
        "page_visibility": cap("page_visibility", structural, struct_reason),
        "export": cap("export", has, pages_reason),
        "present": cap("present", presentable, NOT_PRESENTABLE_REASON),
        "present_from_here": cap("present_from_here", presentable, NOT_PRESENTABLE_REASON),
        "generate": {"available": False, "label": labels["generate"], "reason": NOT_READY_REASON},
    }
    if templated:
        caps["adopt_version"] = cap("adopt_version", False, TEMPLATE_RESTORE_REASON)
    return caps
