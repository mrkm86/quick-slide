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
| restore | 版スタックの前の姿に切り替える（その回の台本のページの中身に戻し、ページに `_pin`＝その回の id を書く。描くときはその回の絵と書き出しの部品を使う） | 呼ばない |
| render | いまの台本で描き直す（止まった回の再開・書き出す形式が見せる回に無いとき・描かない直しの後の組み直し） | 呼ばない |
| rebuild | 設定を替えて組み直す（いまの台本を、いまのブランド・スタイル・書式で。版の印は全部外す。P5） | 呼ばない |

**版の印（`_pin`）の決まり**（`docs/adr/0001-version-pins.md`）: 切り替えた版は、そのページの中身が変わるまで指したまま。
直す（revise。絵だけの直しも）・分ける・複製の写しでは外れる（写しは印を持たない）。非表示・並べ替え・差し込み・
描き直し（render）では残る。組み直し（rebuild）は利用者が頼んだ描き直しなので、全部外す（新しい版ができてよい）。
普段は画面の口（`edit_routes.route_adopt`）がその場で当て、ここの restore は直しの仕事が待っているときだけ通る。
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from .. import edit_kinds, importing, motion
from .. import page_images as PI
from ..childenv import child_env
from ..engines import RC_DEPENDENCY, engine_for
from ..pins import PinRefused, pin_source
from ..render import illustrations as ILL
from ..render import look as LOOK
from ..render import workspace as W
from ..preparation.errors import EngineInputUnsupported, PreparationStopped
from ..preparation.service import TemplateRun, load_template_run
from ..render.runner import DOCTOR_HINT, check_prerequisites, dependency_message
from ..scriptdoc import (
    IMPORTED_TYPE,
    HIDDEN_KEY,
    PIN_KEY,
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
    with_hidden,
    with_order,
    with_restored,
    without_pins,
)
from ..writer import jlint as JLINT
from ..writer.backends import WriterFailed, ask, choose_backend, extract_json
from ..writer.patch import apply_revision, insert_after, parse_insert, parse_revision
from ..writer.prompt import (
    ContractMissing,
    build_imported_text_prompt,
    build_insert_prompt,
    build_overlay_prompt,
    build_rebuild_prompt,
    build_replace_prompt,
    build_revision_prompt,
)
from .proc import run_process
from .pipeline import (
    Context,
    RenderPlan,
    TEMPLATE_RENDER_PENDING_MESSAGE,
    TEMPLATE_STOP_KIND,
    _template_stop,
    _writer_progress,
    check_candidate,
    import_recorded,
    lint_view,
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
    # ほかの台本の版に、この直しだけを当て直す（ページを指定した直し。ほかのページの直しが先に当たっていたとき）
    rebase: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    # 検査の警告のうち、この直しでは書き直させるもの（`NG:` の行にして返す。最後の回は止めない）
    ng_warnings: Callable[[list[str]], list[str]] | None = None


# ---- 同じデッキの仕事の順番（docs/adr/0012-parallel-edits.md）----
# 別のページの直しは同時に走る（台帳の `claim_job`）。台本に当てるのと描くのは、デッキごとに 1 つずつ。
# サーバは 1 つの置き場に 1 つしか動かないので、鍵はこのプロセスの中だけで足りる
_TURNS: dict[tuple[str, str], threading.Lock] = {}
_TURNS_GUARD = threading.Lock()
STOPPED_MESSAGE = "サーバを止めたので、直すのを中断しました。もう一度頼んでください。"


class _Stopped(Exception):
    pass


@contextmanager
def deck_turn(ctx: Context, deck_id: str, what: str) -> Iterator[None]:
    """そのデッキの「当てる」か「描く」の番を待つ（サーバを止めたら待つのをやめる）。"""
    with _TURNS_GUARD:
        lock = _TURNS.setdefault((deck_id, what), threading.Lock())
    while not lock.acquire(timeout=0.5):
        if ctx.stop.is_set():
            raise _Stopped()
    try:
        yield
    finally:
        lock.release()


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

    template: TemplateRun | None = None
    if args.get("template_snapshot"):
        if resume is not None or op not in ("revise", "insert"):
            # 描く段・描き直しにつながる直しは、描画（PR 7）がつながるまで、古い経路で描かずに止める
            return fail(TEMPLATE_RENDER_PENDING_MESSAGE, TEMPLATE_STOP_KIND)
        try:
            template = load_template_run(ctx, run)   # 完了した準備の控え（素材の読み直し・LLM はしない）
        except PreparationStopped as exc:
            return _template_stop(ctx, None, run_id, exc)

    if resume == "import":
        return import_recorded(ctx, run)
    if resume == "render":
        script = ctx.call(store.script, run["script_id"])
        try:
            with deck_turn(ctx, deck_id, "render"):
                return render_and_import(ctx, run, script, ctx.call(store.load_script, script), RenderPlan(), start=False)
        except _Stopped:
            return fail(STOPPED_MESSAGE, "stopped")

    if one_page(args) and op in ("duplicate", "visibility", "reorder", "insert"):
        return fail("台本の全ページを 1 枚に描くスタイル（infographic）では、ページの操作はできません。", "edit")
    head = ctx.call(store.head_script, deck_id)
    if head is None:
        return fail("まだページがありません。最初の 1 冊ができてから直してください。", "edit")
    data = ctx.call(store.load_script, head)
    ctx.call(store.set_run_record, run_id, {"base": {"id": head["id"], "version": head["version"]}})
    try:
        if op == "revise":
            result = _revise(ctx, job, run, req, data, template)
        elif op == "insert":
            result = _insert(ctx, job, run, req, data, template)
        elif op == "duplicate":
            result = _duplicate(ctx, deck_id, req, data, engine.mode)
        elif op == "visibility":
            result = _visibility(ctx, deck_id, req, data)
        elif op == "reorder":
            result = _reorder(ctx, deck_id, req, data)
        elif op == "restore":
            result = _restore(ctx, deck_id, req, data, engine.mode, args)
        elif op == "render":
            # 描き直し（再開・書き出しの形式が無いとき・描かない直しの後の組み直し）。台本はそのまま（版の印も残す）。
            # 設定（ブランド・スタイル・書式）はこの回の最初に読み直してある（settle_look）。描いた絵はキャッシュから
            result = EditResult(data=data, plan=RenderPlan())
        elif op == "rebuild":
            # 設定を替えて組み直す（P5）。利用者が頼んだ描き直しなので、版の印は外して全ページをいまの作りで描く
            result = EditResult(data=without_pins(data), plan=RenderPlan())
        else:
            return fail("知らない直しです。", "edit")
    except EditRefused as exc:
        return fail(exc.message, exc.kind)
    if isinstance(result, str):
        return result
    if template is not None:
        # テンプレートの直しも、候補の台本と準備の記録を保存したところで止める（描画が無いので、承認してページを変えない）
        ctx.call(store.set_run_record, run_id, {"template_stop": {
            "reason": "render_unavailable", "generation": template.generation,
            "candidate": result.candidate["id"] if result.candidate else None}})
        return fail(TEMPLATE_RENDER_PENDING_MESSAGE, TEMPLATE_STOP_KIND)
    try:
        with deck_turn(ctx, deck_id, "apply"):
            script = _apply(ctx, run, req, head, data, result)
        if isinstance(script, str):
            return fail(script, "edit")
        return _finish(ctx, run, op, script, result)
    except _Stopped:
        return fail(STOPPED_MESSAGE, "stopped")


def _apply(ctx: Context, run: dict[str, Any], req: dict[str, Any], head: dict[str, Any], data: dict[str, Any],
           result: EditResult) -> dict[str, Any] | str:
    """直した台本をデッキのいまの版にする（デッキごとに 1 つずつ。`deck_turn(…, "apply")` の中で呼ぶ）。
    戻り値はいまの版になった台本の行。断るときは文。

    書き始めたあとで、ほかのページの直しが先に当たっていたら、この直しだけをいまの版に当て直し、もう一度検査する。"""
    store = ctx.store
    deck_id, run_id = run["deck_id"], run["id"]
    now = ctx.call(store.head_script, deck_id)
    if now is not None and now["id"] != head["id"]:
        if result.rebase is None:   # ほかと重ねない直しは 1 つずつ走るので、ここへは来ない（来たら当てない）
            return "ほかの直しが先に当たりました。画面を読み込み直してから、もう一度頼んでください。"
        current = ctx.call(store.load_script, now)
        merged = result.rebase(current)
        result.data = merged
        if dumps(merged) == dumps(current):
            return now
        script = ctx.call(store.save_script, deck_id, merged, written_by=result.written_by, writer_backend=result.backend,
                          intake_id=None, run_id=run_id, instruction=_instruction_note(req))
        checked = check_candidate(ctx, engine_for(run["mode"]), run, merged)
        ctx.call(store.set_script_check, script["id"], -1 if checked.rc is None else checked.rc,
                 checked.problems + checked.warnings)
        if checked.stopped:
            raise _Stopped()
        if checked.rc != 0:
            why = (checked.problems or [""])[0]
            return "ほかのページの直しと合わせると、エンジンの決まりに合いませんでした。もう一度頼んでください。" + (
                f"（{why}）" if why else "")
        ctx.call(store.accept_script, script["id"], screen_pages(run["args"], merged))
        return script
    if result.candidate is not None:
        script = result.candidate
    elif dumps(result.data) != dumps(data):
        script = ctx.call(store.save_script, deck_id, result.data, written_by=result.written_by,
                          writer_backend=result.backend, intake_id=None, run_id=run_id,
                          instruction=_instruction_note(req))
        ctx.call(store.set_script_check, script["id"], 0, [])
    else:
        script = head
    if script["id"] != head["id"]:
        ctx.call(store.accept_script, script["id"], screen_pages(run["args"], result.data))
    return script


def _finish(ctx: Context, run: dict[str, Any], op: str, script: dict[str, Any], result: EditResult) -> str:
    """直しの記録を書き、描く番を待って描く。描くのは、番が来たときのいまの版（あとから当たったほかの直しも一緒に描く）。"""
    store = ctx.store
    deck_id, run_id = run["deck_id"], run["id"]
    shown = _positions(result.data)
    suggestions = [{"id": s["id"], "position": shown.get(s["id"]), "text": s["text"]} for s in result.suggestions]
    ctx.call(store.set_run_record, run_id, {"edit": {"op": op, "summary": result.summary, "routes": result.routes,
                                                     "suggestions": suggestions, "script_version": script["version"]}})
    message = None
    if suggestions:
        message = "ほかのページへの提案（当てていません）: " + " ／ ".join(
            f"{s['position']}枚目: {s['text']}" for s in suggestions)
    with deck_turn(ctx, deck_id, "render"):
        latest = ctx.call(store.head_script, deck_id) or script
        data = result.data if latest["id"] == script["id"] else ctx.call(store.load_script, latest)
        return render_and_import(ctx, run, latest, data, result.plan, message=message)


# --- 小さな道具 ---------------------------------------------------------------------------------------------

def _edit_args(args: dict[str, Any], req: dict[str, Any]) -> dict[str, Any]:
    """直しの台本係に渡す描き方。生成の画像を使えるかは、デッキを作ったときの指定ではなく、頼んだときの機械で決める
    （Codex があれば使える。docs/adr/0010-images-follow-the-machine.md）。前の形の頼み（`can_generate` 無し）はそのまま"""
    if "can_generate" not in req or not engine_for(args.get("mode") or "presentation").has_images:
        return args
    return {**args, "images": bool(req["can_generate"])}


def _instruction_note(req: dict[str, Any]) -> str | None:
    """台本の版に控える指示（書いた文。書かずに「何を直しますか？」だけで頼んだら、選んだ方針の名前）。"""
    return req.get("text") or edit_kinds.summary(req.get("kind"), req.get("options") or []) or None


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


def _page_pictures(ctx: Context, deck_id: str, engine: Any, req: dict[str, Any]) -> list[dict[str, Any]]:
    """直し・足すの指示に書かれたページの本文の画像（頼まれたときだけ・プレゼンだけ。docs/adr/0016）。"""
    if engine.mode != "presentation" or not PI.wanted_urls(req.get("text")):
        return []
    pics = PI.collect(req.get("text"), ctx.root.attachments_dir(deck_id))
    if not pics:
        log.info("ページの画像: 指示のページから取り込める画像がなかった")
    return pics


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
    if shown is None or mode == "motion":   # モーションには直せる絵が無い
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
                 build, parse_and_apply, template: TemplateRun | None = None) -> EditResult | str:
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
                         timeout=ctx.settings.worker.writer_timeout_seconds, stop=ctx.stop,
                         no_tools=template is not None)
        except WriterFailed as exc:
            if exc.kind == "format":
                previous, problems = "（前の答えは JSON として読めませんでした）", [
                    "NG: 返したものが JSON のオブジェクト 1 つではなかった。決まった形の JSON オブジェクトだけを返す"]
                continue
            return fail(exc.message, "writer_" + exc.kind)
        del prompt
        if motion.is_motion(run["args"]):
            answer = motion.normalize_answer(answer)
        result, own = parse_and_apply(answer)
        if own:
            previous, problems = answer, own
            continue
        result.backend = backend
        result.written_by = "writer"
        if template is not None:
            result.rebase = None   # テンプレートの資料は、直しを資料ごとに 1 つずつ当てる（並列で重ねない）
            ref_problems = template.check_script(result.data)
            if ref_problems:
                previous, problems = answer, ref_problems
                continue
            result.data = template.stamped(result.data)
        if dumps(result.data) == dumps(data):
            return result   # 台本は変わらない（絵だけを直す・変えなくてよかった）
        candidate = ctx.call(store.save_script, deck_id, result.data, written_by="writer", writer_backend=backend,
                             intake_id=None, run_id=run_id, round_no=round_no,
                             instruction=_instruction_note(req))
        ctx.call(store.set_run_state, run_id, "checking", progress=_writer_progress(round_no, rounds, backend, "checking"))
        before = {str(x.get("id")): dumps(x) for x in slides_of(data)}
        changed = {str(x.get("id")) for x in slides_of(result.data) if before.get(str(x.get("id"))) != dumps(x)}
        ids = page_id_problems(slides_of(result.data)) + LOOK.type_problems(
            slides_of(result.data), run["args"], only=changed)
        # 日本語の検査（絵文字・前置き）。直したページだけ。最後の回は止めずに記録だけ
        jl_ng, jl_notes = JLINT.problems_and_notes(lint_view(run["args"], slides_of(result.data)),
                                                   enforce=round_no < rounds, only=changed)
        ids += jl_ng
        if ids:
            ctx.call(store.set_script_check, candidate["id"], 2, ids)
            previous, problems = answer, ids
            continue
        try:
            if template is not None:
                template.resolve(candidate, result.data)   # 新しく使う素材だけをコピーし、新しい準備の世代に控える
            checked = check_candidate(ctx, engine, run, result.data, template=template)
        except PreparationStopped as exc:
            return _template_stop(ctx, template, run_id, exc)
        ctx.call(store.set_script_check, candidate["id"], -1 if checked.rc is None else checked.rc,
                 checked.problems + checked.warnings + jl_notes)
        if checked.stopped:
            return fail("サーバを止めたので、台本を確かめるのを中断しました。もう一度頼んでください。", "stopped")
        if checked.rc == 0:
            extra = result.ng_warnings(checked.warnings) if result.ng_warnings and round_no < rounds else []
            if extra:
                previous, problems = answer, extra
                continue
            if template is not None:
                template.finish(candidate)
            result.candidate = candidate
            return result
        if checked.rc == 2 and LOOK.theme_refused(checked.problems):
            return fail(LOOK.BRAND_COLOR_MESSAGE, "brand", checked.rc)
        if checked.rc == 2:
            previous = answer
            problems = _engine_problems(checked.problems or ["NG: 検査で止まった（理由の行が取れなかった）"], result.data)
            continue
        if checked.rc == RC_DEPENDENCY:
            return fail(dependency_message(engine), "dependency", checked.rc)
        return fail("台本を確かめられませんでした（エンジンの検査が動きませんでした）。" + DOCTOR_HINT, "check", checked.rc)
    return fail(f"直した台本を {rounds} 回確かめましたが、エンジンの決まりに合いませんでした。指示を短くするか、もう一度頼んでください。",
                "script")


