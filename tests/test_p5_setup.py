"""P5: 導入の確かめ（`--doctor`）と入れる（`--setup`）。`skills/quick-slide/scripts/quick_slide_setup.py`。

使い捨ての HOME と、本物の python・node だけの PATH で走らせる（本物の claude・codex は PATH に入れない）。
npm と Chrome を入れる道具（`@puppeteer/browsers`）は偽物にして、ネットワークに出ない。本物を入れる通しは
`test_p5_fresh_machine.py`（任意）。
"""

from __future__ import annotations

import json
import os
import shutil
import site
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from helpers import FAKES, REPO, runnable, temp_dir, windows_base_env

SETUP = REPO / "skills" / "quick-slide" / "scripts" / "quick_slide_setup.py"
START = REPO / "skills" / "quick-slide" / "scripts" / "quick_slide_start.py"
FAKE_CODEX = REPO / "skills" / "image-slide" / "tests" / "fake_codex.py"


def tool_dirs(base: Path) -> list[str]:
    """本物の python3・node だけを置いたフォルダ（PATH に入れる。claude・codex は見せない）。

    在りかのフォルダをそのまま入れると、そこにある実物の claude・codex まで見える（Windows の npm は codex.cmd・
    claude.cmd を node.exe と同じフォルダに置く。N17）。Windows は symlink に特権が要るので、`.cmd` の包みにする。
    """
    tools = base / "tools"
    tools.mkdir()
    for name, real in (("python3", sys.executable), ("node", shutil.which("node"))):
        if real is None:
            continue
        if os.name == "nt":
            (tools / f"{name}.cmd").write_text(f'@"{real}" %*\r\n', encoding="oem", newline="")
        else:
            (tools / name).symlink_to(real)
    return [str(tools), "/usr/bin", "/bin"]


class SetupBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = temp_dir(self, "qs-setup-")
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.chromium = self.tmp / "chromium-bin"
        self.chromium.write_text("", encoding="utf-8")
        self.qs = self.home / ".quick-slide"
        self.fakebin = self.tmp / "bin"
        self.fakebin.mkdir()
        self.tool_dirs = tool_dirs(self.tmp)

    def env(self, **extra: str) -> dict[str, str]:
        env = {**windows_base_env(), "HOME": str(self.home), "USERPROFILE": str(self.home), "PATH": os.pathsep.join([str(self.fakebin), *self.tool_dirs]),
               "LANG": "ja_JP.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(self.tmp),
               "IMAGE_SLIDE_CODEX": runnable(FAKE_CODEX), "FAKE_CODEX_LOG": str(self.tmp / "codex-log"),
               "QUICK_SLIDE_CLAUDE": runnable(FAKES / "fake_claude.py"),
               # PATH の外のよくある置き場は探させない（そこにある本物の claude・codex・node を見つけてしまう）
               "QUICK_SLIDE_CLI_DIRS": "",
               # 偽物の Playwright（モーションに要る。本物の Chromium を探さない・入れない）。既定は入っている。
               # 本物の pip も呼ばせない（no_pip）。HOME・USERPROFILE を差し替えても、この Python の利用者の場所の
               # パッケージは見えるようにする（PYTHONUSERBASE。見えないと診断が「無い」と言い、pip で入れに行く。N19）
               "PYTHONPATH": os.pathsep.join([str(FAKES / "playwright_stub"), str(FAKES / "no_pip")]),
               "PYTHONUSERBASE": site.getuserbase(), "FAKE_PLAYWRIGHT_CHROMIUM": str(self.chromium)}
        env.update(extra)
        return env

    def run_tool(self, *args: str, script: Path = SETUP, **extra: str) -> tuple[int, str, dict]:
        r = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, env=self.env(**extra),
                           timeout=120, stdin=subprocess.DEVNULL, encoding="utf-8")
        last = json.loads(r.stdout.strip().splitlines()[-1])
        return r.returncode, r.stdout, last

    def checks(self, out: dict) -> dict[str, dict]:
        return {c["id"]: c for c in out["checks"]}


