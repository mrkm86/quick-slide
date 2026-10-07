"""P4: ブランドを**本物のエンジン**に通す（絵を描く codex だけ偽物・台本係は偽物）。

- プレゼン（html-slide・画像なし）: ブランドの基本色が HTML と PPTX に出て、ロゴが選んだ角に置かれ、フッタ・ページ番号・
  透かしが入る。Node と Chrome が無ければ skip
- ピッチ（image-slide）: ロゴ・白いロゴ・ロゴの角・透かし・作風の一言を受けて描き終える。どのページにロゴを置いたかが
  build.json に残る
- 読めない色のブランド: html-slide の検査がテーマで止まったら、台本係に書き直させずに理由を言って止める
"""

from __future__ import annotations

import io
import json
import unittest
import zipfile

from helpers import fake_tools, intake_payload, png_bytes, start_server, wait_until_idle, writer_calls
from test_engines_e2e import html_slide_ready

from quick_slide.settings import Settings, WorkerSettings

PRIMARY = "#C8102E"


class BrandThroughRealEnginesTests(unittest.TestCase):
    def start(self, *, images: bool = True):
        tools, log = fake_tools(self, images=images)
        self.log = log
        self.fx = start_server(self, tools=tools, settings=Settings(worker=WorkerSettings(parallel=1)))
        return self.fx

    def upload(self, data: bytes) -> str:
        r = self.fx.request("POST", "/api/settings/images", raw_body=data, session=True)
        self.assertEqual(r.status, 201, r.body)
        return r.json()["asset_id"]

    def brand(self, corner: str) -> dict:
        logo = png_bytes(400, 100, color=(20, 40, 200, 255))
        white = png_bytes(400, 100, color=(255, 255, 255, 255))
        assets = [{"asset_id": self.upload(logo), "role": "logo_transparent", "label": "ロゴ"},
                  {"asset_id": self.upload(white), "role": "logo_white", "label": "白いロゴ"}]
        config = {"schema_version": 1, "colors": {"primary": PRIMARY, "accent": "#1D4ED8"},
                  "logo": {"variant": "transparent", "placement": {"corner": corner}},
                  "footer": {"text": "試験の資料（架空）", "style": {"position": "footer_left"}},
                  "format": {"page_numbers": True, "watermark": "配布禁止"}}
        r = self.fx.post("/api/settings/brands", {"name": "赤いブランド", "config": config, "assets": assets})
        self.assertEqual(r.status, 201, r.body)
        self.logo_bytes = logo
        return r.json()["item"]

    def test_presentation_uses_the_brand_color_logo_footer_page_numbers_and_watermark(self) -> None:
        why = html_slide_ready()
        if why:
            self.skipTest(why)
        fx = self.start(images=False)
        brand = self.brand("bottom-left")
        r = fx.post("/api/intakes", intake_payload(mode="presentation", images=False, brand_id=brand["id"]))
        self.assertEqual(r.status, 201, r.body)
        body = r.json()
        run = wait_until_idle(fx, body["deck_id"], seconds=300)
        self.assertEqual(run["state"], "done", run["message"])
        out = fx.root.run_dir(body["deck_id"], run["id"])
        html = (fx.root.work_dir(body["deck_id"]) / "deck.html").read_text(encoding="utf-8")
        self.assertIn(PRIMARY.lower(), html.lower(), "ブランドの基本色がテーマになっている")
        self.assertIn("試験の資料（架空）", html, "フッタ")
        self.assertIn("配布禁止", html, "透かし")
        info = json.loads((out / "deck-info.json").read_text(encoding="utf-8"))
        corners = {p.get("corner") for p in info["logo"]["pages"] if isinstance(p, dict)}
        self.assertIn("bottom-left", corners, info["logo"])
        with zipfile.ZipFile(out / "deck.pptx") as z:
            slides = "".join(z.read(n).decode("utf-8") for n in z.namelist() if n.startswith("ppt/slides/slide"))
            media = [n for n in z.namelist() if n.startswith("ppt/media/")]
        self.assertIn(f'srgbClr val="{PRIMARY[1:]}"', slides, "PPTX の部品にブランドの基本色")
        self.assertIn("配布禁止", slides)
        self.assertIn("!!logo-left", slides, "左下のロゴの部品")
        self.assertTrue(media, "ロゴの画像が PPTX に入っている")
        from PIL import Image

        logo = Image.open(io.BytesIO(self.logo_bytes))
        found = False
        with zipfile.ZipFile(out / "deck.pptx") as z:
            for n in media:
                with Image.open(io.BytesIO(z.read(n))) as im:
                    if im.size[0] * logo.size[1] == im.size[1] * logo.size[0]:
                        found = True
        self.assertTrue(found, "ロゴは縦横比を変えずに入っている")

    def test_unreadable_brand_colors_stop_without_asking_the_writer_again(self) -> None:
        """エンジンの検査がブランドの色（テーマ）で止まったら、台本係に書き直させずに理由を言って止める。"""
        why = html_slide_ready()
        if why:
            self.skipTest(why)
        fx = self.start(images=False)
        config = {"schema_version": 1, "colors": {"primary": "#F5A623", "accent": "#399DD1", "base": "#777777"},
                  "logo": {"variant": "none"}, "footer": {"text": ""}}
        r = fx.post("/api/settings/brands", {"name": "灰色の地", "config": config, "assets": []})
        self.assertEqual(r.status, 201, r.body)
        r = fx.post("/api/intakes", intake_payload(mode="presentation", images=False, brand_id=r.json()["item"]["id"]))
        body = r.json()
        run = wait_until_idle(fx, body["deck_id"], seconds=300)
        self.assertEqual((run["state"], run["failure_kind"]), ("failed", "brand"))
        self.assertIn("ブランドの色", run["message"])
        self.assertEqual(len(writer_calls(self.log)), 1, "台本係は 1 回だけ")

    def test_pitch_takes_the_logo_white_logo_corner_and_watermark(self) -> None:
        fx = self.start()
        brand = self.brand("top-right")
        r = fx.post("/api/intakes", intake_payload(runtime="codex", mode="pitch", brand_id=brand["id"],
                                                   style_note="落ち着いた色で"))
        self.assertEqual(r.status, 201, r.body)
        body = r.json()
        run = wait_until_idle(fx, body["deck_id"], seconds=300)
        self.assertEqual(run["state"], "done", run["message"])
        argv = run["record"]["argv"][0]
        for flag, value in (("--logo-position", "top-right"), ("--watermark", "配布禁止"), ("--style-note", "落ち着いた色で")):
            self.assertEqual(argv[argv.index(flag) + 1], value)
        self.assertIn("--logo-white", argv)
        build = json.loads((fx.root.run_dir(body["deck_id"], run["id"]) / "build.json").read_text(encoding="utf-8"))
        self.assertTrue(build.get("logo"), build.keys())
        engine_json = json.loads(fx.root.engine_script_path(body["deck_id"]).read_text(encoding="utf-8"))
        self.assertEqual((engine_json["footer"], engine_json["page_numbers"]), ("試験の資料（架空）", True))


if __name__ == "__main__":
    unittest.main()
