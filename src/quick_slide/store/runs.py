"""台本の版・描いた回・ページごとの結果・ワーカーの仕事（P2）。

流れ（1 つの仕事 = 原稿から 1 冊）:

    仕事（jobs: queued → running → done|failed。途中で止まったら resume_from から続ける）
      └ 描いた回（runs: queued → writing ⇄ checking → rendering → importing → done|failed）
          ├ 台本の版（scripts: 台本係が書くたびに 1 版。検査に通った版を描いた回が指す）
          │   └ ページ（script_pages: ページの番号 → page_ids の同一性・型・見出し）
          └ ページごとの結果（run_pages: pending → drawn → ready|failed。画像は assets の行）

**段は始める前に台帳に書く。** エンジンが終わったこと（importing。終わり方は engine_result_json）と、
出力を取り込んで画面に見せたこと（done）は別の段で、画面が見せるのは取り込み終えた回（done）だけ。
ページの同一性（page_ids）は台本の各ページの `id`（デッキの中で一意）に結ぶので、台本を書き直しても
同じ `id` のページは同じページのまま。

状態の変化は必ず `run_updated` などの出来事と同じトランザクションで書く（画面の実況は `last_event_seq` が
動いたときだけ描き直す）。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from ..enums import (
    DECK_MODES,
    JOB_KINDS,
    JOB_STATES,
    RUN_ACTIVE_STATES,
    RUN_PAGE_STATES,
    RUN_STATES,
    SCRIPT_WRITTEN_BY,
    WRITER_BACKENDS,
    require,
)
from ..errors import StateError, ValidationError
from .base import StoreBase, dump_json, load_json

# 起動したときに「走っている」まま残っていた仕事（前のサーバが途中で止まった）に付ける文
INTERRUPTED_MESSAGE = "台本を書いている途中でサーバが止まったため、中断しました。もう一度会話から頼んでください。"
GAVE_UP_MESSAGE = "サーバが何度も途中で止まったため、作るのをやめました。もう一度会話から頼んでください。"
EDIT_INTERRUPTED_MESSAGE = "直している途中でサーバが止まったため、中断しました。もう一度頼んでください。"
# 依頼から作る取り込み（docs/adr/0002-create-from-screen.md）は、台本がまだ無くても「生成を再開」でやり直せる
# （画面からも会話からも同じ。`store.retry_request_intake`）
RESEARCH_INTERRUPTED_MESSAGE = "下調べの途中でサーバが止まったため、中断しました。「生成を再開」でもう一度調べます。"
REQUEST_INTERRUPTED_MESSAGE = "台本を書いている途中でサーバが止まったため、中断しました。「生成を再開」でもう一度書きます。"
# 続きから戻すのは、この回数まで（止まり続ける仕事を延々と回さない）
MAX_JOB_ATTEMPTS = 3


class RunsMixin(StoreBase):
    # --- 描いた回・仕事を作る（取り込みのトランザクションの中で呼ぶ） ---
    def create_run(self, deck_id: str, *, args: dict[str, Any]) -> dict[str, Any]:
        self._require_tx()
        from ..engines import engine_for

        mode = require(args.get("mode"), DECK_MODES, "runs.mode")
        number = int(self._scalar("SELECT COALESCE(MAX(number),0)+1 FROM runs WHERE deck_id=?", (deck_id,)))
        rid = self.new_id()
        now = self.now()
        out_rel = f"decks/{deck_id}/runs/{rid}"
        self._exec(
            "INSERT INTO runs(id,deck_id,number,mode,engine,args_json,state,out_dir,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (rid, deck_id, number, mode, engine_for(mode).skill, dump_json(args), "queued", out_rel, now, now))
        return self.run(rid)

    def create_job(self, deck_id: str, *, run_id: str, intake_id: str | None, kind: str = "make",
                   request: dict[str, Any] | None = None) -> dict[str, Any]:
        self._require_tx()
        require(kind, JOB_KINDS, "jobs.kind")
        jid = self.new_id()
        self._exec("INSERT INTO jobs(id,deck_id,kind,run_id,intake_id,request_json,state,created_at)"
                   " VALUES(?,?,?,?,?,?,?,?)",
                   (jid, deck_id, kind, run_id, intake_id, dump_json(request or {}), "queued", self.now()))
        return self.job(jid)

    # --- 参照 ---
    def run(self, run_id: str) -> dict[str, Any]:
        row = self._get("runs", run_id)
        row["args"] = load_json(row.pop("args_json"))
        row["progress"] = load_json(row.pop("progress_json"))
        row["record"] = load_json(row.pop("record_json"))
        row["engine_result"] = load_json(row.pop("engine_result_json"))
        return row

    def job(self, job_id: str) -> dict[str, Any]:
        row = self._get("jobs", job_id)
        row["request"] = load_json(row.pop("request_json")) or {}
        return row

    def script(self, script_id: str) -> dict[str, Any]:
        row = self._get("scripts", script_id)
        row["check_notes"] = load_json(row.pop("check_notes_json"))
        return row

    def script_path(self, script_row: dict[str, Any]) -> Path:
        return self.root.root / script_row["path"]

    # --- いまの台本（P3）---
    def head_script(self, deck_id: str) -> dict[str, Any] | None:
        """デッキのいまの台本（画面のページと、次の直しの元）。無ければ None（まだ検査に通った台本が無い）。"""
        sid = self._scalar("SELECT script_id FROM deck_heads WHERE deck_id=?", (deck_id,))
        return None if sid is None else self.script(sid)

    def load_script(self, script_row: dict[str, Any]) -> dict[str, Any]:
        return json.loads(self.script_path(script_row).read_text(encoding="utf-8"))

    def script_pages(self, script_id: str) -> list[dict[str, Any]]:
        """台本の版のページ（画面に出す並び。非表示も入る）。key は台本の `id`。"""
        rows = self._all(
            "SELECT sp.page_no,sp.page_id,p.key,sp.type,sp.title,sp.body,sp.content_sha256,sp.hidden"
            " FROM script_pages sp JOIN page_ids p ON p.id=sp.page_id WHERE sp.script_id=? ORDER BY sp.page_no",
            (script_id,))
        for r in rows:
            r["hidden"] = bool(r["hidden"])
        return rows

    def page_id_for(self, deck_id: str, key: str) -> str | None:
        return self._scalar("SELECT id FROM page_ids WHERE deck_id=? AND key=?", (deck_id, key))

    def _page_id(self, deck_id: str, key: str, now: str) -> str:
        pid = self._scalar("SELECT id FROM page_ids WHERE deck_id=? AND key=?", (deck_id, key))
        if pid is None:
            pid = self.new_id()
            self._exec("INSERT INTO page_ids(id,deck_id,key,created_at) VALUES(?,?,?,?)", (pid, deck_id, key, now))
        return pid

    def accept_script(self, script_id: str, pages: list[dict[str, Any]]) -> None:
        """検査に通った台本の版を、デッキのいまの台本にする。ページ（同一性・見出し・中身の sha256・非表示）を書く。

        `pages` は `scriptdoc.screen_pages` の形。同じ版をもう一度受けても行は重ねない（いまの台本にするだけ）。
        """
        with self.transaction():
            script = self.script(script_id)
            deck_id = script["deck_id"]
            now = self.now()
            have = self._scalar("SELECT COUNT(*) FROM script_pages WHERE script_id=?", (script_id,))
            if not have:
                for p in pages:
                    pid = self._page_id(deck_id, p["key"], now)
                    self._exec("INSERT INTO script_pages(script_id,page_no,page_id,type,title,body,content_sha256,hidden)"
                               " VALUES(?,?,?,?,?,?,?,?)",
                               (script_id, p["no"], pid, p["type"], p["title"], p["body"], p.get("content_sha"),
                                1 if p.get("hidden") else 0))
            self._exec("INSERT INTO deck_heads(deck_id,script_id,updated_at) VALUES(?,?,?)"
                       " ON CONFLICT(deck_id) DO UPDATE SET script_id=excluded.script_id,updated_at=excluded.updated_at",
                       (deck_id, script_id, now))
            self.append_event("script_accepted", {"script_id": script_id, "version": script["version"],
                                                  "pages": len(pages)}, deck_id=deck_id)

    def runs_for_deck(self, deck_id: str) -> list[dict[str, Any]]:
        rows = self._all("SELECT id FROM runs WHERE deck_id=? ORDER BY number DESC", (deck_id,))
        return [self.run(r["id"]) for r in rows]

    def deck_working(self, deck_id: str) -> bool:
        """そのデッキの仕事が待っているか動いているか（画面の `live.working` と完全削除の可否が見る）。"""
        return self._scalar("SELECT 1 FROM jobs WHERE deck_id=? AND state IN ('queued','running') LIMIT 1",
                            (deck_id,)) is not None

    def shown_run(self, deck_id: str) -> dict[str, Any] | None:
        """画面に出す回: **出力を取り込み終えた（done）回のうち、一番新しい台本の版を描いた回**（同じ版なら番号の大きい回）。
        描いている途中・失敗した回は出さない（進みと失敗の理由は帯の 1 文が言う）。

        別のページの直しは同時に走るので（docs/adr/0012）、あとに始まった回が先に描き終わることがある。描いた版で選べば、
        描き終わった順に関わらず、いまの台本に一番近い回が出る。"""
        rid = self._scalar("SELECT r.id FROM runs r LEFT JOIN scripts s ON s.id=r.script_id"
                           " WHERE r.deck_id=? AND r.state='done' ORDER BY COALESCE(s.version,-1) DESC,r.number DESC LIMIT 1",
                           (deck_id,))
        return None if rid is None else self.run(rid)

    def page_key_of(self, page_id: str) -> str | None:
        return self._scalar("SELECT key FROM page_ids WHERE id=?", (page_id,))

    def latest_run(self, deck_id: str) -> dict[str, Any] | None:
        rid = self._scalar("SELECT id FROM runs WHERE deck_id=? ORDER BY number DESC LIMIT 1", (deck_id,))
        return None if rid is None else self.run(rid)

    def run_pages(self, run_id: str) -> list[dict[str, Any]]:
        """回のページ（描いた順。非表示のページは描かないので入らない）。見出し・型・本文は台本のページから、
        状態と絵は回の結果から。ページは台本のページの同一性（page_id）で結ぶ。"""
        rows = self._all(
            "SELECT rp.page_no,rp.page_id,p.key,COALESCE(sp.type,'') AS type,COALESCE(sp.title,'') AS title,"
            "  COALESCE(sp.body,'') AS body,rp.state,rp.asset_id,rp.warnings_json,a.state AS asset_state,"
            "  a.sha256 AS asset_sha256,rp.content_sha256,rp.picture_sha256"
            " FROM run_pages rp JOIN runs r ON r.id=rp.run_id"
            " LEFT JOIN page_ids p ON p.id=rp.page_id"
            " LEFT JOIN script_pages sp ON sp.script_id=r.script_id AND sp.page_id=rp.page_id"
            " LEFT JOIN assets a ON a.id=rp.asset_id"
            " WHERE rp.run_id=? ORDER BY rp.page_no", (run_id,))
        for r in rows:
            r["warnings"] = load_json(r.pop("warnings_json")) if r["warnings_json"] is not None else []
        return rows

    def deck_page_summary(self, deck_id: str) -> dict[str, Any]:
        """カードに出す枚数・できた枚数・先頭の絵（見せる回から）。"""
        run = self.shown_run(deck_id)
        if run is None:
            return {"page_count": 0, "ready_count": 0, "thumbnail_asset_id": None, "run_id": None}
        pages = self.run_pages(run["id"])
        ready = [p for p in pages if p["state"] == "ready" and p["asset_state"] == "ready"]
        first = pages[0] if pages else None
        thumb = first["asset_id"] if first and first["state"] == "ready" and first["asset_state"] == "ready" else None
        return {"page_count": len(pages), "ready_count": len(ready), "thumbnail_asset_id": thumb, "run_id": run["id"]}

    # --- ワーカー: 仕事を取る・終える ---
    @staticmethod
    def _page_local(job: dict[str, Any]) -> set[str] | None:
        """ほかのページの直しと重ねてよい仕事なら、その仕事が触るページ（page_ids の id）。重ねられなければ None。

        重ねてよいのは、ページを指定した直し（`revise` の page・pages。docs/adr/0012-parallel-edits.md）だけ。
        デッキ全体の直し・足す・複製・並べ替え・非表示・版の切り替え・組み直し・1 冊を作る仕事は、ほかと重ねない。"""
        req = job.get("request") or {}
        if job.get("kind") != "edit" or req.get("op") != "revise" or req.get("scope") not in ("page", "pages"):
            return None
        pages = req.get("pages")
        return set(pages) if isinstance(pages, list) and pages and all(isinstance(p, str) for p in pages) else None

    def _can_overlap(self, a: dict[str, Any], b: dict[str, Any]) -> bool:
        """同じデッキの 2 つの仕事を同時に走らせてよいか（どちらもページを指定した直しで、ページが重ならない）。"""
        pa, pb = self._page_local(a), self._page_local(b)
        return pa is not None and pb is not None and not (pa & pb)

    def _claimable(self) -> dict[str, Any] | None:
        """いま取れる、一番古い待ちの仕事（台帳の行）。

        同じデッキで走っている仕事と、**それより前から待っている**同じデッキの仕事の全部と重ねてよいときだけ取れる
        （前から待っている仕事を、あとから来た仕事が追い越して先にぶつからないように）。1 冊を全ページ 1 枚に描く
        デッキ（infographic）は、直しも全ページに効くので重ねない。"""
        rows = self._all("SELECT j.*,d.deleted_at FROM jobs j JOIN decks d ON d.id=j.deck_id"
                         " WHERE j.state IN ('queued','running') ORDER BY j.created_at,j.rowid")
        jobs = []
        for r in rows:
            r = dict(r)
            r["request"] = load_json(r.get("request_json")) or {}
            jobs.append(r)
        one_page_decks: dict[str, bool] = {}
        for i, row in enumerate(jobs):
            if row["state"] != "queued":
                continue
            if row["deleted_at"] is not None:
                return row
            same = [o for o in jobs if o["deck_id"] == row["deck_id"] and o is not row
                    and (o["state"] == "running" or jobs.index(o) < i)]
            if not same:
                return row
            deck = row["deck_id"]
            if deck not in one_page_decks:
                args = self.deck_run_args(deck)   # type: ignore[attr-defined]
                one_page_decks[deck] = args.get("mode") == "pitch" and args.get("style") == "infographic"
            if not one_page_decks[deck] and all(self._can_overlap(row, o) for o in same):
                return row
        return None

    def claim_job(self, worker: str) -> dict[str, Any] | None:
        """待っている仕事を 1 つ取る（古い順）。**削除済みのデッキの仕事は取らずに失敗にする。**

        同じデッキの仕事は、基本は 1 つずつ（前の仕事が終わったあとのいまの台本から始める）。**別のページを指定した直し
        どうしだけは同時に走る**（`_claimable`。台本に当てるのと描くのはワーカーの中でデッキごとに順番。
        docs/adr/0012-parallel-edits.md）。
        """
        with self.transaction():
            while True:
                row = self._claimable()
                if row is None:
                    return None
                now = self.now()
                if row["deleted_at"] is not None:
                    self._finish_job_row(row["id"], "failed", "deck_deleted", now)
                    self._end_run(row["run_id"], "failed", message="スライドが削除されたので、作るのをやめました。",
                                  failure_kind="cancelled")
                    continue
                n = self._exec("UPDATE jobs SET state='running',worker=?,started_at=?,attempts=attempts+1"
                               " WHERE id=? AND state='queued'", (worker, now, row["id"]))
                if n != 1:
                    continue
                self._exec("UPDATE runs SET started_at=COALESCE(started_at,?),updated_at=? WHERE id=?",
                           (now, now, row["run_id"]))
                self.append_event("job_started", {"job_id": row["id"], "run_id": row["run_id"], "kind": row["kind"]},
                                  deck_id=row["deck_id"])
                return self.job(row["id"])

    def finish_job(self, job_id: str, state: str, *, error_class: str | None = None) -> None:
        require(state, JOB_STATES, "jobs.state")
        with self.transaction():
            self._finish_job_row(job_id, state, error_class, self.now())

    def _finish_job_row(self, job_id: str, state: str, error_class: str | None, now: str) -> None:
        job = self._get("jobs", job_id)
        self._exec("UPDATE jobs SET state=?,error_class=?,ended_at=? WHERE id=?", (state, error_class, now, job_id))
        self.append_event("job_finished", {"job_id": job_id, "state": state, "error_class": error_class},
                          deck_id=job["deck_id"])

    def recover_interrupted_jobs(self) -> dict[str, list[str]]:
        """起動時の掃除: 前のサーバで「走っている」まま残った仕事を、**安全な段から続けるか、理由つきで失敗にする**。
        「働いている」のまま残すことはしない。

        | 止まった段 | どうするか |
        |---|---|
        | queued・writing・checking（検査に通った台本がまだ無い） | 失敗にする。台本係の LLM を黙ってもう一度呼ぶと、利用者の知らないところでサブスクの枠を使うため |
        | rendering（台本は検査済み） | 待ちに戻し、描くところから（エンジンは描き終えた絵をキャッシュから使い回す） |
        | importing（エンジンは終わっている） | 待ちに戻し、取り込むところから |

        続きから戻すのは `MAX_JOB_ATTEMPTS` 回まで。待っていた仕事（一度も始まっていない）はそのまま残す。
        """
        out: dict[str, list[str]] = {"requeued": [], "failed": []}
        with self.transaction():
            rows = self._all("SELECT id,run_id,deck_id,attempts,kind FROM jobs WHERE state='running'")
            now = self.now()
            for r in rows:
                run = self._get("runs", r["run_id"])
                stage = {"rendering": "render", "importing": "import"}.get(run["state"])
                # 絵を直す（エンジンの revise）途中で止まった直しは続けない。続きから描き直すと、利用者の知らない
                # ところでもう一度絵を描く（サブスクの枠を使う）うえ、直しが入ったかどうかを黙って決めることになる
                plan = (load_json(run["record_json"]) or {}).get("plan") or {}
                if stage == "render" and r["kind"] == "edit" and plan.get("revise"):
                    stage = None
                if stage is not None and r["attempts"] < MAX_JOB_ATTEMPTS:
                    self._exec("UPDATE jobs SET state='queued',resume_from=?,worker=NULL WHERE id=?", (stage, r["id"]))
                    if stage == "render":
                        self._exec("UPDATE run_pages SET state='pending' WHERE run_id=? AND state!='pending'",
                                   (r["run_id"],))
                    self.append_event("job_requeued", {"job_id": r["id"], "run_id": r["run_id"], "resume_from": stage,
                                                       "attempts": r["attempts"]}, deck_id=r["deck_id"])
                    out["requeued"].append(r["id"])
                    continue
                self._finish_job_row(r["id"], "failed", "interrupted", now)
                if run["state"] in RUN_ACTIVE_STATES:
                    message = GAVE_UP_MESSAGE if stage is not None else (
                        EDIT_INTERRUPTED_MESSAGE if r["kind"] == "edit" else self._make_interrupted_message(r, run))
                    self._end_run(r["run_id"], "failed", message=message, failure_kind="interrupted")
                out["failed"].append(r["id"])
        return out

    def _make_interrupted_message(self, job: dict[str, Any], run: dict[str, Any]) -> str:
        """1 冊を作る仕事が書く段で止まったときの文。依頼から作る取り込みなら「生成を再開」でやり直せると言う。"""
        intake_id = self._scalar("SELECT intake_id FROM jobs WHERE id=?", (job["id"],))
        request = intake_id and self._scalar("SELECT request FROM intakes WHERE id=?", (intake_id,))
        if not request:
            return INTERRUPTED_MESSAGE
        researching = (load_json(run["progress_json"]) or {}).get("phase") == "researching"
        return RESEARCH_INTERRUPTED_MESSAGE if researching else REQUEST_INTERRUPTED_MESSAGE

    def fail_interrupted_jobs(self) -> list[str]:
        """起動時の掃除の口（`server/app.run_startup_sweep`）。戻した仕事も失敗にした仕事も数に入る。"""
        r = self.recover_interrupted_jobs()
        return r["requeued"] + r["failed"]

    # --- 描いた回の状態 ---
    def set_run_state(self, run_id: str, state: str, *, progress: dict[str, Any] | None = None,
                      args_update: dict[str, Any] | None = None) -> None:
        """進行中の状態（writing / checking / rendering）と進み。終わりは `finish_run`。"""
        require(state, RUN_STATES, "runs.state")
        if state not in RUN_ACTIVE_STATES:
            raise ValidationError("終わりの状態は finish_run で書く")
        with self.transaction():
            run = self.run(run_id)
            if run["state"] not in RUN_ACTIVE_STATES:
                raise StateError("終わった回の状態は変えられません")
            prog = run["progress"] if progress is None else progress
            args = run["args"] if args_update is None else {**run["args"], **args_update}
            if run["state"] == state and prog == run["progress"] and args == run["args"]:
                return  # 何も変わっていなければ出来事も書かない（画面をちらつかせない）
            self._exec("UPDATE runs SET state=?,progress_json=?,args_json=?,updated_at=? WHERE id=?",
                       (state, dump_json(prog), dump_json(args), self.now(), run_id))
            self.append_event("run_updated", {"run_id": run_id, "state": state, "progress": prog},
                              deck_id=run["deck_id"])

    def update_run_args(self, run_id: str, update: dict[str, Any]) -> None:
        """回の引数に足す（描く回を始めるときに決めた設定。P4）。状態は変えないので出来事も書かない。"""
        with self.transaction():
            run = self.run(run_id)
            if run["state"] not in RUN_ACTIVE_STATES:
                raise StateError("終わった回の引数は変えられません")
            self._exec("UPDATE runs SET args_json=? WHERE id=?", (dump_json({**run["args"], **update}), run_id))

    def set_run_record(self, run_id: str, record: dict[str, Any]) -> None:
        """再現の記録（エンジンの引数・設定の控え・台本の版・エンジンの印）を足す（同じキーは上書き）。"""
        with self.transaction():
            run = self._get("runs", run_id)
            merged = {**(load_json(run["record_json"]) or {}), **record}
            self._exec("UPDATE runs SET record_json=? WHERE id=?", (dump_json(merged), run_id))

    def enter_importing(self, run_id: str, result: dict[str, Any]) -> None:
        """エンジンが終わった。終わり方を控えてから取り込みの段に入る（取り込みの途中で止まっても、ここから続く）。"""
        with self.transaction():
            run = self.run(run_id)
            if run["state"] not in RUN_ACTIVE_STATES:
                raise StateError("終わった回です")
            self._exec("UPDATE runs SET state='importing',engine_result_json=?,updated_at=? WHERE id=?",
                       (dump_json(result), self.now(), run_id))
            self.append_event("run_updated", {"run_id": run_id, "state": "importing", "rc": result.get("rc")},
                              deck_id=run["deck_id"])

    def engine_result(self, run_id: str) -> dict[str, Any] | None:
        return load_json(self._scalar("SELECT engine_result_json FROM runs WHERE id=?", (run_id,)))

    def mark_run_page(self, run_id: str, page_no: int, state: str) -> None:
        """描いている途中のページの印（drawn = 絵が返ってきた・failed = 描けなかった）。"""
        require(state, RUN_PAGE_STATES, "run_pages.state")
        if state == "ready":
            raise ValidationError("ready は finish_run で書く（絵の行と一緒に）")
        with self.transaction():
            run = self._get("runs", run_id)
            n = self._exec("UPDATE run_pages SET state=? WHERE run_id=? AND page_no=? AND state!=?",
                           (state, run_id, page_no, state))
            if n:
                self.append_event("run_updated", {"run_id": run_id, "page_no": page_no, "page_state": state},
                                  deck_id=run["deck_id"])

    def _end_run(self, run_id: str, state: str, *, message: str | None, failure_kind: str | None,
                 rc: int | None = None) -> None:
        self._require_tx()
        run = self._get("runs", run_id)
        now = self.now()
        self._exec("UPDATE runs SET state=?,rc=?,message=?,failure_kind=?,ended_at=?,updated_at=? WHERE id=?",
                   (state, rc, message, failure_kind, now, now, run_id))
        # 終わった回に「描いている途中」の印を残さない（画面が永遠に「生成中」と言わないように）
        self._exec("UPDATE run_pages SET state='failed' WHERE run_id=? AND state IN ('pending','drawn')", (run_id,))
        self.append_event("run_finished", {"run_id": run_id, "state": state, "rc": rc, "failure_kind": failure_kind},
                          deck_id=run["deck_id"])

    def fail_run(self, run_id: str, *, message: str, failure_kind: str, rc: int | None = None) -> None:
        with self.transaction():
            run = self._get("runs", run_id)
            if run["state"] not in RUN_ACTIVE_STATES:
                return
            self._end_run(run_id, "failed", message=message, failure_kind=failure_kind, rc=rc)

    # --- 台本の版 ---
    def save_script(self, deck_id: str, data: dict[str, Any], *, written_by: str, writer_backend: str | None,
                    intake_id: str | None, run_id: str | None, round_no: int | None = None,
                    instruction: str | None = None) -> dict[str, Any]:
        """台本の版を 1 つ足す（`decks/<デッキ>/scripts/<版>.json` に書いてから行を足す）。"""
        require(written_by, SCRIPT_WRITTEN_BY, "scripts.written_by")
        if writer_backend is not None:
            require(writer_backend, WRITER_BACKENDS, "scripts.writer_backend")
        if not isinstance(data, dict) or not isinstance(data.get("slides"), list):
            raise ValidationError("台本は slides を持つオブジェクト")
        from ..scriptdoc import dumps

        raw = dumps(data)
        with self.transaction():
            version = int(self._scalar("SELECT COALESCE(MAX(version),0)+1 FROM scripts WHERE deck_id=?", (deck_id,)))
            rel = f"decks/{deck_id}/scripts/{version}.json"
            path = self.root.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(raw)
            os.replace(tmp, path)
            sid = self.new_id()
            self._exec(
                "INSERT INTO scripts(id,deck_id,version,path,sha256,written_by,writer_backend,intake_id,instruction,"
                " round,page_count,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, deck_id, version, rel, hashlib.sha256(raw).hexdigest(), written_by, writer_backend, intake_id,
                 instruction, round_no, len(data["slides"]), self.now()))
            self.append_event("script_saved", {"script_id": sid, "version": version, "run_id": run_id,
                                               "written_by": written_by, "writer_backend": writer_backend,
                                               "round": round_no, "pages": len(data["slides"])}, deck_id=deck_id)
            return self.script(sid)

    def set_script_check(self, script_id: str, rc: int, notes: list[str]) -> None:
        with self.transaction():
            self._exec("UPDATE scripts SET check_rc=?,check_notes_json=? WHERE id=?",
                       (rc, dump_json([str(n)[:500] for n in notes[:50]]), script_id))

    def start_rendering(self, run_id: str, script_id: str, pages: list[dict[str, Any]],
                        progress: dict[str, Any]) -> None:
        """検査に通った台本（`accept_script` 済み）を回に結び、描くページ（`scriptdoc.output_pages`）の
        「描いている途中」の行を作る。ページの同一性は台本の `id`（`pages[].key`）で引く。
        """
        with self.transaction():
            run = self.run(run_id)
            script = self.script(script_id)
            if run["deck_id"] != script["deck_id"]:
                raise ValidationError("別のデッキの台本です")
            if run["state"] not in RUN_ACTIVE_STATES:
                raise StateError("終わった回には台本を結べません")
            now = self.now()
            for p in pages:
                pid = self._page_id(run["deck_id"], p["key"], now)
                self._exec("INSERT INTO run_pages(run_id,page_no,page_id,state,content_sha256) VALUES(?,?,?,'pending',?)",
                           (run_id, p["no"], pid, p.get("content_sha")))
            self._exec("UPDATE runs SET script_id=?,state='rendering',progress_json=?,updated_at=? WHERE id=?",
                       (script_id, dump_json(progress), now, run_id))
            self.append_event("run_updated", {"run_id": run_id, "state": "rendering", "script_id": script_id,
                                              "pages": len(pages), "progress": progress}, deck_id=run["deck_id"])

    def finish_run(self, run_id: str, *, state: str, rc: int | None, message: str | None,
                   failure_kind: str | None, pages: dict[int, dict[str, Any]]) -> None:
        """描き終えた回の結果を書く。`pages` は {番号: {"file": 画像のパスか None, "warnings": [...],
        "picture_sha": 絵の sha256（版スタックの鍵。`render/workspace.snapshot`）}}。

        画像は `assets/decks/<デッキ>/<回>/slide-NN.png` にハードリンクで張ってから資産の行にする
        （配信は assets/ の下だけ。エンジンの出力フォルダは配信しない）。

        **同じページ（page_id）の前の回の絵と中身（バイト）が同じなら、前の資産をそのまま使う**（P3: 直していない
        ページの絵の URL を変えない。画面が読み直さない）。照らすのは**同じページの**絵だけ: 別のページがたまたま同じ
        バイトの絵（複製したページ・同じ姿のページ）でも、その資産は使わない（使うとページの絵の URL が別のページの
        資産へ飛び、直していないページの絵が「替わった」ことになる。2026-10-07 の不安定な試験の原因）。
        同じ台本・同じ設定で描き直したページの絵がバイトまで同じことは、両エンジンで確かめた（`docs/P3-NOTES.md`）。
        """
        require(state, ("done", "failed"), "runs.state")
        run = self.run(run_id)
        page_of = {r["page_no"]: r["page_id"] for r in self._all(
            "SELECT page_no,page_id FROM run_pages WHERE run_id=?", (run_id,))}
        staged: dict[int, tuple[str, str, int, int | None, int | None]] = {}
        reused: dict[int, str] = {}
        for no, info in pages.items():
            src = info.get("file")
            if not src:
                continue
            try:
                sha = _file_sha(Path(src))
            except OSError:
                continue
            same = None if page_of.get(no) is None else self._scalar(
                "SELECT a.id FROM run_pages rp JOIN runs r ON r.id=rp.run_id JOIN assets a ON a.id=rp.asset_id"
                " WHERE r.deck_id=? AND rp.page_id=? AND rp.run_id!=? AND a.sha256=? AND a.state='ready'"
                " ORDER BY r.number DESC LIMIT 1",
                (run["deck_id"], page_of[no], run_id, sha))
            if same is not None and (self.root.root / self._get("assets", same)["relative_path"]).is_file():
                reused[no] = same
                continue
            rel = self.root.page_asset_rel(run["deck_id"], run_id, no)
            dst = self.root.root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + ".tmp")
            tmp.unlink(missing_ok=True)
            try:
                os.link(src, tmp)
            except OSError:
                import shutil

                shutil.copyfile(src, tmp)
            os.replace(tmp, dst)
            data = dst.read_bytes()
            w, h = _png_size(data)
            staged[no] = (rel, hashlib.sha256(data).hexdigest(), len(data), w, h)
        with self.transaction():
            current = self._get("runs", run_id)
            if current["state"] not in RUN_ACTIVE_STATES:
                raise StateError("この回はもう終わっています")
            for no, info in pages.items():
                warnings = [str(w)[:300] for w in (info.get("warnings") or [])][:20]
                if no in reused:
                    asset = self._get("assets", reused[no])
                    self._exec("UPDATE run_pages SET state='ready',asset_id=?,warnings_json=?,picture_sha256=?"
                               " WHERE run_id=? AND page_no=?",
                               (asset["id"], dump_json(warnings), info.get("picture_sha") or asset["sha256"], run_id, no))
                elif no in staged:
                    rel, sha, size, w, h = staged[no]
                    asset = self.register_asset(relative_path=rel, media_type="image/png", sha256=sha, bytes_=size,
                                                width=w, height=h)
                    self._exec("UPDATE run_pages SET state='ready',asset_id=?,warnings_json=?,picture_sha256=?"
                               " WHERE run_id=? AND page_no=?",
                               (asset["id"], dump_json(warnings), info.get("picture_sha") or sha, run_id, no))
                else:
                    self._exec("UPDATE run_pages SET state='failed',warnings_json=? WHERE run_id=? AND page_no=?",
                               (dump_json(warnings), run_id, no))
            self._end_run(run_id, state, message=message, failure_kind=failure_kind, rc=rc)
            self._exec("UPDATE decks SET updated_at=? WHERE id=?", (self.now(), run["deck_id"]))


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _png_size(data: bytes) -> tuple[int | None, int | None]:
    """PNG の IHDR から幅と高さ（読めなければ None）。"""
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    return None, None
