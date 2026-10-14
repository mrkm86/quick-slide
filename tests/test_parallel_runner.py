"""並列ランナーが失敗・skip・重複する ID を失わないこと。"""

import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import unittest

from helpers import temp_dir
import parallel as runner


class ParallelRunnerTests(unittest.TestCase):
    def test_long_first_keeps_groups_duplicates_and_stable_ties(self):
        groups = [["short.C.test_a"], ["long.C.test_b"] * 2,
                  ["tie.C.test_c"], ["unknown.C.test_d"]]
        ordered = runner.order_groups(groups, {"short": 1, "long": 10, "tie": 10, "obsolete": 100})
        self.assertEqual(ordered, [groups[1], groups[2], groups[0], groups[3]])
        self.assertIs(ordered[0], groups[1])
        self.assertEqual(len(ordered[0]), 2)

    def test_parent_starts_long_groups_first(self):
        root = temp_dir(self, "qs-runner-order-")
        directory = root / "tests"
        directory.mkdir()
        entry = root / "parallel.py"
        shutil.copyfile(Path(__file__).with_name("parallel.py"), entry)
        (root / "durations.json").write_text(json.dumps({"test_fixture_a": 1, "test_fixture_b": 10,
                                                       "test_fixture_c": 20}), encoding="utf-8")
        for name in ("a", "b", "c"):
            (directory / f"test_fixture_{name}.py").write_text(
                "import os, time, unittest\n"
                "class Cases(unittest.TestCase):\n"
                "    def test_case(self):\n"
                "        with open(os.environ['RUNNER_TRACE'], 'a', encoding='utf-8') as out:\n"
                f"            out.write('{name}\\n')\n"
                "        time.sleep(1)\n", encoding="utf-8")
        trace = root / "trace.txt"
        proc = subprocess.run([sys.executable, str(entry), "-j", "2"], cwd=root,
                              env={**os.environ, "PYTHONPATH": str(directory), "RUNNER_TRACE": str(trace)},
                              capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(set(trace.read_text(encoding="utf-8").splitlines()[:2]), {"b", "c"})
        self.assertIn("Ran 3 tests", proc.stdout)

    def test_worker_keeps_duplicate_ids_and_reports_failure_and_skip(self):
        root = temp_dir(self, "qs-runner-")
        (root / "runner_fixture.py").write_text(
            "import unittest\n"
            "class Cases(unittest.TestCase):\n"
            "    def test_ok(self): pass\n"
            "    @unittest.skip('fixture skip')\n"
            "    def test_skip(self): pass\n"
            "    def test_fail(self): self.fail('fixture failure')\n", encoding="utf-8")
        manifest, report = root / "manifest.json", root / "report.json"
        names = ["runner_fixture.Cases.test_ok"] * 2 + ["runner_fixture.Cases.test_skip", "runner_fixture.Cases.test_fail"]
        manifest.write_text(json.dumps(names), encoding="utf-8")
        env = {**os.environ, "PYTHONPATH": str(root)}
        proc = subprocess.run([sys.executable, str(Path(__file__).with_name("parallel.py")),
                               "--worker", str(manifest), str(report)], env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(report.read_text(encoding="utf-8")), {"selected": 4, "run": 4, "skipped": 1})
        self.assertIn("fixture failure", proc.stderr)

    def test_invalid_parallelism_is_rejected(self):
        proc = subprocess.run([sys.executable, str(Path(__file__).with_name("parallel.py")), "-j", "0"],
                              capture_output=True, timeout=30)
        self.assertEqual(proc.returncode, 2)

    def test_parent_runs_all_groups_and_propagates_failure(self):
        root = temp_dir(self, "qs-runner-parent-")
        directory = root / "tests"
        directory.mkdir()
        for name, body in (("a", "self.fail('parent fixture failure')"),
                           ("b", "pass"), ("c", "self.skipTest('fixture skip')")):
            (directory / f"test_fixture_{name}.py").write_text(
                f"import unittest\nclass Cases(unittest.TestCase):\n    def test_case(self): {body}\n",
                encoding="utf-8")
        proc = subprocess.run([sys.executable, str(Path(__file__).with_name("parallel.py")), "-j", "2"],
                              cwd=root, env={**os.environ, "PYTHONPATH": str(directory)},
                              capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("parent fixture failure", proc.stdout)
        self.assertIn("OK test_fixture_b", proc.stdout)
        self.assertIn("OK test_fixture_c", proc.stdout)
        self.assertIn("Ran 3 tests", proc.stdout)
        self.assertIn("skipped=1, failed groups=1", proc.stdout)

    def test_class_skip_does_not_look_like_missing_tests(self):
        root = temp_dir(self, "qs-runner-skip-")
        directory = root / "tests"
        directory.mkdir()
        (directory / "test_fixture_skip.py").write_text(
            "import unittest\n"
            "class Cases(unittest.TestCase):\n"
            "    @classmethod\n"
            "    def setUpClass(cls): raise unittest.SkipTest('class fixture skip')\n"
            "    def test_a(self): pass\n"
            "    def test_b(self): pass\n", encoding="utf-8")
        proc = subprocess.run([sys.executable, str(Path(__file__).with_name("parallel.py"))],
                              cwd=root, env={**os.environ, "PYTHONPATH": str(directory)},
                              capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("2 tests / 1 groups / 4 processes", proc.stdout)
        self.assertIn("skipped=1, failed groups=0", proc.stdout)
