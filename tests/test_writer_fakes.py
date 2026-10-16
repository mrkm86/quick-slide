"""並列で使う偽物の台本係は、完成した呼び出しログだけを公開する。"""

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class WriterLogTests(unittest.TestCase):
    def test_readers_never_see_a_partially_written_record(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"FAKE_WRITER_LOG": tmp}):
            spec = importlib.util.spec_from_file_location(
                "writer_log_fake", Path(__file__).parent / "fakes" / "writer_common.py")
            writer = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(writer)
            original = Path.write_text

            def write_in_parts(path, text, **kwargs):
                original(path, "{", **kwargs)
                # ほかの CLI が、書き込みの途中で公開済みのログを読んでも壊れない。
                for visible in Path(tmp).glob("*.json"):
                    json.loads(visible.read_text(encoding="utf-8"))
                return original(path, text, **kwargs)

            writer.record(kind="claude", what="motion_plan")
            with patch.object(Path, "write_text", autospec=True, side_effect=write_in_parts):
                writer.record(kind="claude", what="motion_scene")
            self.assertEqual(writer.calls("claude"), 2)
            self.assertEqual(sorted(json.loads(p.read_text(encoding="utf-8"))["what"]
                                    for p in Path(tmp).glob("*.json")), ["motion_plan", "motion_scene"])
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