def _revise(ctx: Context, job: dict[str, Any], run: dict[str, Any], req: dict[str, Any], data: dict[str, Any],
            template: TemplateRun | None = None):
    deck_id = run["deck_id"]
    engine = engine_for(run["mode"])
    args = _edit_args(run["args"], req)
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
    # 取り込んだページ（PPTX から）への「挿絵」は、元のページに挿絵を重ねる（docs/adr/0013-overlay-illustrations.md）
    page_types = {str(s.get("id")): s.get("type") for s in slides_of(data)}
    if keys and all(page_types.get(k) == IMPORTED_TYPE for k in keys):
        # 取り込んだページ: 文字（0014）・挿絵（0013）・画像と図解（0015）。どれも 1 ページずつ
        if len(keys) != 1:
            raise EditRefused("取り込んだページを直すのは、1 ページずつです。")
        key, kind, option = keys[0], req.get("kind"), (req.get("options") or [None])[0]
        if kind == "illustration":
            return _overlay(ctx, job, run, req, data, key, positions[key])
        if kind == "diagram" and option == "add":
            return _overlay(ctx, job, run, req, data, key, positions[key], what="diagram")
        if kind == "image" and option == "add":
            return _overlay_image(ctx, job, run, req, data, key, positions[key])
        if kind == "image" and option == "replace":
            return _replace_image(ctx, job, run, req, data, key, positions[key])
        if kind in ("image", "diagram") and option == "main":
            return _rebuild_imported(ctx, job, run, req, data, key, positions[key], what=kind)
        return _imported_text(ctx, job, run, req, data, key, positions[key])
    rels, paths = _attachments(ctx, deck_id, req.get("attachments") or [])
    # 指示に書かれたページの本文の画像（頼まれたときだけ。台本の path に書ける。docs/adr/0016）
    page_pics = _page_pictures(ctx, deck_id, engine, req)
    pictures = _pictures(ctx, deck_id, engine.mode, keys) if not one_page(args) else {}
    by_key = {str(s.get("id")): s for s in slides_of(data)}
    targets = [{"id": k, "position": positions[k], "title": headline_of(by_key[k]), "picture": pictures.get(k)}
               for k in keys]
    instruction = req.get("text") or ""
    remake = req.get("edit_mode") == "remake"
    # 「何を直しますか？」で選んだ方針（docs/adr/0008-edit-kinds.md）。選ばなければ空
    direction = edit_kinds.direction(engine.mode, req.get("kind"), req.get("options") or [], scope=req.get("scope") or "page")

    def build(previous, problems):
        return build_revision_prompt(engine, args, script=data, targets=targets, instruction=instruction,
                                     attachments=rels, skills_dir=ctx.tools.skills_dir, scope=req.get("scope") or "pages",
                                     direction=direction, pictures=page_pics, previous=previous, problems=problems,
                                     template=template.writer_sections() if template is not None else None)

    def parse_and_apply(answer):
        patch = parse_revision(answer, script=data, targets=keys, pictures=pictures,
                               attachments=rels + [p["rel"] for p in page_pics], mode=engine.mode,
                               refs=template.ref_ctx if template is not None else None)
        if patch.problems:
            return None, patch.problems
        image_keys = {pp.key for pp in patch.image_changes}

        def rebase(other: dict[str, Any]) -> dict[str, Any]:
            # 絵だけ直すページも新しい姿になるので、前の版の印を外す（中身を直したページは apply_revision が外す）
            return without_pins(apply_revision(other, patch) if patch.script_changes else other, image_keys)

        new = rebase(data)
        plan = RenderPlan(deck=bool(patch.script_changes) or not patch.image_changes)
        for pp in patch.image_changes:
            plan.revise.append({"key": pp.key, "slot": (pp.image or {}).get("slot"),
                                "note": (pp.image or {}).get("note") or instruction or "。".join(direction)
                                or "添えた画像に合わせて直す",
                                "remake": remake, "attach": paths})
        if one_page(args) and plan.revise:
            plan.revise = [{**plan.revise[0], "key": "infographic"}]
        page_local = req.get("scope") in ("page", "pages") and not one_page(args)
        return EditResult(data=new, plan=plan, summary=patch.summary, suggestions=patch.suggestions,
                          routes={k: p.route for k, p in patch.pages.items()},
                          rebase=rebase if page_local else None), []

    ctx.call(ctx.store.set_job_note, job["id"], {"keys": keys})
    return _writer_loop(ctx, job, run, req, data, build, parse_and_apply, template)


