#!/usr/bin/env python3
"""illust — 挿絵の SVG を確かめて、描いてよい形に絞り、PNG にする。

  illust.py check   --svg 挿絵.svg                    確かめるだけ（NG: の行を出す。rc=2）
  illust.py clean   --svg 挿絵.svg [--out 絞った.svg]   アイコンを展開し、描いてよい形に絞った SVG を書く（HTML にそのまま置ける）
  illust.py png     --svg 挿絵.svg --out 挿絵.png [--long 1920] [--chrome PATH]
                                                     絞った SVG を Chrome で PNG にする（長い辺の画素数。既定 1920）
  illust.py palette --primary "#006f83" [--accent "#…"]  ブランドの色から挿絵の配色（JSON）
  illust.py icons                                    使えるアイコンの名前

終了コード: 0 = できた / 2 = 台本の SVG を直す（NG: の行） / 4 = 描く道具（Chrome）が無い / 1 = そのほか。
状態を持たない。ほかのスキルを呼ばない（アイコンの形は共有の置き場 `skills/dads/icons` を読むだけ）。
"""

from __future__ import annotations

import argparse
import colorsys
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import svgclean  # noqa: E402

RC_OK, RC_FAIL, RC_NG, RC_DEPENDENCY = 0, 1, 2, 4
CHROME_TIMEOUT = 60


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _report(c: svgclean.Cleaned) -> int:
    for w in c.warnings:
        print(f"警告: {w}", file=sys.stderr)
    for p in c.problems:
        print(p)
    return RC_NG if c.problems else RC_OK


def find_chrome(given: str | None) -> str | None:
    """Chrome の在りか。`--chrome` → 環境変数 `ILLUST_CHROME` → PATH → よくある置き場（puppeteer の置き場・アプリ）。"""
    for cand in (given, os.environ.get("ILLUST_CHROME")):
        if cand and Path(cand).is_file():
            return cand
    for name in ("chrome-headless-shell", "google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    home = Path.home()
    patterns = [str(home / ".cache/puppeteer/chrome-headless-shell/*/*/chrome-headless-shell*"),
                str(home / ".cache/puppeteer/chrome/*/*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"),
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                r"C:\Program Files\Google\Chrome\Application\chrome.exe"]
    for pat in patterns:
        hits = sorted(p for p in glob.glob(pat) if Path(p).is_file() and not p.endswith((".zip", ".json")))
        if hits:
            return hits[-1]
    return None


def to_png(svg: str, width: float, height: float, out: Path, *, long: int, chrome: str) -> str | None:
    """絞った SVG を PNG にする。うまくいかなければ理由を返す。地は透明（挿絵が地を描かなければ、ページの地が見える）。"""
    scale = long / max(width, height)
    w, h = max(1, round(width * scale)), max(1, round(height * scale))
    with tempfile.TemporaryDirectory(prefix="illust-") as tmp:
        src = Path(tmp) / "illust.svg"
        src.write_text(svgclean.sized(svg, w, h), encoding="utf-8")
        shot = Path(tmp) / "shot.png"
        # スクリプトと外への参照は svgclean が落としてある（Chrome の scriptEnabled=false は撮れなくなるので使わない）
        cmd = [chrome, "--headless", "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--no-default-browser-check",
               "--default-background-color=00000000",
               f"--user-data-dir={Path(tmp) / 'profile'}", f"--window-size={w},{h}", f"--screenshot={shot}", src.as_uri()]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=CHROME_TIMEOUT, encoding="utf-8")
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"Chrome で描けなかった（{exc}）"
        if r.returncode != 0 or not shot.is_file() or shot.stat().st_size == 0:
            return f"Chrome で描けなかった（rc={r.returncode}）"
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = out.with_suffix(".tmp.png")
        shutil.copyfile(shot, tmp_out)
        os.replace(tmp_out, out)
    return None


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in rgb)


