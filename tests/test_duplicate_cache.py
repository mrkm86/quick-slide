"""複製の絵の引き継ぎ（`render/caches.seed_duplicate`）: 区画の根っこは、いまエンジンに渡す環境の置き場の決まりに従う。

キャッシュの置き場を替えた（環境変数か設定の `cache_dir`）あとに複製しても、エンジンが探す区画へ張る。
写した回の記録の `cache` は複製の区画に書き換え、元の回とファイルを共有しない。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from quick_slide.paths import DataRoot
from quick_slide.render import caches


class SeedDuplicateTests(unittest.TestCase):
    def test_uses_the_current_cache_root_and_rewrites_the_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            root = DataRoot.resolve(tmp / "home").ensure()
            old_area = tmp / "old-root" / "scripts-aaaaaaaaaaaa"
            old_area.mkdir(parents=True)
            run = root.run_dir("d1", "r1")
            (run / "pictures").mkdir(parents=True)
            (run / "pictures" / "gen-k1.png").write_bytes(b"png")
            (run / "pictures" / "gen-k1.json").write_text("{}", encoding="utf-8")
            (run / "deck-info.json").write_text(json.dumps({"cache": str(old_area)}), encoding="utf-8")
            (run / "pictures" / "index.json").write_text(json.dumps({"cache": str(old_area), "pages": {}}), encoding="utf-8")
            new_root = tmp / "new-root"
            self.assertEqual(caches.seed_duplicate(root, "d1", {"HTML_SLIDE_CACHE": str(new_root)}), 1)
            areas = [d for d in new_root.iterdir() if d.is_dir()]
            self.assertEqual(len(areas), 1, "いまの根っこ（替えた先）に区画を作る")
            area = areas[0]
            self.assertEqual(json.loads((area / "owner.json").read_text(encoding="utf-8"))["owner"], str(root.scripts_dir("d1").resolve()))
            self.assertEqual((area / "gen-k1.png").stat().st_ino, (run / "pictures" / "gen-k1.png").stat().st_ino, "絵は張る")
            self.assertNotEqual((area / "gen-k1.json").stat().st_ino, (run / "pictures" / "gen-k1.json").stat().st_ino,
                                "添えた JSON は写す")
            self.assertEqual(json.loads((run / "deck-info.json").read_text(encoding="utf-8"))["cache"], str(area))
            self.assertEqual(json.loads((run / "pictures" / "index.json").read_text(encoding="utf-8"))["cache"], str(area))
            self.assertEqual(list(old_area.iterdir()), [], "元の区画には触らない")

    def _run_with_picture(self, root: DataRoot, old_area: Path) -> Path:
        run = root.run_dir("d1", "r1")
        (run / "pictures").mkdir(parents=True)
        (run / "pictures" / "gen-k1.png").write_bytes(b"png")
        (run / "deck-info.json").write_text(json.dumps({"cache": str(old_area)}), encoding="utf-8")
        return run

    def test_relative_root_is_from_the_work_folder_and_config_comes_from_the_child_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            root = DataRoot.resolve(tmp / "home").ensure()
            self._run_with_picture(root, tmp / "old" / "a")
            caches.seed_duplicate(root, "d1", {"HTML_SLIDE_CACHE": "rel-cache"})
            self.assertTrue(any((root.work_dir("d1") / "rel-cache").glob("scripts-*/gen-k1.png")),
                            "相対の指定はエンジンが動く work/ から")
            cfg = tmp / "cfg.yaml"
            cfg.write_text(f"cache_dir: {tmp / 'from-config'}\n", encoding="utf-8")
            root2 = DataRoot.resolve(tmp / "home2").ensure()
            self._run_with_picture(root2, tmp / "old" / "b")
            caches.seed_duplicate(root2, "d1", {"HTML_SLIDE_CONFIG": str(cfg), "HTML_SLIDE_CACHE": ""})
            self.assertTrue(any((tmp / "from-config").glob("scripts-*/gen-k1.png")), "設定ファイルは子の環境のものを読む")

    @unittest.skipIf(os.name == "nt", "Windows は chmod で書けないフォルダを作れない（ADR 0012）")
    def test_records_never_keep_pointing_at_the_original_area(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            root = DataRoot.resolve(tmp / "home").ensure()
            old_area = tmp / "old" / "a"
            run = self._run_with_picture(root, old_area)
            locked = tmp / "locked"
            locked.mkdir()
            locked.chmod(0o500)                  # 区画を作れない
            try:
                with self.assertRaises(OSError):
                    caches.seed_duplicate(root, "d1", {"HTML_SLIDE_CACHE": str(locked)})
            finally:
                locked.chmod(0o700)
            cache = json.loads((run / "deck-info.json").read_text(encoding="utf-8")).get("cache")
            self.assertNotEqual(cache, str(old_area), "張る前に記録を複製の区画へ書き換える")
            (run / "deck-info.json").write_text(json.dumps({"cache": str(old_area)}), encoding="utf-8")
            caches.forget_duplicate_cache(root, "d1")
            self.assertNotIn("cache", json.loads((run / "deck-info.json").read_text(encoding="utf-8")), "元の区画を指す記録は外す")


if __name__ == "__main__":
    unittest.main()
