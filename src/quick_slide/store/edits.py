"""直す（P3）の台帳: 直しの仕事を作る・待っている仕事を読む・ページの版（描いた回をまたいだ姿）を読む。

直しはどれも **新しい描いた回 1 つ** になる（台本の版を進めるか、絵を直すか、その両方をしてから、1 冊を
組み直して描いた回を控える）。直しの中身（何をするか）は `jobs.request_json` の op（`enums.EDIT_OPS`）:

| op | 中身 |
|---|---|
| revise | ページを指定した直し（`pages`・`text`・`edit_mode`・`attachments`・`scope`）。台本係が振り分ける |
| insert | 差し込み（`after` の後ろに。`text`・`attachments`） |
| duplicate / visibility / reorder | ページの複製・非表示/表示・並べ替え（台本係を呼ばない） |
| restore | 版スタックの前の姿に戻す（`page`・`run`） |
| render | いまの台本で描き直す（止まった回の再開・書き出す形式が見せる回に無いとき） |
| rebuild | 設定を替えて組み直す（P5。いまの台本を、いまの・選び直したブランド・スタイルで） |

**1 冊に走る仕事は 1 つだけ**（`claim_job`）。待っている仕事は、走っている仕事が終わったあとのいまの台本から始める。
"""

from __future__ import annotations

from typing import Any

from ..enums import EDIT_OPS, EDIT_SOURCES, require
from ..errors import NotFoundError
from .base import StoreBase, dump_json, load_json

# 直しの回に引き継ぐ、デッキの描き方（モードとエンジンの引数の元）。台本係の選び方は直しごとに決め直す
# `overrides` は会話が言ったスタイル・画風・一言（設定のスタイルより勝つ。P4）。`style` は前の回のエンジンのスタイル
# （全ページを 1 枚に描くかを保つため）。画風・一言・ブランドは回ごとに設定から決め直す（render/look.py）
DECK_ARG_KEYS = ("mode", "style", "image_look", "images", "page_count", "purpose", "audience", "style_note",
                 "origin_runtime", "overrides")


