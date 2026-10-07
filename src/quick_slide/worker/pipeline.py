"""仕事 1 つ（原稿から 1 冊）: 確かめる → 台本係が書く → エンジンの検査 → （NG なら直させる。上限あり）→
描き係が描く → 取り込む。

台帳（store）に触るときは必ずサーバの錠（`store_lock`）を取る。錠を持ったまま子プロセスを待たない
（画面の読み取りを止めないため）。各段の状態は `runs` に書き、画面はそれを `planning.message` として読む。
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..childenv import child_env
from ..engines import Engine, engine_for, engine_marker
from ..paths import DataRoot
from ..render.runner import RenderOutcome, check_prerequisites, check_script, render_deck
from ..settings import Settings
from ..writer.backends import WriterFailed, ask, choose_backend
from ..writer.prompt import ContractMissing, build_prompt
from .tools import Tools

log = logging.getLogger("quick_slide.worker")

# 台本のページから「見出し」と「本文」を取り出すときに読み飛ばすキー（話す台本・動き・描き方の注文・画像の枠）
_NOT_CONTENT = {"type", "talk", "transition", "steps", "fx", "id", "role", "image", "_visual", "_characters", "_layout",
                "layout", "split", "side", "text_side", "tone", "fit", "widths", "focus", "accent", "highlight",
                "current", "arrow", "value_labels", "min", "max", "x", "y", "trend", "initials", "path", "prompt", "alt"}


# 台本の各ページの `id`（両エンジンが受ける共通のキー。描くことには使わない）。ページの同一性に使う
PAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


@dataclass
class Context:
    store: Any
    lock: Any
    root: DataRoot
    settings: Settings
    tools: Tools
    stop: threading.Event

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        with self.lock:
            return fn(*args, **kwargs)


def headline_of(spec: dict[str, Any]) -> str:
    """そのページの見出し（エンジンの `label_of`・`headline_of` と同じ選び方）。"""
    for k in ("headline", "title", "quote"):
        v = spec.get(k)
        if isinstance(v, str) and v.strip():
            return v.replace("**", "").replace("\n", " ").strip()
    if spec.get("value"):
        return f"{spec['value']}{spec.get('unit') or ''}"
    return str(spec.get("type") or "")


def body_of(spec: dict[str, Any]) -> str:
    """画面の「このページの記録」に出す、そのページの文字（話す台本・画像の指示は入れない）。"""
    lines: list[str] = []

    def walk(value: Any, key: str | None = None) -> None:
        if key is not None and key in _NOT_CONTENT:
            return
        if isinstance(value, str):
            text = value.replace("**", "").strip()
            if text:
                lines.append(text)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            lines.append(str(value))
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, dict):
            for k, v in value.items():
                walk(v, k)

    for k, v in spec.items():
        walk(v, k)
    return "\n".join(lines)[:4000]


def output_pages(engine: Engine, args: dict[str, Any], script: dict[str, Any]) -> list[dict[str, Any]]:
    """描いた回のページ（エンジンが出す `slides/slide-NN.png` の数だけ）。infographic は全ページを 1 枚に描く。"""
    slides = [s for s in script["slides"] if isinstance(s, dict)]
    if engine.mode == "pitch" and args.get("style") == "infographic":
        title = str(script.get("title") or (headline_of(slides[0]) if slides else ""))
        return [{"no": 1, "key": "infographic", "type": "infographic", "title": title.replace("**", ""),
                 "body": "\n\n".join(headline_of(s) for s in slides)}]
    return [{"no": i, "key": str(s["id"]), "type": str(s.get("type") or ""), "title": headline_of(s), "body": body_of(s)}
            for i, s in enumerate(slides, 1)]


def _writer_progress(round_no: int, rounds: int, backend: str, phase: str) -> dict[str, Any]:
    return {"phase": phase, "round": round_no, "max_rounds": rounds, "writer": backend}


def page_id_problems(slides: list[dict[str, Any]]) -> list[str]:
    """台本の各ページの `id`（ページの同一性）。無い・形が違う・重なる → `NG:` の行（台本係に直させる）。"""
    problems: list[str] = []
    seen: dict[str, int] = {}
    for no, spec in enumerate(slides, 1):
        pid = spec.get("id")
        if not isinstance(pid, str) or not PAGE_ID_RE.match(pid):
            problems.append(f"NG: {no}枚目: id が無いか形が違う（英小文字・数字・ハイフンで 40 字以内。例 \"cover\"・\"p-why\"）")
        elif pid in seen:
            problems.append(f"NG: {no}枚目: id \"{pid}\" が {seen[pid]}枚目と重なっている（資料の中で重ならない id にする）")
        else:
            seen[pid] = no
    return problems


def make(ctx: Context, job: dict[str, Any]) -> str:
    """仕事を最後まで流す。戻り値は描いた回の終わりの状態（done / failed）。失敗も台帳に書いてから返る。

    段（台帳に書いてから始める）: 確かめる（ピッチは Codex のログイン）→ 書く ⇄ 検査 → 描く → 取り込む → 終わり。
    `job.resume_from` が render なら描くところから、import なら取り込むところから続ける（サーバが途中で止まったとき）。
    """
    store = ctx.store
    run = ctx.call(store.run, job["run_id"])
    deck_id, run_id = run["deck_id"], run["id"]
    engine = engine_for(run["mode"])
    args = run["args"]
    run_dir = ctx.root.run_dir(deck_id, run_id)
    home = ctx.root.root
    resume = job.get("resume_from")

    def fail(message: str, kind: str, rc: int | None = None) -> str:
        ctx.call(store.fail_run, run_id, message=message, failure_kind=kind, rc=rc)
        return "failed"

    if resume != "import":
        # 描く前の確かめ（仕事ごとに 1 回。ピッチは Codex のログイン。書く前に見るので、描けないのに台本係の枠を使わない）
        pre = check_prerequisites(engine, tools=ctx.tools, home=home, cwd=run_dir, stop=ctx.stop)
        ctx.call(store.set_run_record, run_id, {"prerequisites": {"checked": pre.rc is not None, "rc": pre.rc}})
        if not pre.ok:
            return fail(pre.message or "", pre.failure_kind or "engine", pre.rc)

    if resume is None:
        accepted = write_script(ctx, job, run)
        if isinstance(accepted, str):
            return accepted
        script, data = accepted
        pages = output_pages(engine, args, data)
        ctx.call(store.set_run_record, run_id, {"script": {"id": script["id"], "version": script["version"],
                                                           "sha256": script["sha256"]}})
        ctx.call(store.start_rendering, run_id, script["id"], pages,
                 {"phase": "rendering", "step": "starting", "done": 0, "total": len(pages)})
    else:
        script = ctx.call(store.script, run["script_id"])
        log.info("run %s: 途中から続ける（%s）", run_id, resume)

    if resume != "import":
        page_count = ctx.call(lambda: len(store.run_pages(run_id)))
        script_path = ctx.call(store.script_path, script)
        ctx.call(store.set_run_state, run_id, "rendering",
                 progress={"phase": "rendering", "step": "starting", "done": 0, "total": page_count})
        ctx.call(store.set_run_record, run_id, {
            "engine": engine_marker(ctx.tools.skills_dir, engine),
            "argv": engine.deck_command(ctx.tools.python, ctx.tools.skills_dir, script_path, run_dir, args),
        })

        def on_change(snapshot: dict[str, Any], marks: dict[int, str]) -> None:
            with ctx.lock:
                for no, state in marks.items():
                    store.mark_run_page(run_id, no, state)
                store.set_run_state(run_id, "rendering", progress=snapshot)

        outcome = render_deck(engine, tools=ctx.tools, home=home, script=script_path, out=run_dir, args=args,
                              page_count=page_count, timeout=ctx.settings.worker.render_timeout_seconds,
                              stop=ctx.stop, on_change=on_change)
        if outcome.failure_kind == "stopped":
            return fail(outcome.message or "", "stopped", outcome.rc)
        # エンジンは終わった。終わり方を控えてから取り込む（取り込みの途中で止まっても、ここから続く）
        ctx.call(store.enter_importing, run_id, outcome.to_record())
    else:
        rec = ctx.call(store.engine_result, run_id)
        if rec is None:
            return fail("取り込む結果の控えがありません。もう一度会話から頼んでください。", "internal")
        outcome = RenderOutcome.from_record(rec)

    ctx.call(store.finish_run, run_id, state=outcome.state, rc=outcome.rc, message=outcome.message,
             failure_kind=outcome.failure_kind, pages=outcome.pages)
    log.info("run %s: %s rc=%s pages=%d", run_id, outcome.state, outcome.rc,
             sum(1 for p in outcome.pages.values() if p.get("file")))
    return outcome.state


def write_script(ctx: Context, job: dict[str, Any], run: dict[str, Any]):
    """台本係に書かせ、検査に通るまで直させる（上限あり）。通った (台本の行, 台本) か、失敗の "failed"。"""
    store = ctx.store
    intake = ctx.call(store.intake, job["intake_id"])
    deck_id, run_id = run["deck_id"], run["id"]
    engine = engine_for(run["mode"])
    args = run["args"]
    run_dir = ctx.root.run_dir(deck_id, run_id)
    home = ctx.root.root
    rounds = ctx.settings.worker.max_check_rounds

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
        # ブランド（テーマ・ロゴ）は P4 でエンジンへ渡す。いまはエンジンの既定
        "theme": None, "logo": None, "logo_position": None,
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
        script = ctx.call(store.save_script, deck_id, data, written_by="writer", writer_backend=backend,
                          intake_id=intake["id"], run_id=run_id, round_no=round_no)
        ctx.call(store.set_run_state, run_id, "checking", progress=_writer_progress(round_no, rounds, backend, "checking"))
        own = page_id_problems(slides)
        if own:
            ctx.call(store.set_script_check, script["id"], 2, own)
            previous, problems = data, own
            continue
        result = check_script(engine, tools=ctx.tools, home=home, script=ctx.call(store.script_path, script),
                              args=args, stop=ctx.stop)
        ctx.call(store.set_script_check, script["id"], -1 if result.rc is None else result.rc,
                 result.problems + result.warnings)
        if result.stopped:
            return fail("サーバを止めたので、台本を確かめるのを中断しました。もう一度会話から頼んでください。", "stopped")
        if result.rc == 0:
            return script, data
        if result.rc == 2:
            previous, problems = data, result.problems or ["NG: 検査で止まった（理由の行が取れなかった）"]
            log.info("run %s: 台本の検査で止まった（%d 回目・%d 行）", run_id, round_no, len(problems))
            continue
        return fail("台本を確かめられませんでした（エンジンの検査が動きませんでした）。導入の手順を確かめてください。", "check",
                    result.rc)
    return fail(f"台本を {rounds} 回書き直しましたが、エンジンの決まりに合いませんでした。原稿を短くするか、もう一度頼んでください。",
                "script")
