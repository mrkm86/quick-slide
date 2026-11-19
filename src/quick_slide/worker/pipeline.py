"""仕事 1 つ（原稿から 1 冊）: 確かめる → 台本係が書く → エンジンの検査 → （NG なら直させる。上限あり）→
描き係が描く → 控える → 取り込む。描く・控える・取り込むところ（`render_and_import`）は直す仕事（`edit.py`）も使う。

台帳（store）に触るときは必ずサーバの錠（`store_lock`）を取る。錠を持ったまま子プロセスを待たない
（画面の読み取りを止めないため）。各段の状態は `runs` に書き、画面はそれを `planning.message` として読む。

**エンジンはデッキの作業フォルダ（`work/`）に描き、終わった回を `runs/<回>/` へ控える**（P3。理由は
`render/workspace.py`）。台本はいまの台本から非表示のページを抜いて `scripts/engine.json` に書いて渡す。

**版を切り替えたページ**（台本の `_pin`。`pins.py`・`docs/adr/0001-version-pins.md`）は、描くたびに、その版を描いた回の
ものを使う（新しい版を作らない）:
- ピッチ: その版の絵を区画に置き、`--keep` でその絵を使わせてエンジンを呼ぶ（ブランドの色・スタイルを替えて描く指示の鍵が
  変わっていても描き直さない。その絵で PPTX・PDF まで組む）
- プレゼン: 生成の枠の絵を区画へ戻してから描き、描き終えたら、ページの画像をその版の回のものに張り替え、
  書き出し（PPTX・PDF）をページごとの部品から組み直す（その版の部品＋この回の部品。エンジンの `assemble`）。
  部品の無い版（部品を残す前の回）は、書き出しだけこの回で描いたものを使う
- モーション: ページの画像をその版の回のものに張り替える（HTML 1枚は中身から組む。場面のキャッシュで撮り直さない）
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import importing, motion
from ..childenv import child_env
from ..engines import RC_DEPENDENCY, Engine, engine_for, engine_marker
from ..errors import StateError
from ..paths import DataRoot
from ..pins import Pin
from ..pins import resolve as resolve_pins
from .. import page_images as PI
from ..render import illustrations as ILL
from ..render import look as LOOK
from ..render import workspace as W
from ..render.runner import (
    DOCTOR_HINT,
    CHECK_TIMEOUT_SECONDS,
    CheckResult,
    RenderOutcome,
    chrome_path,
    check_prerequisites,
    check_script,
    dependency_message,
    render_deck,
)
from ..scriptdoc import (
    content_sha,
    engine_view,
    is_hidden,
    slides_of,
    one_page,
    outline_headings,
    output_pages,
    page_id_problems,
    pins_of,
    screen_pages,
)
from ..settings import Settings
from ..writer import motion as MOTION_WRITER
from ..writer import jlint as JLINT
from ..preparation.errors import EngineInputUnsupported, PreparationStopped
from ..preparation.service import TemplateRun, prepare_run
from ..writer.backends import WriterFailed, ask, choose_backend
from ..writer.prompt import ContractMissing, build_prompt
from .research import research_manuscript
from .tools import Tools

log = logging.getLogger("quick_slide.worker")
# 書き出しをページごとの部品から組む時間の上限（PPTX は python-pptx・PDF はページを抜いて並べるだけ。数秒で終わる）
ASSEMBLE_TIMEOUT_SECONDS = 120
# デザインテンプレートで作る回は、描画（PR 7）がつながるまで、台本の確定（素材の準備と検査）で止める。
# 古い（テンプレートなしの）経路で描いたり、「できた」ように見せたりしない
TEMPLATE_RENDER_PENDING_MESSAGE = (
    "デザインテンプレートを使った資料づくりは、絵を描く段がまだ使えないため、台本を確かめたところで止めました。"
    "テンプレートの指定は受け付けた内容のまま残っています。テンプレートを使わずに作るか、対応した版を待ってください。")
TEMPLATE_PREPARATION_PENDING_MESSAGE = TEMPLATE_RENDER_PENDING_MESSAGE   # 前の名前（試験・直しの口が使う）
TEMPLATE_STOP_KIND = "template_render_pending"


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
    """エンジンに渡す台本: 非表示のページを抜き、ブランドのフッタ・ページ番号を一番外に入れる（台本の版は変えない）。
    モーションは場面の配列を `scenes` に直し、フッタ・ページ番号は入れない（motion-slide は一番外のキーを知らないと NG にする）。"""
    if motion.is_motion(args):
        return motion.engine_view(engine_view(data))
    return LOOK.apply_format(engine_view(data), args.get("look"))


def engine_script_with_pictures(ctx: Context, deck_id: str, data: dict[str, Any], args: dict[str, Any]
                                ) -> tuple[dict[str, Any], list[str]]:
    """エンジンに渡す台本（`engine_script_view`）の挿絵（`image.svg`）を PNG の `path` に替える（プレゼンだけ。
    docs/adr/0011-svg-illustrations.md）。戻り値の 2 つ目は、台本係に書き直させる `NG:` の行。
    Chrome が無いなどで描けなければ `ILL.IllustrationFailed`。"""
    view = engine_script_view(data, args)
    if args.get("mode") != "presentation" or not ILL.has_illustrations(view):
        return view, []
    work = ctx.root.work_dir(deck_id)
    work.mkdir(parents=True, exist_ok=True)
    chrome = chrome_path(tools=ctx.tools, home=ctx.root.root, cwd=work, stop=ctx.stop)
    out, bad = ILL.materialize(view, scripts_dir=ctx.root.scripts_dir(deck_id), tools=ctx.tools, home=ctx.root.root,
                               chrome=chrome, stop=ctx.stop)
    # 取り込んだページに重ねる挿絵（PNG になったもの）は、ページの HTML の最後に足す（docs/adr/0013）
    return ILL.apply_overlays(out), bad


def lint_view(args: dict[str, Any], slides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """日本語の検査（`writer/jlint.py`）にかけるページ。モーションは場面の HTML の見える文字・題・言葉・話す台本。"""
    return motion.jlint_pages(slides) if motion.is_motion(args) else slides


def _writer_progress(round_no: int, rounds: int, backend: str, phase: str) -> dict[str, Any]:
    return {"phase": phase, "round": round_no, "max_rounds": rounds, "writer": backend}


def make(ctx: Context, job: dict[str, Any]) -> str:
    """仕事を最後まで流す。戻り値は描いた回の終わりの状態（done / failed）。失敗も台帳に書いてから返る。

    段（台帳に書いてから始める）: 確かめる（ピッチは Codex のログイン）→ （依頼から作るときは下調べ）→ 書く ⇄ 検査 →
    描く → 取り込む → 終わり。
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

    template = run["args"].get("template_snapshot")
    if template and resume is not None:
        return fail(TEMPLATE_RENDER_PENDING_MESSAGE, TEMPLATE_STOP_KIND)
    if resume == "import":
        return import_recorded(ctx, run)
    if resume is None:
        if template:
            try:
                settle_look(ctx, run, previous_style=None)   # 控えから設定を確定して記録する（最新の設定は見ない）
            except StateError as exc:   # 控えが無い・受け付けたときと違う: 別のものを使わずに止める
                return fail(str(exc), "template_snapshot")
            return make_template(ctx, job, run)
        settle_look(ctx, run, previous_style=None)
        # 描く前の確かめ（仕事ごとに 1 回。ピッチは Codex のログイン。書く前に見るので、描けないのに台本係の枠を使わない）
        pre = check_prerequisites(engine, tools=ctx.tools, home=ctx.root.root, cwd=run_dir, stop=ctx.stop)
        ctx.call(store.set_run_record, run_id, {"prerequisites": {"checked": pre.rc is not None, "rc": pre.rc}})
        if not pre.ok:
            return fail(pre.message or "", pre.failure_kind or "engine", pre.rc)
        intake = ctx.call(store.intake, job["intake_id"]) if job.get("intake_id") else None
        if importing.is_import(intake):
            # 既存の PPTX の取り込み: 台本係の代わりにエンジンの import で台本を起こす（docs/adr/0011-import-pptx.md）
            accepted = import_script(ctx, run, intake)
        else:
            # 依頼から作る取り込み（画面の入力欄）は、台本係の前に下調べ係が原稿を起こす（worker/research.py）
            if research_manuscript(ctx, job, run) is not None:
                return "failed"
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


