"""画面（web/app。v1 から写したもの）とサーバの突き合わせ。JavaScript は動かさない（動かす試験は tests/live）。

- 画面が呼ぶ URL はすべてサーバのルート表にある（P3 以降の口も「まだ使えない」と答えるために残っている）
- 画面は新しいウィンドウを開かず、外のサイトから何も読み込まない
"""

from __future__ import annotations

import re
import unittest
import uuid

from helpers import REPO

from quick_slide.server.app import ROUTES

WEB = REPO / "web" / "app"
_LITERAL = re.compile(r'"(/api/[^"]*)"')


def screen_api_paths() -> list[str]:
    """画面の JS が組み立てる `/api/...` を、変数の所に UUID を入れた具体的なパスにする。"""
    out: list[str] = []
    for js in sorted(WEB.glob("*.js")):
        source = js.read_text(encoding="utf-8")
        for m in _LITERAL.finditer(source):
            path = m.group(1)
            rest = source[m.end():]
            while True:
                nxt = re.match(r'\s*\+\s*(?:"([^"]*)"|([A-Za-z_][\w.]*(?:\([^()]*\))?))', rest)
                if nxt is None:
                    break
                if nxt.group(1) is not None:
                    path += nxt.group(1)
                else:
                    path += str(uuid.uuid4())
                rest = rest[nxt.end():]
            out.append(path.split("?")[0])
    return sorted(set(out))


def routed(path: str) -> bool:
    return any(matcher(path) is not None for _methods, matcher, _handler in ROUTES)


class ScreenAndServerAgreeTests(unittest.TestCase):
    def test_every_url_the_screen_calls_is_in_the_route_table(self) -> None:
        paths = screen_api_paths()
        self.assertGreater(len(paths), 25, "抜き出しが働いていない")
        missing = [p for p in paths if not routed(p)]
        self.assertEqual(missing, [], "画面が呼ぶのにサーバに無い URL")

    def test_the_screen_pages_are_routed(self) -> None:
        for path in ("/", "/slides", f"/slides/{uuid.uuid4()}", "/settings", "/settings/brands", "/settings/styles", "/settings/server",
                     f"/settings/brands/{uuid.uuid4()}"):
            self.assertTrue(routed(path), path)

    def test_no_new_windows_and_no_outside_resources(self) -> None:
        for f in WEB.iterdir():
            text = f.read_text(encoding="utf-8")
            self.assertNotIn("window.open(", text, f.name)
            self.assertNotIn('target="_blank"', text, f.name)
            self.assertNotRegex(text, r'(src|href)="https?://', f.name)


if __name__ == "__main__":
    unittest.main()
