"""全件を発見して、ファイルごとに隔離したプロセスで実行する。"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def order_groups(groups, timings):
    """実測が長いモジュールから投入する。同じ長さなら発見順を保つ。"""
    return sorted(groups, key=lambda names: timings.get(names[0].rsplit(".", 2)[0], 0), reverse=True)


def load_timings():
    path = Path(__file__).with_name("durations.json")
    if not path.exists():
        return {}
    timings = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(timings, dict) or any(
        not isinstance(name, str) or isinstance(seconds, bool)
        or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0
        for name, seconds in timings.items()
    ):
        raise ValueError("durations.json はモジュール名と非負の秒数の対応にしてください")
    return timings


def worker(manifest: str, report: str, verbose: bool) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    names = json.loads(Path(manifest).read_text(encoding="utf-8"))
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    selected = suite.countTestCases()
    result = unittest.TextTestRunner(verbosity=2 if verbose else 1).run(suite)
    Path(report).write_text(json.dumps({"selected": selected, "run": result.testsRun,
                                      "skipped": len(result.skipped)}), encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-j", "--jobs", type=int, default=4)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-k", action="append", default=[])
    parser.add_argument("--worker", nargs=2, metavar=("MANIFEST", "REPORT"))
    args = parser.parse_args()
    if args.worker:
        return worker(*args.worker, args.verbose)
    if args.jobs < 1:
        parser.error("並列数は 1 以上にしてください")
    loader = unittest.TestLoader()
    if args.k:
        loader.testNamePatterns = [k if "*" in k else f"*{k}*" for k in args.k]
    # unittest discover と同じ発見を行う。重複する ID も省かない。
    suite = loader.discover("tests", top_level_dir="tests")
    for error in loader.errors:
        print(error, file=sys.stderr, flush=True)
    by_module = {}
    for test in cases(suite):
        name = test.id()
        by_module.setdefault(name.rsplit(".", 2)[0], []).append(name)
    groups = list(by_module.values())
    if args.jobs > 1:
        try:
            groups = order_groups(groups, load_timings())
        except (ValueError, OSError) as error:
            parser.error(str(error))
    expected = sum(map(len, groups))
    started = time.monotonic()

    def run(names):
        # 消せないものが残っても、ほかの組の結果を捨てない（Windows では握られたファイルを消せない。残りは run.sh が最後に消す）
        with tempfile.TemporaryDirectory(prefix="qs-test-process-", ignore_cleanup_errors=True) as folder:
            root = Path(folder)
            env = os.environ.copy()
            for key, directory in (("TMPDIR", "tmp"), ("CODEX_HOME", "no-codex-home"),
                                   ("HTML_SLIDE_CACHE", "html-cache"), ("IMAGE_SLIDE_CACHE", "image-cache"),
                                   ("MOTION_SLIDE_CACHE", "motion-cache")):
                path = root / directory
                path.mkdir()
                env[key] = str(path)
            manifest, report = root / "cases.json", root / "report.json"
            manifest.write_text(json.dumps(names), encoding="utf-8")
            command = [sys.executable, str(Path(__file__).resolve()), "--worker", str(manifest), str(report)]
            if args.verbose:
                command.append("-v")
            before = time.monotonic()
            proc = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding="utf-8", errors="replace")
            stats = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {"selected": 0, "run": 0, "skipped": 0}
            return names[0].rsplit(".", 2)[0], proc.returncode, proc.stdout, stats, time.monotonic() - before

    print(f"{expected} tests / {len(groups)} groups / {args.jobs} processes", flush=True)
    selected = total = skipped = failed = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        pending = [pool.submit(run, names) for names in groups]
        for future in as_completed(pending):
            name, rc, output, stats, elapsed = future.result()
            total += stats["run"]
            selected += stats["selected"]
            skipped += stats["skipped"]
            failed += bool(rc)
            print(f"{'FAIL' if rc else 'OK'} {name}: {stats['run']} tests, {elapsed:.1f}s", flush=True)
            if rc or args.verbose:
                print(output, end="", flush=True)
    # setUpClass の skip は testsRun に入らない。ロード件数を照合し、結果は unittest に従う。
    if selected != expected:
        print(f"FAIL: discovered {expected}, loaded {selected}", flush=True)
        failed += 1
    print(f"Ran {total} tests in {time.monotonic() - started:.1f}s; skipped={skipped}, failed groups={failed}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
