"""発表の共有字体: リンクできなくても一度だけ保存し、過去の回・配信の境界を保つ。"""

from __future__ import annotations

import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from helpers import can_symlink, temp_dir
from quick_slide.paths import DataRoot
from quick_slide.render import workspace as W
from quick_slide.server.static import PresentFiles


class SharedFontTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = DataRoot.resolve(temp_dir(self, "present-fonts-"))
        self.deck = str(uuid.uuid4())
        self.font = self.root.root / "source # 字体.ttc"
        self.font.write_bytes(b"first-font")
        self.work = self.root.root / "work"
        self.work.mkdir()
        (self.work / "deck.html").write_text(f'<style>src: url("{self.font.as_uri()}");</style>', encoding="utf-8")
        self.name = W.present_font_name(W.font_path_of_url(self.font.as_uri()[len("file://"):]))
        self.files = PresentFiles(self.root.root)

    def snapshot(self):
        run = str(uuid.uuid4())
        folder = self.root.run_dir(self.deck, run)
        self.assertTrue(W.snapshot_present(self.work, folder, self.root.present_fonts_dir(self.deck)))
        return run, folder

    def read_font(self, run):
        file = self.files.resolve(self.deck, run, f"fonts/{self.name}")
        self.assertIsNotNone(file)
        return file.read()

    def test_link_failure_still_saves_only_one_copy(self) -> None:
        with mock.patch.object(W.os, "link", side_effect=OSError("links unavailable")), \
                mock.patch.object(W.shutil, "copyfile", wraps=W.shutil.copyfile) as copy:
            first, a = self.snapshot()
            second, b = self.snapshot()
        font_copies = [c for c in copy.call_args_list if c.args[0] == self.font]
        self.assertEqual(len(font_copies), 1)
        shared = list(self.root.present_fonts_dir(self.deck).iterdir())
        self.assertEqual(len(shared), 1)
        self.assertEqual(shared[0].stat().st_nlink, 1)
        self.assertFalse((a / W.PRESENT / "fonts").exists())
        self.assertFalse((b / W.PRESENT / "fonts").exists())
        self.assertEqual(self.read_font(first), b"first-font")
        self.assertEqual(self.read_font(second), b"first-font")

    def test_font_update_preserves_old_runs_and_source_can_disappear(self) -> None:
        first, _ = self.snapshot()
        self.font.write_bytes(b"updated-font")
        second, _ = self.snapshot()
        self.font.unlink()
        self.assertEqual(self.read_font(first), b"first-font")
        self.assertEqual(self.read_font(second), b"updated-font")
        self.assertEqual(len(list(self.root.present_fonts_dir(self.deck).iterdir())), 2)

    def test_parallel_runs_publish_a_complete_shared_font(self) -> None:
        with ThreadPoolExecutor(max_workers=4) as pool:
            runs = list(pool.map(lambda _: self.snapshot(), range(8)))
        self.assertEqual(len(list(self.root.present_fonts_dir(self.deck).iterdir())), 1)
        for run, _ in runs:
            self.assertEqual(self.read_font(run), b"first-font")

    def test_same_bytes_at_another_path_are_shared(self) -> None:
        self.snapshot()
        other = self.root.root / "other.ttc"
        other.write_bytes(self.font.read_bytes())
        html = self.work / "deck.html"
        html.write_text(html.read_text(encoding="utf-8") + f'url("{other.as_uri()}")', encoding="utf-8")
        _, folder = self.snapshot()
        names = W.read_json(folder / W.PRESENT / W.PRESENT_FONTS_INDEX)
        self.assertEqual(len(names), 2)
        self.assertEqual(len(set(names.values())), 1)
        self.assertEqual(len(list(self.root.present_fonts_dir(self.deck).iterdir())), 1)

    def test_legacy_run_copy_is_still_served(self) -> None:
        run, folder = self.snapshot()
        (folder / W.PRESENT / W.PRESENT_FONTS_INDEX).unlink()
        old = folder / W.PRESENT / "fonts" / self.name
        old.parent.mkdir()
        old.write_bytes(b"legacy-font")
        self.assertEqual(self.read_font(run), b"legacy-font")

    def test_manifest_cannot_expose_other_files_or_unreferenced_fonts(self) -> None:
        run, folder = self.snapshot()
        index = folder / W.PRESENT / W.PRESENT_FONTS_INDEX
        valid = W.read_json(index)[self.name]
        for bad in ("../deck.html", "/tmp/font.ttc", "A" * 64 + ".ttc", valid.replace(".ttc", ".ttf"),
                    {"path": valid}, None):
            with self.subTest(bad=bad):
                W.write_json(index, {self.name: bad})
                self.assertIsNone(self.files.resolve(self.deck, run, f"fonts/{self.name}"))
        W.write_json(index, {self.name: valid})
        self.assertIsNone(self.files.resolve(self.deck, run, "fonts/0000000000000000.ttc"))
        # 内容の名前を URL に渡しても、目録の鍵に無い字体は配らない。
        self.assertIsNone(self.files.resolve(self.deck, run, f"fonts/{valid}"))
        index.write_text("not json", encoding="utf-8")
        self.assertIsNone(self.files.resolve(self.deck, run, f"fonts/{self.name}"))

    @unittest.skipUnless(can_symlink(), "symlink を作れない環境")
    def test_symlinks_in_manifest_and_shared_fonts_are_refused(self) -> None:
        run, folder = self.snapshot()
        index = folder / W.PRESENT / W.PRESENT_FONTS_INDEX
        stored = self.root.present_fonts_dir(self.deck) / W.read_json(index)[self.name]
        stored.unlink()
        stored.symlink_to(self.font)
        self.assertIsNone(self.files.resolve(self.deck, run, f"fonts/{self.name}"))
        stored.unlink()
        stored.write_bytes(b"first-font")
        outside = self.root.root / "outside.json"
        outside.write_bytes(index.read_bytes())
        index.unlink()
        index.symlink_to(outside)
        self.assertIsNone(self.files.resolve(self.deck, run, f"fonts/{self.name}"))