def _imported_text(ctx: Context, job: dict[str, Any], run: dict[str, Any], req: dict[str, Any], data: dict[str, Any],
                   key: str, position: int):
    """取り込んだページの文字を直す。台本係は番号ごとの直した文字だけを返し、ページの HTML のその文字だけを差し替える
    （位置・色・書式・アニメーションは変えない）。"""
    page = next(s for s in slides_of(data) if str(s.get("id")) == key)
    segments = importing.text_segments(page.get("html") or "")
    by_n = {s["n"]: s for s in segments}
    instruction = req.get("text") or ""
    direction = edit_kinds.direction("presentation", "text", req.get("options") or []) if req.get("kind") == "text" else []
    target = {"id": key, "position": position, "title": headline_of(page)}

    def build(previous, problems):
        return build_imported_text_prompt(page=target, segments=segments, instruction=instruction, direction=direction,
                                          previous=previous, problems=problems)

    def apply(other: dict[str, Any], changes: dict[int, str]) -> dict[str, Any]:
        out = deep(other)
        for s in slides_of(out):
            if str(s.get("id")) == key:
                old_title = (s.get("title") or "").strip()
                s["html"] = importing.replace_texts(s.get("html") or "", changes)
                for n, text in changes.items():   # 一覧に出す見出し（取り込みの title）も、同じ文字なら直す
                    if old_title and by_n[n]["text"] == old_title:
                        s["title"] = text
        return without_pins(out, {key})

    def parse_and_apply(answer):
        try:
            got = answer if isinstance(answer, dict) else extract_json(str(answer))
        except WriterFailed:
            return None, ["NG: 返したものが JSON のオブジェクト 1 つではなかった。決まった形の JSON オブジェクトだけを返す"]
        raw = got.get("changes")
        if not isinstance(raw, list):
            return None, ["NG: changes を [{\"n\": 番号, \"text\": \"直した文字\"}] の配列で書く"]
        changes: dict[int, str] = {}
        problems = []
        for c in raw:
            n, text = (c.get("n"), c.get("text")) if isinstance(c, dict) else (None, None)
            if not isinstance(n, int) or n not in by_n:
                problems.append(f"NG: 番号 {n!r} は無い（1〜{len(segments)}）")
            elif not isinstance(text, str) or not text.strip():
                problems.append(f"NG: {n} の文字を空にしない（消すことはできない）")
            elif n in changes:
                problems.append(f"NG: {n} を 2 回書かない")
            else:
                old = by_n[n]["text"]
                if len(text.strip()) > max(2 * len(old), len(old) + 20):
                    problems.append(f"NG: {n} の文字が元（{len(old)} 字）より大きく増えた（{len(text.strip())} 字）。"
                                    "箱からはみ出すので短くする")
                changes[n] = text.strip()
        if problems:
            return None, problems
        summary = str(got.get("summary") or "")[:200]
        if not changes:
            raise EditRefused(f"{position}ページの文字は直しませんでした" + (f"（{summary}）" if summary else "") + "。")
        def overflow(warnings: list[str]) -> list[str]:
            # 直した文字が箱からはみ出したら短くさせる（取り込んだページのはみ出しは、字体の違いでもともと出ることが
            # あるので警告止まり。直した文字のものだけを拾う）
            heads = [t[:12] for t in changes.values()]
            return [f"NG: {w.split(':', 1)[1].strip()}" for w in warnings
                    if "[text-overflow]" in w and any(h and h in w for h in heads)]

        return EditResult(data=apply(data, changes), plan=RenderPlan(), summary=summary or "文字を直した",
                          routes={key: "script"}, rebase=lambda other: apply(other, changes), ng_warnings=overflow), []

    ctx.call(ctx.store.set_job_note, job["id"], {"keys": [key]})
    return _writer_loop(ctx, job, run, req, data, build, parse_and_apply)


