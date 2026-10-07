"""原稿 1 本から、両モードで 1 冊ずつ: サーバ → 台本係（偽物の LLM）→ **本物のエンジンの検査** → **本物のエンジン**
（絵を描く codex だけ偽物）→ スライドの画面の材料にページの画像が出る。

- プレゼン（html-slide）: 会話の出所は Claude Code（偽物の `claude`）。Node と Chrome（headless shell）が要る。無ければ skip
- ピッチ（image-slide）: 会話の出所は Codex（偽物の `codex`）。1 回目の台本は検査で止まり、`NG:` を読んで直させる
- どちらも API キーを環境に置いたまま流す（台本係にもエンジンにも渡らないこと）
"""

from __future__ import annotations

import os
import shutil
import unittest
from pathlib import Path
from unittest import mock

from helpers import fake_tools, intake_payload, start_server, wait_until_idle, writer_calls

from quick_slide.childenv import API_KEY_VARS
from quick_slide.settings import Settings, WorkerSettings

KEYS = {name: "sk-should-never-reach-the-child-0123456789" for name in API_KEY_VARS}


def html_slide_ready() -> str | None:
    """html-slide が描ける機械か（Node・puppeteer-core・Chrome。置き方は skills/html-slide/references/deploy.md）。"""
    if shutil.which("node") is None:
        return "node が無い"
    if not (Path.home() / ".cache" / "puppeteer").is_dir():
        return "Chrome（~/.cache/puppeteer）が無い"
    if not (Path.home() / ".cache" / "html-video" / "node_modules" / "puppeteer-core").is_dir():
        return "puppeteer-core が無い"
    return None


class BothModesEndToEndTests(unittest.TestCase):
    def check_deck(self, fx, body: dict, run: dict, *, pages: int) -> None:
        self.assertEqual(run["state"], "done", run["message"])
        out = fx.root.run_dir(body["deck_id"], run["id"])
        for name in ("deck.pptx", "deck.pdf", "thumbs.png"):
            self.assertTrue((out / name).is_file(), name)
        view = fx.get(f"/api/decks/{body['deck_id']}").json()
        self.assertEqual(len(view["pages"]), pages)
        with fx.server.store_lock:
            for p in view["pages"]:
                self.assertEqual(p["display"]["key"], "ready", p)
                asset_id = p["image_url"].split("/")[3]
                asset = fx.store.asset(asset_id)
                self.assertEqual((asset["width"], asset["height"], asset["media_type"]), (1920, 1080, "image/png"))
                self.assertTrue(asset["relative_path"].startswith(f"assets/decks/{body['deck_id']}/{run['id']}/"))
        img = fx.get(view["pages"][0]["image_url"])
        self.assertEqual((img.status, img.headers["content-type"]), (200, "image/png"))
        self.assertEqual(img.body, (out / "slides" / "slide-01.png").read_bytes())
        card = fx.get(f"/api/decks?limit=5").json()["items"][0]
        self.assertEqual((card["id"], card["page_count"], card["thumbnail_url"]),
                         (body["deck_id"], pages, view["pages"][0]["image_url"]))
        self.assertFalse(view["live"]["working"])

    def test_presentation_with_html_slide(self) -> None:
        why = html_slide_ready()
        if why:
            self.skipTest(why)
        tools, log = fake_tools(self)
        fx = start_server(self, tools=tools, settings=Settings(worker=WorkerSettings(parallel=1)))
        with mock.patch.dict(os.environ, KEYS):
            r = fx.post("/api/intakes", intake_payload(runtime="claude_code", mode="presentation", style="visual",
                                                       image_look="illustration"))
            self.assertEqual(r.status, 201, r.body)
            body = r.json()
            run = wait_until_idle(fx, body["deck_id"], seconds=300)
        self.check_deck(fx, body, run, pages=4)
        self.assertEqual(run["args"]["writer"], "claude")
        self.assertEqual([c["keys"] for c in writer_calls(log)], [[]])
        # 画像の枠は本物の html-slide が偽物の codex に描かせ、PPTX に入っている
        info = (fx.root.run_dir(body["deck_id"], run["id"]) / "deck-info.json").read_text(encoding="utf-8")
        self.assertIn('"outputs"', info)
        self.assertTrue(any((log / "codex-calls").glob("*.json")), "画像の枠を描かせていない")

    def test_pitch_with_image_slide_and_a_rewrite(self) -> None:
        tools, log = fake_tools(self, mode="invalid_first")
        fx = start_server(self, tools=tools, settings=Settings(worker=WorkerSettings(parallel=1)))
        with mock.patch.dict(os.environ, KEYS):
            r = fx.post("/api/intakes", intake_payload(runtime="codex", mode="pitch"))
            self.assertEqual(r.status, 201, r.body)
            body = r.json()
            run = wait_until_idle(fx, body["deck_id"], seconds=300)
        self.check_deck(fx, body, run, pages=4)
        self.assertEqual(run["args"]["writer"], "codex")
        calls = writer_calls(log)
        self.assertEqual(len(calls), 2, "1 回目は本物の image-slide の検査で止まり、直させる")
        self.assertTrue(any("知らない型" in ln for ln in calls[1]["ng"]), calls[1]["ng"])
        self.assertEqual([c["keys"] for c in calls], [[], []])
        with fx.server.store_lock:
            versions = fx.store._all("SELECT version,check_rc FROM scripts WHERE deck_id=? ORDER BY version",
                                     (body["deck_id"],))
        self.assertEqual([(v["version"], v["check_rc"]) for v in versions], [(1, 2), (2, 0)])
        self.assertTrue((fx.root.run_dir(body["deck_id"], run["id"]) / "build.json").is_file())


if __name__ == "__main__":
    unittest.main()