def import_script(ctx: Context, run: dict[str, Any], intake: dict[str, Any]):
    """エンジンの import で PPTX から台本を起こし、台本の版にする。(台本の行, 台本) か、失敗の "failed"。

    検査（check）は描く段のエンジンがする（取り込んだページは型を組まないので、台本係の直しの回しは要らない）。"""
    store = ctx.store
    deck_id, run_id = run["deck_id"], run["id"]

    def fail(message: str, kind: str, rc: int | None = None) -> str:
        ctx.call(store.fail_run, run_id, message=message, failure_kind=kind, rc=rc)
        return "failed"

    source = ctx.root.import_source_path(deck_id)
    if not source.is_file():
        return fail("取り込む PPTX が見つかりません。もう一度取り込んでください。", "import")
    ctx.call(store.set_run_state, run_id, "writing", progress={"phase": "importing"})
    out = ctx.root.imported_dir(deck_id)
    env = child_env(home=ctx.root.root, extra=ctx.tools.extra_env)
    rc, tail = importing.run_engine(ctx.tools.python, ctx.tools.skills_dir, source, out, env=env,
                                    log_path=ctx.root.run_dir(deck_id, run_id) / "import.log")
    if ctx.stop.is_set():
        return fail("サーバを止めたので、取り込みを中断しました。もう一度取り込んでください。", "stopped")
    if rc == RC_DEPENDENCY:
        return fail(dependency_message(engine_for(run["mode"])), "dependency", rc)
    if rc != 0:
        why = tail.replace("NG: ", "") if tail else "PPTX を読めませんでした。"
        return fail(f"PPTX を取り込めませんでした: {why}", "import", rc)
    try:
        raw = json.loads((out / "script.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fail("取り込んだ台本を読めませんでした。もう一度取り込んでください。", "import")
    data = importing.to_script(raw)
    if not data["slides"]:
        return fail("PPTX にスライドがありませんでした。", "import")
    script = ctx.call(store.save_script, deck_id, data, written_by="import", writer_backend=None,
                      intake_id=intake["id"], run_id=run_id)
    rep = importing.report_of(out)
    lines = importing.report_lines(rep)
    ctx.call(store.set_script_check, script["id"], 0, lines)
    ctx.call(store.set_run_record, run_id, {"import": {
        "source": (intake.get("payload") or {}).get("import"), "slides": rep.get("slides"), "size": rep.get("size"),
        "notes": lines}})
    return script, data


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
    if run["args"].get("template_snapshot"):   # テンプレートの回は、描画がつながるまで、古い経路で描かない
        ctx.call(store.fail_run, run_id, message=TEMPLATE_RENDER_PENDING_MESSAGE, failure_kind=TEMPLATE_STOP_KIND)
        return "failed"
    engine = engine_for(run["mode"])
    args = run["args"]
    home = ctx.root.root
    work = ctx.root.work_dir(deck_id)
    run_dir = ctx.root.run_dir(deck_id, run_id)
    work.mkdir(parents=True, exist_ok=True)
    pages = output_pages(args, data)
    # モーションは進みの行が無いので、最初から「組んでいます」（検査と組み立てが終わるまで変わらない）
    first = {"phase": "rendering", "step": "motion" if engine.mode == "motion" else "starting", "done": 0, "total": len(pages)}
    if start:
        ctx.call(store.start_rendering, run_id, script["id"], pages, first)
    else:
        ctx.call(store.set_run_state, run_id, "rendering", progress=first)
    try:
        view, bad = engine_script_with_pictures(ctx, deck_id, data, args)
    except ILL.IllustrationFailed as exc:
        ctx.call(store.fail_run, run_id, message=str(exc), failure_kind="dependency", rc=None)
        return "failed"
    if bad:   # 検査を通った台本なので、ここで NG が出るのは挿絵の描き方が変わったときだけ
        ctx.call(store.fail_run, run_id, message="挿絵を描けませんでした。" + bad[0], failure_kind="script", rc=None)
        return "failed"
    script_path = W.write_engine_script(ctx.root.engine_script_path(deck_id), view)
    pins: dict[str, Pin] = {} if one_page(args) else ctx.call(resolve_pins, store, deck_id, pins_of(data), pages)

    placed: dict[str, str] = {}
    if engine.mode == "pitch" and not one_page(args):
        for key, pin in pins.items():   # 版を切り替えたページは、その版の絵を区画に置く（鍵が合えば描かない）
            got = None if key in plan.seeds else W.pitch_picture(pin.run_dir, key, legacy_no=pin.row["page_no"])
            if got is not None:
                plan.seeds[key] = got
        placed = W.arrange_pitch_cache(work, [p["key"] for p in pages], area=known_pitch_area(ctx, deck_id, work),
                                       seeds=plan.seeds, aliases=plan.aliases, fallback=_pitch_fallback(ctx, deck_id))
    restored = 0
    for old_dir, key in plan.restore_presentation:
        restored += W.restore_presentation_pictures(old_dir, key)
    if engine.mode == "presentation":
        for key, pin in pins.items():   # 生成の枠の絵をその版のものへ（部品の無い版の書き出しも、その絵で描く）
            restored += W.restore_presentation_pictures(pin.run_dir, key)

    position = {p["key"]: p["no"] for p in pages}
    commands: list[tuple[str, list[str]]] = []
    # 版を切り替えたページは、置いたその版の絵を鍵が合わなくても使わせる（ピッチ。描き直すと別の絵になり版が増える）
    keep = [position[k] for k in pins if placed.get(k) == "seed" and k in position]
    if plan.deck or not plan.revise:
        commands.append(("engine", engine.deck_command(ctx.tools.python, ctx.tools.skills_dir, script_path, work, args,
                                                       keep=keep)))
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
        "pins": {k: pin.run_id for k, pin in pins.items()},
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
            W.snapshot_present(work, run_dir, ctx.root.present_fonts_dir(run["deck_id"]))   # 発表の画面の段ごとの再生用
        except OSError as exc:
            log.warning("run %s: 発表の画面用の HTML を控えられませんでした: %s", run_id, exc)
    elif outcome.state == "done" and engine.mode == "motion":
        try:
            W.snapshot_present_motion(work, run_dir)   # 発表の画面（HTML 1枚をそのまま動かす）。無ければページの PNG で出す
        except OSError as exc:
            log.warning("run %s: 発表の画面用の HTML を控えられませんでした: %s", run_id, exc)
    for no, info in outcome.pages.items():
        s = snap.get(no) or {}
        info["file"] = s.get("file") if info.get("file") else None
        info["picture_sha"] = s.get("picture_sha")
    if outcome.state == "done" and engine.keeps_parts:
        W.snapshot_parts(work, run_dir, pages)   # 書き出しの部品をページごとに（版を混ぜた書き出しの材料）
    if outcome.state == "done" and pins and engine.mode in ("presentation", "motion"):
        ctx.call(store.set_run_record, run_id, {"pinned": adopt_pins(ctx, engine, run_dir, pages, pins, outcome)})
    if message and outcome.state == "done":
        outcome.message = (outcome.message + " " if outcome.message else "") + message
    ctx.call(store.enter_importing, run_id, outcome.to_record())
    ctx.call(store.finish_run, run_id, state=outcome.state, rc=outcome.rc, message=outcome.message,
             failure_kind=outcome.failure_kind, pages=outcome.pages)
    log.info("run %s: %s rc=%s pages=%d", run_id, outcome.state, outcome.rc,
             sum(1 for p in outcome.pages.values() if p.get("file")))
    if outcome.state == "done" and engine.mode == "presentation" and not one_page(args):
        try:
            draw_hidden_previews(ctx, engine, run, data)
        except Exception:  # noqa: BLE001 - 下見の絵は描けなくても回は成り立つ（画面は「未生成」のまま）
            log.exception("run %s: 非表示のページの下見の絵を描けませんでした", run_id)
    return outcome.state


def draw_hidden_previews(ctx: Context, engine: Engine, run: dict[str, Any], data: dict[str, Any]) -> int:
    """非表示のページだけを、絵（PNG）だけ描いて下見にする（docs/adr/0012-hidden-page-previews.md）。描いた枚数を返す。

    非表示のページは描く回に入らない（発表・書き出し・枚数に入らない）。中身は見たいので、描き終えたあとに別に描く。
    プレゼン（html-slide）だけ: 数秒で描ける。新しい絵は生成しない（`--no-fill`。描いてある生成の絵はキャッシュから使う）。
    中身の sha256 が下見と同じページは描き直さない。"""
    store = ctx.store
    deck_id = run["deck_id"]
    hidden = [s for s in slides_of(data) if is_hidden(s) and isinstance(s.get("id"), str)]
    if not hidden:
        return 0
    have = ctx.call(store.page_previews, deck_id)
    keys = {p["key"]: p["page_id"] for p in ctx.call(store.script_pages, ctx.call(store.head_script, deck_id)["id"])}
    todo = []
    for s in hidden:
        sha = content_sha(s)
        pv = have.get(keys.get(s["id"], ""))
        if pv is None or pv["content_sha256"] != sha or pv["asset_state"] != "ready":
            todo.append((s, sha))
    if not todo:
        return 0
    args = run["args"]
    view = engine_script_view({**data, "slides": [{k: v for k, v in s.items() if not k.startswith("_")} for s, _ in todo]},
                              args)
    script_path = W.write_engine_script(ctx.root.scripts_dir(deck_id) / "engine-hidden.json", view)
    out = ctx.root.deck_dir(deck_id) / "work-hidden"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    cmd = engine.deck_command(ctx.tools.python, ctx.tools.skills_dir, script_path, out, args)
    i = cmd.index("--format")
    cmd[i + 1] = "png"
    if "--parts" in cmd:
        j = cmd.index("--parts")
        del cmd[j:j + 2]
    cmd.append("--no-fill")
    env = child_env(home=ctx.root.root, extra=ctx.tools.extra_env)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=ctx.settings.worker.render_timeout_seconds,
                           env=env, cwd=str(out))
    except subprocess.TimeoutExpired:
        log.warning("run %s: 非表示のページの下見の絵が時間内に描けませんでした", run["id"])
        return 0
    (ctx.root.run_dir(deck_id, run["id"]) / "hidden-previews.log").write_text(r.stdout + "\n--- stderr ---\n" + r.stderr,
                                                                              encoding="utf-8")
    drawn = 0
    for no, (s, sha) in enumerate(todo, 1):
        png = out / "slides" / f"slide-{no:02d}.png"
        if png.is_file():
            ctx.call(store.save_page_preview, deck_id, run["id"], s["id"], sha, png)
            drawn += 1
    log.info("run %s: 非表示のページの下見の絵 %d/%d 枚（rc=%s）", run["id"], drawn, len(todo), r.returncode)
    return drawn


def adopt_pins(ctx: Context, engine: Engine, run_dir: Path, pages: list[dict[str, Any]], pins: dict[str, Pin],
               outcome: RenderOutcome) -> dict[str, Any]:
    """描き終えた回に、版を切り替えたページを、その版の回から持ち込む（プレゼン・モーション）。記録を返す。

    プレゼンは先に書き出しを組み直す（部品のある版のページはその版の部品、ほかはこの回の部品）。組めなかったら
    エンジンの書き出しのまま（全ページいまの作り）にして、持ち込んだページの部品は控えない（ページの画像と食い違うので）。
    ページの画像・絵の控え・発表の区切りの在りかは、組めても組めなくても持ち込む（画面と版の数え方を揃える）。"""
    by_key = {p["key"]: p for p in pages}
    record: dict[str, Any] = {"pages": {}, "assembled": None}
    if engine.keeps_parts:
        exact = {k for k, pin in pins.items() if W.has_parts(pin.run_dir, k)}
        if exact:
            record["assembled"] = _assemble(ctx, engine, run_dir, pages, pins, exact)
            if not record["assembled"]:
                exact = set()
        for k, pin in pins.items():
            if not (k in exact and W.adopt_part(pin.run_dir, run_dir, k, by_key[k]["no"])):
                W.drop_part(run_dir, k)
        record["exact"] = sorted(exact)
    for k, pin in pins.items():
        no = by_key[k]["no"]
        present = W.present_source(pin.run_dir, pin.run_id, k, pin.row["page_no"]) if engine.mode == "presentation" else None
        shot = W.adopt_page(pin.run_dir, pin.row["page_no"], run_dir, no, k, present=present)
        info = outcome.pages.get(no)
        if shot is None or info is None:
            continue
        info["file"] = str(shot)
        info["picture_sha"] = pin.row.get("picture_sha256") or info.get("picture_sha")
        info["warnings"] = list(pin.row.get("warnings") or [])
        record["pages"][k] = pin.run_id
    return record


def _assemble(ctx: Context, engine: Engine, run_dir: Path, pages: list[dict[str, Any]], pins: dict[str, Pin],
              exact: set[str]) -> bool:
    """この回の書き出し（deck.pptx・deck.pdf）を、ページごとの部品から組み直す。組めたか。"""
    slides, picks = [], []
    for p in pages:
        part = W.load_part(pins[p["key"]].run_dir if p["key"] in exact else run_dir, p["key"])
        if part is None or part["pdf"] is None:
            log.warning("書き出しの部品が揃わないので、版を混ぜずに書き出します（%s）", p["key"])
            return False
        slides.append(part["slide"])
        picks.append(list(part["pdf"]))
    cmd_dir = run_dir / "assemble"
    shutil.rmtree(cmd_dir, ignore_errors=True)
    manifest = cmd_dir / "manifest.json"
    W.write_json(manifest, {**W.parts_deck(run_dir), "slides": slides, "pdf": picks})
    cmd = engine.assemble_command(ctx.tools.python, ctx.tools.skills_dir, manifest, cmd_dir)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=ASSEMBLE_TIMEOUT_SECONDS,
                           env=child_env(home=ctx.root.root, extra=ctx.tools.extra_env), cwd=str(run_dir), encoding="utf-8")
        ok = r.returncode == 0 and (cmd_dir / "deck.pptx").is_file() and (cmd_dir / "deck.pdf").is_file()
        if not ok:
            log.warning("書き出しを部品から組めませんでした（rc=%s）: %s", r.returncode, (r.stderr or "").strip()[:300])
        else:
            for name in ("deck.pptx", "deck.pdf"):
                (cmd_dir / name).replace(run_dir / name)   # 控えのハードリンクを外して入れ替える（作業フォルダ側は変えない）
        return ok
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("書き出しを部品から組めませんでした: %s", exc)
        return False
    finally:
        shutil.rmtree(cmd_dir, ignore_errors=True)