OVERLAY_WORDS = {"illustration": "挿絵", "diagram": "図解", "image": "画像"}
GENERATE_TIMEOUT_SECONDS = 900


def _add_overlay(other: dict[str, Any], key: str, item: dict[str, Any]) -> dict[str, Any]:
    out = deep(other)
    for s in slides_of(out):
        if str(s.get("id")) == key:
            s[ILL.OVERLAY_KEY] = [*(s.get(ILL.OVERLAY_KEY) or []), item]
    return without_pins(out, {key})


def _answer(answer: Any) -> dict[str, Any] | None:
    try:
        return answer if isinstance(answer, dict) else extract_json(str(answer))   # ask は JSON を読んで返す
    except WriterFailed:
        return None


JSON_NG = "NG: 返したものが JSON のオブジェクト 1 つではなかった。決まった形の JSON オブジェクトだけを返す"


def _page_boxes(page: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(台本係に渡す箱, 重ねてはいけない箱)。箱は元のページの HTML から拾ったものに、前に重ねた挿絵・図解・画像を足す
    （重ねたものはページの `illustrations` にあり、HTML にはまだ無い）。重ねてはいけないのは、文字の箱と重ねたもの。"""
    boxes = ILL.page_boxes(page.get("html") or "")
    laid = [{"x": int(it.get("x") or 0), "y": int(it.get("y") or 0), "w": int(it.get("w") or 0), "h": int(it.get("h") or 0),
             "kind": "重ねた絵", "text": ""} for it in page.get(ILL.OVERLAY_KEY) or [] if isinstance(it, dict)]
    return boxes + laid, [b for b in boxes if b["kind"] == "文字"] + laid


def _overlay_box(answer: Any, texts: list[dict[str, Any]], position: int, what: str
                 ) -> tuple[dict[str, Any], dict[str, int], list[str]]:
    """重ねるものの箱を確かめる。(答え, 箱, NG の行)。置けないという答えは断る（理由を出す）。"""
    word = OVERLAY_WORDS[what]
    got = _answer(answer)
    if got is None:
        return {}, {}, [JSON_NG]
    if isinstance(got.get("skip"), str) and got["skip"].strip():
        raise EditRefused(f"{position}ページに{word}を置ける場所がありませんでした（{got['skip'].strip()[:200]}）。"
                          "補足に置き場所を書いて、もう一度頼んでください。")
    try:
        box = {k: float(got[k]) for k in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError):
        return got, {}, ["NG: x・y・w・h を数で書く"]
    problems = []
    if box["w"] < OVERLAY_MIN_SIDE or box["h"] < OVERLAY_MIN_SIDE:
        problems.append(f"NG: {word}の幅・高さは {OVERLAY_MIN_SIDE} 以上")
    if box["x"] < 0 or box["y"] < 0 or box["x"] + box["w"] > ILL.PAGE_W + 1 or box["y"] + box["h"] > ILL.PAGE_H + 1:
        problems.append(f"NG: {word}の箱がページ（{ILL.PAGE_W}×{ILL.PAGE_H}）からはみ出している")
    area = max(1.0, box["w"] * box["h"])
    for b in texts:
        if _overlap(box, b) / area > OVERLAY_TEXT_OVERLAP:
            what_box = f"文字の箱（x={b['x']} y={b['y']} 幅={b['w']} 高さ={b['h']}「{b['text'][:20]}」）" if b["kind"] == "文字" \
                else f"前に重ねた絵（x={b['x']} y={b['y']} 幅={b['w']} 高さ={b['h']}）"
            problems.append(f"NG: {word}が{what_box}に重なりすぎ。空いている場所へ動かすか小さくする")
            break
    return got, {k: round(v) for k, v in box.items()}, problems


def _picture_source(ctx: Context, run: dict[str, Any], req: dict[str, Any]) -> tuple[str | None, Path | None, bool]:
    """画像の出どころ: (渡した画像の台本からの相対パス, その絶対パス, 生成するか)。渡していなければ生成（Codex が要る）。"""
    rels, paths = _attachments(ctx, run["deck_id"], req.get("attachments") or [])
    if rels:
        return rels[0], paths[0], False
    if not req.get("can_generate"):
        raise EditRefused("この機械では画像を生成できません。「＋」から画像を渡して、もう一度頼んでください。")
    return None, None, True


def _generate_picture(ctx: Context, run: dict[str, Any], prompt: str, *, portrait: bool) -> str:
    """Codex で画像を 1 枚描き、台本のフォルダの `overlays/` に置く（エンジンの fill に、画像の枠 1 つだけの仮の台本を
    渡す）。戻り値は台本からの相対パス。描けなければ断る（理由を出す）。"""
    deck_id = run["deck_id"]
    folder = ctx.root.scripts_dir(deck_id) / "overlays"
    work = folder / f"gen-{run['id']}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        page: dict[str, Any] = {"id": "g", "type": "photo", "headline": "生成", "image": {"prompt": prompt}}
        if portrait:
            page.update(layout="right", split="1/2")
        script = work / "script.json"
        script.write_text(json.dumps({"title": "生成", "slides": [page]}, ensure_ascii=False), encoding="utf-8")
        entry = engine_for("presentation").entry_path(ctx.tools.skills_dir)
        extra: list[str] = []
        theme = (run["args"].get("look") or {}).get("theme") if isinstance(run["args"].get("look"), dict) else None
        if theme:
            extra += ["--theme", str(theme)]
        if run["args"].get("image_look"):
            extra += ["--image-look", str(run["args"]["image_look"])]
        env = child_env(home=ctx.root.root, extra=ctx.tools.extra_env)
        r = run_process([ctx.tools.python, str(entry), "fill", "--script", str(script), *extra], cwd=work, env=env,
                        timeout=GENERATE_TIMEOUT_SECONDS, stop=ctx.stop)
        if r.rc == RC_DEPENDENCY:
            raise EditRefused("画像を生成する Codex にログインできていません（または見つかりません）。"
                              "`codex login` をするか、「＋」から画像を渡してください。", "dependency")
        slots_json = work / "slots.json"
        r2 = run_process([ctx.tools.python, str(entry), "images", "--script", str(script), *extra, "--json", str(slots_json)],
                         cwd=work, env=env, timeout=120, stop=ctx.stop)
        slots = json.loads(slots_json.read_text(encoding="utf-8")) if r.ok and r2.ok and slots_json.is_file() else []
        src = Path(slots[0].get("gen_path") or slots[0].get("path") or "") if slots else None
        if src is None or not src.is_file():
            raise EditRefused("画像を生成できませんでした。もう一度頼むか、「＋」から画像を渡してください。")
        name = f"gen-{hashlib.sha256(src.read_bytes()).hexdigest()[:16]}{src.suffix.lower() or '.png'}"
        shutil.copyfile(src, folder / name)
        return f"overlays/{name}"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _aspect(path: Path | None) -> float | None:
    if path is None:
        return None
    try:
        from PIL import Image

        with Image.open(path) as im:
            return im.width / max(1, im.height)
    except OSError:
        return None


def _overlay_image(ctx: Context, job: dict[str, Any], run: dict[str, Any], req: dict[str, Any], data: dict[str, Any],
                   key: str, position: int):
    """取り込んだページの空いた所に、渡した画像か生成した画像を重ねる（docs/adr/0015）。"""
    page = next(s for s in slides_of(data) if str(s.get("id")) == key)
    boxes, texts = _page_boxes(page)
    rel, path, generate = _picture_source(ctx, run, req)
    aspect = _aspect(path)
    target = {"id": key, "position": position, "title": headline_of(page)}

    def build(previous, problems):
        return build_overlay_prompt(run["args"], page=target, boxes=boxes, instruction=req.get("text") or "", what="image",
                                    aspect=aspect, generate=generate, skills_dir=ctx.tools.skills_dir,
                                    previous=previous, problems=problems)

    def parse_and_apply(answer):
        got, box, problems = _overlay_box(answer, texts, position, "image")
        if generate and not (isinstance(got.get("prompt"), str) and got["prompt"].strip()):
            problems.append("NG: prompt に何を描くかを書く")
        if problems:
            return None, problems
        src = rel or _generate_picture(ctx, run, got["prompt"].strip(), portrait=box["h"] > box["w"])
        item = {"image": {"path": src}, **box, "fit": "cover"}
        summary = str(got.get("summary") or "画像を重ねた")[:200]
        return EditResult(data=_add_overlay(data, key, item), plan=RenderPlan(), summary=summary, routes={key: "script"},
                          rebase=lambda other: _add_overlay(other, key, item)), []

    ctx.call(ctx.store.set_job_note, job["id"], {"keys": [key]})
    return _writer_loop(ctx, job, run, req, data, build, parse_and_apply)


def _replace_image(ctx: Context, job: dict[str, Any], run: dict[str, Any], req: dict[str, Any], data: dict[str, Any],
                   key: str, position: int):
    """取り込んだページの画像を、同じ枠のまま中身だけ替える（docs/adr/0015）。画像が 1 つで渡した画像があれば台本係は呼ばない。

    替えられるのは、元のページの画像と、前に重ねた挿絵・図解・画像（ページの `illustrations`）のうち見えているもの。
    重ねたものは位置と大きさをそのままに、中身だけを替える。その下に大半が隠れていた重ねたものは取り除く（新しい画像の
    周りからはみ出して見えるため）。"""
    page = next(s for s in slides_of(data) if str(s.get("id")) == key)
    images = _replaceable(page)
    if not images:
        raise EditRefused(f"{position}ページには差し替える画像がありません。「空いた所に足す」を選んでください。")
    rel, _, generate = _picture_source(ctx, run, req)
    target = {"id": key, "position": position, "title": headline_of(page)}

    def replaced(other: dict[str, Any], n: int, src: str) -> dict[str, Any]:
        out = deep(other)
        where, index = images[n - 1]["ref"]
        for s in slides_of(out):
            if str(s.get("id")) != key:
                continue
            if where == "html":
                s["html"] = importing.replace_image(s.get("html") or "", index, src)
            else:   # 重ねたもの: 位置と大きさはそのまま、中身だけ（写真は枠いっぱいに切り抜く）
                s[ILL.OVERLAY_KEY] = _replace_overlay(list(s.get(ILL.OVERLAY_KEY) or []), index, src)
        return without_pins(out, {key})

    if rel and len(images) == 1 and not (req.get("text") or "").strip():
        return EditResult(data=replaced(data, 1, rel), plan=RenderPlan(), summary="画像を差し替えた", routes={key: "script"},
                          rebase=lambda other: replaced(other, 1, rel))

    def build(previous, problems):
        return build_replace_prompt(page=target, images=images, instruction=req.get("text") or "", generate=generate,
                                    previous=previous, problems=problems)

    def parse_and_apply(answer):
        got = _answer(answer)
        if got is None:
            return None, [JSON_NG]
        n = got.get("n")
        if not isinstance(n, int) or not 1 <= n <= len(images):
            return None, [f"NG: n は 1〜{len(images)} の番号"]
        if generate and not (isinstance(got.get("prompt"), str) and got["prompt"].strip()):
            return None, ["NG: prompt に何を描くかを書く"]
        img = images[n - 1]
        src = rel or _generate_picture(ctx, run, got["prompt"].strip(), portrait=img["h"] > img["w"])
        summary = str(got.get("summary") or "画像を差し替えた")[:200]
        return EditResult(data=replaced(data, n, src), plan=RenderPlan(), summary=summary, routes={key: "script"},
                          rebase=lambda other: replaced(other, n, src)), []

    ctx.call(ctx.store.set_job_note, job["id"], {"keys": [key]})
    return _writer_loop(ctx, job, run, req, data, build, parse_and_apply)


def _replaceable(page: dict[str, Any]) -> list[dict[str, Any]]:
    """差し替えられる画像の並び（{n, kind, src, x, y, w, h, ref}）。元のページの画像のあとに、前に重ねたもの。
    あとから重ねたものの下に大半が隠れているものは除く（見えていないものを替えても、利用者には替わって見えない）。"""
    out = [{**img, "kind": "元の画像", "ref": ("html", img["n"])} for img in importing.page_images(page.get("html") or "")]
    laid = [(j, it) for j, it in enumerate(page.get(ILL.OVERLAY_KEY) or []) if isinstance(it, dict)]
    for j, it in laid:
        image = it.get("image") if isinstance(it.get("image"), dict) else {}
        kind = "重ねた画像" if image.get("path") else "重ねた挿絵・図"
        out.append({"kind": kind, "src": image.get("path") or "（SVG）", **_box_of(it), "ref": ("overlay", j)})
    out = [img for img in out if not _mostly_under(img, [_box_of(it) for j, it in laid
                                                         if img["ref"][0] == "html" or j > img["ref"][1]])]
    for n, img in enumerate(out, 1):
        img["n"] = n
    return out


def _replace_overlay(items: list[Any], index: int, src: str) -> list[Any]:
    """重ねたものの `index` 番目の中身を替え、その下に大半が隠れていた重ねたものを取り除く。"""
    items[index] = {**{k: v for k, v in items[index].items() if k not in ("image", "fit")}, "image": {"path": src}, "fit": "cover"}
    box = _box_of(items[index])
    return [it for j, it in enumerate(items) if j == index or not isinstance(it, dict) or not _mostly_under(_box_of(it), [box])]


def _box_of(item: dict[str, Any]) -> dict[str, int]:
    return {k: int(item.get(k) or 0) for k in ("x", "y", "w", "h")}


def _mostly_under(box: dict[str, Any], covers: list[dict[str, Any]]) -> bool:
    """箱の半分より多くが、どれか 1 つの箱の下に隠れているか。"""
    area = max(1, box["w"] * box["h"])
    return any(_overlap(box, c) / area > HIDDEN_UNDER for c in covers)


def _rebuild_imported(ctx: Context, job: dict[str, Any], run: dict[str, Any], req: dict[str, Any], data: dict[str, Any],
                      key: str, position: int, *, what: str):
    """取り込んだページを、画像か図解を主役にした型のページに組み直す（docs/adr/0015。見た目は型のものに変わる）。"""
    engine = engine_for(run["mode"])
    page = next(s for s in slides_of(data) if str(s.get("id")) == key)
    texts = [t["text"] for t in importing.text_segments(page.get("html") or "") if not t["bullet"]]
    picture, generate = None, False
    if what == "image":
        rels, _ = _attachments(ctx, run["deck_id"], req.get("attachments") or [])
        picture = rels[0] if rels else None
        generate = not picture and bool(req.get("can_generate"))
    args = _edit_args(run["args"], req)
    target = {"id": key, "position": position, "title": headline_of(page)}

    def build(previous, problems):
        return build_rebuild_prompt(engine, args, page=target, texts=texts, what=what, picture=picture, generate=generate,
                                    instruction=req.get("text") or "", skills_dir=ctx.tools.skills_dir,
                                    previous=previous, problems=problems)

    def swap(other: dict[str, Any], new_page: dict[str, Any]) -> dict[str, Any]:
        out = deep(other)
        out["slides"] = [deep(new_page) if str(s.get("id")) == key else s for s in slides_of(out)]
        return without_pins(out, {key})

    def parse_and_apply(answer):
        got = _answer(answer)
        if got is None:
            return None, [JSON_NG]
        new_page = got.get("page")
        if not isinstance(new_page, dict) or not isinstance(new_page.get("type"), str):
            return None, ["NG: page に型のページ 1 枚（type を持つ JSON オブジェクト）を書く"]
        if new_page.get("type") == IMPORTED_TYPE:
            return None, ["NG: type に html は使わない（型の一覧の型にする）"]
        new_page = {**{k: v for k, v in new_page.items() if not str(k).startswith("_")}, "id": key}
        summary = str(got.get("summary") or "型のページに組み直した")[:200]
        return EditResult(data=swap(data, new_page), plan=RenderPlan(), summary=summary, routes={key: "script"},
                          rebase=lambda other: swap(other, new_page)), []

    ctx.call(ctx.store.set_job_note, job["id"], {"keys": [key]})
    return _writer_loop(ctx, job, run, req, data, build, parse_and_apply)


OVERLAY_MIN_SIDE = 160
OVERLAY_TEXT_OVERLAP = 0.1   # 文字の箱との重なりは、挿絵の面積の 1 割まで
HIDDEN_UNDER = 0.5   # 差し替え: 半分より多くが上の絵の下にあるものは「隠れている」


def _overlap(a: dict[str, float], b: dict[str, Any]) -> float:
    w = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    h = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    return max(0.0, w) * max(0.0, h)


def _overlay(ctx: Context, job: dict[str, Any], run: dict[str, Any], req: dict[str, Any], data: dict[str, Any],
             key: str, position: int, *, what: str = "illustration"):
    """取り込んだページに挿絵（か図解）を 1 つ重ねる。台本係が空いている場所・大きさ・SVG を決め、ページの
    `illustrations` に足す（描く前にサーバが PNG にしてページの HTML に重ねる。元の文字・図形・画像は変えない）。"""
    page = next(s for s in slides_of(data) if str(s.get("id")) == key)
    boxes, texts = _page_boxes(page)
    instruction = req.get("text") or ""
    target = {"id": key, "position": position, "title": headline_of(page)}

    def build(previous, problems):
        return build_overlay_prompt(run["args"], page=target, boxes=boxes, instruction=instruction, what=what,
                                    skills_dir=ctx.tools.skills_dir, previous=previous, problems=problems)

    def parse_and_apply(answer):
        got, box, problems = _overlay_box(answer, texts, position, what)
        if problems:
            return None, problems
        svg = got.get("svg")
        if not isinstance(svg, str) or "<svg" not in svg:
            return None, ["NG: svg に SVG（<svg viewBox=…>…</svg>）を書く"]
        item = {"image": {"svg": svg}, **box}
        summary = str(got.get("summary") or f"{OVERLAY_WORDS[what]}を重ねた")[:200]
        return EditResult(data=_add_overlay(data, key, item), plan=RenderPlan(), summary=summary, routes={key: "script"},
                          rebase=lambda other: _add_overlay(other, key, item)), []

    ctx.call(ctx.store.set_job_note, job["id"], {"keys": [key]})
    return _writer_loop(ctx, job, run, req, data, build, parse_and_apply)


def _insert(ctx: Context, job: dict[str, Any], run: dict[str, Any], req: dict[str, Any], data: dict[str, Any],
            template: TemplateRun | None = None):
    deck_id = run["deck_id"]
    engine = engine_for(run["mode"])
    args = _edit_args(run["args"], req)
    anchor = None
    after = None
    if req.get("after"):
        anchor = _key(ctx, deck_id, req["after"], data, what="足す場所のページ")
        page = next(s for s in slides_of(data) if s.get("id") == anchor)
        after = {"id": anchor, "position": _positions(data)[anchor], "title": headline_of(page)}
    rels, _ = _attachments(ctx, deck_id, req.get("attachments") or [])
    page_pics = _page_pictures(ctx, deck_id, engine, req)
    instruction = req.get("text") or ""

    def build(previous, problems):
        return build_insert_prompt(engine, args, script=data, after=after, instruction=instruction, attachments=rels,
                                   pictures=page_pics,
                                   skills_dir=ctx.tools.skills_dir, previous=previous, problems=problems,
                                   template=template.writer_sections() if template is not None else None)

    def parse_and_apply(answer):
        pages, summary, problems = parse_insert(answer, script=data, attachments=rels + [p["rel"] for p in page_pics],
                                                mode=engine.mode, refs=template.ref_ctx if template is not None else None)
        if problems:
            return None, problems
        return EditResult(data=insert_after(data, anchor, pages), plan=RenderPlan(), summary=summary,
                          routes={str(p.get("id")): "insert" for p in pages}), []

    return _writer_loop(ctx, job, run, req, data, build, parse_and_apply, template)


# --- 台本係を呼ばない直し --------------------------------------------------------------------------------------

def _duplicate(ctx: Context, deck_id: str, req: dict[str, Any], data: dict[str, Any], mode: str) -> EditResult:
    key = _key(ctx, deck_id, req.get("page"), data)
    slides = list(data.get("slides") or [])
    at = next(i for i, s in enumerate(slides) if isinstance(s, dict) and s.get("id") == key)
    copy = deep(slides[at])
    copy.pop(HIDDEN_KEY, None)
    copy.pop(PIN_KEY, None)   # 写しは新しいページ（指している回にはこのページが無い）
    scene_ids = {str(s.get("id")) for s in slides_of(data)}
    if mode == "motion":
        # 場面の id・中の要素の id・stage の要素の id は、まとめて 1 つの使用済みの集合（DOM の id は台本全体で重ねられない）。
        # 場面の採番も要素の採番も、この集合を避ける。場面の中の要素の id・それを引く CSS・script・hook も付け替える
        used = scene_ids | motion.element_ids(data.get("stage"), *(s.get("html") for s in slides_of(data)))
        new_id = fresh_id(key, used)
        copy = motion.duplicate_scene(copy, new_id, used)
    else:
        copy["id"] = fresh_id(key, scene_ids)
        new_id = copy["id"]
    plan = RenderPlan()
    if mode == "pitch":
        plan.aliases[copy["id"]] = key   # 同じ中身なので同じ鍵。区画の絵を写せば描き直さない
    return EditResult(data={**data, "slides": slides[:at + 1] + [copy] + slides[at + 1:]}, plan=plan,
                      summary="ページを複製しました。", routes={copy["id"]: "duplicate"})


def _visibility(ctx: Context, deck_id: str, req: dict[str, Any], data: dict[str, Any]) -> EditResult:
    key = _key(ctx, deck_id, req.get("page"), data)
    hidden = bool(req.get("hidden"))
    new = with_hidden(data, key, hidden)
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
    return EditResult(data=with_order(data, wanted), plan=RenderPlan(), summary="並べ替えました。")


def _restore(ctx: Context, deck_id: str, req: dict[str, Any], data: dict[str, Any], mode: str,
             args: dict[str, Any]) -> EditResult:
    """版の切り替え（直しの仕事が待っていたとき。普段は画面の口がその場で当てる）。中身を戻して版の印を書くだけで、
    絵と書き出しの部品は描くとき（`render_and_import`）に、印の指す回から持ってくる。"""
    if one_page(args):
        raise EditRefused("台本の全ページを 1 枚に描くスタイル（infographic）では、ページの版から戻せません。")
    key = _key(ctx, deck_id, req.get("page"), data)
    try:
        old_page = ctx.call(pin_source, ctx.store, deck_id, req.get("run"), key)
    except PinRefused as exc:
        raise EditRefused(exc.message) from None
    return EditResult(data=with_restored(data, key, old_page, str(req.get("run"))), plan=RenderPlan(),
                      summary="前の版に戻しました。", routes={key: "restore"})
