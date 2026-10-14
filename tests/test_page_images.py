"""依頼・直しの指示に書かれたページの本文の画像を使う（docs/adr/0016-page-images.md）。

- 集めるのは、指示にページの URL と「画像・写真・図」などの言葉が両方あるときだけ
- 本文（article・main）の画像を、図の説明・代替テキスト・近くの見出しと一緒に拾い、小さい画像・ロゴは除く
- 台本係には「使ってよい画像」と出典を渡し、使ったページには出典を書かせる
ネットにはつながない（ページと画像は偽物の取り込み口が返す）。
"""

from __future__ import annotations

import io
import unittest
from pathlib import Path
from unittest import mock

from helpers import temp_dir

from quick_slide import page_images as PI
from quick_slide.engines import ENGINES
from quick_slide.server.errors import ApiError
from quick_slide.writer import prompt as P

from p3_edit_helpers import EditBase

PAGE = """<html><head><title>T</title><meta property="og:title" content="記事の題"></head><body>
<img src="/logo.png" alt="ロゴ"><nav><img src="/nav.png"></nav>
<article><h2>評価の流れ</h2><figure><img src="a.jpg" alt="図1"><figcaption>評価の流れの図</figcaption></figure>
<p>本文</p><img srcset="b-300.png 300w, b-1200.png 1200w" src="b.png" alt="写真"><img src="tiny.png"><img src="data:x"></article>
</body></html>"""


def picture(w: int, h: int, fmt: str = "PNG") -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 120, 200)).save(buf, fmt)
    return buf.getvalue()


SITE = {"https://example.test/col/x/": PAGE.encode("utf-8"),
        "https://example.test/col/x/a.jpg": picture(800, 500, "JPEG"),
        "https://example.test/col/x/b-1200.png": picture(1200, 800),
        "https://example.test/col/x/tiny.png": picture(40, 40)}


def fake_fetch(url, *, max_bytes, accept="image/*"):
    if url not in SITE:
        raise ApiError(400, "image_url_status", "なし")
    return SITE[url], url


class UnitTests(unittest.TestCase):
    def test_only_when_asked(self) -> None:
        self.assertEqual(PI.wanted_urls("https://example.test/col/x/ を10枚に。画像も本文中のものを使って"),
                         ["https://example.test/col/x/"])
        self.assertEqual(PI.wanted_urls("https://example.test/col/x/ を10枚に"), [], "画像を頼んでいなければ集めない")
        self.assertEqual(PI.wanted_urls("画像を使って"), [])

    def test_body_pictures_are_chosen_with_their_words(self) -> None:
        title, cands = PI.candidates(PAGE, "https://example.test/col/x/")
        self.assertEqual(title, "記事の題")
        self.assertEqual([(c["url"], c["alt"], c["caption"], c["heading"]) for c in cands],
                         [("https://example.test/col/x/a.jpg", "図1", "評価の流れの図", "評価の流れ"),
                          ("https://example.test/col/x/b-1200.png", "写真", "", "評価の流れ"),
                          ("https://example.test/col/x/tiny.png", "", "", "評価の流れ")],
                         "本文の外・ロゴ・data: は拾わない。srcset は一番大きいもの")

    def test_collect_keeps_big_pictures_and_their_source(self) -> None:
        folder = temp_dir(self) / "attachments"
        pics = PI.collect("https://example.test/col/x/ の画像を使って", folder, fetch=fake_fetch)
        self.assertEqual(len(pics), 2, "小さい画像は除く")
        self.assertTrue(all((folder / Path(p["rel"]).name).is_file() for p in pics))
        self.assertEqual({p["page_title"] for p in pics}, {"記事の題"})
        line = PI.describe(pics)[0]
        self.assertIn("評価の流れの図", line)
        self.assertIn("出典: 記事の題 https://example.test/col/x/", line)
        self.assertEqual(PI.collect("https://example.test/none/ の画像を使って", folder, fetch=fake_fetch), [],
                         "ページを取れなくても止めない")

    def test_prompts_tell_to_use_only_when_asked_and_to_cite(self) -> None:
        pics = [{"rel": "attachments/page-1.png", "alt": "図1", "heading": "", "caption": "", "page_title": "記事の題",
                 "page_url": "https://example.test/col/x/"}]
        text = P.build_prompt(ENGINES["presentation"], {"mode": "presentation", "images": True}, manuscript="m",
                              title_hint=None, skills_dir=P.Path(__file__).resolve().parents[1] / "skills", pictures=pics)
        self.assertIn("# 使ってよい画像", text)
        self.assertIn("求めているときだけ", text)
        self.assertIn("必ず出典を書く", text)
        self.assertNotIn("画像の path は書かない（手元にある画像は無い）", text)


class RevisePresentationTests(EditBase):
    mode = "presentation"
    runtime = "claude_code"

    @classmethod
    def setUpClass(cls) -> None:
        from test_engines_e2e import html_slide_ready

        why = html_slide_ready()
        if why:
            raise unittest.SkipTest(why)

    def test_a_page_picture_is_placed_with_its_source(self) -> None:
        deck = self.make_deck()
        real = PI.collect
        with mock.patch.object(PI, "collect", lambda text, folder, fetch=None: real(text, folder, fetch=fake_fetch)):
            self.instruct(deck, [2], "https://example.test/col/x/ の本文の画像を使って")
            run = self.wait(deck)
        self.assertEqual(run["state"], "done", run["message"])
        data, _ = self.head(deck)
        page = data["slides"][1]
        self.assertEqual(page["type"], "photo")
        self.assertTrue(page["image"]["path"].startswith("attachments/page-"))
        self.assertEqual(page["caption"], "出典: 記事の題 https://example.test/col/x/")
        self.assertTrue((self.fx.root.scripts_dir(deck) / page["image"]["path"]).is_file())


if __name__ == "__main__":
    unittest.main()
