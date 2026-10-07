"""直す仕事（P3）: いまの台本を前に進め、1 冊を組み直して新しい描いた回にする。

**どの直しも、仕事を始めたときのいまの台本から始める**（1 冊に走る仕事は 1 つだけ。ページは id で指すので、
前の直しの上にそのまま重なる）。指定したページが台本から消えていたら、その仕事は理由を言って失敗にする。

| op | 何をするか | 台本係 |
|---|---|---|
| revise | 指定したページだけを直す。台本係がページごとに振り分ける（script＝台本を差し替える／image＝エンジンの revise で絵だけ直す／both＝台本 → revise）。指定外のページは 1 文字も変えない。隣も直した方がよければ提案（suggestions）として返し、当てない | 呼ぶ |
| insert | 指定したページの後ろに、指示に合うページを足す | 呼ぶ |
| duplicate | ページを複製してすぐ後ろに置く（新しい id。ピッチは区画の絵を写して描き直さない） | 呼ばない |
| visibility | 非表示 / 表示（台本の `_hidden`。エンジンに渡す台本からは抜く） | 呼ばない |
| reorder | 並べ替え | 呼ばない |
| restore | 版スタックの前の姿に戻す（その回の台本のページの中身に戻し、絵だけが違ったなら、その回の絵をエンジンが読む場所へ戻す） | 呼ばない |
| render | いまの台本で描き直す（止まった回の再開・書き出す形式が見せる回に無いとき） | 呼ばない |
| rebuild | 設定を替えて組み直す（いまの台本を、いまのブランド・スタイル・書式で。P5） | 呼ばない |
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..childenv import child_env
from ..engines import RC_DEPENDENCY, engine_for
from ..render import look as LOOK
from ..render import workspace as W
from ..render.runner import DEPENDENCY_MESSAGE, DOCTOR_HINT, check_prerequisites
from ..scriptdoc import (
    HIDDEN_KEY,
    deep,
    dumps,
    engine_view,
    fresh_id,
    headline_of,
    is_hidden,
    one_page,
    page_id_problems,
    screen_pages,
    slides_of,
)
from ..writer.backends import WriterFailed, ask, choose_backend
from ..writer.patch import apply_revision, insert_after, parse_insert, parse_revision
from ..writer.prompt import ContractMissing, build_insert_prompt, build_revision_prompt
from .pipeline import (
    Context,
    RenderPlan,
    _writer_progress,
    check_candidate,
    import_recorded,
    render_and_import,
    settle_look,
)

log = logging.getLogger("quick_slide.worker")

GONE_MESSAGE = "直すページ（{what}）は、いまの台本にもうありません。画面を読み込み直してから、もう一度頼んでください。"


class EditRefused(Exception):
    def __init__(self, message: str, kind: str = "edit") -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind


@dataclass
class EditResult:
    data: dict[str, Any]
    plan: RenderPlan
    written_by: str = "screen"
    backend: str | None = None
    summary: str = ""
    suggestions: list[dict[str, str]] = field(default_factory=list)
    routes: dict[str, str] = field(default_factory=dict)
    candidate: dict[str, Any] | None = None   # 台本係が書いて検査を通った版（もう保存してある）


def edit(ctx: Context, job: dict[str, Any]) -> str:
    store = ctx.store
    run = ctx.call(store.run, job["run_id"])
    deck_id, run_id = run["deck_id"], run["id"]
    engine = engine_for(run["mode"])
    req = job.get("request") or {}
    op = req.get("op")
    resume = job.get("resume_from")
    if resume is None:
        # 設定（ブランド・スタイル・書式）はこの回から効く。全ページを 1 枚に描くかは前の回のまま（P4）
        settle_look(ctx, run, previous_style=run["args"].get("style"))
    args = run["args"]

    def fail(message: str, kind: str, rc: int | None = None) -> str:
        ctx.call(store.fail_run, run_id, message=message, failure_kind=kind, rc=rc)
        return "failed"

    if resume == "import":
        return import_recorded(ctx, run)
    if resume == "render":
        script = ctx.call(store.script, run["script_id"])
        return render_and_import(ctx, run, script, ctx.call(store.load_script, script), RenderPlan(), start=False)

    if one_page(args) and op in ("duplicate", "visibility", "reorder", "insert"):
        return fail("台本の全ページを 1 枚に描くスタイル（infographic）では、ページの操作はできません。", "edit")
    head = ctx.call(store.head_script, deck_id)
    if head is None:
        return fail("まだページがありません。最初の 1 冊ができてから直してください。", "edit")
    data = ctx.call(store.load_script, head)
    ctx.call(store.set_run_record, run_id, {"base": {"id": head["id"], "version": head["version"]}})
    try:
        if op == "revise":
            result = _revise(ctx, job, run, req, data)
        elif op == "insert":
            result = _insert(ctx, job, run, req, data)
        elif op == "duplicate":
            result = _duplicate(ctx, deck_id, req, data, engine.mode)
        elif op == "visibility":
            result = _visibility(ctx, deck_id, req, data)
        elif op == "reorder":
            result = _reorder(ctx, deck_id, req, data)
        elif op == "restore":
            result = _restore(ctx, deck_id, req, data, engine.mode, args)
        elif op in ("render", "rebuild"):
            # 描き直し（再開・書き出しの形式が無いとき）と、設定を替えて組み直す（P5）。どちらも台本はそのまま、
            # 設定（ブランド・スタイル・書式）はこの回の最初に読み直してある（settle_look）。描いた絵はキャッシュから
            result = EditResult(data=data, plan=RenderPlan())
        else:
            return fail("知らない直しです。", "edit")
    except EditRefused as exc:
        return fail(exc.message, exc.kind)
    if isinstance(result, str):
        return result
    if result.candidate is not None:
        script = result.candidate
    elif dumps(result.data) != dumps(data):
        script = ctx.call(store.save_script, deck_id, result.data, written_by=result.written_by,
                          writer_backend=result.backend, intake_id=None, run_id=run_id,
                          instruction=(req.get("text") or None))
        ctx.call(store.set_script_check, script["id"], 0, [])
    else:
        script = head
    if script["id"] != head["id"]:
        ctx.call(store.accept_script, script["id"], screen_pages(args, result.data))
    shown = _positions(result.data)
    suggestions = [{"id": s["id"], "position": shown.get(s["id"]), "text": s["text"]} for s in result.suggestions]
    ctx.call(store.set_run_record, run_id, {"edit": {"op": op, "summary": result.summary, "routes": result.routes,
                                                     "suggestions": suggestions, "script_version": script["version"]}})
    message = None
    if suggestions:
        message = "ほかのページへの提案（当てていません）: " + " ／ ".join(
            f"{s['position']}枚目: {s['text']}" for s in suggestions)
    return render_and_import(ctx, run, script, result.data, result.plan, message=message)


# --- 小さな道具 ---------------------------------------------------------------------------------------------

def _positions(data: dict[str, Any]) -> dict[str, int]:
    """ページの id → 画面での番号（非表示も数える。画面の並びと同じ）。"""
    return {str(s.get("id")): i for i, s in enumerate(slides_of(data), 1)}


def _key(ctx: Context, deck_id: str, page_id: Any, data: dict[str, Any], what: str = "") -> str:
    key = ctx.call(ctx.store.page_key_of, page_id) if isinstance(page_id, str) else None
    if key is None or key not in _positions(data):
        raise EditRefused(GONE_MESSAGE.format(what=what or "指定したページ"))
    return key


def _attachments(ctx: Context, deck_id: str, asset_ids: list[str]) -> tuple[list[str], list[Path]]:
    """添えた画像を台本のフォルダの `attachments/` に写す。(台本に書ける相対パス, 絶対パス)。"""
    rels: list[str] = []
    paths: list[Path] = []
    folder = ctx.root.attachments_dir(deck_id)
    for aid in asset_ids:
        asset = ctx.call(ctx.store.asset, aid)
        src = ctx.root.root / asset["relative_path"]
        if not src.is_file():
            raise EditRefused("添えた画像が見つかりません。添え直してから、もう一度頼んでください。")
        name = f"{hashlib.sha256(src.read_bytes()).hexdigest()[:16]}{src.suffix.lower() or '.png'}"
        dst = folder / name
        if not dst.is_file():
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        rels.append(f"attachments/{name}")
        paths.append(dst)
    return rels, paths


def _writer(ctx: Context, run: dict[str, Any], req: dict[str, Any]) -> tuple[str, str, dict[str, str]]:
    available = {"claude": ctx.tools.claude_path(), "codex": ctx.tools.codex_path()}
    origin = req.get("origin_runtime") or run["args"].get("origin_runtime")
    backend, why = choose_backend(ctx.settings.writer, origin, available)
    exe = available[backend]
    assert exe is not None
    return backend, exe, {"writer": backend, "writer_reason": why}


def _pictures(ctx: Context, deck_id: str, mode: str, keys: list[str]) -> dict[str, Any]:
    """直せる絵: ピッチは描き終えた絵があるページ（"あり"）、プレゼンは描いた生成の枠の名前の並び。"""
    shown = ctx.call(ctx.store.shown_run, deck_id)
    if shown is None:
        return {}
    rows = {r["key"]: r for r in ctx.call(ctx.store.run_pages, shown["id"]) if r.get("key")}
    out: dict[str, Any] = {}
    work = ctx.root.work_dir(deck_id)
    layout = W.read_layout(work).get("ids") or []
    for k in keys:
        row = rows.get(k)
        if row is None or row["state"] != "ready":
            continue
        if mode == "pitch":
            out[k] = "あり"
            continue
        no = layout.index(k) + 1 if k in layout else row["page_no"]
        folder = work if k in layout else ctx.root.run_dir(deck_id, shown["id"])
        names = [s["slot"] for s in W.presentation_slots(folder, no) if s["origin"] == "generated" and s["exists"]]
        if names:
            out[k] = names
    return out


def _engine_problems(problems: list[str], data: dict[str, Any]) -> list[str]:
    """エンジンの `NG: N枚目` に、その番号のページの id を添える（台本係が読み違えないように）。"""
    import re

    visible = [str(s.get("id")) for s in slides_of(engine_view(data))]

    def name(m: "re.Match[str]") -> str:
        n = int(m.group(1))
        return f"{n}枚目（id={visible[n - 1]}）" if 1 <= n <= len(visible) else m.group(0)

    return [re.sub(r"(\d+)枚目", name, p, count=1) for p in problems]


# --- 台本係を呼ぶ直し ------------------------------------------------------------------------------------------

def _writer_loop(ctx: Context, job: dict[str, Any], run: dict[str, Any], req: dict[str, Any], data: dict[str, Any],
                 build, parse_and_apply) -> EditResult | str:
    """台本係に書かせ、確かめ、当て、検査に通るまで直させる（上限あり）。"""
    store = ctx.store
    run_id, deck_id = run["id"], run["deck_id"]
    engine = engine_for(run["mode"])
    rounds = ctx.settings.worker.max_check_rounds
    run_dir = ctx.root.run_dir(deck_id, run_id)

    def fail(message: str, kind: str, rc: int | None = None) -> str:
        ctx.call(store.fail_run, run_id, message=message, failure_kind=kind, rc=rc)
        return "failed"

    # 描く前の確かめ（ピッチは Codex のログイン）。描けないのに台本係の枠を使わない
    pre = check_prerequisites(engine, tools=ctx.tools, home=ctx.root.root, cwd=run_dir, stop=ctx.stop)
    if not pre.ok:
        return fail(pre.message or "", pre.failure_kind or "engine", pre.rc)
    try:
        backend, exe, why = _writer(ctx, run, req)
    except WriterFailed as exc:
        return fail(exc.message, "writer_" + exc.kind)
    env = child_env(home=ctx.root.root, extra=ctx.tools.extra_env)
    previous: Any = None
    problems: list[str] | None = None
    for round_no in range(1, rounds + 1):
        if ctx.stop.is_set():
            return fail("サーバを止めたので、直すのを中断しました。もう一度頼んでください。", "stopped")
        ctx.call(store.set_run_state, run_id, "writing", progress=_writer_progress(round_no, rounds, backend, "writing"),
                 args_update=why)
        try:
            prompt = build(previous, problems)
        except ContractMissing as exc:
            return fail(f"エンジンの台本の決まり（{exc}）が見つかりません。導入をやり直してください。", "contract")
        try:
            answer = ask(backend, exe, prompt, job_dir=run_dir / "writer" / f"round-{round_no}", env=env,
                         timeout=ctx.settings.worker.writer_timeout_seconds, stop=ctx.stop)
        except WriterFailed as exc:
            if exc.kind == "format":
                previous, problems = "（前の答えは JSON として読めませんでした）", [
                    "NG: 返したものが JSON のオブジェクト 1 つではなかった。決まった形の JSON オブジェクトだけを返す"]
                continue
            return fail(exc.message, "writer_" + exc.kind)
        del prompt
        result, own = parse_and_apply(answer)
        if own:
            previous, problems = answer, own
            continue
        result.backend = backend
        result.written_by = "writer"
        if dumps(result.data) == dumps(data):
            return result   # 台本は変わらない（絵だけを直す・変えなくてよかった）
        candidate = ctx.call(store.save_script, deck_id, result.data, written_by="writer", writer_backend=backend,
                             intake_id=None, run_id=run_id, round_no=round_no,
                             instruction=(req.get("text") or None))
        ctx.call(store.set_run_state, run_id, "checking", progress=_writer_progress(round_no, rounds, backend, "checking"))
        before = {str(x.get("id")): dumps(x) for x in slides_of(data)}
        changed = {str(x.get("id")) for x in slides_of(result.data) if before.get(str(x.get("id"))) != dumps(x)}
        ids = page_id_problems(slides_of(result.data)) + LOOK.type_problems(
            slides_of(result.data), run["args"], only=changed)
        if ids:
            ctx.call(store.set_script_check, candidate["id"], 2, ids)
            previous, problems = answer, ids
            continue
        checked = check_candidate(ctx, engine, run, result.data)
        ctx.call(store.set_script_check, candidate["id"], -1 if checked.rc is None else checked.rc,
                 checked.problems + checked.warnings)
        if checked.stopped:
            return fail("サーバを止めたので、台本を確かめるのを中断しました。もう一度頼んでください。", "stopped")
        if checked.rc == 0:
            result.candidate = candidate
            return result
        if checked.rc == 2 and LOOK.theme_refused(checked.problems):
            return fail(LOOK.BRAND_COLOR_MESSAGE, "brand", checked.rc)
        if checked.rc == 2:
            previous = answer
            problems = _engine_problems(checked.problems or ["NG: 検査で止まった（理由の行が取れなかった）"], result.data)
            continue
        if checked.rc == RC_DEPENDENCY:
            return fail(DEPENDENCY_MESSAGE, "dependency", checked.rc)
        return fail("台本を確かめられませんでした（エンジンの検査が動きませんでした）。" + DOCTOR_HINT, "check", checked.rc)
    return fail(f"直した台本を {rounds} 回確かめましたが、エンジンの決まりに合いませんでした。指示を短くするか、もう一度頼んでください。",
                "script")


def _revise(ctx: Context, job: dict[str, Any], run: dict[str, Any], req: dict[str, Any], data: dict[str, Any]):
    deck_id = run["deck_id"]
    engine = engine_for(run["mode"])
    args = run["args"]
    positions = _positions(data)
    keys: list[str] = []
    if one_page(args):
        # 1 枚にまとめるスタイル: 画面のページは 1 枚（全ページの姿）。直すのは台本の全ページ
        keys = [str(s.get("id")) for s in slides_of(data)]
    for pid in [] if one_page(args) else req.get("pages") or []:
        k = _key(ctx, deck_id, pid, data, what=", ".join(str(n) for n in req.get("positions") or []) or "")
        if k not in keys:
            keys.append(k)
    if not keys:
        raise EditRefused("直すページがありません。")
    rels, paths = _attachments(ctx, deck_id, req.get("attachments") or [])
    pictures = _pictures(ctx, deck_id, engine.mode, keys) if not one_page(args) else {}
    by_key = {str(s.get("id")): s for s in slides_of(data)}
    targets = [{"id": k, "position": positions[k], "title": headline_of(by_key[k]), "picture": pictures.get(k)}
               for k in keys]
    instruction = req.get("text") or ""
    remake = req.get("edit_mode") == "remake"

    def build(previous, problems):
        return build_revision_prompt(engine, args, script=data, targets=targets, instruction=instruction,
                                     attachments=rels, skills_dir=ctx.tools.skills_dir, scope=req.get("scope") or "pages",
                                     previous=previous, problems=problems)

    def parse_and_apply(answer):
        patch = parse_revision(answer, script=data, targets=keys, pictures=pictures, attachments=rels, mode=engine.mode)
        if patch.problems:
            return None, patch.problems
        new = apply_revision(data, patch) if patch.script_changes else data
        plan = RenderPlan(deck=bool(patch.script_changes) or not patch.image_changes)
        for pp in patch.image_changes:
            plan.revise.append({"key": pp.key, "slot": (pp.image or {}).get("slot"),
                                "note": (pp.image or {}).get("note") or instruction or "添えた画像に合わせて直す",
                                "remake": remake, "attach": paths})
        if one_page(args) and plan.revise:
            plan.revise = [{**plan.revise[0], "key": "infographic"}]
        return EditResult(data=new, plan=plan, summary=patch.summary, suggestions=patch.suggestions,
                          routes={k: p.route for k, p in patch.pages.items()}), []

    ctx.call(ctx.store.set_job_note, job["id"], {"keys": keys})
    return _writer_loop(ctx, job, run, req, data, build, parse_and_apply)


def _insert(ctx: Context, job: dict[str, Any], run: dict[str, Any], req: dict[str, Any], data: dict[str, Any]):
    deck_id = run["deck_id"]
    engine = engine_for(run["mode"])
    args = run["args"]
    anchor = None
    after = None
    if req.get("after"):
        anchor = _key(ctx, deck_id, req["after"], data, what="足す場所のページ")
        page = next(s for s in slides_of(data) if s.get("id") == anchor)
        after = {"id": anchor, "position": _positions(data)[anchor], "title": headline_of(page)}
    rels, _ = _attachments(ctx, deck_id, req.get("attachments") or [])
    instruction = req.get("text") or ""

    def build(previous, problems):
        return build_insert_prompt(engine, args, script=data, after=after, instruction=instruction, attachments=rels,
                                   skills_dir=ctx.tools.skills_dir, previous=previous, problems=problems)

    def parse_and_apply(answer):
        pages, summary, problems = parse_insert(answer, script=data, attachments=rels)
        if problems:
            return None, problems
        return EditResult(data=insert_after(data, anchor, pages), plan=RenderPlan(), summary=summary,
                          routes={str(p.get("id")): "insert" for p in pages}), []

    return _writer_loop(ctx, job, run, req, data, build, parse_and_apply)


# --- 台本係を呼ばない直し --------------------------------------------------------------------------------------

def _duplicate(ctx: Context, deck_id: str, req: dict[str, Any], data: dict[str, Any], mode: str) -> EditResult:
    key = _key(ctx, deck_id, req.get("page"), data)
    slides = list(data.get("slides") or [])
    at = next(i for i, s in enumerate(slides) if isinstance(s, dict) and s.get("id") == key)
    copy = deep(slides[at])
    copy.pop(HIDDEN_KEY, None)
    copy["id"] = fresh_id(key, {str(s.get("id")) for s in slides_of(data)})
    plan = RenderPlan()
    if mode == "pitch":
        plan.aliases[copy["id"]] = key   # 同じ中身なので同じ鍵。区画の絵を写せば描き直さない
    return EditResult(data={**data, "slides": slides[:at + 1] + [copy] + slides[at + 1:]}, plan=plan,
                      summary="ページを複製しました。", routes={copy["id"]: "duplicate"})


def _visibility(ctx: Context, deck_id: str, req: dict[str, Any], data: dict[str, Any]) -> EditResult:
    key = _key(ctx, deck_id, req.get("page"), data)
    hidden = bool(req.get("hidden"))
    slides = []
    for s in data.get("slides") or []:
        if isinstance(s, dict) and s.get("id") == key:
            s = dict(s)
            if hidden:
                s[HIDDEN_KEY] = True
            else:
                s.pop(HIDDEN_KEY, None)
        slides.append(s)
    new = {**data, "slides": slides}
    if not any(not is_hidden(s) for s in slides_of(new)):
        raise EditRefused("最後の 1 ページは非表示にできません。")
    return EditResult(data=new, plan=RenderPlan(), summary="非表示にしました。" if hidden else "表示に戻しました。",
                      routes={key: "hidden" if hidden else "shown"})


def _reorder(ctx: Context, deck_id: str, req: dict[str, Any], data: dict[str, Any]) -> EditResult:
    wanted: list[str] = []
    for pid in req.get("order") or []:
        k = ctx.call(ctx.store.page_key_of, pid) if isinstance(pid, str) else None
        if k is not None and k in _positions(data) and k not in wanted:
            wanted.append(k)
    slides = slides_of(data)
    by_key = {str(s.get("id")): s for s in slides}
    order = list(wanted)
    # 頼んだあとに足されたページ（並びに無い）は、前にいたページのすぐ後ろへ
    for i, s in enumerate(slides):
        k = str(s.get("id"))
        if k in order:
            continue
        prev = next((str(slides[j].get("id")) for j in range(i - 1, -1, -1) if str(slides[j].get("id")) in order), None)
        order.insert(order.index(prev) + 1 if prev else 0, k)
    return EditResult(data={**data, "slides": [by_key[k] for k in order]}, plan=RenderPlan(), summary="並べ替えました。")


def _restore(ctx: Context, deck_id: str, req: dict[str, Any], data: dict[str, Any], mode: str,
             args: dict[str, Any]) -> EditResult:
    if one_page(args):
        raise EditRefused("台本の全ページを 1 枚に描くスタイル（infographic）では、ページの版から戻せません。")
    key = _key(ctx, deck_id, req.get("page"), data)
    store = ctx.store
    try:
        old_run = ctx.call(store.run, req.get("run"))
    except Exception:  # noqa: BLE001 - 無い回
        raise EditRefused("その版はもうありません。画面を読み込み直してください。") from None
    if old_run["deck_id"] != deck_id or old_run["state"] != "done" or not old_run.get("script_id"):
        raise EditRefused("その版には戻せません。")
    old_script = ctx.call(store.load_script, ctx.call(store.script, old_run["script_id"]))
    old_page = next((s for s in slides_of(old_script) if s.get("id") == key), None)
    if old_page is None:
        raise EditRefused("その版にはこのページがありません。")
    restored = deep(old_page)
    restored.pop(HIDDEN_KEY, None)
    slides = []
    for s in data.get("slides") or []:
        if isinstance(s, dict) and s.get("id") == key:
            new = dict(restored)
            if is_hidden(s):
                new[HIDDEN_KEY] = True
            s = new
        slides.append(s)
    old_dir = ctx.root.run_dir(deck_id, old_run["id"])
    plan = RenderPlan()
    if mode == "pitch":
        no = next((r["page_no"] for r in ctx.call(store.run_pages, old_run["id"]) if r.get("key") == key), None)
        got = W.pitch_picture(old_dir, key, legacy_no=no)
        if got is not None:
            plan.seeds[key] = got
    else:
        plan.restore_presentation.append((old_dir, key))
    return EditResult(data={**data, "slides": slides}, plan=plan, summary="前の版に戻しました。",
                      routes={key: "restore"})