def check_candidate(ctx: Context, engine: Engine, run: dict[str, Any], data: dict[str, Any], *,
                    template: TemplateRun | None = None):
    """台本の候補をエンジンの `check` に通す（非表示のページを抜いた形で）。CheckResult を返す。

    テンプレートの回（`template`）は、素材の参照を除き取得済みの参照を変換した台本（`TemplateRun.convert`）を通す。
    変換できないもの（まだ対応していないモード・役割）は `EngineInputUnsupported`（呼ぶ側が理由を言って止める）。

    形だけを先に見られるエンジン（モーション）は、先に `--quick`（Chrome を使わず数秒）で形の NG を返し、
    形が通ったときだけ全部の拍の検査（拍の数に比例して時間がかかる）を回す。書き直しのたびに全拍を回さない。"""
    if template is not None:
        data = template.convert(data)
    try:
        view, bad = engine_script_with_pictures(ctx, run["deck_id"], data, run["args"])
    except ILL.IllustrationFailed as exc:
        return CheckResult(RC_DEPENDENCY, [f"NG: {exc}"], [])
    if bad:
        return CheckResult(2, bad, [])   # 挿絵を直させる（台本係に返す）
    # 検査は回ごとの台本ファイルで（同じデッキの別のページの直しと同時に走る。docs/adr/0012）。台本と同じフォルダに
    # 置くので、添えた画像（attachments/）と挿絵（illustrations/）の相対パスはそのまま読める
    path = W.write_engine_script(ctx.root.scripts_dir(run["deck_id"]) / f"check-{run['id']}.json", view)
    try:
        timeout = motion.check_timeout(data) if motion.is_motion(run["args"]) else CHECK_TIMEOUT_SECONDS
        if engine.quick_check:
            quick = check_script(engine, tools=ctx.tools, home=ctx.root.root, script=path, args=run["args"], stop=ctx.stop,
                                 quick=True)
            if quick.rc != 0:
                return quick
        return check_script(engine, tools=ctx.tools, home=ctx.root.root, script=path, args=run["args"], stop=ctx.stop,
                            timeout=timeout)
    finally:
        path.unlink(missing_ok=True)


