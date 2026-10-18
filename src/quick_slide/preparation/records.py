"""`record.preparation`（§3.5）: 世代ごとの段・入力 digest・完了ファイルのハッシュ・許可対象 ID・停止理由。

`set_run_record` はトップレベルのキー単位で併合するので、更新は**既存の preparation 全体を読んで、世代を残したまま全体を保存**する
（読み書きは 1 つのサーバの錠の中。LLM・画像コピーの間は錠を持たず、段の開始と完了だけを短く書く）。
世代の番号はデッキの中で単調に増え、失敗した番号も再利用しない（フォルダの作成そのものが番号の予約）。
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from ..paths import DataRoot

SCHEMA = 1
STAGES = ("intake", "reading", "script", "resolve", "manifest")


class PrepRecord:
    def __init__(self, ctx: Any, run_id: str) -> None:
        self.ctx = ctx
        self.run_id = run_id

    def _mutate(self, fn: Callable[[dict[str, Any]], Any]) -> Any:
        def work() -> Any:
            run = self.ctx.store.run(self.run_id)
            obj = copy.deepcopy((run.get("record") or {}).get("preparation") or {"schema_version": SCHEMA, "generations": {}})
            out = fn(obj)
            self.ctx.store.set_run_record(self.run_id, {"preparation": obj})
            return out

        return self.ctx.call(work)

    def read(self) -> dict[str, Any]:
        run = self.ctx.call(self.ctx.store.run, self.run_id)
        return copy.deepcopy((run.get("record") or {}).get("preparation") or {"schema_version": SCHEMA, "generations": {}})

    def generation(self, n: int) -> dict[str, Any] | None:
        return self.read()["generations"].get(str(n))

    def current(self) -> dict[str, Any] | None:
        obj = self.read()
        cur = obj.get("current")
        return None if cur is None else obj["generations"].get(str(cur))

    def start(self, n: int, preparation_id: str, *, parent: int | None = None) -> None:
        def fn(obj: dict[str, Any]) -> None:
            obj["current"] = n
            obj["generations"][str(n)] = {"generation": n, "preparation_id": preparation_id, "parent": parent,
                                          "state": "running", "stage": None, "running": None, "stages": {},
                                          "input_digest": None, "folder_ids": [], "stop_reason": None}

        self._mutate(fn)

    def begin(self, n: int, stage: str) -> None:
        self._mutate(lambda obj: obj["generations"][str(n)].update(running=stage, state="running"))

    def complete(self, n: int, stage: str, files: dict[str, str] | None = None, **extra: Any) -> None:
        def fn(obj: dict[str, Any]) -> None:
            g = obj["generations"][str(n)]
            g["stages"][stage] = {"files": files or {}, **extra}
            g["stage"], g["running"] = stage, None
            for key in ("input_digest", "folder_ids"):
                if key in extra:
                    g[key] = extra[key]

        self._mutate(fn)

    def invalidate(self, n: int, *stages: str) -> None:
        """やり直す段（と、その後ろ）の完了の印を外す（候補の台本が変わったとき）。"""
        def fn(obj: dict[str, Any]) -> None:
            g = obj["generations"][str(n)]
            for s in stages:
                g["stages"].pop(s, None)
            done = [s for s in STAGES if s in g["stages"]]
            g["stage"] = done[-1] if done else None
            g["state"] = "running"

        self._mutate(fn)

    def finish(self, n: int) -> None:
        self._mutate(lambda obj: obj["generations"][str(n)].update(state="completed", running=None))

    def fail(self, n: int, code: str, message: str) -> None:
        self._mutate(lambda obj: obj["generations"][str(n)].update(state="failed", stop_reason={"code": code, "message": message}))


def allocate_generation(ctx: Any, root: DataRoot, deck_id: str) -> int:
    """デッキの次の世代番号を取り、フォルダを作る（番号の予約。失敗した番号も残るので再利用しない）。"""
    def work() -> int:
        base = root.deck_preparations_dir(deck_id)
        base.mkdir(parents=True, exist_ok=True)
        n = 1 + max((int(p.name[4:]) for p in base.glob("gen-*") if p.name[4:].isdigit()), default=0)
        while True:
            try:
                root.preparation_dir(deck_id, n).mkdir()
                return n
            except FileExistsError:
                n += 1

    return ctx.call(work)


def deck_generations(ctx: Any, deck_id: str) -> list[tuple[str, dict[str, Any]]]:
    """デッキの全回の準備の世代（新しい回・新しい世代から）。(回の ID, 世代の記録)。"""
    def work() -> list[tuple[str, dict[str, Any]]]:
        out = []
        for r in sorted(ctx.store.runs_for_deck(deck_id), key=lambda r: r.get("number", 0), reverse=True):
            full = ctx.store.run(r["id"])
            gens = ((full.get("record") or {}).get("preparation") or {}).get("generations") or {}
            for n in sorted(gens, key=int, reverse=True):
                out.append((r["id"], copy.deepcopy(gens[n])))
        return out

    return ctx.call(work)