class EditsMixin(StoreBase):
    def deck_run_args(self, deck_id: str) -> dict[str, Any]:
        """そのデッキの描き方（一番新しい回の引数から）。モードは作ったときのまま替えない。"""
        raw = self._scalar("SELECT args_json FROM runs WHERE deck_id=? ORDER BY number DESC LIMIT 1", (deck_id,))
        args = load_json(raw) or {}
        deck = self._get("decks", deck_id)
        out = {k: args.get(k) for k in DECK_ARG_KEYS if k in args}
        out["mode"] = deck["mode"]
        return out

    def create_edit(self, deck_id: str, request: dict[str, Any]) -> dict[str, Any]:
        """直しの仕事を 1 つ作る（描いた回も 1 つ作る）。戻り値 {job, run, ahead（この仕事の前に終わるのを待つ仕事の数）}。

        別のページを指定した直しどうしは待たずに同時に走るので、`ahead` に数えない（docs/adr/0012-parallel-edits.md）。"""
        require(request.get("op"), EDIT_OPS, "edit.op")
        require(request.get("source"), EDIT_SOURCES, "edit.source")
        with self.transaction():
            deck_args = self.deck_run_args(deck_id)
            infographic = deck_args.get("mode") == "pitch" and deck_args.get("style") == "infographic"
            mine = {"kind": "edit", "request": request}
            ahead = sum(1 for other in self.open_jobs(deck_id)
                        if infographic or not self._can_overlap(mine, other))  # type: ignore[attr-defined]
            args = {**deck_args, "edit": request["op"]}
            if request.get("origin_runtime"):
                args["origin_runtime"] = request["origin_runtime"]
            run = self.create_run(deck_id, args=args)
            job = self.create_job(deck_id, run_id=run["id"], intake_id=None, kind="edit", request=request)
            # 指示文は出来事に入れない（文字数と対象の数だけ）
            self.append_event("edit_requested", {
                "job_id": job["id"], "run_id": run["id"], "op": request["op"], "source": request["source"],
                "pages": len(request.get("pages") or []), "text_chars": len(request.get("text") or ""),
                "attachments": len(request.get("attachments") or []), "ahead": ahead,
            }, deck_id=deck_id)
            return {"job": job, "run": run, "ahead": ahead}

    def apply_screen_edit(self, deck_id: str, data: dict[str, Any], pages: list[dict[str, Any]], *, op: str,
                          detail: dict[str, Any]) -> dict[str, Any]:
        """描かない直し（非表示・並べ替え・版の切り替え）: 台本の版を 1 つ進めて、いまの台本にする。回は作らない。

        呼ぶ側は、そのデッキの仕事が待っていない・走っていないことを同じ錠の中で確かめてから呼ぶ（走っている直しは
        古い台本を元に新しい台本を作るので、割り込むと、ここで当てた直しが消える）。戻り値は台本の版の行。
        """
        require(op, ("visibility", "reorder", "restore"), "edit.op")
        with self.transaction():
            script = self.save_script(deck_id, data, written_by="screen", writer_backend=None, intake_id=None,
                                      run_id=None)
            self.set_script_check(script["id"], 0, [])
            self.accept_script(script["id"], pages)
            self.append_event("edit_applied", {"op": op, "script_version": script["version"], **detail}, deck_id=deck_id)
            return script

    def tidy_job_waiting(self, deck_id: str) -> bool:
        """描かない直しの後の組み直し（`render` の `reason: tidy`）が、まだ始まらずに待っているか。待っていれば足さない。"""
        for r in self._all("SELECT request_json FROM jobs WHERE deck_id=? AND state='queued'", (deck_id,)):
            req = load_json(r["request_json"]) or {}
            if req.get("op") == "render" and req.get("reason") == "tidy":
                return True
        return False

    def open_jobs(self, deck_id: str) -> list[dict[str, Any]]:
        """待っている・走っている仕事（古い順）。画面の「待っている差し込み」と、どのページを直している途中かに使う。"""
        rows = self._all("SELECT id FROM jobs WHERE deck_id=? AND state IN ('queued','running') ORDER BY created_at,rowid",
                         (deck_id,))
        return [self.job(r["id"]) for r in rows]

    def job_for_deck(self, deck_id: str, job_id: str) -> dict[str, Any]:
        job = self.job(job_id)
        if job["deck_id"] != deck_id:
            raise NotFoundError("そのデッキの仕事ではない")
        return job

    def run_requests(self, deck_id: str) -> dict[str, dict[str, Any]]:
        """描いた回 → その回を作った頼みごと（版スタックの「指示」の欄に出す）。"""
        out: dict[str, dict[str, Any]] = {}
        for r in self._all("SELECT run_id,kind,request_json FROM jobs WHERE deck_id=?", (deck_id,)):
            out[r["run_id"]] = {"kind": r["kind"], **(load_json(r["request_json"]) or {})}
        return out

    def page_history(self, deck_id: str) -> list[dict[str, Any]]:
        """描き終えた回の、絵のあるページ（新しい回から）。版スタックの材料。"""
        return self._all(
            "SELECT r.id AS run_id,r.number,r.created_at,r.script_id,rp.page_no,rp.page_id,rp.asset_id,"
            "  a.state AS asset_state,a.sha256 AS asset_sha256,rp.content_sha256,rp.picture_sha256"
            " FROM run_pages rp JOIN runs r ON r.id=rp.run_id LEFT JOIN assets a ON a.id=rp.asset_id"
            " WHERE r.deck_id=? AND r.state='done' AND rp.state='ready'"
            " ORDER BY r.number DESC,rp.page_no", (deck_id,))

    def conversation_deck(self, runtime: str, conversation_key: str) -> dict[str, Any] | None:
        """その会話から作った一番新しいデッキ（削除済みは除く）。識別子の生値は HMAC にしてから引く。"""
        h = self.conversation_hash_for(runtime, conversation_key)
        row = self._one("SELECT i.deck_id FROM intakes i JOIN decks d ON d.id=i.deck_id"
                        " WHERE i.conversation_hash=? AND d.deleted_at IS NULL"
                        " ORDER BY i.created_at DESC,i.rowid DESC LIMIT 1", (h,))
        return None if row is None else self.deck(row["deck_id"])

    def set_job_note(self, job_id: str, note: dict[str, Any]) -> None:
        """仕事の頼みごとに、走ってから分かったこと（対象のページの名前など）を足す。"""
        with self.transaction():
            job = self.job(job_id)
            self._exec("UPDATE jobs SET request_json=? WHERE id=?", (dump_json({**job["request"], **note}), job_id))
