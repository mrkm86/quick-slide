"""Node・puppeteer-core・Chrome（headless shell）の在りか（ADR 0016）。Chrome で HTML を開く道具はみなここで引く。

探す順（上から。最初に当たったものを使う）:

| 何 | 1. 環境変数 | 2. 設定（config.yaml） | 3. Quick Slide の置き場 | 4. 前からの置き場 |
|---|---|---|---|---|
| Node | HTML_SLIDE_NODE | node_bin | — | PATH の node |
| Node のモジュール | HTML_SLIDE_NODE_MODULES | node_modules | <QUICK_SLIDE_HOME>/engines/html-slide/node/node_modules | ~/.cache/html-video/node_modules |
| Chrome | HTML_SLIDE_CHROME | chrome（実行ファイルか、入れたフォルダ） | <QUICK_SLIDE_HOME>/engines/html-slide/chrome/ | ~/.cache/puppeteer/ |

- 環境変数と設定は「そこにある」と言われたものなので、無くても次へは落ちない（無いと言って rc=4）
- 3 と 4 は、中身があるほうを使う（モジュールは puppeteer-core があること・Chrome は実行ファイルがあること）。
  どちらにも無ければ、入れる先（3）を「無い」と言う
- Chrome は入れたフォルダの中の一番新しい版（`chrome-headless-shell/<版>/chrome-headless-shell-<機種>/`）
- 見つけた在りかは子の node に環境変数（HTML_SLIDE_NODE_MODULES・HTML_SLIDE_CHROME）で渡す（.mjs は探さない）

`python3 browser.py` は見つけたものを JSON 1 行で出す（導入の確かめ・Quick Slide の doctor が読む）。
"""
from __future__ import annotations

import json
import os
import pathlib
import platform
import re
import shutil
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import places as PL  # noqa: E402

SKILL = "html-slide"
SETUP_HINT = ("入れ方はスキルの references/deploy.md（Quick Slide から使うなら "
              "`python3 <プラグイン>/skills/quick-slide/scripts/quick_slide_setup.py --setup`。Windows は python3 を py -3 に）")


def _cfg(cfg: dict | None) -> dict:
    if cfg is not None:
        return cfg
    try:
        return PL.load_config(SKILL)
    except Exception:  # noqa: BLE001 - 設定が読めなくても在りかは探せる（読めない設定は deck が言う）
        return {}


def product_dir() -> pathlib.Path:
    """Quick Slide の置き場の中の、html-slide の道具の置き場（入れるのは Quick Slide の setup）。"""
    return PL.home() / "engines" / SKILL


def legacy_node_modules() -> pathlib.Path:
    return pathlib.Path.home() / ".cache" / "html-video" / "node_modules"


def legacy_chrome_root() -> pathlib.Path:
    return pathlib.Path.home() / ".cache" / "puppeteer"


def node(cfg: dict | None = None) -> tuple[str | None, str]:
    """(Node の実行ファイル, どこで決まったか)。"""
    cfg = _cfg(cfg)
    for source, value in (("env", os.environ.get("HTML_SLIDE_NODE")), ("config", cfg.get("node_bin"))):
        if value:
            p = pathlib.Path(str(value)).expanduser()
            return (str(p) if p.is_file() else shutil.which(str(value))), source
    return shutil.which("node"), "path"


def node_modules(cfg: dict | None = None) -> tuple[pathlib.Path, str, bool]:
    """(puppeteer-core のある node_modules, どこで決まったか, 入っているか)。"""
    cfg = _cfg(cfg)
    for source, value in (("env", os.environ.get("HTML_SLIDE_NODE_MODULES")), ("config", cfg.get("node_modules"))):
        if value:
            p = pathlib.Path(str(value)).expanduser()
            return p, source, (p / "puppeteer-core").is_dir()
    product = product_dir() / "node" / "node_modules"
    for source, p in (("quick-slide", product), ("legacy", legacy_node_modules())):
        if (p / "puppeteer-core").is_dir():
            return p, source, True
    return product, "quick-slide", False