@unittest.skipIf(shutil.which("node") is None, "node が無い")
class DoctorTests(SetupBase):
    def test_doctor_on_a_fresh_home_says_what_is_missing_and_writes_nothing(self) -> None:
        rc, text, out = self.run_tool("--doctor")
        self.assertEqual(rc, 4, text)
        self.assertEqual(out["status"], "doctor")
        c = self.checks(out)
        for key in ("python", "package:python-pptx", "package:lxml", "package:Pillow", "package:PyYAML", "node",
                    "puppeteer-core", "chrome", "font", "codex", "codex_login", "writer", "home", "port"):
            self.assertIn(key, c)
        self.assertEqual((c["puppeteer-core"]["level"], c["chrome"]["level"]), ("ng", "ng"))
        self.assertIn(str(self.qs / "engines" / "html-slide"), c["chrome"]["detail"])
        self.assertEqual(c["codex_login"]["level"], "ok", "偽物の codex は ChatGPT でログイン済みと答える")
        self.assertEqual(c["writer"]["found"], ["claude"])
        self.assertFalse(out["ready"]["presentation"])
        self.assertIn("--setup", out["setup"])
        self.assertIn("足りない", text)
        self.assertFalse(self.qs.exists(), "doctor は何も書かない")
        # 本物の claude・codex の名前の出力（ログインの文）をそのまま出さない
        self.assertNotIn("Logged in", text)

    def test_doctor_sees_tools_given_by_environment(self) -> None:
        nm = self.tmp / "nm" / "node_modules"
        (nm / "puppeteer-core").mkdir(parents=True)
        chrome = self.tmp / "chrome-bin"
        chrome.write_text("", encoding="utf-8")
        rc, text, out = self.run_tool("--doctor", HTML_SLIDE_NODE_MODULES=str(nm), HTML_SLIDE_CHROME=str(chrome))
        c = self.checks(out)
        self.assertEqual((c["puppeteer-core"]["level"], c["puppeteer-core"]["source"]), ("ok", "env"))
        self.assertEqual((c["chrome"]["level"], c["chrome"]["source"]), ("ok", "env"))

    def test_motion_needs_playwright_and_its_chromium_only_for_motion(self) -> None:
        # 入っている（既定の偽物）: モーションは使える
        rc, text, out = self.run_tool("--doctor")
        c = self.checks(out)
        self.assertEqual((c["playwright"]["level"], c["playwright-chromium"]["level"]), ("ok", "ok"))
        self.assertEqual(c["playwright"]["needed_for"], ["motion"])
        self.assertTrue(out["ready"]["motion"])
        # Chromium が無い: モーションだけが使えない（プレゼン・ピッチの可否には効かない）
        rc, text, out = self.run_tool("--doctor", FAKE_PLAYWRIGHT_CHROMIUM="")
        c = self.checks(out)
        self.assertEqual((c["playwright"]["level"], c["playwright-chromium"]["level"]), ("ok", "ng"))
        self.assertEqual(c["playwright-chromium"]["needed_for"], ["motion"])
        self.assertIn("モーションを使わないなら要りません", c["playwright-chromium"]["fix"])
        self.assertFalse(out["ready"]["motion"])
        self.assertEqual(out["ready"]["presentation"], c["font"]["level"] != "ng" and c["node"]["level"] != "ng"
                         and c["puppeteer-core"]["level"] != "ng" and c["chrome"]["level"] != "ng")
        self.assertIn("モーション（まだ）", text)

    def test_codex_logged_out_and_no_writer(self) -> None:
        rc, text, out = self.run_tool("--doctor", FAKE_CODEX_LOGGEDOUT="1", QUICK_SLIDE_CLAUDE="")
        c = self.checks(out)
        self.assertEqual(c["codex_login"]["level"], "ng")
        self.assertIn("codex login", c["codex_login"]["fix"])
        self.assertEqual(c["writer"]["level"], "ng")

    def test_inside_codex_it_does_not_tell_to_install_what_may_only_be_hidden(self) -> None:
        """Codex の砂場の中では、入っている codex・claude が見えないことがある。入れる案内の前にターミナルで確かめてもらう。"""
        for marker in ("CODEX_SANDBOX", "CODEX_THREAD_ID"):
            with self.subTest(marker=marker):
                rc, text, out = self.run_tool("--doctor", IMAGE_SLIDE_CODEX="", QUICK_SLIDE_CLAUDE="", **{marker: "1"})
                c = self.checks(out)
                self.assertTrue(out["inside_codex"])
                self.assertEqual((c["codex"]["level"], c["writer"]["level"]), ("ng", "ng"))
                for key in ("codex", "codex_login", "writer"):
                    self.assertTrue(c[key]["fix"].startswith("Codex の中からは、入っているものが見えないことがあります"), c[key]["fix"])
                    self.assertIn("--doctor", c[key]["fix"])
                self.assertIn("Codex の中で確かめています", text)
        # 外（利用者のターミナル）では、いままでどおり入れ方を言う
        rc, text, out = self.run_tool("--doctor", IMAGE_SLIDE_CODEX="", QUICK_SLIDE_CLAUDE="")
        c = self.checks(out)
        self.assertFalse(out["inside_codex"])
        self.assertTrue(c["codex"]["fix"].startswith("`npm install -g @openai/codex`"), c["codex"]["fix"])
        self.assertNotIn("Codex の中で確かめています", text)

    def test_it_finds_the_clis_outside_path_like_the_server_does(self) -> None:
        extra = self.tmp / "npm-global"
        extra.mkdir()
        if os.name == "nt":
            (extra / "claude.cmd").write_text("@exit /b 0\r\n", encoding="ascii", newline="")
        else:
            (extra / "claude").write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
            (extra / "claude").chmod(0o755)
        rc, text, out = self.run_tool("--doctor", QUICK_SLIDE_CLAUDE="", QUICK_SLIDE_CLI_DIRS=str(extra))
        c = self.checks(out)
        self.assertEqual((c["writer"]["level"], c["writer"]["found"]), ("ok", ["claude"]))

    def test_the_start_script_runs_the_doctor_too(self) -> None:
        rc, text, out = self.run_tool("--doctor", script=START)
        self.assertEqual((rc, out["status"]), (4, "doctor"))
        self.assertFalse(list((REPO / "skills" / "quick-slide" / "scripts").glob("__pycache__")))


