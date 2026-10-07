"""描き出し係を Python から呼ぶ薄い層。Node（extract.mjs）を subprocess で呼び、pptx_writer で書く。

正式な入口は html_slide.py（check / render / from-video / verify）。ここは関数だけを持つ。

    extract(html_path, mode="contract", out_json=None) -> dict
        HTML（または index.html のあるフォルダ）→ 部品 JSON（形は references/parts-format.md）。
        mode="hyperframes" は html-video の project を読む（段は決めない。from_video.convert が決める）

    check(html_path) -> list[dict]
        決まり違反の一覧 [{slide, el, rule, detail, level}]。level が "error" のものが1つでもあれば rc=2 相当。
        slide は 1 から（0 は HTML 全体の話・字体の読み込みなど）

    render(html_path, out_pptx, mode="contract", *, script=None, build=None, report_path=None, parts_path=None,
           line_breaks=False, force=False) -> dict
        HTML → PPTX。返り値は report（pptx_writer.write の report に violations・report_path を足したもの）。
        - mode="contract": 決まり違反（error）があれば PPTX を書かずに RenderError(rc=2)。force=True なら書く
        - mode="hyperframes": html_path は html-video の project。script（台本 JSON）は要る。build は省けば
          project の隣の build.json
        - report は report_path（省けば out_pptx と同じフォルダの build.json。よその build.json があれば
          <名前>.build.json）に保存する。verify.py --odp がこれを読む
        - 段の数とノートの［クリック］がずれたスライドがあれば、PPTX と report を書いたうえで RenderError(rc=2)
          （force=True なら投げない）
        - parts_path を渡すと、部品 JSON もそこに保存する（調べるとき用）

例外: RenderError(msg, rc, violations)。rc は 1（実行時エラー）/ 2（決まり違反・ずれ）/ 4（Node・Chrome・puppeteer-core が無い）
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True   # 置いた場所（配る物の中・ほかの環境からのリンク先）に __pycache__ を残さない
sys.path.insert(0, str(HERE))

import pptx_writer  # noqa: E402

EXTRACT = HERE / "extract.mjs"
TIMEOUT = 600


class RenderError(Exception):
    def __init__(self, msg: str, rc: int = 1, violations: list | None = None):
        super().__init__(msg)
        self.rc = rc
        self.violations = violations or []


def _html(path) -> Path:
    p = Path(path).expanduser().resolve()
    if p.is_dir():
        p = p / "index.html"
    if not p.exists():
        raise RenderError(f"HTML が無い: {p}", 2)
    return p


def extract(html_path, mode: str = "contract", out_json=None) -> dict:
    node = shutil.which("node")
    if not node:
        raise RenderError("node が無い", 4)
    html = _html(html_path)
    with tempfile.TemporaryDirectory(prefix="html-slide-") as tmp:
        out = Path(out_json) if out_json else Path(tmp) / "parts.json"
        try:
            r = subprocess.run([node, str(EXTRACT), "--mode", mode, str(html), str(out)],
                               capture_output=True, text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            raise RenderError(f"extract.mjs が {TIMEOUT} 秒で終わらない", 1)
        if r.returncode != 0:
            raise RenderError((r.stderr.strip() or r.stdout.strip() or f"extract.mjs rc={r.returncode}"), 4 if r.returncode == 4 else 1)
        return json.loads(out.read_text())


def check(html_path) -> list[dict]:
    return extract(html_path, "contract")["violations"]


def errors(violations: list[dict]) -> list[dict]:
    return [v for v in violations if v.get("level", "error") == "error"]


def format_violation(v: dict) -> str:
    where = f"slide{v['slide']}" if v.get("slide") else "HTML"
    el = f" {v['el']}" if v.get("el") else ""
    return f"{'NG' if v.get('level', 'error') == 'error' else '警告'}: {where}{el}: [{v['rule']}] {v['detail']}"


def default_report_path(out_pptx: Path) -> Path:
    p = out_pptx.parent / "build.json"
    if p.exists():
        try:
            if json.loads(p.read_text()).get("renderer") != "html-slide":
                return out_pptx.with_suffix(".build.json")
        except (json.JSONDecodeError, AttributeError):
            return out_pptx.with_suffix(".build.json")
    return p


def render(html_path, out_pptx, mode: str = "contract", *, script=None, build=None, report_path=None,
           parts_path=None, line_breaks: bool = False, force: bool = False) -> dict:
    out_pptx = Path(out_pptx).expanduser().resolve()
    out_pptx.parent.mkdir(parents=True, exist_ok=True)
    if mode == "contract":
        parts = extract(html_path, "contract")
        errs = errors(parts["violations"])
        if errs and not force:
            raise RenderError(f"決まり違反が {len(errs)} 件", 2, parts["violations"])
    elif mode == "hyperframes":
        import from_video
        project = _html(html_path).parent
        if not script:
            cand = [project.parent / "script.json", project.parent.parent / "script.json"]
            script = next((c for c in cand if c.exists()), None)
            if not script:
                raise RenderError("hyperframes には台本（script）が要る", 2)
        build = build or (project.parent / "build.json")
        parts = extract(project, "hyperframes")
        try:
            from_video.convert(parts, json.loads(Path(script).read_text()),
                               json.loads(Path(build).read_text()) if Path(build).exists() else None)
        except ValueError as e:
            raise RenderError(str(e), 2)
    else:
        raise RenderError(f"mode は contract / hyperframes（{mode!r}）", 2)
    if parts_path:
        Path(parts_path).write_text(json.dumps(parts, ensure_ascii=False))
    report = pptx_writer.write(parts, out_pptx, line_breaks=line_breaks)
    report["violations"] = parts.get("violations") or []
    rp = Path(report_path) if report_path else default_report_path(out_pptx)
    report["report_path"] = str(rp)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    if report["mismatch"] and not force:
        raise RenderError(f"段の数とノートの［クリック］が合わないスライド: {report['mismatch']}", 2, report["violations"])
    return report


def main(argv=None) -> int:
    """試しに呼ぶための最小の CLI（正式な入口は html_slide.py）。"""
    import argparse
    ap = argparse.ArgumentParser(description="html-slide の描き出し係（試し用）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("html")
    r = sub.add_parser("render")
    r.add_argument("html")
    r.add_argument("out")
    r.add_argument("--mode", default="contract", choices=("contract", "hyperframes"))
    r.add_argument("--script")
    r.add_argument("--build")
    r.add_argument("--parts")
    r.add_argument("--line-breaks", action="store_true")
    r.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "check":
            vs = check(a.html)
            for v in vs:
                print(format_violation(v), file=sys.stderr)
            return 2 if errors(vs) else 0
        rep = render(a.html, a.out, a.mode, script=a.script, build=a.build, parts_path=a.parts,
                     line_breaks=a.line_breaks, force=a.force)
        for v in rep["violations"]:
            print(format_violation(v), file=sys.stderr)
        for s in rep["slides"]:
            print(f"slide{s['no']:>2} {s['id']:<6} steps={s['steps']} effects={s['effects']:>2} notes={s['notes_clicks']} {s['transition']}")
        print(json.dumps(rep["totals"], ensure_ascii=False))
        print(rep["pptx"])
        return 0
    except RenderError as e:
        for v in e.violations:
            print(format_violation(v), file=sys.stderr)
        print(f"NG: {e}", file=sys.stderr)
        return e.rc


if __name__ == "__main__":
    sys.exit(main())
