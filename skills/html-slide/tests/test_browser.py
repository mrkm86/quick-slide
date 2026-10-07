#!/usr/bin/env python3
"""Node のモジュールと Chrome の在りか（scripts/browser.py・ADR 0016）のテスト。`python3 test_browser.py <名前>`。

本物の Chrome は起こさない。置き場は使い捨てのフォルダに作った空のファイルで見立てる（HOME も差し替える）。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(SKILL / "scripts"))

import browser as B  # noqa: E402

TMP = pathlib.Path(tempfile.mkdtemp(prefix="html-slide-browser-")).resolve()
KEYS = ("HOME", "QUICK_SLIDE_HOME", "HTML_SLIDE_NODE", "HTML_SLIDE_NODE_MODULES", "HTML_SLIDE_CHROME", "HTML_SLIDE_CONFIG")


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


@contextlib.contextmanager
def env(**values):
    saved = {k: os.environ.get(k) for k in KEYS}
    for k in KEYS:
        os.environ.pop(k, None)
    os.environ.update({k: str(v) for k, v in values.items()})
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def fake_chrome(folder: pathlib.Path, version: str) -> pathlib.Path:
    tag = B._platform_tags()[0]
    prefix = {"mac-arm64": "mac_arm", "mac-x64": "mac", "linux64": "linux", "linux-arm64": "linux_arm",
              "win64": "win64", "win32": "win32"}[tag]
    exe = folder / "chrome-headless-shell" / f"{prefix}-{version}" / f"chrome-headless-shell-{tag}" / "chrome-headless-shell"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("")
    exe.chmod(0o755)
    return exe


def fake_modules(folder: pathlib.Path) -> pathlib.Path:
    (folder / "puppeteer-core").mkdir(parents=True, exist_ok=True)
    return folder


def case_order():
    """環境変数 → 設定 → Quick Slide の置き場 → 前からの置き場。3 と 4 は中身があるほう。"""
    home = TMP / "order" / "home"
    qs = TMP / "order" / "qs"
    legacy_nm = fake_modules(home / ".cache" / "html-video" / "node_modules")
    legacy_ch = fake_chrome(home / ".cache" / "puppeteer", "131.0.1")
    with env(HOME=home, QUICK_SLIDE_HOME=qs):
        nm, src, ok = B.node_modules({})
        check((nm, src, ok) == (legacy_nm, "legacy", True), (nm, src, ok))
        ch, src, _ = B.chrome({})
        check((ch, src) == (legacy_ch, "legacy"), (ch, src))
        # Quick Slide の置き場に入れたら、そちらが先
        product_nm = fake_modules(qs / "engines" / "html-slide" / "node" / "node_modules")
        product_ch = fake_chrome(qs / "engines" / "html-slide" / "chrome", "152.0.7")
        check(B.node_modules({})[:2] == (product_nm, "quick-slide"), B.node_modules({}))
        check(B.chrome({})[:2] == (product_ch, "quick-slide"), B.chrome({}))
        # 設定は置き場より先
        cfg_nm = fake_modules(TMP / "order" / "cfg-nm")
        cfg_ch = fake_chrome(TMP / "order" / "cfg-ch", "140.0.1")
        cfg = {"node_modules": str(cfg_nm), "chrome": str(TMP / "order" / "cfg-ch")}
        check(B.node_modules(cfg)[:2] == (cfg_nm, "config"), B.node_modules(cfg))
        check(B.chrome(cfg)[:2] == (cfg_ch, "config"), B.chrome(cfg))
        check(B.chrome({"chrome": str(cfg_ch)})[:2] == (cfg_ch, "config"), "実行ファイルを直に書いても読む")
    # 環境変数は設定より先。無くても次へ落ちない（無いと言う）
    with env(HOME=home, QUICK_SLIDE_HOME=qs, HTML_SLIDE_NODE_MODULES=TMP / "order" / "nowhere",
             HTML_SLIDE_CHROME=TMP / "order" / "nowhere" / "chrome"):
        nm, src, ok = B.node_modules(cfg)
        check((src, ok) == ("env", False), (nm, src, ok))
        ch, src, where = B.chrome(cfg)
        check((ch, src) == (None, "env"), (ch, src))
        lack = B.missing(cfg)
        check(len(lack) == 2 and "puppeteer-core" in lack[0] and "Chrome" in lack[1], lack)
        e = B.child_env(cfg)
        check(e["HTML_SLIDE_NODE_MODULES"] == str(TMP / "order" / "nowhere"), e["HTML_SLIDE_NODE_MODULES"])


def case_nothing_installed():
    """どこにも無ければ、入れる先（Quick Slide の置き場）を「無い」と言う。"""
    home = TMP / "empty" / "home"
    qs = TMP / "empty" / "qs"
    home.mkdir(parents=True)
    with env(HOME=home, QUICK_SLIDE_HOME=qs):
        nm, src, ok = B.node_modules({})
        check((nm, src, ok) == (qs / "engines" / "html-slide" / "node" / "node_modules", "quick-slide", False), (nm, src, ok))
        ch, src, where = B.chrome({})
        check((ch, src, where) == (None, "quick-slide", qs / "engines" / "html-slide" / "chrome"), (ch, src, where))
        check(any(str(qs) in m for m in B.missing({})), B.missing({}))
        check("HTML_SLIDE_CHROME" not in B.child_env({}), "無い Chrome は渡さない")


def case_newest_version():
    """入れたフォルダの中の一番新しい版（数字で比べる。131 < 152・9 < 10）。"""
    folder = TMP / "versions"
    fake_chrome(folder, "131.0.6778.204")
    fake_chrome(folder, "9.0.1")
    newest = fake_chrome(folder, "152.0.7977.54")
    (folder / "chrome-headless-shell" / "zzz-broken").mkdir()
    check(B.chrome_in(folder) == newest, B.chrome_in(folder))
    check(B.chrome_in(folder / "chrome-headless-shell") == newest, "chrome-headless-shell のフォルダを直に渡しても読む")
    check(B.chrome_in(TMP / "no-such") is None, "無いフォルダは None")


def case_cli_json():
    """`python3 browser.py` は JSON 1 行（導入の確かめが読む）。"""
    import subprocess
    home = TMP / "cli" / "home"
    home.mkdir(parents=True)
    e = {k: v for k, v in os.environ.items() if k not in KEYS}
    e.update(HOME=str(home), QUICK_SLIDE_HOME=str(TMP / "cli" / "qs"), PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, str(SKILL / "scripts" / "browser.py")], capture_output=True, text=True, env=e)
    check(r.returncode == 0, r.stderr)
    data = json.loads(r.stdout.strip().splitlines()[-1])
    check(set(data) == {"node", "node_modules", "chrome"}, data)
    check(data["chrome"]["ok"] is False and data["node_modules"]["ok"] is False, data)


def case_rc4_before_drawing():
    """足りなければ check・deck は描く前に rc=4 で止まり、入れ方を言う（Codex は呼ばない）。"""
    import html_slide as H
    script = SKILL / "examples" / "sample-deck.json"
    home = TMP / "rc4" / "home"
    home.mkdir(parents=True)
    calls = TMP / "rc4" / "codex-calls"
    with env(HOME=home, QUICK_SLIDE_HOME=TMP / "rc4" / "qs", HTML_SLIDE_CONFIG=TMP / "rc4" / "none.yaml"):
        os.environ["HTML_SLIDE_CODEX"] = str(HERE / "fake_codex.py")
        os.environ["FAKE_CODEX_LOG"] = str(calls)
        try:
            for argv in (["check", "--script", str(script)],
                         ["deck", "--script", str(script), "--out", str(TMP / "rc4" / "out"), "--no-odp"]):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    rc = H.main(argv)
                check(rc == 4, (argv[0], rc, err.getvalue()[-400:]))
                check("quick_slide_setup.py --setup" in err.getvalue() and "deploy.md" in err.getvalue(), err.getvalue())
        finally:
            os.environ.pop("HTML_SLIDE_CODEX", None)
            os.environ.pop("FAKE_CODEX_LOG", None)
    check(not calls.exists() or not any(calls.iterdir()), "足りないのに Codex を呼んだ")


CASES = {k[5:]: v for k, v in globals().items() if k.startswith("case_")}

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if name not in CASES and name != "all":
        print("使えるケース: all, " + ", ".join(CASES))
        sys.exit(2)
    try:
        for k, f in CASES.items():
            if name in ("all", k):
                f()
                print(f"ok   {k}")
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
