"""P5: 書き出しの配布・完全に削除したときのキャッシュの区画・設定を替えて組み直す・ブランドの字体。

エンジンはここでは偽物（`tests/fakes/fake_engine.py`。`deck.pptx`・`deck.pdf`・ページの画像を書く）。
本物のエンジンで書き出すのは `test_p5_fresh_machine.py`（任意・ネットワークを使う）と、下の字体の試験（Chrome が要る）。
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

from helpers import temp_dir, wait_until_idle, writer_calls
from test_engines_e2e import html_slide_ready
from test_p4_settings import Base, engine_calls

from quick_slide.paths import SKILLS_DIR, DataRoot
from quick_slide.render import look as LOOK
from quick_slide.render.caches import deck_cache_areas
from quick_slide.server.export_routes import content_disposition, safe_filename


def wait_job(fx, deck_id: str, job_id: str) -> dict:
    wait_until_idle(fx, deck_id)
    with fx.server.store_lock:
        job = fx.store.job(job_id)
        return fx.store.run(job["run_id"])


class ExportTests(Base):
    def setUp(self) -> None:
        self.start()

    def shown(self, deck_id: str) -> dict:
        with self.fx.server.store_lock:
            return self.fx.store.shown_run(deck_id)

    def export(self, deck_id: str, fmt: str):
        return self.fx.post(f"/api/decks/{deck_id}/exports", {"format": fmt})

    def test_pptx_and_pdf_are_the_shown_run_files_with_the_deck_title(self) -> None:
        body, run = self.make(title="会議を半分に: 試す/計画")
        deck = body["deck_id"]
        view = self.fx.get(f"/api/decks/{deck}").json()
        self.assertTrue(view["capabilities"]["export"]["available"], view["capabilities"]["export"])
        for fmt, name in (("pptx", "deck.pptx"), ("pdf", "deck.pdf")):
            r = self.export(deck, fmt)
            self.assertEqual(r.status, 201, r.body)
            ex = r.json()["export"]
            self.assertEqual((ex["format"], ex["run_id"], ex["state"]), (fmt, run["id"], "ready"))
            self.assertEqual(ex["filename"], f"会議を半分に_ 試す_計画.{fmt}")
            got = self.fx.get(ex["download_url"])
            self.assertEqual(got.status, 200, got.body[:200])
            on_disk = (self.fx.root.run_dir(deck, run["id"]) / name).read_bytes()
            self.assertEqual(got.body, on_disk)
            cd = got.headers["content-disposition"]
            self.assertTrue(cd.startswith("attachment;"), cd)
            self.assertEqual(unquote(cd.split("filename*=UTF-8''")[1]), ex["filename"])
            self.assertEqual(got.headers["content-type"],
                             "application/pdf" if fmt == "pdf" else
                             "application/vnd.openxmlformats-officedocument.presentationml.presentation")
            # 同じ回・同じ中身はもう一度押しても同じ記録（URL も同じ）
            again = self.export(deck, fmt).json()["export"]
            self.assertEqual(again["id"], ex["id"])
        with self.fx.server.store_lock:
            rows = self.fx.store.deck_exports(deck)
        self.assertEqual(sorted(r["format"] for r in rows), ["pdf", "pptx"])
        self.assertTrue(all(r["run_id"] == run["id"] and len(r["sha256"]) == 64 for r in rows))

    def test_page_images_zip_is_stable_and_leaves_out_hidden_pages(self) -> None:
        body, run = self.make()
        deck = body["deck_id"]
        first = self.export(deck, "images").json()["export"]
        z = zipfile.ZipFile(io.BytesIO(self.fx.get(first["download_url"]).body))
        self.assertEqual(z.namelist(), [f"slide-{i:02d}.png" for i in range(1, 5)])
        self.assertTrue(first["filename"].endswith(".zip"))
        self.assertEqual(self.export(deck, "images").json()["export"]["sha256"], first["sha256"])
        # 2 枚目を隠すと、新しい回の書き出しには入らない（エンジンに渡す台本から抜いてある）
        pages = self.fx.get(f"/api/decks/{deck}").json()["pages"]
        r = self.fx.post(f"/api/decks/{deck}/pages/{pages[1]['slide_id']}/visibility", {"hidden": True})
        self.assertEqual(r.status, 200, r.body)
        later = wait_job(self.fx, deck, r.json()["job_id"])
        self.assertEqual(later["state"], "done", later["message"])
        ex = self.export(deck, "images").json()["export"]
        self.assertEqual(ex["run_id"], later["id"])
        self.assertEqual(len(zipfile.ZipFile(io.BytesIO(self.fx.get(ex["download_url"]).body)).namelist()), 3)
        self.assertEqual(self.export(deck, "pptx").json()["export"]["run_id"], later["id"])
        # 前の書き出しの URL は前の回のバイトのまま
        self.assertEqual(len(zipfile.ZipFile(io.BytesIO(self.fx.get(first["download_url"]).body)).namelist()), 4)

    def test_a_missing_format_is_produced_by_rendering_again(self) -> None:
        self.fx.server.tools.extra_env["FAKE_ENGINE_SKIP"] = "pdf"
        body, run = self.make()
        deck = body["deck_id"]
        self.assertFalse((self.fx.root.run_dir(deck, run["id"]) / "deck.pdf").exists())
        del self.fx.server.tools.extra_env["FAKE_ENGINE_SKIP"]
        calls = len(writer_calls(self.log))
        r = self.export(deck, "pdf")
        self.assertEqual(r.status, 201, r.body)
        data = r.json()
        self.assertTrue(data["produced"])
        self.assertNotEqual(data["export"]["run_id"], run["id"])
        self.assertEqual(self.shown(deck)["id"], data["export"]["run_id"], "描き直した回が画面の回になる")
        self.assertTrue(self.fx.get(data["export"]["download_url"]).body.startswith(b"%PDF"))
        self.assertEqual(len(writer_calls(self.log)), calls, "台本係は呼ばない")

    def test_refusals(self) -> None:
        body, _ = self.make()
        deck = body["deck_id"]
        self.assertEqual(self.export(deck, "gif").error_code, "export_format")
        empty = self.fx.make_deck("ページの無いスライド")
        r = self.export(empty["id"], "pptx")
        self.assertEqual((r.status, r.error_code), (409, "no_pages"))
        ex = self.export(deck, "pptx").json()["export"]
        # 中身が変わったファイルは配らない（同じ URL は同じバイト）
        with self.fx.server.store_lock:
            path = self.fx.root.root / self.fx.store.export(ex["id"])["path"]
        data = path.read_bytes()
        path.unlink()
        path.write_bytes(data + b"changed")
        self.assertEqual(self.fx.get(ex["download_url"]).error_code, "export_changed")
        path.unlink()
        path.write_bytes(data)
        self.assertEqual(self.fx.get(ex["download_url"]).status, 200)
        # ごみ箱へ移したスライドの書き出しは配らない
        self.assertEqual(self.fx.post(f"/api/decks/{deck}/delete").status, 200)
        self.assertEqual(self.fx.get(ex["download_url"]).status, 404)
        self.assertEqual(self.fx.get(f"/api/exports/{empty['id']}/file").error_code, "export_not_found")

    def test_filenames(self) -> None:
        self.assertEqual(safe_filename("  a/b\\c:d*e?f\"g<h>i|j\x01  ", "pdf"), "a_b_c_d_e_f_g_h_i_j_.pdf")
        self.assertEqual(safe_filename("...", "pptx"), "スライド.pptx")
        self.assertEqual(len(safe_filename("長" * 300, "pptx")), 120)
        cd = content_disposition("会議.pdf")
        self.assertIn('filename="slides.pdf"', cd)
        self.assertIn("filename*=UTF-8''%E4%BC%9A%E8%AD%B0.pdf", cd)


class PurgeCacheTests(Base):
    def setUp(self) -> None:
        self.start()

    def area(self, base: Path, owner: Path, name: str) -> Path:
        d = base / name
        d.mkdir(parents=True)
        (d / "owner.json").write_text(json.dumps({"owner": str(owner), "used": "2026-10-07T00:00:00"}), encoding="utf-8")
        (d / "gen-x.png").write_bytes(b"x" * 1000)
        return d

    def test_purge_removes_the_deck_cache_areas_and_trash_keeps_them(self) -> None:
        body, _ = self.make()
        deck = body["deck_id"]
        other, _ = self.make(title="別のスライド")
        recorded_root = temp_dir(self, "qs-cache-") / "html-slide"
        env_root = temp_dir(self, "qs-cache-env-") / "image-slide"
        work = self.fx.root.work_dir(deck)
        mine = self.area(recorded_root, work, "work-aaaa")
        from_env = self.area(env_root, self.fx.root.scripts_dir(deck), "scripts-bbbb")
        theirs = self.area(recorded_root, self.fx.root.work_dir(other["deck_id"]), "work-cccc")
        stranger = recorded_root / "no-owner"
        stranger.mkdir()
        info = json.loads((work / "deck-info.json").read_text(encoding="utf-8"))
        (work / "deck-info.json").write_text(json.dumps({**info, "cache": str(mine)}), encoding="utf-8")
        with mock.patch.dict(os.environ, {"IMAGE_SLIDE_CACHE": str(env_root)}):
            self.assertEqual(sorted(deck_cache_areas(self.fx.root, deck)), sorted([mine.resolve(), from_env.resolve()]))
            self.assertEqual(self.fx.post(f"/api/decks/{deck}/delete").status, 200)
            self.assertTrue(mine.is_dir() and from_env.is_dir(), "ごみ箱の間は区画を残す")
            trash = self.fx.get("/api/trash").json()["items"]
            self.assertGreaterEqual(trash[0]["reclaimable_bytes"], 2000, "空く容量に区画も入る")
            r = self.fx.post(f"/api/decks/{deck}/purge")
            self.assertEqual(r.status, 200, r.body)
        self.assertFalse(mine.exists() or from_env.exists())
        self.assertTrue(theirs.is_dir() and stranger.is_dir(), "ほかのデッキの区画・印の無いフォルダは消さない")
        self.assertFalse(self.fx.root.deck_dir(deck).exists())


class RebuildTests(Base):
    def setUp(self) -> None:
        self.start()

    def test_rebuild_with_another_brand_redraws_without_the_writer(self) -> None:
        body, first = self.make()
        deck = body["deck_id"]
        look = self.fx.get(f"/api/decks/{deck}/look").json()
        self.assertEqual(look["mode"], "presentation")
        self.assertIn("文章は変わりません", look["notice"])
        self.assertTrue(look["capability"]["available"])
        brand = self.brand(primary="#2E7D32", accent="#2E7D32", logo=False, white=False, footer="", watermark="")
        self.assertIn(brand["id"], [b["id"] for b in self.fx.get(f"/api/decks/{deck}/look").json()["brands"]])
        calls = len(writer_calls(self.log))
        r = self.fx.post(f"/api/decks/{deck}/rebuild", {"brand_id": brand["id"]})
        self.assertEqual(r.status, 200, r.body)
        run = wait_job(self.fx, deck, r.json()["job_id"])
        self.assertEqual(run["state"], "done", run["message"])
        self.assertEqual(len(writer_calls(self.log)), calls, "台本係は呼ばない")
        self.assertEqual(run["script_id"], first["script_id"], "台本は同じ版")
        theme = Path(run["args"]["look"]["theme"]).read_text(encoding="utf-8")
        self.assertIn('primary: "#2E7D32"', theme)
        with self.fx.server.store_lock:
            d = self.fx.store.deck(deck)
            rev = self.fx.store.brand_revision(d["brand_revision_id"])
        self.assertEqual(rev["brand_id"], brand["id"])
        self.assertEqual(self.fx.get(f"/api/decks/{deck}/look").json()["brand"]["id"], brand["id"])
        # 何も選ばなくても組み直せる（いまの設定のまま）
        r = self.fx.post(f"/api/decks/{deck}/rebuild", {})
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual(wait_job(self.fx, deck, r.json()["job_id"])["state"], "done")
        argv = [c["argv"] for c in engine_calls(self.log) if c["argv"][0] == "deck"]
        self.assertEqual(len(argv), 3)

    def test_rebuild_refusals(self) -> None:
        empty = self.fx.make_deck("ページの無いスライド")
        self.assertEqual(self.fx.post(f"/api/decks/{empty['id']}/rebuild", {}).error_code, "no_pages")
        body, _ = self.make()
        r = self.fx.post(f"/api/decks/{body['deck_id']}/rebuild", {"brand_id": "00000000-0000-4000-8000-000000000000"})
        self.assertEqual((r.status, r.error_code), (400, "brand_not_found"))


class BrandFontTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = temp_dir(self, "qs-font-")
        self.root = DataRoot(self.tmp / "home")
        self.regular = self.tmp / "fonts" / "My-Regular.ttf"
        self.bold = self.tmp / "fonts" / "My-Bold.ttf"
        self.regular.parent.mkdir()
        self.regular.write_bytes(b"x")
        self.bold.write_bytes(b"x")

    def config(self, skill: str, fonts: dict) -> None:
        path = self.root.root / "engines" / skill / "config.yaml"
        path.parent.mkdir(parents=True)
        import yaml
        path.write_text(yaml.safe_dump({"fonts": fonts}, allow_unicode=True), encoding="utf-8")

    def brand(self, family: str, fallbacks=()) -> dict:
        return {"fonts": {"body": {"requested_family": family, "weight": 400, "fallback_families": list(fallbacks)},
                          "heading": {"requested_family": family, "weight": 700, "fallback_families": []}}}

    def test_known_files_are_passed_to_the_theme(self) -> None:
        self.config("html-slide", {"My Sans": {"regular": str(self.regular), "bold": str(self.bold)}})
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HTML_SLIDE_CONFIG", None)
            font = LOOK.brand_font(self.root, self.brand("My Sans"), "html-slide")
        self.assertEqual((font["applied"], font["files"]), ("My Sans", {"regular": str(self.regular), "bold": str(self.bold)}))
        text = LOOK.font_yaml(font)
        import yaml
        self.assertEqual(yaml.safe_load(text), {"font": "My Sans", "font_files": {"regular": str(self.regular),
                                                                                 "bold": str(self.bold)}})
        # 代わりの名前（fallback_families）も見る
        os.environ.pop("HTML_SLIDE_CONFIG", None)
        font = LOOK.brand_font(self.root, self.brand("Unknown Font", ["My Sans"]), "html-slide")
        self.assertEqual(font["applied"], "My Sans")
        self.assertIn("代わりの My Sans", font["note"])

    def test_unknown_or_default_fonts_keep_the_engine_default_and_say_why(self) -> None:
        os.environ.pop("IMAGE_SLIDE_CONFIG", None)
        font = LOOK.brand_font(self.root, self.brand("Noto Sans JP", ["Hiragino Sans", "sans-serif"]), "image-slide")
        self.assertIsNone(font["applied"])
        self.assertIn("Noto Sans JP", font["note"])
        self.assertEqual(LOOK.font_yaml(font), "")
        self.assertIsNone(LOOK.brand_font(self.root, self.brand("Noto Sans JP"), "html-slide")["note"])    # html-slide の既定は Noto Sans JP（ADR 0020）
        self.assertIsNone(LOOK.brand_font(self.root, self.brand("Yu Gothic"), "image-slide")["note"])     # image-slide は游ゴシックのまま
        self.assertIn("既定（Noto Sans JP）", LOOK.brand_font(self.root, self.brand("Unknown Font"), "html-slide")["note"])
        self.assertIn("既定（Yu Gothic）", LOOK.brand_font(self.root, self.brand("Unknown Font"), "image-slide")["note"])
        self.assertIsNone(LOOK.brand_font(self.root, {}, "html-slide")["requested"])
        # 字体ファイルが無ければ渡さない
        self.config("image-slide", {"Gone": {"regular": str(self.tmp / "nope.ttf")}})
        self.assertIsNone(LOOK.brand_font(self.root, self.brand("Gone"), "image-slide")["applied"])

    def test_the_real_engine_accepts_a_theme_with_brand_font_files(self) -> None:
        why = html_slide_ready()
        yu = Path("/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts")
        if why or not (yu / "YuGothR.ttc").is_file():
            self.skipTest(why or "游ゴシックの字体ファイルが無い")
        self.config("html-slide", {"Brand Gothic": {"regular": str(yu / "YuGothR.ttc"), "bold": str(yu / "YuGothB.ttc")}})
        font = LOOK.brand_font(self.root, self.brand("Brand Gothic"), "html-slide")
        theme = self.tmp / "theme.yaml"
        theme.write_text('background: "#FFFFFF"\nink: "#404040"\nprimary: "#0DB4F2"\n' + LOOK.font_yaml(font),
                         encoding="utf-8")
        env = {**os.environ, "QUICK_SLIDE_HOME": str(self.root.root), "PYTHONDONTWRITEBYTECODE": "1"}
        r = subprocess.run([sys.executable, str(SKILLS_DIR / "html-slide" / "scripts" / "html_slide.py"), "check",
                            "--script", str(SKILLS_DIR / "html-slide" / "examples" / "sample-deck.json"),
                            "--theme", str(theme)], capture_output=True, text=True, env=env, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])


if __name__ == "__main__":
    unittest.main()