@unittest.skipIf(shutil.which("node") is None, "node が無い")
class SetupTests(SetupBase):
    def fake_npm(self) -> Path:
        """偽物の npm: `install --prefix <P> … puppeteer-core` で <P>/node_modules/puppeteer-core と、Chrome を入れる偽物の
        `.bin/browsers` を作る。呼ばれた引数を書く。"""
        tag_py = textwrap.dedent('''
            import platform, sys
            m = platform.machine().lower()
            if sys.platform == "darwin":
                tag, pre = ("mac-arm64", "mac_arm") if m in ("arm64", "aarch64") else ("mac-x64", "mac")
            elif sys.platform.startswith("win"):
                tag, pre = ("win64", "win64")
            else:
                tag, pre = ("linux64", "linux")
        ''')
        browsers = self.tmp / "browsers.py"
        browsers.write_text("#!" + sys.executable + "\n" + tag_py + textwrap.dedent('''
            import json, os, pathlib
            args = sys.argv[1:]
            pathlib.Path(os.environ["FAKE_NPM_LOG"]).open("a").write(json.dumps(["browsers", *args]) + "\\n")
            assert args[:2] == ["install", "chrome-headless-shell@stable"], args
            root = pathlib.Path(args[args.index("--path") + 1])
            name = "chrome-headless-shell.exe" if sys.platform.startswith("win") else "chrome-headless-shell"
            exe = root / "chrome-headless-shell" / f"{pre}-150.0.1" / f"chrome-headless-shell-{tag}" / name
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_text("")
            exe.chmod(0o755)
        '''), encoding="utf-8")
        browsers.chmod(0o755)
        # Windows は shebang のスクリプトを起こせない。本物の npm と同じく .cmd の包みを置く（setup は Windows ではそちらを起こす）
        browsers_cmd = f'@"{sys.executable}" -X utf8 "{browsers}" %*\r\n' if os.name == "nt" else ""
        npm = self.fakebin / "npm"
        npm.write_text("#!" + sys.executable + "\n" + textwrap.dedent(f'''
            import json, os, pathlib, sys
            args = sys.argv[1:]
            pathlib.Path(os.environ["FAKE_NPM_LOG"]).open("a").write(json.dumps(["npm", *args]) + "\\n")
            assert args[0] == "install" and "-g" not in args and "--global" not in args, args
            prefix = pathlib.Path(args[args.index("--prefix") + 1])
            nm = prefix / "node_modules"
            (nm / "puppeteer-core").mkdir(parents=True, exist_ok=True)
            (nm / ".bin").mkdir(exist_ok=True)
            link = nm / ".bin" / "browsers"
            link.write_bytes(pathlib.Path({str(browsers)!r}).read_bytes())
            link.chmod(0o755)
            if {bool(browsers_cmd)!r}:
                (nm / ".bin" / "browsers.cmd").write_text({browsers_cmd!r}, encoding="oem", newline="")
        '''), encoding="utf-8")
        npm.chmod(0o755)
        if os.name == "nt":
            (self.fakebin / "npm.cmd").write_text(f'@"{sys.executable}" -X utf8 "{npm}" %*\r\n', encoding="oem", newline="")
        return npm

    def test_setup_without_yes_only_shows_the_plan(self) -> None:
        self.fake_npm()
        rc, text, out = self.run_tool("--setup", FAKE_NPM_LOG=str(self.tmp / "npm-log"))
        self.assertEqual(rc, 4)
        self.assertEqual(out["installed"], [])
        self.assertEqual(out["planned"], ["puppeteer-core", "chrome"])
        self.assertRegex(text, r"(?i)npm(\.cmd)? install --prefix")   # Windows は npm.cmd の包み
        self.assertFalse((self.tmp / "npm-log").exists(), "聞かずに入れない")
        self.assertFalse(self.qs.exists())

    def test_setup_yes_installs_into_the_quick_slide_home_only(self) -> None:
        self.fake_npm()
        log = self.tmp / "npm-log"
        rc, text, out = self.run_tool("--setup", "--yes", FAKE_NPM_LOG=str(log))
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        engine = self.qs / "engines" / "html-slide"
        self.assertEqual(calls[0][:4], ["npm", "install", "--prefix", str(engine / "node")])
        self.assertEqual(calls[1][:3], ["browsers", "install", "chrome-headless-shell@stable"])
        self.assertEqual(calls[1][-1], str(engine / "chrome"))
        self.assertEqual([d["id"] for d in out["installed"]], ["puppeteer-core", "chrome"])
        self.assertTrue(all(d["ok"] for d in out["installed"]), out["installed"])
        c = self.checks(out)
        self.assertEqual((c["puppeteer-core"]["level"], c["puppeteer-core"]["source"]), ("ok", "quick-slide"))
        self.assertEqual((c["chrome"]["level"], c["chrome"]["source"]), ("ok", "quick-slide"))
        self.assertEqual(out["ready"]["presentation"], c["font"]["level"] != "ng")
        # 入れたのは Quick Slide の置き場の中だけ（HOME の直下に .npm・.cache などを作らない＝偽物なので作らない）
        made = sorted(p.name for p in self.home.iterdir())
        self.assertEqual(made, [".quick-slide"])
        # 2 回目は入れるものが無い
        rc2, text2, out2 = self.run_tool("--setup", "--yes", FAKE_NPM_LOG=str(log))
        self.assertEqual(out2["installed"], [])
        self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 2)

    def test_setup_plans_the_motion_chromium_when_it_is_missing(self) -> None:
        self.fake_npm()
        log = self.tmp / "playwright-log"
        installed = self.tmp / "chromium-installed"
        rc, text, out = self.run_tool("--setup", FAKE_NPM_LOG=str(self.tmp / "npm-log"), FAKE_PLAYWRIGHT_CHROMIUM="")
        self.assertEqual(out["planned"], ["puppeteer-core", "chrome", "playwright-chromium"])
        self.assertIn("-m playwright install chromium", text)
        rc, text, out = self.run_tool("--setup", "--yes", FAKE_NPM_LOG=str(self.tmp / "npm-log"),
                                      FAKE_PLAYWRIGHT_CHROMIUM=str(installed), FAKE_PLAYWRIGHT_INSTALLS=str(installed),
                                      FAKE_PLAYWRIGHT_LOG=str(log))
        # Chromium の確かめは「あるか」だけ。偽物のインストーラがファイルを置いたので、入れ終えたあとは使える
        self.assertEqual(json.loads(log.read_text(encoding="utf-8").splitlines()[0]), ["install", "chromium"])
        self.assertTrue(out["ready"]["motion"], out["checks"])

    def test_yes_alone_is_an_input_error(self) -> None:
        rc, _, out = self.run_tool("--yes")
        self.assertEqual((rc, out["code"]), (2, "yes_without_setup"))


if __name__ == "__main__":
    unittest.main()
