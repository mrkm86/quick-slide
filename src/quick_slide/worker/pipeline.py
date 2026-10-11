"""仕事 1 つ（原稿から 1 冊）: 確かめる → 台本係が書く → エンジンの検査 → （NG なら直させる。上限あり）→
描き係が描く → 控える → 取り込む。描く・控える・取り込むところ（`render_and_import`）は直す仕事（`edit.py`）も使う。

台帳（store）に触るときは必ずサーバの錠（`store_lock`）を取る。錠を持ったまま子プロセスを待たない
（画面の読み取りを止めないため）。各段の状態は `runs` に書き、画面はそれを `planning.message` として読む。

**エンジンはデッキの作業フォルダ（`work/`）に描き、終わった回を `runs/<回>/` へ控える**（P3。理由は
`render/workspace.py`）。台本はいまの台本から非表示のページを抜いて `scripts/engine.json` に書いて渡す。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..childenv import child_env
from ..engines import RC_DEPENDENCY, Engine, engine_for, engine_marker
from ..paths import DataRoot
from ..render import look as LOOK
from ..render import workspace as W
from ..render.runner import (
    DEPENDENCY_MESSAGE,
    DOCTOR_HINT,
    RenderOutcome,
    check_prerequisites,
    check_script,
    render_deck,
)
from ..scriptdoc import (
    engine_view,
    one_page,
    outline_headings,
    output_pages,
    page_id_problems,
    screen_pages,
)
from ..settings import Settings
from ..writer import jlint as JLINT
from ..writer.backends import WriterFailed, ask, choose_backend
from ..writer.prompt import ContractMissing, build_prompt
from .tools import Tools

log = logging.getLogger("quick_slide.worker")


class Abandoned(BaseException):
    """「今すぐ再起動」で仕事を手放した。台帳に何も書かずに抜ける（次のサーバの起動時の掃除が、描く段は続きから・
    書く段は理由つきで失敗にする）。`except Exception` に呑まれないよう BaseException にする。"""


@dataclass
class Context:
    store: Any
    lock: Any
    root: DataRoot
    settings: Settings
    tools: Tools
    stop: threading.Event
    abandon: threading.Event | None = None

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        with self.lock:
            if self.abandon is not None and self.abandon.is_set():
                raise Abandoned()
            return fn(*args, **kwargs)


@dataclass
class RenderPlan:
    """1 回の描き方。`deck` を流すか・どのページの絵を直すか（revise）・区画に置く絵（版から戻す・複製）。"""
    deck: bool = True
    revise: list[dict[str, Any]] = field(default_factory=list)   # {"key", "slot", "note", "remake", "attach": [Path]}
    seeds: dict[str, tuple[Path, Path | None]] = field(default_factory=dict)   # ピッチ: ページの id → 戻す絵
    aliases: dict[str, str] = field(default_factory=dict)                       # ピッチ: 複製の id → 元の id
    restore_presentation: list[tuple[Path, str]] = field(default_factory=list)  # プレゼン: (控えの回, ページの id)

    def record(self) -> dict[str, Any]:
        return {"deck": self.deck, "revise": [{k: (str(v) if isinstance(v, Path) else v) for k, v in r.items()
                                               if k != "attach"} | {"attach": len(r.get("attach") or [])}
                                              for r in self.revise],
                "seeds": sorted(self.seeds), "aliases": dict(self.aliases),
                "restore_presentation": [k for _, k in self.restore_presentation]}


def settle_look(ctx: Context, run: dict[str, Any], *, previous_style: str | None) -> None:
    """この回に使う設定（ブランド・スタイル・書式）を決めて、回の引数（`runs.args`）と記録に書く（P4）。

    仕事を始めるときに 1 回だけ。止まった回を続けるとき（resume）は呼ばない（控えた引数のまま描く）。
    `run["args"]` もその場で書き換える（このあと台本係・エンジンの引数がこれを読む）。
    """
    def work() -> dict[str, Any]:
        update, record = LOOK.resolve(ctx.store, ctx.root, run, previous_style=previous_style)
        ctx.store.update_run_args(run["id"], update)
        ctx.store.set_run_record(run["id"], {"look": record})
        return update

    update = ctx.call(work)
    run["args"] = {**run["args"], **update}


def engine_script_view(data: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """エンジンに渡す台本: 非表示のページを抜き、ブランドのフッタ・ページ番号を一番外に入れる（台本の版は変えない）。"""
    return LOOK.apply_format(engine_view(data), args.get("look"))


def _writer_progress(round_no: int, rounds: int, backend: str, phase: str) -> dict[str, Any]:
    return {"phase": phase, "round": round_no, "max_rounds": rounds, "writer": backend}


def make(ctx: Context, job: dict[str, Any]) -> str:
    """仕事を最後まで流す。戻り値は描いた回の終わりの状態（done / failed）。失敗も台帳に書いてから返る。

    段（台帳に書いてから始める）: 確かめる（ピッチは Codex のログイン）→ 書く ⇄ 検査 → 描く → 取り込む → 終わり。
    `job.resume_from` が render なら描くところから、import なら取り込むところから続ける（サーバが途中で止まったとき）。
    """
    store = ctx.store
    run = ctx.call(store.run, job["run_id"])
    run_id = run["id"]
    engine = engine_for(run["mode"])
    run_dir = ctx.root.run_dir(run["deck_id"], run_id)
    resume = job.get("resume_from")

    def fail(message: str, kind: str, rc: int | None = None) -> str:
        ctx.call(store.fail_run, run_id, message=message, failure_kind=kind, rc=rc)
        return "failed"

    if resume == "import":
        return import_recorded(ctx, run)
    if resume is None:
        settle_look(ctx, run, previous_style=None)
        # 描く前の確かめ（仕事ごとに 1 回。ピッチは Codex のログイン。書く前に見るので、描けないのに台本係の枠を使わない）
        pre = check_prerequisites(engine, tools=ctx.tools, home=ctx.root.root, cwd=run_dir, stop=ctx.stop)
        ctx.call(store.set_run_record, run_id, {"prerequisites": {"checked": pre.rc is not None, "rc": pre.rc}})
        if not pre.ok:
            return fail(pre.message or "", pre.failure_kind or "engine", pre.rc)
        accepted = write_script(ctx, job, run)
        if isinstance(accepted, str):
            return accepted
        script, data = accepted
        ctx.call(store.accept_script, script["id"], screen_pages(run["args"], data))
        ctx.call(store.set_run_record, run_id, {"script": {"id": script["id"], "version": script["version"],
                                                           "sha256": script["sha256"]}})
        return render_and_import(ctx, run, script, data, RenderPlan())
    # resume == "render": 台本は検査済み。描くところから（エンジンは描き終えた絵をキャッシュから使い回す）
    script = ctx.call(store.script, run["script_id"])
    log.info("run %s: 途中から続ける（%s）", run_id, resume)
    return render_and_import(ctx, run, script, ctx.call(store.load_script, script), RenderPlan(), start=False)


def import_recorded(ctx: Context, run: dict[str, Any]) -> str:
    """エンジンが終わったあとで止まった回を、控えた終わり方から取り込む。"""
    rec = ctx.call(ctx.store.engine_result, run["id"])
    if rec is None:
        ctx.call(ctx.store.fail_run, run["id"], message="取り込む結果の控えがありません。もう一度頼んでください。",
                 failure_kind="internal")
        return "failed"
    outcome = RenderOutcome.from_record(rec)
    ctx.call(ctx.store.finish_run, run["id"], state=outcome.state, rc=outcome.rc, message=outcome.message,
             failure_kind=outcome.failure_kind, pages=outcome.pages)
    return outcome.state


def known_pitch_area(ctx: Context, deck_id: str, work: Path) -> Path | None:
    """作業フォルダの区画（image-slide）。記録から読み、まだ無ければ前に知った区画と同じ根っこに求める。"""
    layout = W.read_layout(work)
    if isinstance(layout.get("cache"), str):
        return Path(layout["cache"])
    cache = W.engine_record("pitch", work).get("cache")
    if isinstance(cache, str):
        return Path(cache)
    for r in ctx.call(ctx.store.runs_for_deck, deck_id):
        cache = W.engine_record("pitch", ctx.root.run_dir(deck_id, r["id"])).get("cache")
        if isinstance(cache, str):
            return W.pitch_area_for(work, cache)
    return None


def _pitch_fallback(ctx: Context, deck_id: str) -> Callable[[str], tuple[Path, Path | None] | None]:
    """そのページの一番新しい控えの絵（隠していたページを戻したとき・P2 の回から引き継ぐとき）。"""
    rows = ctx.call(ctx.store.page_history, deck_id)
    keys = {}
    for r in rows:
        key = ctx.call(ctx.store.page_key_of, r["page_id"]) if r["page_id"] else None
        if key:
            keys.setdefault(key, []).append((ctx.root.run_dir(deck_id, r["run_id"]), r["page_no"]))

    def find(key: str) -> tuple[Path, Path | None] | None:
        for run_dir, no in keys.get(key, []):
            got = W.pitch_picture(run_dir, key, legacy_no=no)
            if got is not None:
                return got
        return None

    return find


def render_and_import(ctx: Context, run: dict[str, Any], script: dict[str, Any], data: dict[str, Any],
                      plan: RenderPlan, *, start: bool = True, message: str | None = None) -> str:
    """いまの台本（`data`）をエンジンで描き（deck と revise）、描いた回のフォルダへ控え、ページの絵を取り込む。"""
    store = ctx.store
    deck_id, run_id = run["deck_id"], run["id"]
    engine = engine_for(run["mode"])
    args = run["args"]
    home = ctx.root.root
    work = ctx.root.work_dir(deck_id)
    run_dir = ctx.root.run_dir(deck_id, run_id)
    work.mkdir(parents=True, exist_ok=True)
    pages = output_pages(args, data)
    if start:
        ctx.call(store.start_rendering, run_id, script["id"], pages,
                 {"phase": "rendering", "step": "starting", "done": 0, "total": len(pages)})
    else:
        ctx.call(store.set_run_state, run_id, "rendering",
                 progress={"phase": "rendering", "step": "starting", "done": 0, "total": len(pages)})
    script_path = W.write_engine_script(ctx.root.engine_script_path(deck_id), engine_script_view(data, args))

    placed: dict[str, str] = {}
    if engine.mode == "pitch" and not one_page(args):
        placed = W.arrange_pitch_cache(work, [p["key"] for p in pages], area=known_pitch_area(ctx, deck_id, work),
                                       seeds=plan.seeds, aliases=plan.aliases, fallback=_pitch_fallback(ctx, deck_id))
    restored = 0
    for old_dir, key in plan.restore_presentation:
        restored += W.restore_presentation_pictures(old_dir, key)

    position = {p["key"]: p["no"] for p in pages}
    commands: list[tuple[str, list[str]]] = []
    if plan.deck or not plan.revise:
        commands.append(("engine", engine.deck_command(ctx.tools.python, ctx.tools.skills_dir, script_path, work, args)))
    for i, r in enumerate(plan.revise, 1):
        if r["key"] not in position:
            ctx.call(store.fail_run, run_id, message="直すページが描くページの中にありません（非表示にしたページは直せません）。",
                     failure_kind="edit")
            return "failed"
        commands.append((f"revise-{i}", engine.revise_command(
            ctx.tools.python, ctx.tools.skills_dir, script_path, work, args, page=position[r["key"]],
            note=r.get("note") or "", remake=bool(r.get("remake")), slot=r.get("slot"), attach=r.get("attach"))))
    ctx.call(store.set_run_record, run_id, {
        "engine": engine_marker(ctx.tools.skills_dir, engine),
        "argv": [c for _, c in commands], "work": str(work), "engine_script": str(script_path),
        "plan": plan.record(), "cache_placed": placed, "pictures_restored": restored,
    })

    def on_change(snapshot: dict[str, Any], marks: dict[int, str]) -> None:
        with ctx.lock:
            for no, state in marks.items():
                store.mark_run_page(run_id, no, state)
            store.set_run_state(run_id, "rendering", progress=snapshot)

    outcome: RenderOutcome | None = None
    for name, cmd in commands:
        W.clear_outputs(work)
        outcome = render_deck(engine, tools=ctx.tools, home=home, script=script_path, out=work, args=args,
                              page_count=len(pages), timeout=ctx.settings.worker.render_timeout_seconds,
                              stop=ctx.stop, on_change=on_change, command=cmd, log_dir=run_dir, log_name=name)
        if engine.mode == "pitch" and not one_page(args):
            cache = W.engine_record("pitch", work).get("cache")
            if isinstance(cache, str):
                W.write_layout(work, [p["key"] for p in pages], cache)
        elif engine.mode == "presentation":
            W.write_layout(work, [p["key"] for p in pages], W.engine_record("presentation", work).get("cache"))
        if outcome.state != "done":
            break
    assert outcome is not None
    if outcome.failure_kind == "stopped":
        ctx.call(store.fail_run, run_id, message=outcome.message or "", failure_kind="stopped", rc=outcome.rc)
        return "failed"
    # 控える（ハードリンク）。取り込むのは控えの絵。エンジンの終わり方を控えてから取り込む
    snap = W.snapshot(engine.mode, work, run_dir, pages) if outcome.state == "done" else {}
    if outcome.state == "done" and engine.mode == "presentation":
        try:
            W.snapshot_present(work, run_dir)   # 発表の画面の段ごとの再生用（無くても描いた回は成り立つ。ページの PNG で出す）
        except OSError as exc:
            log.warning("run %s: 発表の画面用の HTML を控えられませんでした: %s", run_id, exc)
    for no, info in outcome.pages.items():
        s = snap.get(no) or {}
        info["file"] = s.get("file") if info.get("file") else None
        info["picture_sha"] = s.get("picture_sha")
    if message and outcome.state == "done":
        outcome.message = (outcome.message + " " if outcome.message else "") + message
    ctx.call(store.enter_importing, run_id, outcome.to_record())
    ctx.call(store.finish_run, run_id, state=outcome.state, rc=outcome.rc, message=outcome.message,
             failure_kind=outcome.failure_kind, pages=outcome.pages)
    log.info("run %s: %s rc=%s pages=%d", run_id, outcome.state, outcome.rc,
             sum(1 for p in outcome.pages.values() if p.get("file")))
    return outcome.state


def check_candidate(ctx: Context, engine: Engine, run: dict[str, Any], data: dict[str, Any]):
    """台本の候補をエンジンの `check` に通す（非表示のページを抜いた形で）。CheckResult を返す。"""
    path = W.write_engine_script(ctx.root.engine_script_path(run["deck_id"]), engine_script_view(data, run["args"]))
    return check_script(engine, tools=ctx.tools, home=ctx.root.root, script=path, args=run["args"], stop=ctx.stop)


def write_script(ctx: Context, job: dict[str, Any], run: dict[str, Any]):
    """台本係に書かせ、検査に通るまで直させる（上限あり）。通った (台本の行, 台本) か、失敗の "failed"。"""
    store = ctx.store
    intake = ctx.call(store.intake, job["intake_id"])
    deck_id, run_id = run["deck_id"], run["id"]
    engine = engine_for(run["mode"])
    args = dict(run["args"])
    run_dir = ctx.root.run_dir(deck_id, run_id)
    home = ctx.root.root
    rounds = ctx.settings.worker.max_check_rounds
    outline = outline_headings(intake["manuscript"]) if args.get("outline_fixed") else []
    if outline and not args.get("page_count"):
        args["page_count"] = len(outline)

    def fail(message: str, kind: str, rc: int | None = None) -> str:
        ctx.call(store.fail_run, run_id, message=message, failure_kind=kind, rc=rc)
        return "failed"

    available = {"claude": ctx.tools.claude_path(), "codex": ctx.tools.codex_path()}
    try:
        backend, why = choose_backend(ctx.settings.writer, intake["origin_runtime"], available)
    except WriterFailed as exc:
        return fail(exc.message, "writer_" + exc.kind)
    exe = available[backend]
    assert exe is not None
    env = child_env(home=home, extra=ctx.tools.extra_env)
    ctx.call(store.set_run_record, run_id, {"settings": {
        "writer_setting": ctx.settings.writer, "writer": backend, "writer_reason": why, "max_check_rounds": rounds,
        "style": args.get("style"), "image_look": args.get("image_look"), "images": args.get("images"),
        "page_count": args.get("page_count"), "style_note": args.get("style_note") or None,
        "outline_fixed": bool(args.get("outline_fixed")), "outline_pages": len(outline) or None,
        # ブランド・スタイル・書式の控えは record の look（テーマの YAML・ロゴの sha256・引数の元）
        "theme": (args.get("look") or {}).get("theme"), "logo": (args.get("look") or {}).get("logo"),
        "logo_position": (args.get("look") or {}).get("logo_position"),
    }})

    previous: dict[str, Any] | str | None = None
    problems: list[str] | None = None
    for round_no in range(1, rounds + 1):
        if ctx.stop.is_set():
            return fail("サーバを止めたので、台本を書くのを中断しました。もう一度会話から頼んでください。", "stopped")
        ctx.call(store.set_run_state, run_id, "writing", progress=_writer_progress(round_no, rounds, backend, "writing"),
                 args_update={"writer": backend, "writer_reason": why})
        try:
            prompt = build_prompt(engine, args, manuscript=intake["manuscript"], title_hint=intake["title_hint"],
                                  skills_dir=ctx.tools.skills_dir, previous=previous, problems=problems)
        except ContractMissing as exc:
            return fail(f"エンジンの台本の決まり（{exc}）が見つかりません。導入をやり直してください。", "contract")
        try:
            data = ask(backend, exe, prompt, job_dir=run_dir / "writer" / f"round-{round_no}", env=env,
                       timeout=ctx.settings.worker.writer_timeout_seconds, stop=ctx.stop)
        except WriterFailed as exc:
            if exc.kind == "format":
                previous, problems = "（前の答えは JSON として読めませんでした）", [
                    "NG: 返したものが JSON のオブジェクト 1 つではなかった。台本の JSON オブジェクトだけを返す"]
                continue
            return fail(exc.message, "writer_" + exc.kind)
        del prompt
        slides = data.get("slides")
        if not isinstance(slides, list) or not slides or not all(isinstance(s, dict) for s in slides):
            previous, problems = data, ["NG: slides（ページのオブジェクトの配列）が無いか空"]
            continue
        for s in slides:
            s.pop("_hidden", None)   # 非表示は画面が決める（台本係は書かない）
        script = ctx.call(store.save_script, deck_id, data, written_by="writer", writer_backend=backend,
                          intake_id=intake["id"], run_id=run_id, round_no=round_no)
        ctx.call(store.set_run_state, run_id, "checking", progress=_writer_progress(round_no, rounds, backend, "checking"))
        own = page_id_problems(slides) + LOOK.type_problems(slides, args)
        # 日本語の検査（絵文字・前置き）。最後の回は止めずに記録だけ（リンターのせいで 1 冊が作れなくなるのを避ける）
        jl_ng, jl_notes = JLINT.problems_and_notes(slides, enforce=round_no < rounds)
        own += jl_ng
        if outline and len(slides) != len(outline):
            own.append(f"NG: 構成は {len(outline)} ページ（`## N.` の見出しの数）なのに、台本は {len(slides)} ページ。"
                       "構成のページ割りどおり、見出し 1 つを 1 ページにして、順番も変えずに書き直す")
        if own:
            ctx.call(store.set_script_check, script["id"], 2, own)
            previous, problems = data, own
            continue
        result = check_candidate(ctx, engine, run, data)
        ctx.call(store.set_script_check, script["id"], -1 if result.rc is None else result.rc,
                 result.problems + result.warnings + jl_notes)
        if result.stopped:
            return fail("サーバを止めたので、台本を確かめるのを中断しました。もう一度会話から頼んでください。", "stopped")
        if result.rc == 0:
            return script, data
        if result.rc == 2 and LOOK.theme_refused(result.problems):
            return fail(LOOK.BRAND_COLOR_MESSAGE, "brand", result.rc)
        if result.rc == 2:
            previous, problems = data, result.problems or ["NG: 検査で止まった（理由の行が取れなかった）"]
            log.info("run %s: 台本の検査で止まった（%d 回目・%d 行）", run_id, round_no, len(problems))
            continue
        if result.rc == RC_DEPENDENCY:
            return fail(DEPENDENCY_MESSAGE, "dependency", result.rc)
        return fail("台本を確かめられませんでした（エンジンの検査が動きませんでした）。" + DOCTOR_HINT, "check", result.rc)
    return fail(f"台本を {rounds} 回書き直しましたが、エンジンの決まりに合いませんでした。原稿を短くするか、もう一度頼んでください。",
                "script")
