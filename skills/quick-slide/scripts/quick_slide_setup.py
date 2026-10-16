#!/usr/bin/env python3
"""Quick Slide の導入の確かめ（`--doctor`）と、管理者の権限なしで入れられるものを入れる（`--setup`）。

    python3 quick_slide_setup.py --doctor            # 何が揃っていて何が足りないか（何も書かない）
    python3 quick_slide_setup.py --setup             # 入れる前に、何をどこへ入れるかを出して聞く
    python3 quick_slide_setup.py --setup --yes       # 聞かずに入れる（会話で利用者に聞いてから使う）

見るもの: Python と 4 つのパッケージ（python-pptx・lxml・Pillow・PyYAML）・Node・puppeteer-core・Chrome（headless shell）・
游ゴシックの字体ファイル・Playwright と、その Chromium（**モーションを使うときだけ要る**。`ready.motion`）・
`codex` CLI と ChatGPT のログイン・台本を書く CLI（`claude` / `codex`）・データの置き場・ポート。

入れるもの（`--setup`。どれも Quick Slide の置き場か利用者の場所。全体に入れるもの・ログインはしない。コマンドを言う）:

| 何 | どこへ | どうやって |
|---|---|---|
| Python のパッケージ | 利用者の場所（`pip --user`） | `python3 -m pip install --user …`（Python が「外から管理されている」ときは入れずに手順を言う） |
| puppeteer-core | `<置き場>/engines/html-slide/node/` | `npm install --prefix … puppeteer-core` |
| Chrome（headless shell） | `<置き場>/engines/html-slide/chrome/` | `@puppeteer/browsers install chrome-headless-shell@stable --path …` |
| Playwright（モーション用） | 利用者の場所（`pip --user`） | `python3 -m pip install --user playwright` |
| Playwright の Chromium（モーション用） | Playwright の置き場（`~/Library/Caches/ms-playwright` など） | `python3 -m playwright install chromium` |

Node・`codex` CLI・PowerPoint（字体）は入れない（入れ方を言う）。`codex login` もしない（コマンドを言う）。

`node`・`npm`・`codex`・`claude` は、PATH で見つからなければよくある置き場（npm のグローバルの置き場など）も探す。
サーバとその子も同じ探し方をする（同じ中身の `clidirs.py`）。**Codex の中で走らせたとき**（`CODEX_SANDBOX`・`CODEX_THREAD_ID`
などの印がある）は、入っているものが見えないことがあるので、それらが見つからなくても入れる案内はせず、利用者のターミナルで
`--doctor` を走らせて確かめてもらう（JSON の `inside_codex`）。

出力: 日本語の行のあと、**stdout の最終行は JSON 1 行**。終了コードは 0=揃っている（入れ終えた）/ 2=入力の誤り /
4=足りないものがある（入れられなかった）。標準ライブラリだけを使う（サーバ本体も import しない）。
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import platform
import runpy
import shlex
import shutil
import socket
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_INPUT = 2
EXIT_FAILED = 4

ENV_HOME = "QUICK_SLIDE_HOME"
DEFAULT_PORT = 7801
PYTHON_MIN = (3, 12)   # サーバの SQLite の接続（`autocommit=`）が 3.12 から
NODE_MIN = 18
PACKAGES = (("python-pptx", "pptx"), ("lxml", "lxml"), ("Pillow", "PIL"), ("PyYAML", "yaml"))
# 游ゴシックの在りか。mac は PowerPoint for Mac 同梱、Windows は OS の字体（エンジンの oscompat.yu_gothic_files と同じ決め方）
YU_GOTHIC = (Path(os.environ.get("WINDIR") or r"C:\Windows") / "Fonts" if os.name == "nt"
             else Path("/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts"))
YU_FILES = ("YuGothR.ttc", "YuGothB.ttc")
CHROME_SPEC = "chrome-headless-shell@stable"

SKILLS_DIR = Path(__file__).resolve().parents[2]
# CLI の探し方（サーバと同じ中身）。import せずに読む（配ったプラグインの中に __pycache__ を書かない）
CLI = runpy.run_path(str(Path(__file__).resolve().with_name("clidirs.py")), run_name="clidirs")
BROWSER_PY = SKILLS_DIR / "html-slide" / "scripts" / "browser.py"
# 入れるときに打つコマンド。この診断を動かしている Python そのもので書く（Windows の python3 は Store のスタブで、
# python と py -3 で版も site-packages も違うことがある。A1）
SETUP_CMD = (subprocess.list2cmdline if os.name == "nt" else shlex.join)(
    [sys.executable, str(Path(__file__).resolve()), "--setup"])
DOCTOR_CMD = (subprocess.list2cmdline if os.name == "nt" else shlex.join)(
    [sys.executable, str(Path(__file__).resolve()), "--doctor"])
# Codex が会話の中でコマンドを走らせるときに付く印。どれかがあれば「Codex の中」とみなす（Windows はサンドボックスの印が
# 付かないことがあるので、会話の印も見る）
CODEX_MARKERS = ("CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED", "CODEX_THREAD_ID", "CODEX_SESSION_ID")
# 入れるコマンドに渡す環境。子の Python（pip・playwright）も UTF-8 で出させる（この出力に混ざるので）。
# Playwright は入れるときに「使っていない版」の Chromium を消すが、ほかの道具が使っている版も消えるので消させない
INSTALL_ENV = {**CLI["with_search_path"](os.environ), "PYTHONUTF8": "1", "PLAYWRIGHT_SKIP_BROWSER_GC": "1"}
PLAYWRIGHT_INSTALL_CMD = (subprocess.list2cmdline if os.name == "nt" else shlex.join)(
    [sys.executable, "-m", "playwright", "install", "chromium"])


def data_root(explicit: str | None) -> Path:
    """サーバの `paths.py` と同じ既定（`$QUICK_SLIDE_HOME` → `~/.quick-slide`）。"""
    for value in (explicit, os.environ.get(ENV_HOME)):
        if value:
            return Path(value).expanduser().resolve()
    return (Path.home() / ".quick-slide").resolve()


def inside_codex() -> bool:
    return any(os.environ.get(name) for name in CODEX_MARKERS)


def which(command: str) -> str | None:
    """PATH と、よくある置き場から探す（サーバと同じ探し方）。"""
    return CLI["which"](command)


def maybe_hidden(fix: str) -> str:
    """見つからないときの直し方。Codex の中では、入れる前にターミナルで確かめてもらう（見えないだけのことがある）。"""
    if not inside_codex():
        return fix
    return (f"Codex の中からは、入っているものが見えないことがあります。すぐに入れずに、利用者のターミナルで `{DOCTOR_CMD}` を"
            f"走らせて確かめてください（そこでも見つからなければ: {fix}）")


def say(line: str = "") -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def emit(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")
    sys.stdout.flush()


def run(cmd: list[str], *, env: dict[str, str] | None = None, timeout: float = 20.0) -> tuple[int | None, str]:
    """短いコマンドを走らせ、(終了コード, 標準出力＋標準エラー) を返す。起動できなければ (None, "")。"""
    # 子の Python も UTF-8 で出させる（Windows の既定は cp932）。ほかの道具の出力が UTF-8 でなくても落ちない
    # PATH にはよくある置き場も足す（サーバが子を起こすときと同じ。npm の包みの codex は node を PATH で探す）
    env = {**CLI["with_search_path"](os.environ if env is None else env), "PYTHONUTF8": "1"}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL,
                           encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None, ""
    return r.returncode, (r.stdout or "") + (r.stderr or "")


class Report:
    """確かめた結果。level: ok / warn（無くても動く・一部だけ使えない）/ ng（要る）。"""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, key: str, label: str, level: str, detail: str, *, fix: str | None = None,
            needed_for: tuple[str, ...] = (), **extra: Any) -> dict[str, Any]:
        item = {"id": key, "label": label, "level": level, "ok": level != "ng", "detail": detail, "fix": fix,
                "needed_for": list(needed_for), **extra}
        self.items.append(item)
        return item

    def get(self, key: str) -> dict[str, Any]:
        return next(i for i in self.items if i["id"] == key)

    def ready(self) -> dict[str, bool]:
        def ok(use: str) -> bool:
            return all(i["level"] != "ng" for i in self.items if use in i["needed_for"] or "all" in i["needed_for"])
        return {"presentation": ok("presentation"), "pitch": ok("pitch"), "motion": ok("motion"), "images": ok("images")}


# --- 確かめる ------------------------------------------------------------------

def externally_managed() -> bool:
    """この Python が「外から管理されている」（PEP 668。pip --user が断られる）か。仮想環境なら違う。"""
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return False
    stdlib = sysconfig.get_paths().get("stdlib")
    return bool(stdlib) and (Path(stdlib) / "EXTERNALLY-MANAGED").is_file()


def check_python(rep: Report) -> list[str]:
    v = sys.version_info
    ok = (v.major, v.minor) >= PYTHON_MIN
    rep.add("python", "Python", "ok" if ok else "ng", f"{v.major}.{v.minor}.{v.micro}（{sys.executable}）",
            fix=None if ok else (f"Python {PYTHON_MIN[0]}.{PYTHON_MIN[1]} 以上で走らせてください（Windows は `py -3`。"
                                 "入っていなければ python.org か Homebrew で入れる）"),
            needed_for=("all",))
    import importlib.util
    try:
        from importlib import metadata
    except ImportError:  # pragma: no cover
        metadata = None  # type: ignore[assignment]
    missing = []
    for dist, mod in PACKAGES:
        found = importlib.util.find_spec(mod) is not None
        version = None
        if found and metadata is not None:
            try:
                version = metadata.version(dist)
            except Exception:  # noqa: BLE001 - 版が読めなくても入っている
                version = None
        if not found:
            missing.append(dist)
        rep.add(f"package:{dist}", dist, "ok" if found else "ng", version or ("入っています" if found else "入っていません"),
                fix=None if found else f"{SETUP_CMD}（pip --user で入れます）", needed_for=("all",))
    return missing


def check_node(rep: Report) -> dict[str, Any]:
    node = which("node")
    if not node:
        rep.add("node", "Node", "ng", "見つかりません",
                fix=maybe_hidden(f"Node {NODE_MIN} 以上を入れてください（nodejs.org のインストーラか `brew install node`。管理者の権限が要ることがあるので、Quick Slide は入れません）"),
                needed_for=("presentation",))
        rep.add("npm", "npm", "ng", "見つかりません（Node と一緒に入ります）", needed_for=())
        return {"node": None, "npm": None}
    rc, out = run([node, "--version"])
    version = out.strip() if rc == 0 else "?"
    try:
        major = int(version.lstrip("v").split(".")[0])
    except ValueError:
        major = 0
    ok = major >= NODE_MIN
    rep.add("node", "Node", "ok" if ok else "ng", f"{version}（{node}）",
            fix=None if ok else f"Node {NODE_MIN} 以上に上げてください", needed_for=("presentation",))
    npm = which("npm")
    rep.add("npm", "npm", "ok" if npm else "warn", npm or "見つかりません（puppeteer-core と Chrome を入れるのに使います）")
    return {"node": node, "npm": npm}


def browser_report(home: Path) -> dict[str, Any] | None:
    """html-slide の `browser.py`（在りかの探し方の正本）に聞く。"""
    env = dict(os.environ, **{ENV_HOME: str(home), "PYTHONDONTWRITEBYTECODE": "1"})
    rc, out = run([sys.executable, str(BROWSER_PY)], env=env)
    if rc != 0:
        return None
    try:
        return json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def check_browser(rep: Report, home: Path) -> dict[str, Any] | None:
    found = browser_report(home)
    if found is None:
        rep.add("puppeteer-core", "puppeteer-core", "ng", "探せませんでした（html-slide の browser.py が動きません）",
                needed_for=("presentation",))
        rep.add("chrome", "Chrome（headless shell）", "ng", "探せませんでした", needed_for=("presentation",))
        return None
    where = {"env": "環境変数", "config": "エンジンの設定", "quick-slide": "Quick Slide の置き場", "legacy": "前からの置き場"}
    nm = found["node_modules"]
    rep.add("puppeteer-core", "puppeteer-core", "ok" if nm["ok"] else "ng",
            f"{nm['path']}（{where.get(nm['source'], nm['source'])}）" if nm["ok"] else f"入っていません（入れる先: {nm['path']}）",
            fix=None if nm["ok"] else f"{SETUP_CMD}", needed_for=("presentation",), source=nm["source"])
    ch = found["chrome"]
    rep.add("chrome", "Chrome（headless shell）", "ok" if ch["ok"] else "ng",
            f"{ch['path']}（{where.get(ch['source'], ch['source'])}）" if ch["ok"] else f"入っていません（入れる先: {ch['where']}）",
            fix=None if ch["ok"] else f"{SETUP_CMD}", needed_for=("presentation",), source=ch["source"])
    return found


def engine_config(home: Path, skill: str) -> dict[str, Any]:
    """エンジンの設定（PyYAML があれば読む。無ければ空）。"""
    path = Path(os.environ.get(skill.upper().replace("-", "_") + "_CONFIG") or home / "engines" / skill / "config.yaml")
    if not path.is_file():
        return {}
    try:
        import yaml  # noqa: PLC0415 - 無い機体でも doctor は動かす
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def check_fonts(rep: Report, home: Path) -> None:
    cfg = engine_config(home, "html-slide")
    configured = ((cfg.get("fonts") or {}).get("Yu Gothic") or {}) if isinstance(cfg.get("fonts"), dict) else {}
    if isinstance(configured, dict) and configured.get("regular"):
        files = [Path(str(configured[k])).expanduser() for k in ("regular", "bold") if configured.get(k)]
        ok = all(f.is_file() for f in files)
        rep.add("font", "字体（游ゴシック）", "ok" if ok else "ng",
                "設定の fonts で指定: " + "・".join(str(f) for f in files) + ("" if ok else "（ファイルがありません）"),
                fix=None if ok else "html-slide の設定（config.yaml）の fonts のパスを直してください", needed_for=("presentation",))
        return
    files = [YU_GOTHIC / n for n in YU_FILES]
    if all(f.is_file() for f in files):
        where = "Windows の字体" if os.name == "nt" else "PowerPoint for Mac 同梱"
        rep.add("font", "字体（游ゴシック）", "ok", f"{where}（{YU_GOTHIC}）", needed_for=("presentation",))
        return
    config_path = home / "engines" / "html-slide" / "config.yaml"
    rep.add("font", "字体（游ゴシック）", "ng",
            "PowerPoint for Mac 同梱の游ゴシックが見つかりません" if sys.platform == "darwin" else
            f"Windows の字体のフォルダ（{YU_GOTHIC}）に游ゴシックが見つかりません" if os.name == "nt" else
            "この OS では游ゴシックの場所を探しません",
            fix=(f"PowerPoint for Mac を入れるか、游ゴシックの字体ファイル（regular と bold）の在りかを {config_path} の "
                 "`fonts:` に書いてください（雛形は skills/html-slide/config.example.yaml）。プレゼンは文字の幅をこの字体で測ります"),
            needed_for=("presentation",))


def chromium_path() -> str | None:
    """Playwright の Chromium の本体の在りか（無ければ None）。motion-slide が全部の拍を開くのに使う（html-slide の Chrome とは別）。
    Playwright を子プロセスで起こして聞く（このプロセスに Playwright を読み込まない）。"""
    code = "from playwright.sync_api import sync_playwright\nwith sync_playwright() as p:\n    print(p.chromium.executable_path)\n"
    rc, out = run([sys.executable, "-c", code], env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    path = out.strip().splitlines()[-1] if rc == 0 and out.strip() else ""
    return path if path and Path(path).exists() else None


def check_motion(rep: Report) -> dict[str, Any]:
    """モーション（motion-slide）に要るもの: Playwright（Python）と、その Chromium。**モーションを使うときだけ要る**。"""
    import importlib.util

    have = importlib.util.find_spec("playwright") is not None
    rep.add("playwright", "Playwright（モーションの検査・組み立て用の Python パッケージ）", "ok" if have else "ng",
            "入っています" if have else "入っていません",
            fix=None if have else f"{SETUP_CMD}（pip --user で入れます）。モーションを使わないなら要りません",
            needed_for=("motion",))
    chromium = chromium_path() if have else None
    rep.add("playwright-chromium", "Playwright の Chromium（モーションが全部の拍を開く）", "ok" if chromium else "ng",
            chromium or ("入っていません" if have else "Playwright が無いので確かめられません"),
            fix=None if chromium else f"{SETUP_CMD}（`{PLAYWRIGHT_INSTALL_CMD}`。数百 MB）。モーションを使わないなら要りません",
            needed_for=("motion",))
    return {"playwright": have, "chromium": chromium}


def codex_cli() -> str | None:
    value = os.environ.get("IMAGE_SLIDE_CODEX")
    if value:
        return value if Path(value).is_file() else which(value)
    return which("codex")


def check_codex(rep: Report) -> None:
    codex = codex_cli()
    if not codex:
        rep.add("codex", "codex CLI（絵を描く）", "ng", "見つかりません",
                fix=maybe_hidden("`npm install -g @openai/codex` で入れてください（全体に入れるので Quick Slide は入れません）"),
                needed_for=("pitch", "images"))
        rep.add("codex_login", "ChatGPT のログイン（codex）", "ng", "codex が無いので確かめられません",
                fix=maybe_hidden("codex を入れたあと `codex login`（ChatGPT でログイン）"), needed_for=("pitch", "images"))
        return
    rep.add("codex", "codex CLI（絵を描く）", "ok", codex, needed_for=("pitch", "images"))
    rc, out = run([codex, "login", "status"])
    said = out.lower()
    # 出力そのものは出さない（API キーの一部が載ることがある）。分かったことだけ言う
    if rc == 0 and "chatgpt" in said:
        rep.add("codex_login", "ChatGPT のログイン（codex）", "ok", "ChatGPT でログインしています", needed_for=("pitch", "images"))
    elif rc == 0 and "api key" in said:
        rep.add("codex_login", "ChatGPT のログイン（codex）", "ng", "API キーでログインしています（Quick Slide は ChatGPT のサブスク枠で描きます）",
                fix="ターミナルで `codex logout` のあと `codex login`（ChatGPT でログイン）", needed_for=("pitch", "images"))
    else:
        rep.add("codex_login", "ChatGPT のログイン（codex）", "ng", "ログインしていません（または確かめられません）",
                fix="ターミナルで `codex login`（ChatGPT でログイン）。Quick Slide はログインを代わりにしません",
                needed_for=("pitch", "images"))


def check_writers(rep: Report) -> None:
    claude = os.environ.get("QUICK_SLIDE_CLAUDE") or which("claude")
    codex = os.environ.get("QUICK_SLIDE_CODEX_TEXT") or which("codex")
    found = [n for n, p in (("claude", claude), ("codex", codex)) if p]
    rep.add("writer", "台本を書く CLI（claude / codex）", "ok" if found else "ng",
            "・".join(found) + "（会話しているアプリと同じものを使います）" if found else "どちらも見つかりません",
            fix=None if found else maybe_hidden("Claude Code（`claude`）か Codex（`codex`）を入れて、ログインしてください"),
            needed_for=("all",), found=found)


def check_home(rep: Report, home: Path) -> dict[str, Any]:
    exists = home.is_dir()
    parent = home if exists else next((p for p in home.parents if p.exists()), Path("/"))
    writable = os.access(parent, os.W_OK)
    free = None
    try:
        free = shutil.disk_usage(parent).free
    except OSError:
        pass
    detail = f"{home}（{'あります' if exists else 'まだありません。初めて起動したときに作ります'}）"
    if free is not None:
        detail += f"・空き {free / 1e9:.1f} GB"
    rep.add("home", "データの置き場", "ok" if writable else "ng", detail,
            fix=None if writable else f"{parent} に書き込めません。環境変数 {ENV_HOME} で書ける場所を指してください",
            needed_for=("all",))
    return {"exists": exists, "writable": writable, "free_bytes": free}



# Windows で置き場を読めてはいけない広いグループ（名前は訳されるので SID で比べる。ADR 0012）
WIDE_READERS = {"S-1-1-0": "Everyone", "S-1-5-32-545": "Users", "S-1-5-11": "Authenticated Users"}


def windows_wide_readers(path: Path) -> list[str] | None:
    """置き場（無ければ、在る一番近い親）を読める広いグループの名前。調べられなければ None。

    DACL の「許す」の ACE のうち、読める権利（FILE_READ_DATA・GENERIC_READ・GENERIC_ALL）を持ち、継承用だけでないもの。
    """
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.GetNamedSecurityInfoW.argtypes = (wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
                                             ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
                                             ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
                                             ctypes.POINTER(ctypes.c_void_p))
    advapi.GetAce.argtypes = (ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))
    advapi.ConvertSidToStringSidW.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR))
    kernel.LocalFree.argtypes = (ctypes.c_void_p,)

    class Acl(ctypes.Structure):
        _fields_ = [("AclRevision", ctypes.c_ubyte), ("Sbz1", ctypes.c_ubyte), ("AclSize", ctypes.c_ushort),
                    ("AceCount", ctypes.c_ushort), ("Sbz2", ctypes.c_ushort)]

    target = path if path.exists() else next((q for q in path.parents if q.exists()), None)
    if target is None:
        return None
    dacl, sd = ctypes.c_void_p(), ctypes.c_void_p()
    if advapi.GetNamedSecurityInfoW(str(target), 1, 4, None, None, ctypes.byref(dacl), None, ctypes.byref(sd)) != 0:
        return None   # SE_FILE_OBJECT・DACL_SECURITY_INFORMATION
    try:
        if not dacl.value:
            return ["Everyone"]   # DACL が無い＝誰でも読める
        found: list[str] = []
        for i in range(ctypes.cast(dacl, ctypes.POINTER(Acl)).contents.AceCount):
            ace = ctypes.c_void_p()
            if not advapi.GetAce(dacl, i, ctypes.byref(ace)):
                continue
            ace_type, ace_flags = ctypes.cast(ace, ctypes.POINTER(ctypes.c_ubyte * 2)).contents
            if ace_type != 0 or ace_flags & 0x08:   # ACCESS_ALLOWED_ACE_TYPE だけ・INHERIT_ONLY_ACE は除く
                continue
            mask = ctypes.cast(ace.value + 4, ctypes.POINTER(wintypes.DWORD)).contents.value
            if not mask & (0x0001 | 0x80000000 | 0x10000000):
                continue
            text = wintypes.LPWSTR()
            if advapi.ConvertSidToStringSidW(ctypes.c_void_p(ace.value + 8), ctypes.byref(text)):
                sid = text.value
                kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
                if sid in WIDE_READERS and WIDE_READERS[sid] not in found:
                    found.append(WIDE_READERS[sid])
        return found
    finally:
        kernel.LocalFree(sd)


def windows_long_paths_enabled() -> bool | None:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            return bool(winreg.QueryValueEx(key, "LongPathsEnabled")[0])
    except OSError:
        return None


# 置き場の下に作る一番深いパスの長さの見積もり（decks/<id>/runs/<id>/present/assets/<名前>）
DEEPEST_UNDER_HOME = 180


def check_home_windows(rep: Report, home: Path) -> None:
    """Windows で置き場を守れているか（ADR 0012）。どれも「注意」で、起動は止めない。"""
    readers = windows_wide_readers(home)
    if readers is None:
        rep.add("home_access", "置き場のアクセス権", "warn", "調べられませんでした")
    elif readers:
        rep.add("home_access", "置き場のアクセス権", "warn",
                f"{'・'.join(readers)} が読めます（原稿・画像・ログインの記録を、このパソコンのほかの利用者が読めます）",
                fix=f"置き場をあなたのユーザーのフォルダ（{Path.home()}）の下に置くか、環境変数 {ENV_HOME} で指し直してください")
    else:
        rep.add("home_access", "置き場のアクセス権", "ok", "あなたと管理者だけが読めます")
    clouds = [Path(v) for v in (os.environ.get(n) for n in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")) if v]
    synced = next((c for c in clouds if home == c or c in home.parents), None)
    if synced is not None:
        rep.add("home_cloud", "置き場のクラウド同期", "warn",
                f"OneDrive の同期先（{synced}）の中にあります。原稿や画像がクラウドへ送られます",
                fix=f"環境変数 {ENV_HOME} で、同期しないフォルダ（例: {Path.home() / '.quick-slide'}）を指してください")
    long_paths = windows_long_paths_enabled()
    if long_paths is False and len(str(home)) + DEEPEST_UNDER_HOME > 260:
        rep.add("long_paths", "長いパス", "warn",
                f"置き場のパスが長く（{len(str(home))} 文字）、Windows の長いパス（260 文字を超えるもの）が有効でありません",
                fix=f"環境変数 {ENV_HOME} で短いパスを指すか、Windows の「長いパスを有効にする」（LongPathsEnabled）を有効にしてください")

def settings_listen(home: Path) -> tuple[str, int]:
    try:
        raw = json.loads((home / "config" / "settings.json").read_text(encoding="utf-8"))
        server = raw.get("server") or {}
        port = server.get("port", DEFAULT_PORT)
        host = server.get("host", "127.0.0.1")
        return (host if isinstance(host, str) else "127.0.0.1",
                port if isinstance(port, int) and not isinstance(port, bool) else DEFAULT_PORT)
    except (OSError, ValueError, AttributeError):
        return "127.0.0.1", DEFAULT_PORT


def check_port(rep: Report, home: Path) -> None:
    host, port = settings_listen(home)
    settings = home / "config" / "settings.json"
    if not 0 <= port <= 65535:
        rep.add("port", "ポート", "ng", f"server.port の {port} は、ポートの番号ではありません（0〜65535）",
                fix=f"{settings} の server.port を直してください")
        return
    try:
        record = json.loads((home / "runtime" / "server.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = None
    if port and isinstance(record, dict) and record.get("port") == port:
        try:
            with socket.create_connection((str(record.get("host") or "127.0.0.1"), port), timeout=1.0):
                rep.add("port", "ポート", "ok", f"{port}（Quick Slide が動いています: {record.get('url')}）")
                return
        except (OSError, ValueError, TypeError):   # 壊れた記録（つなげない名前）でも診断は続ける
            pass
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    s = None
    try:
        s = socket.socket(family, socket.SOCK_STREAM)
        s.bind((host, port))   # 0 でも、その名前で待てるかは確かめる
        if port == 0:
            rep.add("port", "ポート", "ok", "起動のたびに空いているポートを使う設定（server.port が 0）")
        else:
            rep.add("port", "ポート", "ok", f"{host}:{port} は空いています")
    except (ValueError, TypeError):   # 設定の名前が、そもそもアドレスとして読めない（ソケットは TypeError も投げる）
        rep.add("port", "ポート", "ng", f"{host!r}:{port} は、その名前・アドレスでは待てません",
                fix=f"{settings} の server.host を直してください")
    except OSError as exc:
        if s is None:   # 名前やポートの話ではなく、この機体がいまソケットを作れない
            rep.add("port", "ポート", "warn", f"確かめられませんでした（ソケットを作れませんでした: {exc}）")
            return
        busy = exc.errno in (errno.EADDRINUSE, errno.EACCES)
        rep.add("port", "ポート", "warn" if busy else "ng",
                f"{host}:{port} は{'ほかのアプリが使っています' if busy else 'その名前・アドレスでは待てません'}",
                fix=f"そのアプリを止めるか、{settings} の server.port を変えてください")
    finally:
        if s is not None:
            s.close()


def _server_get(host: str, port: int, path: str, *, timeout: float = 5.0) -> dict[str, Any] | None:
    """動いているサーバに GET する（記録の host・port に直接。表示用の URL・プロキシ・転送は使わない）。
    200 の JSON オブジェクトでなければ None。"""
    import http.client
    shown = f"[{host}]" if ":" in host and not host.startswith("[") else host
    conn = None
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", path, headers={"Host": f"{shown}:{port}"})
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            return None
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        if conn is not None:
            conn.close()
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _resource_check_shape(result: dict[str, Any]) -> bool:
    """resource-check の応答の形を確かめる（別のアプリの応答や壊れた応答を「全部ある」と読まない）。"""
    def count(v: Any) -> bool:
        return type(v) is int and v >= 0
    missing = result.get("missing")
    return (count(result.get("templates")) and count(result.get("resources")) and isinstance(missing, list)
            and all(isinstance(m, dict) and all(isinstance(m.get(k), str) for k in ("template_name", "resource_name"))
                    for m in missing))


def _server_record(home: Path) -> tuple[str, int, str] | None:
    """サーバの記録（runtime/server.json）から、つなぐ先と instance_id を読む。形が違えば None（診断は続ける）。"""
    try:
        record = json.loads((home / "runtime" / "server.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    host, port, instance = record.get("host"), record.get("port"), record.get("instance_id")
    if not (isinstance(host, str) and host and host.isprintable() and not any(c.isspace() for c in host)):
        return None
    if not (type(port) is int and 0 < port < 65536):
        return None
    if not (isinstance(instance, str) and instance):
        return None
    return host, port, instance


def check_template_resources(rep: Report, home: Path) -> None:
    """デザインテンプレートのリソース（登録したファイル・フォルダ）が、この機体にあるかだけを見る。
    データはサーバが持つので、動いているサーバに聞く（サーバを起こさない・データベースを直接開かない）。
    見つからなくても、そのテンプレートを使う資料のほかには効かないので「注意」にとどめる。"""
    label = "デザインテンプレートのリソース"
    record = _server_record(home)
    # 古い記録のポートを別のサーバが使っていることがある。記録したそのサーバだけに聞く
    health = _server_get(*record[:2], "/api/health") if record else None
    if record is None or health is None or health.get("instance_id") != record[2]:
        rep.add("template_resources", label, "ok", "サーバが動いていないので確かめていません（サーバが動いているときに確かめます）")
        return
    result = _server_get(*record[:2], "/api/settings/design-templates/resource-check")
    if result is None or not _resource_check_shape(result):
        rep.add("template_resources", label, "warn", "確かめられませんでした（サーバの応答を読めませんでした）",
                fix="Quick Slide を新しい版にしてから、もう一度確かめてください")
        return
    missing = result["missing"]
    if not missing:
        rep.add("template_resources", label, "ok",
                f"テンプレート {result['templates']} 件・リソース {result['resources']} 件とも、この機体にあります")
        return
    names = "／".join(f"{m['template_name']}: {m['resource_name']}" for m in missing[:5]) + ("ほか" if len(missing) > 5 else "")
    rep.add("template_resources", label, "warn", f"{len(missing)} 件が見つかりません（{names}）",
            fix="設定の画面で、そのテンプレートのリソースのパスを直してください", missing=missing)


def doctor(home: Path) -> tuple[Report, dict[str, Any]]:
    rep = Report()
    facts: dict[str, Any] = {}
    facts["missing_packages"] = check_python(rep)
    facts.update(check_node(rep))
    facts["browser"] = check_browser(rep, home)
    check_fonts(rep, home)
    facts["motion"] = check_motion(rep)
    check_codex(rep)
    check_writers(rep)
    facts["home"] = check_home(rep, home)
    if os.name == "nt":
        check_home_windows(rep, home)
    check_port(rep, home)
    check_template_resources(rep, home)
    return rep, facts


MARK = {"ok": "OK", "warn": "注意", "ng": "足りない"}


def print_report(rep: Report, home: Path) -> None:
    say(f"Quick Slide の導入の確かめ（データの置き場: {home}・{platform.system()} {platform.machine()}）")
    if inside_codex():
        say("  ※ Codex の中で確かめています。Node・codex・claude が「足りない」と出ても、入っているのに見えないだけのことがあります")
    for i in rep.items:
        say(f"  [{MARK[i['level']]}] {i['label']}: {i['detail']}")
        if i["fix"] and i["level"] != "ok":
            say(f"         → {i['fix']}")
    ready = rep.ready()
    say("使えるもの: " + "・".join(
        f"{name}{'（使える）' if ok else '（まだ）'}" for name, ok in
        (("プレゼン", ready["presentation"]), ("ピッチ", ready["pitch"]), ("モーション", ready["motion"]),
         ("画像の枠を描く", ready["images"]))))
    if inside_codex():
        # 描くのはサーバ（利用者のターミナルで起こしたもの）で、砂場の中から見えたものでは決まらない
        say("  ※ これは Codex の中から見えたものだけでの判断です。利用者のターミナルで起こしたサーバは、ターミナルの環境で描きます"
            "（描けるかは画面で確かめてください）")


# --- 入れる --------------------------------------------------------------------

def plan_steps(rep: Report, facts: dict[str, Any], home: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """入れる手順（コマンドつき）と、Quick Slide が入れないもの（人がすること）。"""
    steps: list[dict[str, Any]] = []
    manual: list[str] = []
    engine = home / "engines" / "html-slide"
    missing = facts.get("missing_packages") or []
    if missing:
        if externally_managed():
            manual.append(f"Python のパッケージ（{'・'.join(missing)}）: この Python（{sys.executable}）は外から管理されているので、"
                          "pip --user では入れません。Homebrew の Python なら `brew install python-lxml pillow` などで入れるか、"
                          f"`{sys.executable} -m pip install --user --break-system-packages {' '.join(missing)}` を自分で判断して実行してください")
        else:
            steps.append({"id": "packages", "what": f"Python のパッケージ（{'・'.join(missing)}）を利用者の場所に入れる（pip --user）",
                          "cmd": [sys.executable, "-m", "pip", "install", "--user", *missing]})
    found = facts.get("browser") or {}
    npm = facts.get("npm")
    node_bin = facts.get("node")
    nm = (found.get("node_modules") or {}) if found else {}
    ch = (found.get("chrome") or {}) if found else {}
    if not node_bin:
        manual.append("Node: " + maybe_hidden("nodejs.org のインストーラか `brew install node` で入れてください（そのあと、もう一度 --setup）"))
    target_nm = engine / "node" / "node_modules"
    if node_bin and nm and not nm.get("ok"):
        if nm.get("source") in ("env", "config"):
            manual.append(f"puppeteer-core: {'環境変数' if nm['source'] == 'env' else 'エンジンの設定'}が {nm['path']} を指しています。"
                          "そこに入れるか、指定を外してください")
        elif not npm:
            manual.append("puppeteer-core: npm が無いので入れられません（Node と一緒に入ります）")
        else:
            steps.append({"id": "puppeteer-core", "what": f"puppeteer-core を {engine / 'node'} に入れる（npm・全体には入れない）",
                          "cmd": [npm, "install", "--prefix", str(engine / "node"), "--no-audit", "--no-fund", "puppeteer-core"]})
    if node_bin and ch and not ch.get("ok"):
        if ch.get("source") in ("env", "config"):
            manual.append(f"Chrome: {'環境変数' if ch['source'] == 'env' else 'エンジンの設定'}が {ch['where']} を指しています。"
                          "そこに入れるか、指定を外してください")
        else:
            steps.append({"id": "chrome", "what": f"Chrome（headless shell）を {engine / 'chrome'} に入れる（@puppeteer/browsers・全体には入れない）",
                          "cmd": None, "path": str(engine / "chrome"), "node_modules": str(target_nm)})
    motion = facts.get("motion") or {}
    if motion and not motion.get("playwright"):
        if externally_managed():
            manual.append("Playwright（モーションに要る）: この Python は外から管理されているので pip --user では入れません。"
                          f"`{sys.executable} -m pip install --user --break-system-packages playwright` を自分で判断して実行してください"
                          "（モーションを使わないなら要りません）")
        else:
            steps.append({"id": "playwright", "what": "Playwright（モーションの検査・組み立て用。利用者の場所へ pip --user）を入れる",
                          "cmd": [sys.executable, "-m", "pip", "install", "--user", "playwright"]})
    if motion and not motion.get("chromium") and (motion.get("playwright") or not externally_managed()):
        steps.append({"id": "playwright-chromium", "what": "Playwright の Chromium（モーションが全部の拍を開く。数百 MB）を入れる",
                      "cmd": [sys.executable, "-m", "playwright", "install", "chromium"]})
    for key in ("codex", "codex_login", "font", "writer"):
        item = rep.get(key)
        if item["level"] == "ng" and item["fix"]:
            manual.append(f"{item['label']}: {item['fix']}")
    return steps, manual


def browsers_command(step: dict[str, Any], npm: str | None, node_bin: str | None) -> list[str] | None:
    """Chrome を入れるコマンド。puppeteer-core と一緒に入る @puppeteer/browsers を使う（無ければ npx で取る）。"""
    nm = Path(step["node_modules"])
    # Windows の npm は .bin に bash 用の browsers と browsers.cmd を両方置く。bash 用は Windows では起こせない
    names = ("browsers.cmd", "browsers") if os.name == "nt" else ("browsers", "browsers.cmd")
    for exe in (nm / ".bin" / n for n in names):
        if exe.is_file():
            return [str(exe), "install", CHROME_SPEC, "--path", step["path"]]
    npx = which("npx") or (str(Path(npm).with_name("npx")) if npm else None)
    if npx and Path(npx).exists():
        return [npx, "--yes", "@puppeteer/browsers", "install", CHROME_SPEC, "--path", step["path"]]
    return None


def ask(question: str) -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        return input(question + " [y/N] ").strip().lower() in ("y", "yes", "はい")
    except EOFError:
        return False


def setup(home: Path, *, yes: bool) -> int:
    rep, facts = doctor(home)
    print_report(rep, home)
    steps, manual = plan_steps(rep, facts, home)
    say()
    if steps:
        say("入れるもの（管理者の権限は要りません。全体には入れません）:")
        for i, st in enumerate(steps, 1):
            say(f"  {i}. {st['what']}")
            if st.get("cmd"):
                say(f"     $ {' '.join(st['cmd'])}")
    else:
        say("Quick Slide が入れるものはありません。")
    if manual:
        say("自分ですること（Quick Slide は代わりにしません）:")
        for m in manual:
            say(f"  - {m}")
    done: list[dict[str, Any]] = []
    if steps and not yes and not ask("上のものを入れますか？"):
        say("入れませんでした（--yes を付けると聞かずに入れます）。")
        emit({"status": "setup", "ok": False, "installed": [], "planned": [s["id"] for s in steps], "manual": manual,
              "home": str(home), "message": "入れていません（確かめてから --setup --yes）"})
        return EXIT_FAILED
    for st in steps:
        cmd = st.get("cmd")
        if st["id"] == "chrome":
            cmd = browsers_command(st, facts.get("npm"), facts.get("node"))
            if cmd is None:
                say("  Chrome を入れるコマンド（@puppeteer/browsers）が見つかりません。puppeteer-core を先に入れてください。")
                done.append({"id": st["id"], "ok": False})
                continue
        say(f"→ {st['what']}")
        say(f"  $ {' '.join(cmd)}")
        if st["id"] in ("puppeteer-core", "chrome"):
            Path(st.get("path") or (home / "engines" / "html-slide" / "node")).mkdir(parents=True, exist_ok=True)
        try:
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, timeout=1800, env=INSTALL_ENV)
            ok = r.returncode == 0
        except (OSError, subprocess.SubprocessError) as exc:
            say(f"  動きませんでした（{exc.__class__.__name__}）")
            ok = False
        done.append({"id": st["id"], "ok": ok})
        say("  できました" if ok else "  入れられませんでした（上の出力を見てください）")
    say()
    rep2, _ = doctor(home)
    print_report(rep2, home)
    ready = rep2.ready()
    ok = all(d["ok"] for d in done) and all(i["level"] != "ng" for i in rep2.items)
    emit({"status": "setup", "ok": ok, "installed": done, "manual": manual, "home": str(home), "ready": ready,
          "checks": rep2.items})
    return EXIT_OK if ok else EXIT_FAILED


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="quick_slide_setup.py", description="Quick Slide の導入の確かめ（--doctor）と入れる（--setup）")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--doctor", action="store_true", help="何が揃っていて何が足りないかを出す（既定。何も書かない）")
    g.add_argument("--setup", action="store_true", help="管理者の権限なしで入れられるものを Quick Slide の置き場に入れる")
    p.add_argument("--yes", action="store_true", help="--setup で聞かずに入れる（会話で利用者に聞いてから使う）")
    p.add_argument("--home", help=f"データの置き場（既定: ${ENV_HOME} または ~/.quick-slide）")
    args = p.parse_args(argv)
    if args.yes and not args.setup:
        emit({"status": "error", "code": "yes_without_setup", "message": "--yes は --setup と一緒に使います。"})
        return EXIT_INPUT
    home = data_root(args.home)
    if args.setup:
        return setup(home, yes=args.yes)
    rep, _ = doctor(home)
    print_report(rep, home)
    ready = rep.ready()
    ok = all(i["level"] != "ng" for i in rep.items)
    emit({"status": "doctor", "ok": ok, "ready": ready, "home": str(home), "checks": rep.items,
          "setup": None if ok else SETUP_CMD, "inside_codex": inside_codex()})
    return EXIT_OK if ok else EXIT_FAILED


if __name__ == "__main__":
    for _out in (sys.stdout, sys.stderr):   # Windows の既定（cp932・\r\n）でなく UTF-8・\n で出す。読むのはエージェントとサーバ
        _out.reconfigure(encoding="utf-8", newline="\n")
    raise SystemExit(main())
