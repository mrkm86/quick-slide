"""エンジンを子プロセスで呼ぶ: 台本の検査（`check`）と、描いて書き出す（`deck`）・1 枚の絵を直す（`revise`）。

`--out` はデッキごとに 1 つの作業フォルダ（`decks/<デッキ>/work/`。P3 から）。両エンジンとも `--out` の直下に
`slides/slide-NN.png`・`deck.pptx`・`deck.pdf`・`thumbs.png` と、`deck-info.json`（html-slide）か
`build.json`（image-slide）を書く（台本の名前のフォルダは足さない）。終わったら描き係が描いた回のフォルダへ
控える（`render/workspace.snapshot`）。エンジンの標準出力・標準エラーは描いた回のフォルダに書く。

進みはエンジンの標準エラーの行から拾う（エンジンは変えない）:

| エンジン | 行 | 意味 |
|---|---|---|
| image-slide | `5枚（描く 3・使い回す 2）` | 描くページの数 |
| image-slide | `  ✓ 3枚目` / `  ✗ 3枚目` | そのページの絵が返った / 描けなかった |
| html-slide | `画像の枠 4（生成 3・描く 2）` | 描く画像の枠の数 |
| html-slide | `  ✓ 2.` / `  ✗ 2.` | 枠の通し番号 2 が描けた / 描けなかった |
| motion-slide | （進みの行は無い。`index: …`・`png: …`・`thumbs: …` は終わりに標準出力へ出る） | 検査と組み立てが終わるまで「組んでいます」 |
| 両方 | `警告: 3枚目…` | そのページの警告 |

motion-slide は全部の拍を Chrome で開いて検査する（1 拍あたり約 2 秒）ので、`check` の時間切れは拍の数で伸ばす（`motion.check_timeout`）。
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
from ..paths import SKILLS_DIR
from ..worker.proc import run_process
from ..worker.tools import Tools

CHECK_TIMEOUT_SECONDS = 300.0

# 導入の確かめと入れる口（skills/quick-slide/scripts/quick_slide_setup.py）。止まっている帯の文がこれを言う
SETUP_SCRIPT = SKILLS_DIR / "quick-slide" / "scripts" / "quick_slide_setup.py"
DOCTOR_HINT = f"足りないものは `python3 {SETUP_SCRIPT} --doctor` で分かります（会話で「Quick Slide の導入を確かめて」と頼んでも同じ）。"
CODEX_LOGIN_MESSAGE = ("画像を描く Codex にログインできていません（または見つかりません）。"
                       "ターミナルで `codex login`（ChatGPT でログイン）をしてから、もう一度頼んでください。" + DOCTOR_HINT)
MOTION_DEPENDENCY_MESSAGE = ("モーションを組むのに要るもの（Playwright の Chromium・Pillow・PyYAML）が足りません。"
                             f"`python3 {SETUP_SCRIPT} --doctor` で足りないものを確かめ、`--setup` で入れてから"
                             "（会話で「Quick Slide の導入をして」と頼んでも入ります）、もう一度頼んでください。")
DEPENDENCY_MESSAGE = ("描くのに要るもの（Node・puppeteer-core・Chrome など）が足りません。"
                      f"`python3 {SETUP_SCRIPT} --doctor` で足りないものを確かめ、`--setup` で入れてから"
                      "（会話で「Quick Slide の導入をして」と頼んでも入ります）、もう一度頼んでください。")
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


def dependency_message(engine: Engine) -> str:
    return MOTION_DEPENDENCY_MESSAGE if engine.mode == "motion" else DEPENDENCY_MESSAGE


def check_script(engine: Engine, *, tools: Tools, home: Path, script: Path, args: dict[str, Any],
                 stop: threading.Event | None = None, quick: bool = False,
                 timeout: float = CHECK_TIMEOUT_SECONDS) -> CheckResult:
    """エンジンの `check`。rc=2 のときの `NG:` の行を返す（台本係に直させる）。

    `quick` は形だけの検査（`Engine.quick_check` のエンジンだけ。Chrome を使わず速い）。"""
    cmd = engine.check_command(tools.python, tools.skills_dir, script, args, quick=quick)
    r = run_process(cmd, cwd=script.parent, env=child_env(home=home, extra=tools.extra_env),
                    timeout=timeout, stop=stop)
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


def _browser_probe(*, tools: Tools, home: Path, cwd: Path, stop: threading.Event | None = None) -> dict[str, Any] | None:
    """html-slide の `browser.py`（Node・puppeteer-core・Chrome の在りかの探し方の正本）の答え。聞けなければ None。"""
    probe = tools.skills_dir / "html-slide" / "scripts" / "browser.py"
    if not probe.is_file():
        return None
    r = run_process([tools.python, str(probe)], cwd=cwd, env=child_env(home=home, extra=tools.extra_env),
                    timeout=AUTH_TIMEOUT_SECONDS, stop=stop)
    if r.rc != RC_OK or r.launch_error or r.timed_out or r.stopped:
        return None
    try:
        data = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def browser_missing(*, tools: Tools, home: Path, cwd: Path, stop: threading.Event | None = None) -> list[str] | None:
    """プレゼン（html-slide）が Chrome で組むのに要るもの（Node・puppeteer-core・Chrome）のうち足りないもの。
    エンジンの `browser.py`（在りかの探し方の正本）に聞く。聞けなければ None（描くときの rc=4 で分かる）。"""
    data = _browser_probe(tools=tools, home=home, cwd=cwd, stop=stop)
    if data is None:
        return None
    return [k for k in ("node", "node_modules", "chrome") if not (data.get(k) or {}).get("ok")]


def chrome_path(*, tools: Tools, home: Path, cwd: Path, stop: threading.Event | None = None) -> str | None:
    """html-slide が使う Chrome の在りか（挿絵を PNG にするのにも使う）。分からなければ None。"""
    data = _browser_probe(tools=tools, home=home, cwd=cwd, stop=stop)
    chrome = (data or {}).get("chrome") or {}
    return chrome.get("path") if chrome.get("ok") and isinstance(chrome.get("path"), str) else None


# motion-slide が組むのに要るもの（Playwright・Pillow・PyYAML のモジュールと、Playwright の Chromium の本体）を 1 回で確かめる。
# 足りなければ 4 で終わり、足りないものの名前を標準出力に 1 行（JSON）で出す
_MOTION_PROBE = (
    "import importlib.util as u, json, os, sys\n"
    "lack = [n for m, n in (('playwright', 'playwright'), ('PIL', 'Pillow'), ('yaml', 'PyYAML')) if u.find_spec(m) is None]\n"
    "if not lack:\n"
    "    try:\n"
    "        from playwright.sync_api import sync_playwright\n"
    "        with sync_playwright() as p:\n"
    "            if not os.path.exists(p.chromium.executable_path):\n"
    "                lack.append('chromium')\n"
    "    except Exception:\n"
    "        lack.append('chromium')\n"
    "print(json.dumps(lack))\n"
    "sys.exit(4 if lack else 0)\n"
)


def motion_missing(*, tools: Tools, home: Path, cwd: Path, stop: threading.Event | None = None) -> list[str] | None:
    """モーションが組むのに要るもののうち足りないもの。聞けなければ None（描くときの rc=4 で分かる）。"""
    # -I でなく -E（PYTHON* の環境変数は無視するが、利用者の場所の site-packages は見る。setup は pip --user で入れる。N23）
    cmd = tools.motion_probe or [tools.python, "-E", "-c", _MOTION_PROBE]
    r = run_process(cmd, cwd=cwd, env=child_env(home=home, extra=tools.extra_env),
                    timeout=AUTH_TIMEOUT_SECONDS, stop=stop)
    if r.launch_error or r.timed_out or r.stopped or r.rc not in (RC_OK, RC_DEPENDENCY):
        return None
    try:
        lack = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError, TypeError):
        return None
    return [str(x) for x in lack] if isinstance(lack, list) else None


def check_prerequisites(engine: Engine, *, tools: Tools, home: Path, cwd: Path,
                        stop: threading.Event | None = None) -> Prerequisites:
    """描く前に 1 回だけ確かめる。台本係を呼ぶ前なので、描けないのにサブスクの枠を使わない。
    ピッチは `image_slide.py auth`（Codex のログイン）。プレゼンは Chrome で組む道具が揃っているか（P5）。"""
    cmd = engine.auth_command(tools.python, tools.skills_dir)
    if cmd is None:
        if engine.mode == "motion":
            cwd.mkdir(parents=True, exist_ok=True)
            lack = motion_missing(tools=tools, home=home, cwd=cwd, stop=stop)
            if lack:
                return Prerequisites(False, RC_DEPENDENCY, MOTION_DEPENDENCY_MESSAGE, "dependency")
            return Prerequisites(True, None if lack is None else 0)
        if engine.mode == "presentation":
            cwd.mkdir(parents=True, exist_ok=True)
            lack = browser_missing(tools=tools, home=home, cwd=cwd, stop=stop)
            if lack:
                return Prerequisites(False, RC_DEPENDENCY, DEPENDENCY_MESSAGE, "dependency")
            return Prerequisites(True, None if lack is None else 0)
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


# 形式の札に出す「いま使えない理由」（`engine_unavailable`）。描く前の確かめより軽く、画面を開くたびに聞ける
QUICK_CHECK_TIMEOUT_SECONDS = 10.0
CODEX_MISSING_REASON = "画像を描く Codex が見つかりません。Codex を入れて `codex login`（ChatGPT でログイン）をすると使えます。"
CODEX_LOGGED_OUT_REASON = "画像を描く Codex にログインしていません。ターミナルで `codex login`（ChatGPT でログイン）をすると使えます。"


def engine_unavailable(engine: Engine, *, tools: Tools, home: Path) -> str | None:
    """その形式でいま作り始められない理由（使えるなら None）。ピッチだけが見る: 全ページを Codex で描くので、
    `image_slide.py auth --quick`（codex が見つかるか・ChatGPT のログインの記録があるか。子プロセスは起こさない）。
    codex の探し方（`IMAGE_SLIDE_CODEX`・設定の `codex_bin`・PATH）の正本はエンジンにあるので、ここでは探さない。
    聞けなかったとき（起動できない・時間切れ）は使える扱いにする（描く前の `check_prerequisites` が拾う）。"""
    if not engine.needs_codex_auth:
        return None
    cmd = [tools.python, str(engine.entry_path(tools.skills_dir)), "auth", "--quick"]
    r = run_process(cmd, cwd=home, env=child_env(home=home, extra=tools.extra_env), timeout=QUICK_CHECK_TIMEOUT_SECONDS)
    if r.launch_error or r.timed_out or r.stopped or r.rc != RC_DEPENDENCY:
        return None
    return CODEX_MISSING_REASON if "見つからない" in "\n".join(r.stderr_tail) else CODEX_LOGGED_OUT_REASON


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
        if self.engine.mode == "motion":
            # 進みの行が無い（検査と組み立ての間は静か）。終わるまで「組んでいます」
            return {"phase": "rendering", "step": "motion", "done": 0, "total": self.pages}
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
            if self.engine.mode == "motion":
                pass   # 進みの行は無い（警告だけ拾う）
            elif self.engine.mode == "pitch":
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
                on_change: Callable[[dict[str, Any], dict[int, str]], None] | None = None,
                command: list[str] | None = None, log_dir: Path | None = None, log_name: str = "engine") -> RenderOutcome:
    """エンジンを 1 回呼ぶ（既定は `deck`。`command` で `revise` も）。`pages[n].file` は `out` の中の絵。"""
    out.mkdir(parents=True, exist_ok=True)
    log_dir = log_dir or out
    log_dir.mkdir(parents=True, exist_ok=True)
    progress = RenderProgress(engine, page_count, on_change)
    cmd = command or engine.deck_command(tools.python, tools.skills_dir, script, out, args)
    r = run_process(cmd, cwd=out, env=child_env(home=home, extra=tools.extra_env), timeout=timeout, stop=stop,
                    on_line=progress.feed, stdout_path=log_dir / f"{log_name}-stdout.txt",
                    stderr_path=log_dir / f"{log_name}-stderr.txt")
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
        if engine.mode == "motion":
            return RenderOutcome("failed", r.rc, MOTION_DEPENDENCY_MESSAGE, "dependency", unfinished)
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
