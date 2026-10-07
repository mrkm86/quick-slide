"""エンジンを子プロセスで呼ぶ: 台本の検査（`check`）と、描いて書き出す（`deck`）。

`deck` の `--out` は描いた回のフォルダ（`decks/<デッキ>/runs/<回>/`）そのもの。両エンジンとも `--out` の直下に
`slides/slide-NN.png`・`deck.pptx`・`deck.pdf`・`thumbs.png` と、`deck-info.json`（html-slide）か
`build.json`（image-slide）を書く（台本の名前のフォルダは足さない）。

進みはエンジンの標準エラーの行から拾う（エンジンは変えない）:

| エンジン | 行 | 意味 |
|---|---|---|
| image-slide | `5枚（描く 3・使い回す 2）` | 描くページの数 |
| image-slide | `  ✓ 3枚目` / `  ✗ 3枚目` | そのページの絵が返った / 描けなかった |
| html-slide | `画像の枠 4（生成 3・描く 2）` | 描く画像の枠の数 |
| html-slide | `  ✓ 2.` / `  ✗ 2.` | 枠の通し番号 2 が描けた / 描けなかった |
| 両方 | `警告: 3枚目…` | そのページの警告 |
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..childenv import child_env
from ..engines import RC_DEPENDENCY, RC_OK, RC_PARTIAL, RC_SCRIPT, Engine
from ..worker.proc import run_process
from ..worker.tools import Tools

CHECK_TIMEOUT_SECONDS = 300.0

CODEX_LOGIN_MESSAGE = ("画像を描く Codex にログインできていません（または見つかりません）。"
                       "ターミナルで `codex login`（ChatGPT でログイン）をしてから、もう一度頼んでください。")
DEPENDENCY_MESSAGE = ("描くのに要るもの（Node・Chrome など）が足りません。導入の手順"
                      "（skills/html-slide/references/deploy.md）のとおりに入れてから、もう一度頼んでください。")
AUTH_TIMEOUT_SECONDS = 120.0

_PITCH_PLAN = re.compile(r"^(\d+)枚（描く (\d+)・使い回す (\d+)）")
_PITCH_PAGE = re.compile(r"^\s*([✓✗]) (\d+)枚目")
_SLOTS_PLAN = re.compile(r"^画像の枠 (\d+)（生成 (\d+)・描く (\d+)）")
_SLOT_DONE = re.compile(r"^\s*([✓✗]) (\d+)\.")
_PAGE_WARNING = re.compile(r"^(?:警告|NG): (\d+)枚目")


@dataclass
class CheckResult:
    rc: int | None
    problems: list[str]
    warnings: list[str]
    timed_out: bool = False
    stopped: bool = False


def check_script(engine: Engine, *, tools: Tools, home: Path, script: Path, args: dict[str, Any],
                 stop: threading.Event | None = None) -> CheckResult:
    """エンジンの `check`。rc=2 のときの `NG:` の行を返す（台本係に直させる）。"""
    cmd = engine.check_command(tools.python, tools.skills_dir, script, args)
    r = run_process(cmd, cwd=script.parent, env=child_env(home=home, extra=tools.extra_env),
                    timeout=CHECK_TIMEOUT_SECONDS, stop=stop)
    lines = [ln.strip() for ln in r.stderr_tail if ln.strip()]
    rc = None if r.launch_error else r.rc
    return CheckResult(rc=rc, problems=[ln for ln in lines if ln.startswith("NG")],
                       warnings=[ln for ln in lines if ln.startswith("警告")], timed_out=r.timed_out,
                       stopped=r.stopped)


@dataclass
class Prerequisites:
    ok: bool
    rc: int | None
    message: str | None = None
    failure_kind: str | None = None


def check_prerequisites(engine: Engine, *, tools: Tools, home: Path, cwd: Path,
                        stop: threading.Event | None = None) -> Prerequisites:
    """描く前に 1 回だけ確かめる（ピッチは `image_slide.py auth`。プレゼンは描くときの rc=4 で分かる）。"""
    cmd = engine.auth_command(tools.python, tools.skills_dir)
    if cmd is None:
        return Prerequisites(True, None)
    cwd.mkdir(parents=True, exist_ok=True)
    r = run_process(cmd, cwd=cwd, env=child_env(home=home, extra=tools.extra_env), timeout=AUTH_TIMEOUT_SECONDS,
                    stop=stop)
    if r.rc == RC_OK and not (r.timed_out or r.stopped or r.launch_error):
        return Prerequisites(True, 0)
    if r.stopped:
        return Prerequisites(False, r.rc, "サーバを止めたので中断しました。もう一度会話から頼んでください。", "stopped")
    if r.rc == RC_DEPENDENCY or r.timed_out:
        return Prerequisites(False, r.rc, CODEX_LOGIN_MESSAGE, "auth")
    return Prerequisites(False, r.rc, "エンジンを起動できませんでした。導入の手順を確かめてください。", "engine")


class RenderProgress:
    """標準エラーの行から進みを数える。変わったときだけ `on_change(snapshot, page_marks)` を呼ぶ。"""

    def __init__(self, engine: Engine, pages: int,
                 on_change: Callable[[dict[str, Any], dict[int, str]], None] | None = None) -> None:
        self.engine = engine
        self.pages = pages
        self.on_change = on_change
        self.todo: int | None = None
        self.done = 0
        self.failed = 0
        self.marks: dict[int, str] = {}
        self.warnings: dict[int, list[str]] = {}
        self._lock = threading.Lock()

    def snapshot(self) -> dict[str, Any]:
        if self.engine.mode == "pitch":
            if self.todo is None:
                return {"phase": "rendering", "step": "starting", "done": 0, "total": self.pages}
            if self.done + self.failed < self.todo:
                return {"phase": "rendering", "step": "pages", "done": self.done, "total": self.todo}
            return {"phase": "rendering", "step": "finishing", "done": self.done, "total": self.todo}
        if self.todo is None:
            return {"phase": "rendering", "step": "starting", "done": 0, "total": 0}
        if self.done + self.failed < self.todo:
            return {"phase": "rendering", "step": "images", "done": self.done, "total": self.todo}
        return {"phase": "rendering", "step": "compose", "done": self.done, "total": self.todo}

    def feed(self, line: str) -> None:
        changed_marks: dict[int, str] = {}
        with self._lock:
            before = self.snapshot()
            w = _PAGE_WARNING.match(line)
            if w:
                self.warnings.setdefault(int(w.group(1)), []).append(line.split(": ", 1)[-1][:300])
            if self.engine.mode == "pitch":
                m = _PITCH_PLAN.match(line)
                if m:
                    self.todo = int(m.group(2))
                m = _PITCH_PAGE.match(line)
                if m:
                    no = int(m.group(2))
                    if m.group(1) == "✓":
                        self.done += 1
                        changed_marks[no] = "drawn"
                    else:
                        self.failed += 1
                        changed_marks[no] = "failed"
                    self.marks.update(changed_marks)
            else:
                m = _SLOTS_PLAN.match(line)
                if m:
                    self.todo = int(m.group(3))
                m = _SLOT_DONE.match(line)
                if m and self.todo is not None:
                    if m.group(1) == "✓":
                        self.done += 1
                    else:
                        self.failed += 1
            after = self.snapshot()
        if self.on_change is not None and (after != before or changed_marks):
            self.on_change(after, changed_marks)


@dataclass
class RenderOutcome:
    state: str                     # done / failed
    rc: int | None
    message: str | None
    failure_kind: str | None
    pages: dict[int, dict[str, Any]] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        """台帳に控える形（取り込みの途中で止まっても、ここから取り込み直す）。"""
        return {"state": self.state, "rc": self.rc, "message": self.message, "failure_kind": self.failure_kind,
                "pages": {str(k): v for k, v in self.pages.items()}}

    @classmethod
    def from_record(cls, rec: dict[str, Any]) -> "RenderOutcome":
        return cls(rec["state"], rec.get("rc"), rec.get("message"), rec.get("failure_kind"),
                   {int(k): v for k, v in (rec.get("pages") or {}).items()})


def render_deck(engine: Engine, *, tools: Tools, home: Path, script: Path, out: Path, args: dict[str, Any],
                page_count: int, timeout: float, stop: threading.Event | None = None,
                on_change: Callable[[dict[str, Any], dict[int, str]], None] | None = None) -> RenderOutcome:
    out.mkdir(parents=True, exist_ok=True)
    progress = RenderProgress(engine, page_count, on_change)
    cmd = engine.deck_command(tools.python, tools.skills_dir, script, out, args)
    r = run_process(cmd, cwd=out, env=child_env(home=home, extra=tools.extra_env), timeout=timeout, stop=stop,
                    on_line=progress.feed, stdout_path=out / "engine-stdout.txt", stderr_path=out / "engine-stderr.txt")
    pages: dict[int, dict[str, Any]] = {}
    built = _engine_record(engine, out)
    for no in range(1, page_count + 1):
        png = out / "slides" / f"slide-{no:02d}.png"
        warnings = list(progress.warnings.get(no, []))
        status = (built.get(no) or {}).get("status")
        if status == "failed":
            warnings.append("このページを描けませんでした")
        pages[no] = {"file": str(png) if png.is_file() else None, "warnings": warnings}
    # 途中で止まった回の絵は取り込まない（前の回の残りや、書きかけの絵が混ざるため）
    unfinished = {no: {**p, "file": None} for no, p in pages.items()}
    if r.launch_error:
        return RenderOutcome("failed", None, "エンジンを起動できませんでした。導入の手順を確かめてください。", "engine", unfinished)
    if r.stopped:
        return RenderOutcome("failed", r.rc, "サーバを止めたので、描くのを中断しました。もう一度会話から頼んでください。",
                             "stopped", unfinished)
    if r.timed_out:
        return RenderOutcome("failed", r.rc, "描くのが時間内に終わりませんでした。もう一度会話から頼んでください。", "timeout",
                             unfinished)
    if r.rc == RC_DEPENDENCY:
        said = "\n".join(r.stderr_tail).lower()
        if "codex" not in said and any(w in said for w in ("node", "chrome", "puppeteer")):
            return RenderOutcome("failed", r.rc, DEPENDENCY_MESSAGE, "dependency", unfinished)
        return RenderOutcome("failed", r.rc, CODEX_LOGIN_MESSAGE, "auth", unfinished)
    if r.rc == RC_SCRIPT:
        return RenderOutcome("failed", r.rc, "台本をエンジンが受け付けませんでした（検査は通っていました）。もう一度会話から頼んでください。",
                             "script", unfinished)
    if r.rc not in (RC_OK, RC_PARTIAL):
        return RenderOutcome("failed", r.rc, "描いている途中でエンジンが止まりました。もう一度会話から頼んでください。", "engine",
                             unfinished)
    ready = sum(1 for p in pages.values() if p["file"])
    if ready == 0:
        return RenderOutcome("failed", r.rc, f"ページを描けませんでした（{page_count}枚中 0枚）。もう一度会話から頼んでください。",
                             "partial", pages)
    if r.rc == RC_PARTIAL or ready < page_count:
        if engine.mode == "presentation" and ready == page_count:
            message = "描けなかった画像の枠があります（その枠は仮の枠のまま組みました）。"
        else:
            message = f"描けなかったページがあります（{page_count}枚中 {page_count - ready}枚）。"
        return RenderOutcome("done", r.rc, message, "partial", pages)
    return RenderOutcome("done", r.rc, None, None, pages)


def _engine_record(engine: Engine, out: Path) -> dict[int, dict[str, Any]]:
    """エンジンの記録（`build.json` / `deck-info.json`）のページごとの行。読めなければ空。"""
    name = "build.json" if engine.mode == "pitch" else "deck-info.json"
    try:
        data = json.loads((out / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rows = data.get("slides") if isinstance(data, dict) else None
    out_rows: dict[int, dict[str, Any]] = {}
    for row in rows or []:
        if isinstance(row, dict) and isinstance(row.get("no"), int):
            out_rows[row["no"]] = row
    return out_rows