def write_script(ctx: Context, job: dict[str, Any], run: dict[str, Any], *, template: TemplateRun | None = None):
    """台本係に書かせ、検査に通るまで直させる（上限あり）。通った (台本の行, 台本) か、失敗の "failed"。

    `template`（デザインテンプレートの回）は、台本係に `writer.md` と準備カタログを渡し（道具なし）、`_references` を
    確かめ、選んだ素材をコピーし、変換した台本で検査し、通ったら準備の控えにする。"""
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

    # 止めたときの次の一歩。依頼から作るデッキは台本が無くても「生成を再開」でやり直せる（docs/adr/0002-create-from-screen.md）
    again = "「生成を再開」でもう一度書きます。" if intake.get("request") else "もう一度会話から頼んでください。"
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
        # どちらの道で来たか（原稿を持ってきた／依頼から下調べした。docs/adr/0002-create-from-screen.md）
        "intake_path": "request" if intake.get("request") else "manuscript",
        "style": args.get("style"), "image_look": args.get("image_look"), "images": args.get("images"),
        "page_count": args.get("page_count"), "style_note": args.get("style_note") or None,
        "outline_fixed": bool(args.get("outline_fixed")), "outline_pages": len(outline) or None,
        # ブランド・スタイル・書式の控えは record の look（テーマの YAML・ロゴの sha256・引数の元）
        "theme": (args.get("look") or {}).get("theme"), "logo": (args.get("look") or {}).get("logo"),
        "logo_position": (args.get("look") or {}).get("logo_position"),
    }})

    # 依頼に書かれたページの本文の画像（頼まれたときだけ集める。使うかは台本係が依頼から決める。docs/adr/0016）
    pictures = PI.collect(intake.get("request"), ctx.root.attachments_dir(deck_id)) if engine.mode == "presentation" else []
    if pictures:
        ctx.call(store.set_run_record, run_id, {"page_pictures": [{"rel": p["rel"], "page_url": p["page_url"]} for p in pictures]})
    previous: dict[str, Any] | str | None = None
    problems: list[str] | None = None
    if template is not None and template.resumed:
        # 中断した回の続き: 完了した段は、控えのハッシュが合うときだけ再利用する（台本係を呼び直さない）
        row = template.completed(lambda sid: ctx.call(store.script, sid))
        if row is not None:
            return row, ctx.call(store.load_script, row)
        row = template.pending_candidate(lambda sid: ctx.call(store.script, sid))
        if row is not None:
            cand = ctx.call(store.load_script, row)
            try:
                result = check_candidate(ctx, engine, run, cand, template=template)
            except PreparationStopped as exc:
                return _template_stop(ctx, template, run_id, exc)
            if result.rc == 0:
                template.finish(row)
                return row, cand
    for round_no in range(1, rounds + 1):
        if ctx.stop.is_set():
            return fail("サーバを止めたので、台本を書くのを中断しました。" + again, "stopped")
        ctx.call(store.set_run_state, run_id, "writing", progress=_writer_progress(round_no, rounds, backend, "writing"),
                 args_update={"writer": backend, "writer_reason": why})
        try:
            prompt = build_prompt(engine, args, manuscript=intake["manuscript"], title_hint=intake["title_hint"],
                                  skills_dir=ctx.tools.skills_dir,
                                  previous=None if motion.is_motion(args) else previous,
                                  problems=None if motion.is_motion(args) else problems,
                                  request=intake.get("request"), pictures=pictures,
                                  template=template.writer_sections() if template is not None else None)
        except ContractMissing as exc:
            return fail(f"エンジンの台本の決まり（{exc}）が見つかりません。導入をやり直してください。", "contract")
        if template is not None:
            try:
                template.check_prompt_budget(prompt)   # 完成したプロンプト全体を、再試行のたびに測る（超えたら要約せず止める）
            except PreparationStopped as exc:
                return _template_stop(ctx, template, run_id, exc)
        try:
            if motion.is_motion(args):
                def scene_progress(phase, done, total):
                    ctx.call(store.set_run_state, run_id, "writing", progress={
                        **_writer_progress(round_no, rounds, backend, phase), "done": done, "total": total})
                data = MOTION_WRITER.write(
                    prompt=prompt, backend=backend, exe=exe, env=env,
                    job_dir=run_dir / "writer" / f"round-{round_no}",
                    checkpoint_dir=ctx.root.motion_draft_dir(deck_id, intake["id"]),
                    timeout=ctx.settings.worker.writer_timeout_seconds, stop=ctx.stop,
                    parallel=MOTION_WRITER.MAX_PARALLEL, page_count=args.get("page_count"),
                    previous=previous, problems=problems, progress=scene_progress)
            else:
                data = ask(backend, exe, prompt, job_dir=run_dir / "writer" / f"round-{round_no}", env=env,
                           timeout=ctx.settings.worker.writer_timeout_seconds, stop=ctx.stop,
                           no_tools=template is not None)
        except WriterFailed as exc:
            if exc.kind == "format":
                if motion.is_motion(args) and isinstance(previous, dict):
                    # 場面の修正が壊れても、元の検査結果と直す対象を失わない。
                    continue
                previous, problems = "（前の答えは JSON として読めませんでした）", [
                    "NG: 返したものが JSON のオブジェクト 1 つではなかった。台本の JSON オブジェクトだけを返す"]
                continue
            return fail(exc.message, "writer_" + exc.kind)
        del prompt
        if motion.is_motion(args):
            data = motion.normalize(data)   # 台本係が scenes と書いても slides に読み替える
        slides = data.get("slides")
        if not isinstance(slides, list) or not slides or not all(isinstance(s, dict) for s in slides):
            previous, problems = data, ["NG: slides（ページのオブジェクトの配列）が無いか空"]
            continue
        for s in slides:
            s.pop("_hidden", None)   # 非表示と版の印は画面が決める（台本係は書かない）
            s.pop("_pin", None)
        if template is not None:
            ref_problems = template.check_script(data, fresh=True, allowed_paths={p['rel'] for p in pictures})
            if ref_problems:
                previous, problems = data, ref_problems
                continue
            data = template.stamped(data)
        script = ctx.call(store.save_script, deck_id, data, written_by="writer", writer_backend=backend,
                          intake_id=intake["id"], run_id=run_id, round_no=round_no)
        ctx.call(store.set_run_state, run_id, "checking", progress=_writer_progress(round_no, rounds, backend, "checking"))
        own = page_id_problems(slides) + LOOK.type_problems(slides, args)
        # 日本語の検査（絵文字・前置き）。最後の回は止めずに記録だけ（リンターのせいで 1 冊が作れなくなるのを避ける）
        jl_ng, jl_notes = JLINT.problems_and_notes(lint_view(args, slides), enforce=round_no < rounds)
        own += jl_ng
        if outline and len(slides) != len(outline):
            own.append(f"NG: 構成は {len(outline)} ページ（`## N.` の見出しの数）なのに、台本は {len(slides)} ページ。"
                       "構成のページ割りどおり、見出し 1 つを 1 ページにして、順番も変えずに書き直す")
        if own:
            ctx.call(store.set_script_check, script["id"], 2, own)
            previous, problems = data, own
            continue
        try:
            if template is not None:
                template.resolve(script, data)   # 選んだ素材だけをコピーし、ページへの束縛を控える
            result = check_candidate(ctx, engine, run, data, template=template)
        except PreparationStopped as exc:
            return _template_stop(ctx, template, run_id, exc)
        ctx.call(store.set_script_check, script["id"], -1 if result.rc is None else result.rc,
                 result.problems + result.warnings + jl_notes)
        if result.stopped:
            return fail("サーバを止めたので、台本を確かめるのを中断しました。" + again, "stopped")
        if result.rc == 0:
            if template is not None:
                template.finish(script)
            return script, data
        if result.rc == 2 and LOOK.theme_refused(result.problems):
            return fail(LOOK.BRAND_COLOR_MESSAGE, "brand", result.rc)
        if result.rc == 2:
            previous, problems = data, result.problems or ["NG: 検査で止まった（理由の行が取れなかった）"]
            log.info("run %s: 台本の検査で止まった（%d 回目・%d 行）", run_id, round_no, len(problems))
            continue
        if result.rc == RC_DEPENDENCY:
            return fail(dependency_message(engine), "dependency", result.rc)
        return fail("台本を確かめられませんでした（エンジンの検査が動きませんでした）。" + DOCTOR_HINT, "check", result.rc)
    return fail(f"台本を {rounds} 回書き直しましたが、エンジンの決まりに合いませんでした。原稿を短くするか、もう一度頼んでください。",
                "script")


