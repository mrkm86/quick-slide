"""Windows で壊れる書き方を、mac の試験で先に見つける（静的な検査）。

- `os.open` は `O_BINARY` を付ける。付けないと Windows ではテキストモードで開き、`os.write` が `\\n` を `\\r\\n` に
  変える（32 バイトの `hmac.key` が 0x0A を含むたびに長くなり、壊れた鍵が残ってサーバが起動しなくなる）
"""

from __future__ import annotations

import ast
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


if __name__ == "__main__":
    unittest.main()
