"""仕事の最初の段: 依頼から作る取り込み（画面の入力欄）なら、台本係の前に下調べ係を呼んで原稿を起こす。

下調べ係の指示文と答えの検査は `writer/research.py`、CLI の呼び方（Web の検索と取得だけを許す）は `writer/backends.py`。
起こした原稿は取り込みの `manuscript` に書き（`store.set_researched`）、そのあとはいままでどおり台本係 → 描き係へ進む。

段の状態は描いた回の `writing` のまま、進みの `phase` を `researching` にする（画面の帯は「下調べをしています」。
`server/vocabulary.planning_message`）。サーバが下調べの途中で止まったら、書く段と同じく理由つきで失敗にする
（黙ってもう一度呼ばない。`store/runs.recover_interrupted_jobs`）。

失敗の種類は `research_<kind>`（auth / rate / missing / timeout / stopped / failed / format）。文は台本係のものの
「台本を書く」を「下調べをする」に替えたもの。
"""

from __future__ import annotations

import logging
from typing import Any

from ..childenv import child_env
from ..engines import engine_for
from ..writer.backends import WriterFailed, ask_claude, ask_codex, choose_backend, extract_json
from ..writer.research import build_prompt, check_answer

log = logging.getLogger("quick_slide.worker.research")


def _say(message: str) -> str:
    return message.replace("台本を書く", "下調べをする")


def research_manuscript(ctx: Any, job: dict[str, Any], run: dict[str, Any]) -> str | None:
    """依頼から原稿を起こす。原稿ができたか、要らない（原稿のある取り込み）なら None。失敗したら台帳に書いて "failed"。

    原稿ができたら、依頼に書いてあった枚数・目的・読み手を `run["args"]` にもその場で入れる（このあと台本係が読む）。
    """
    store = ctx.store
    if not job.get("intake_id"):
        return None
    intake = ctx.call(store.intake, job["intake_id"])
    if not intake.get("request") or intake["manuscript"]:
        return None
    run_id = run["id"]
    engine = engine_for(run["mode"])
    run_dir = ctx.root.run_dir(run["deck_id"], run_id)
    rounds = ctx.settings.worker.max_check_rounds

    def fail(message: str, kind: str) -> str:
        ctx.call(store.fail_run, run_id, message=message, failure_kind="research_" + kind)
        return "failed"

    available = {"claude": ctx.tools.claude_path(), "codex": ctx.tools.codex_path()}
    try:
        backend, why = choose_backend(ctx.settings.writer, intake["origin_runtime"], available)
    except WriterFailed as exc:
        return fail(_say(exc.message), exc.kind)
    exe = available[backend]
    assert exe is not None
    runner = ask_claude if backend == "claude" else ask_codex
    env = child_env(home=ctx.root.root, extra=ctx.tools.extra_env)

    previous: dict[str, Any] | str | None = None
    problems: list[str] | None = None
    for round_no in range(1, rounds + 1):
        if ctx.stop.is_set():
            return fail("サーバを止めたので、下調べを中断しました。「生成を再開」でもう一度調べます。", "stopped")
        ctx.call(store.set_run_state, run_id, "writing",
                 progress={"phase": "researching", "round": round_no, "max_rounds": rounds, "writer": backend},
                 args_update={"researcher": backend, "researcher_reason": why})
        prompt = build_prompt(engine, request=intake["request"], previous=previous, problems=problems)
        try:
            answer = extract_json(runner(exe, prompt, job_dir=run_dir / "research" / f"round-{round_no}", env=env,
                                         timeout=ctx.settings.worker.writer_timeout_seconds, stop=ctx.stop, web=True))
        except WriterFailed as exc:
            if exc.kind == "format":
                previous, problems = "（前の答えは JSON として読めませんでした）", [
                    "NG: 返したものが JSON のオブジェクト 1 つではなかった。返す JSON の形のオブジェクトだけを返す"]
                continue
            return fail(_say(exc.message), exc.kind)
        del prompt
        clean, ng = check_answer(answer, max_manuscript_chars=ctx.settings.intake.max_manuscript_chars,
                                 extra_secret_patterns=ctx.settings.intake.extra_secret_patterns)
        if clean is None:
            log.info("run %s: 下調べの答えが形に合わない（%d 回目・%d 行）", run_id, round_no, len(ng))
            previous, problems = answer, ng
            continue
        update = ctx.call(store.set_researched, intake["id"], run_id, manuscript=clean["manuscript"],
                          title=clean["title"], purpose=clean["purpose"], audience=clean["audience"],
                          page_count=clean["page_count"], sources=len(clean["sources"]))
        run["args"] = {**run["args"], **update, "researcher": backend, "researcher_reason": why}
        ctx.call(store.set_run_record, run_id, {"research": {
            "researcher": backend, "reason": why, "rounds": round_no, "sources": clean["sources"],
            "page_count": clean["page_count"], "manuscript_chars": len(clean["manuscript"]),
        }})
        return None
    return fail(f"下調べの答えが {rounds} 回とも決まりの形に合いませんでした。依頼を書き直して、もう一度頼んでください。",
                "format")