def _template_stop(ctx: Context, template: TemplateRun | None, run_id: str, exc: PreparationStopped) -> str:
    """素材の準備・変換が止まった。理由を回の失敗にして記録に残す（古い経路・既定の絵へは落とさない）。"""
    if template is not None:
        template.stop(exc)
    ctx.call(ctx.store.set_run_record, run_id, {"template_stop": {"reason": exc.code}})
    kind = "template_unsupported" if isinstance(exc, EngineInputUnsupported) else "template_preparation"
    ctx.call(ctx.store.fail_run, run_id, message=exc.message, failure_kind=kind)
    return "failed"


def make_template(ctx: Context, job: dict[str, Any], run: dict[str, Any]) -> str:
    """デザインテンプレートの回: 素材の準備（入口の確認・読解）→ 台本 → 検査 まで。描画（PR 7）がつながるまで、ここで止める。"""
    store = ctx.store
    run_id = run["id"]
    intake = ctx.call(store.intake, job["intake_id"]) if job.get("intake_id") else None
    if intake is None:
        ctx.call(store.fail_run, run_id, message="テンプレートの回の元の依頼が見つかりません。", failure_kind="internal")
        return "failed"
    if research_manuscript(ctx, job, run) is not None:   # 依頼から作るときの下調べ（素材の準備ではない）
        return "failed"
    available = {"claude": ctx.tools.claude_path(), "codex": ctx.tools.codex_path()}
    try:
        backend, why = choose_backend(ctx.settings.writer, intake["origin_runtime"], available)
    except WriterFailed as exc:
        ctx.call(store.fail_run, run_id, message=exc.message, failure_kind="writer_" + exc.kind)
        return "failed"
    exe = available[backend]
    assert exe is not None
    env = child_env(home=ctx.root.root, extra=ctx.tools.extra_env)
    prep_dir = ctx.root.run_dir(run["deck_id"], run_id) / "preparation"
    calls = {"n": 0}

    def llm(prompt: str) -> dict[str, Any]:
        calls["n"] += 1
        return ask(backend, exe, prompt, job_dir=prep_dir / f"llm-{calls['n']}", env=env,
                   timeout=ctx.settings.worker.writer_timeout_seconds, stop=ctx.stop, no_tools=True)

    ctx.call(store.set_run_state, run_id, "writing", progress={"phase": "preparing", "writer": backend},
             args_update={"writer": backend, "writer_reason": why})
    try:
        tr = prepare_run(ctx, run, llm=llm)
    except PreparationStopped as exc:
        return _template_stop(ctx, None, run_id, exc)
    accepted = write_script(ctx, job, run, template=tr)
    if isinstance(accepted, str):
        return accepted
    script, data = accepted
    ctx.call(store.set_run_record, run_id, {
        "script": {"id": script["id"], "version": script["version"], "sha256": script["sha256"]},
        "template_stop": {"reason": "render_unavailable", "generation": tr.generation}})
    ctx.call(store.fail_run, run_id, message=TEMPLATE_RENDER_PENDING_MESSAGE, failure_kind=TEMPLATE_STOP_KIND)
    return "failed"