def _platform_tags() -> tuple[str, ...]:
    """chrome-headless-shell のフォルダ名の機種（@puppeteer/browsers の名付け）。"""
    mach = platform.machine().lower()
    if sys.platform == "darwin":
        return ("mac-arm64",) if mach in ("arm64", "aarch64") else ("mac-x64",)
    if sys.platform.startswith("win"):
        return ("win64", "win32")
    return ("linux64", "linux-arm64") if mach not in ("aarch64", "arm64") else ("linux-arm64", "linux64")


def _version(name: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", name))


def chrome_in(folder: pathlib.Path) -> pathlib.Path | None:
    """入れたフォルダ（`--path` に渡したところ・~/.cache/puppeteer）の中の一番新しい chrome-headless-shell。"""
    folder = pathlib.Path(folder).expanduser()
    base = folder if folder.name == "chrome-headless-shell" else folder / "chrome-headless-shell"
    if not base.is_dir():
        return None
    exe_names = ("chrome-headless-shell.exe", "chrome-headless-shell")
    for d in sorted((d for d in base.iterdir() if d.is_dir()), key=lambda d: _version(d.name), reverse=True):
        for tag in _platform_tags():
            for exe in exe_names:
                p = d / f"chrome-headless-shell-{tag}" / exe
                if p.is_file():
                    return p
    return None


def chrome(cfg: dict | None = None) -> tuple[pathlib.Path | None, str, pathlib.Path]:
    """(Chrome の実行ファイルか None, どこで決まったか, 入れる先・探した先)。"""
    cfg = _cfg(cfg)
    env = os.environ.get("HTML_SLIDE_CHROME")
    if env:
        p = pathlib.Path(env).expanduser()
        return (p if p.is_file() else None), "env", p
    if cfg.get("chrome"):
        p = pathlib.Path(str(cfg["chrome"])).expanduser()
        return (p if p.is_file() else chrome_in(p)), "config", p
    product = product_dir() / "chrome"
    for source, folder in (("quick-slide", product), ("legacy", legacy_chrome_root())):
        exe = chrome_in(folder)
        if exe is not None:
            return exe, source, folder
    return None, "quick-slide", product


def report(cfg: dict | None = None) -> dict:
    cfg = _cfg(cfg)
    nb, nb_src = node(cfg)
    nm, nm_src, nm_ok = node_modules(cfg)
    ch, ch_src, ch_where = chrome(cfg)
    return {"node": {"path": nb, "source": nb_src, "ok": bool(nb)},
            "node_modules": {"path": str(nm), "source": nm_src, "ok": nm_ok},
            "chrome": {"path": str(ch) if ch else None, "where": str(ch_where), "source": ch_src, "ok": ch is not None}}


def missing(cfg: dict | None = None) -> list[str]:
    """足りないもの（`NG:` の後ろに付ける文）。空なら Chrome で HTML を開ける。"""
    r = report(cfg)
    out = []
    if not r["node"]["ok"]:
        out.append("node が無い")
    if not r["node_modules"]["ok"]:
        out.append(f"puppeteer-core が無い（{r['node_modules']['path']}）")
    if not r["chrome"]["ok"]:
        out.append(f"Chrome（headless shell）が無い（{r['chrome']['where']}）")
    return out


def child_env(cfg: dict | None = None, base: dict | None = None) -> dict:
    """子の node に渡す環境（見つけた在りかを環境変数で渡す）。"""
    cfg = _cfg(cfg)
    env = dict(os.environ if base is None else base)
    nm, _, _ = node_modules(cfg)
    env["HTML_SLIDE_NODE_MODULES"] = str(nm)
    ch, _, _ = chrome(cfg)
    if ch is not None:
        env["HTML_SLIDE_CHROME"] = str(ch)
    return env


if __name__ == "__main__":
    for _out in (sys.stdout, sys.stderr):   # Windows の既定（cp932・\r\n）でなく UTF-8・\n で出す。読むのはエージェントとサーバ
        _out.reconfigure(encoding="utf-8", newline="\n")
    print(json.dumps(report(), ensure_ascii=False, sort_keys=True))
