"""Windows で壊れる書き方を、mac の試験で先に見つける（静的な検査）。

- `os.open` は `O_BINARY` を付ける。付けないと Windows ではテキストモードで開き、`os.write` が `\\n` を `\\r\\n` に
  変える（32 バイトの `hmac.key` が 0x0A を含むたびに長くなり、壊れた鍵が残ってサーバが起動しなくなる）
- 試験の偽物（`.py`）は `runnable()` で包んで渡す。Windows は `.py` を直接起こせない
- OS を見分ける呼び出し（ロック・PID・子を孫ごと止める）は `oscompat.py` に寄せ、3 つの写しを同じに保つ（ADR 0006）
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = [ROOT / "src", *sorted((ROOT / "skills").glob("*/scripts"))]


def python_files() -> list[Path]:
    return sorted(p for base in SOURCES for p in base.rglob("*.py") if "__pycache__" not in p.parts)


def os_open_calls(tree: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "open" and isinstance(n.func.value, ast.Name) and n.func.value.id == "os"]


def flags_source(source: str, tree: ast.AST, call: ast.Call) -> str:
    """`os.open` の 2 つ目の引数の書き方。変数なら、同じファイルでその名前に代入している式（すべて）。"""
    flags = call.args[1] if len(call.args) > 1 else next((k.value for k in call.keywords if k.arg == "flags"), None)
    if flags is None:
        return ""
    if isinstance(flags, ast.Name):
        return " ".join(ast.get_source_segment(source, n.value) or "" for n in ast.walk(tree)
                        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == flags.id
                                                             for t in n.targets))
    return ast.get_source_segment(source, flags) or ""


class OsOpenBinaryTests(unittest.TestCase):
    def test_every_os_open_passes_o_binary(self) -> None:
        missing, seen = [], 0
        for path in python_files():
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for call in os_open_calls(tree):
                seen += 1
                if "O_BINARY" not in flags_source(source, tree, call):
                    missing.append(f"{path.relative_to(ROOT).as_posix()}:{call.lineno}")
        self.assertGreater(seen, 0, "os.open が 1 つも見つからない（探す場所がずれた）")
        self.assertEqual(missing, [], "os.open に getattr(os, \"O_BINARY\", 0) を足す（Windows でテキストモードになる）")

    def test_the_check_catches_a_missing_flag(self) -> None:
        source = "import os\nf = os.O_RDONLY\nos.open('a', f)\nos.open('b', os.O_WRONLY | getattr(os, 'O_BINARY', 0))\n"
        tree = ast.parse(source)
        found = ["O_BINARY" in flags_source(source, tree, c) for c in os_open_calls(tree)]
        self.assertEqual(found, [False, True])


# 偽物の `.py` を、`runnable()` で包まずにコマンドとして渡している書き方
BARE_FAKE = re.compile(r'str\([^()\n]*(?:fake_[a-z_]+|refuse)\.py"\)|str\(FAKE_CODEX\)')


class FakeCommandTests(unittest.TestCase):
    def test_fakes_are_passed_through_runnable(self) -> None:
        """偽物は `runnable()` で包んで渡す。Windows は `.py` を直接起こせない（`shutil.which` も見つけない）。"""
        bases = [ROOT / "tests", *sorted((ROOT / "skills").glob("*/tests"))]
        bare = [f"{p.relative_to(ROOT).as_posix()}:{n}" for base in bases for p in sorted(base.rglob("test_*.py"))
                + sorted(base.rglob("helpers.py")) if p != Path(__file__).resolve()   # 下の試験の見本は除く
                for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if BARE_FAKE.search(line)]
        self.assertEqual(bare, [], "偽物を runnable() で包む")

    def test_the_check_catches_a_bare_fake(self) -> None:
        self.assertTrue(BARE_FAKE.search('env = {"IMAGE_SLIDE_CODEX": str(HERE / "fake_codex.py")}'))
        self.assertTrue(BARE_FAKE.search('"QUICK_SLIDE_CLAUDE": str(FAKES / "refuse.py"),'))
        self.assertFalse(BARE_FAKE.search('"QUICK_SLIDE_CLAUDE": runnable(FAKES / "refuse.py"),'))


if __name__ == "__main__":
    unittest.main()


# OS を見分けて書く呼び出し。`oscompat.py` の外に書かない（ADR 0006）
POSIX_ONLY = re.compile(r"^\s*import fcntl|os\.killpg\(|os\.kill\([^)]*,\s*0\)|start_new_session\s*=|signal\.SIGKILL")
OSCOMPAT_COPIES = [ROOT / "src" / "quick_slide" / "oscompat.py",
                   ROOT / "skills" / "html-slide" / "scripts" / "oscompat.py",
                   ROOT / "skills" / "image-slide" / "scripts" / "oscompat.py"]


class OsCompatTests(unittest.TestCase):
    def test_the_three_copies_are_the_same(self) -> None:
        first = OSCOMPAT_COPIES[0].read_bytes()
        for path in OSCOMPAT_COPIES[1:]:
            self.assertEqual(path.read_bytes(), first, f"{path.relative_to(ROOT).as_posix()} がサーバの写しとずれた")

    def test_posix_only_calls_live_in_oscompat(self) -> None:
        found = [f"{p.relative_to(ROOT).as_posix()}:{n}" for p in python_files() if p.name != "oscompat.py"
                 for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if POSIX_ONLY.search(line)]
        self.assertEqual(found, [], "OS を見分ける呼び出しは oscompat.py に寄せる（ADR 0006）")

    def test_the_lock_is_exclusive_and_released(self) -> None:
        from quick_slide import oscompat as OS

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server.lock"
            first = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
            second = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
            try:
                self.assertTrue(OS.lock_nowait(first))
                self.assertFalse(OS.lock_nowait(second), "2 つ目が同じロックを取れた")
                OS.unlock(first)
                self.assertTrue(OS.lock_nowait(second), "外したロックを取れない")
            finally:
                os.close(first)
                os.close(second)

    def test_pid_alive_tells_a_finished_process(self) -> None:
        from quick_slide import oscompat as OS

        self.assertTrue(OS.pid_alive(os.getpid()))
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        self.assertFalse(OS.pid_alive(child.pid), "終わったプロセスを生きていると言った")
        self.assertFalse(OS.pid_alive(0))

    def test_kill_group_stops_grandchildren(self) -> None:
        """子が起こした孫も止まる（Windows の .cmd の包みの下の node・codex.exe にあたる）。"""
        from quick_slide import oscompat as OS

        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "grandchild.pid"
            code = ("import subprocess, sys, time\n"
                    "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                    f"open({str(pid_file)!r}, 'w').write(str(g.pid))\n"
                    "time.sleep(60)\n")
            proc = OS.popen_group([sys.executable, "-c", code])
            try:
                deadline = time.monotonic() + 10
                while not pid_file.exists() or not pid_file.read_text():
                    self.assertLess(time.monotonic(), deadline, "孫が起きない")
                    time.sleep(0.05)
                grandchild = int(pid_file.read_text())
                self.assertTrue(OS.pid_alive(grandchild))
                OS.kill_group(proc, grace=5.0)
                self.assertIsNotNone(proc.poll(), "子が止まらない")
                deadline = time.monotonic() + 10
                while OS.pid_alive(grandchild) and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertFalse(OS.pid_alive(grandchild), "孫が残った")
            finally:
                OS.end_group(proc)


class FakeEncodingAndShellTests(unittest.TestCase):
    def test_fakes_read_stdin_as_utf8(self) -> None:
        """偽物は本物と同じく stdin を UTF-8 で読む（`sys.stdin.read()` は Windows の既定の cp932 で読む）。"""
        fakes = sorted((ROOT / "tests" / "fakes").glob("*.py")) + sorted((ROOT / "skills").glob("*/tests/fake_*.py"))
        found = [f"{p.relative_to(ROOT).as_posix()}:{n}" for p in fakes
                 for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if "sys.stdin.read()" in line]
        self.assertEqual(found, [], "sys.stdin.buffer.read().decode(\"utf-8\") で読む")

    def test_bash_is_not_called_by_name(self) -> None:
        """Windows は名前の "bash" を PATH より先に System32 で探し、WSL の bash.exe に当たる。"""
        bases = [ROOT / "tests", *sorted((ROOT / "skills").glob("*/tests"))]
        found = [f"{p.relative_to(ROOT).as_posix()}:{n}" for base in bases for p in sorted(base.rglob("*.py"))
                 if p != Path(__file__).resolve()
                 for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if '["bash",' in line]
        self.assertEqual(found, [], "shutil.which(\"bash\") で見つけたパスで呼ぶ")


class ChildStdinTests(unittest.TestCase):
    def test_the_prompt_reaches_the_child_byte_for_byte(self) -> None:
        """台本係に渡すプロンプトは、改行も含めて UTF-8 のバイトのまま届く（Windows のテキストの包みは \\n を \\r\\n に
        変える。N16）。"""
        from quick_slide.worker.proc import run_process

        prompt = "1 行目\n指示ここまで>>>\n対象の id: p-1\n"
        echo = "import sys; sys.stdout.write(sys.stdin.buffer.read().hex())"
        with tempfile.TemporaryDirectory() as tmp:
            result = run_process([sys.executable, "-c", echo], cwd=Path(tmp), env=os.environ, timeout=30,
                                 stdin_text=prompt)
        self.assertTrue(result.ok, result)
        self.assertEqual(bytes.fromhex(result.stdout.strip()), prompt.encode("utf-8"))


class AssetPathTests(unittest.TestCase):
    def test_asset_paths_are_slash_separated(self) -> None:
        """DB の relative_path はいつも "/" 区切り。"\\" が入ると配信が通らない（Windows の str(Path)。N2）。"""
        from helpers import open_store

        from quick_slide.errors import ValidationError

        store = open_store(self)
        with store.transaction():
            with self.assertRaises(ValidationError):
                store.register_asset(relative_path="assets\\brand\\original.png", media_type="image/png",
                                     sha256="0" * 64, bytes_=1)
            store.register_asset(relative_path="assets/brand/original.png", media_type="image/png",
                                 sha256="0" * 64, bytes_=1)


class FontTests(unittest.TestCase):
    def test_mac_font_candidates_did_not_change(self) -> None:
        from quick_slide import oscompat as OS

        if OS.WINDOWS:
            self.skipTest("mac の候補")
        dfonts = "/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts"
        self.assertEqual(OS.yu_gothic_files(), {"regular": f"{dfonts}/YuGothR.ttc", "bold": f"{dfonts}/YuGothB.ttc"})
        self.assertEqual(OS.japanese_fonts("bold"), (f"{dfonts}/YuGothB.ttc", "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
                                                     "/System/Library/Fonts/Hiragino Sans GB.ttc"))
        self.assertEqual(OS.japanese_fonts("regular"), (f"{dfonts}/YuGothR.ttc", "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc"))

    @unittest.skipUnless(os.name == "nt", "Windows の字体のフォルダ")
    def test_windows_has_yu_gothic_and_a_japanese_font(self) -> None:
        """Windows 10 以降は游ゴシックが OS に入っている（B7）。PIL で日本語を描けること。"""
        from PIL import ImageFont

        from quick_slide import oscompat as OS

        for weight, path in OS.yu_gothic_files().items():
            self.assertTrue(Path(path).is_file(), f"{weight}: {path}")
        for weight in ("bold", "regular"):
            first = next(p for p in OS.japanese_fonts(weight) if Path(p).is_file())
            self.assertIsNotNone(ImageFont.truetype(first, 40, index=0).getbbox("日本語"))