def _rgb(color: str) -> tuple[float, float, float]:
    c = color.lstrip("#")
    if len(c) != 6:
        raise ValueError(color)
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _mix(a: tuple[float, float, float], b: tuple[float, float, float], t: float) -> tuple[float, float, float]:
    return tuple(x + (y - x) * t for x, y in zip(a, b))  # type: ignore[return-value]


def palette(primary: str, accent: str | None = None) -> dict[str, str]:
    """挿絵の配色。主役の色と、その淡い段・濃い段、差し色、灰色、人の肌と髪。テーマの色にそろえる。"""
    p, white, black = _rgb(primary), (1.0, 1.0, 1.0), (0.0, 0.0, 0.0)
    h, l, s = colorsys.rgb_to_hls(*p)
    out = {"primary": _hex(p), "primary_dark": _hex(_mix(p, black, 0.3)), "primary_mid": _hex(_mix(p, white, 0.45)),
           "primary_light": _hex(_mix(p, white, 0.75)), "primary_pale": _hex(_mix(p, white, 0.9)),
           "accent": _hex(_rgb(accent)) if accent else _hex(p),
           "ink": "#333333", "gray_dark": "#9a9a9a", "gray": "#dcdcdc", "gray_light": "#f2f2f2", "white": "#ffffff",
           "skin": "#f1d3c1", "hair": "#3b3b3b"}
    if s < 0.08:   # 主役が灰色に近いときは、淡い段が見分けにくいので少し濃いめに
        out["primary_light"] = _hex(_mix(p, white, 0.6))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="illust.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "clean", "png"):
        sp = sub.add_parser(name)
        sp.add_argument("--svg", required=True, help="挿絵の SVG のファイル（- なら標準入力）")
        if name != "check":
            sp.add_argument("--out", required=(name == "png"))
        if name == "png":
            sp.add_argument("--long", type=int, default=1920, help="長い辺の画素数（既定 1920）")
            sp.add_argument("--chrome", default=None, help="Chrome の在りか（既定は探す）")
    pp = sub.add_parser("palette")
    pp.add_argument("--primary", required=True)
    pp.add_argument("--accent", default=None)
    sub.add_parser("icons")
    a = ap.parse_args(argv)

    if a.cmd == "icons":
        print("\n".join(svgclean.icon_names()))
        return RC_OK
    if a.cmd == "palette":
        try:
            print(json.dumps(palette(a.primary, a.accent), ensure_ascii=False, indent=1))
        except ValueError:
            print("NG: 色は #RRGGBB で書く")
            return RC_NG
        return RC_OK
    text = sys.stdin.read() if a.svg == "-" else _read(a.svg)
    c = svgclean.clean(text)
    rc = _report(c)
    if rc != RC_OK or a.cmd == "check":
        return rc
    if a.cmd == "clean":
        if a.out:
            Path(a.out).write_text(c.svg, encoding="utf-8")
        else:
            sys.stdout.write(c.svg + "\n")
        return RC_OK
    chrome = find_chrome(a.chrome)
    if chrome is None:
        print("NG: 挿絵を PNG にする Chrome が見つからない（--chrome か ILLUST_CHROME で在りかを渡す）", file=sys.stderr)
        return RC_DEPENDENCY
    why = to_png(c.svg, c.width, c.height, Path(a.out), long=max(64, min(a.long, 4096)), chrome=chrome)
    if why:
        print(f"NG: {why}", file=sys.stderr)
        return RC_FAIL
    print(json.dumps({"png": str(a.out), "viewBox": [c.width, c.height]}, ensure_ascii=False))
    return RC_OK


if __name__ == "__main__":
    for _out in (sys.stdout, sys.stderr):   # Windows の既定（cp932・\r\n）でなく UTF-8・\n で出す。読むのはエージェントとサーバ
        _out.reconfigure(encoding="utf-8", newline="\n")
    sys.exit(main())
