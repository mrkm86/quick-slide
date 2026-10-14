"""版の置き場（`__version__` とプラグインのマニフェスト 2 つ）が揃っていること・CHANGELOG の形・リリースの手伝い
（tools/release.py。docs/RELEASING.md）。`prepare` は写しの上で試す（本物のファイルは書かない）。"""

from __future__ import annotations

import importlib.util
import json
import shutil
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from helpers import temp_dir

from quick_slide import __version__

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_tool", ROOT / "tools" / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)  # type: ignore[union-attr]


class RepositoryTests(unittest.TestCase):
    def test_versions_agree_and_changelog_is_well_formed(self) -> None:
        self.assertEqual(release.check(), [])
        self.assertEqual(set(release.versions().values()), {__version__})


class ReleaseToolTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = temp_dir(self)
        for rel in ("src/quick_slide/__init__.py", ".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
            (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, tmp / rel)
        self.tmp = tmp
        patches = {"ROOT": tmp, "INIT_PY": tmp / "src/quick_slide/__init__.py",
                   "MANIFESTS": (tmp / ".claude-plugin/plugin.json", tmp / ".codex-plugin/plugin.json"),
                   "CHANGELOG": tmp / "CHANGELOG.md"}
        for name, value in patches.items():
            p = mock.patch.object(release, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.write_versions("2.0.0")

    def write_versions(self, version: str) -> None:
        init = self.tmp / "src/quick_slide/__init__.py"
        init.write_text(f'__version__ = "{version}"\n', encoding="utf-8")
        for path in release.MANIFESTS:
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["version"] = version
            path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def changelog(self, text: str) -> None:
        release.CHANGELOG.write_text(text, encoding="utf-8")

    def test_first_release_keeps_the_version(self) -> None:
        self.changelog("# 変更の記録\n\n## [Unreleased]\n\n### 追加\n\n- 最初\n")
        release.prepare("2.0.0", today=date(2026, 11, 1))
        text = release.CHANGELOG.read_text(encoding="utf-8")
        self.assertEqual(text, "# 変更の記録\n\n## [Unreleased]\n\n## [2.0.0] - 2026-11-01\n\n### 追加\n\n- 最初\n")
        self.assertEqual(release.notes("2.0.0"), "### 追加\n\n- 最初\n")
        self.assertEqual(release.check(), [])

    def test_next_release_bumps_every_place(self) -> None:
        self.changelog("## [Unreleased]\n\n### 修正\n\n- 直した\n\n## [2.0.0] - 2026-11-01\n\n- 最初\n")
        release.prepare("2.0.1", today=date(2026, 11, 2))
        self.assertEqual(set(release.versions().values()), {"2.0.1"})
        self.assertEqual(release.notes("2.0.1"), "### 修正\n\n- 直した\n")
        self.assertEqual(release.notes("2.0.0"), "- 最初\n")
        self.assertEqual(release.check(), [])
        # マニフェストのほかの項目はそのまま
        manifest = json.loads(release.MANIFESTS[1].read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "quick-slide")

    def test_refusals(self) -> None:
        self.changelog("## [Unreleased]\n\n## [2.0.0] - 2026-11-01\n\n- 最初\n")
        with self.assertRaisesRegex(release.ReleaseError, "空"):
            release.prepare("2.0.1")
        self.changelog("## [Unreleased]\n\n- 何か\n\n## [2.0.0] - 2026-11-01\n\n- 最初\n")
        for bad in ("2.0.0", "1.9.0", "2.1", "v2.1.0"):
            with self.assertRaises(release.ReleaseError, msg=bad):
                release.prepare(bad)
        with self.assertRaisesRegex(release.ReleaseError, "節がありません"):
            release.notes("9.9.9")

    def test_check_finds_problems(self) -> None:
        self.changelog("## [Unreleased]\n\n## [2.0.0] - 2026-11-01\n\n- a\n")
        (self.tmp / "src/quick_slide/__init__.py").write_text('__version__ = "2.0.1"\n', encoding="utf-8")
        self.assertTrue(any("揃っていません" in p for p in release.check()))
        self.write_versions("2.0.0")
        self.changelog("## [2.0.0] - 2026-11-01\n\n- a\n")
        self.assertTrue(any("Unreleased" in p for p in release.check()))
        self.changelog("## [Unreleased]\n\n## [2.0.0] - 2026-11-01\n\n## [2.1.0] - 2026-11-02\n")
        self.assertTrue(release.check())
        self.changelog("## [Unreleased]\n\n## [2.0.0]\n")
        self.assertTrue(any("見出しの形" in p for p in release.check()))


if __name__ == "__main__":
    unittest.main()
