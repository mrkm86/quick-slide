"""画面のデザイントークン（web/app/dads-tokens.css）が、参照の置き場の公式トークン（skills/dads/tokens/dads.css）と同じか。

どちらもデジタル庁デザインシステムの公式トークンをそのまま写したもの。先頭のコメント（出典とライセンス）だけが違ってよい。
片方だけ更新して画面とスライドの色や段がずれないように、変数の部分（`:root` 以降）が一字一句同じことを見る。
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return text[text.index(":root"):].strip()


class DadsTokensTest(unittest.TestCase):
    def test_screen_tokens_match_the_shared_tokens(self) -> None:
        screen = _body(ROOT / "web" / "app" / "dads-tokens.css")
        shared = _body(ROOT / "skills" / "dads" / "tokens" / "dads.css")
        self.assertEqual(screen, shared, "web/app/dads-tokens.css と skills/dads/tokens/dads.css の変数の部分がずれた")


if __name__ == "__main__":
    unittest.main()
