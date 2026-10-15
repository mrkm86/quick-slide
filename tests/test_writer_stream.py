"""逐次出力の保存と、途中の台本を完成扱いしない境界。"""
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from quick_slide.worker.proc import ProcResult, run_process
from quick_slide.writer.backends import WriterFailed, ask_claude


class WriterStreamTests(unittest.TestCase):
    def test_small_output_is_saved_before_exit_and_survives_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "events.jsonl"
            stop = threading.Event()
            results = []
            code = "import sys,time; sys.stdout.buffer.write('途中'.encode('utf-8')); sys.stdout.flush(); time.sleep(30)"
            thread = threading.Thread(target=lambda: results.append(run_process(
                [sys.executable, "-c", code], cwd=root, env=os.environ, timeout=3,
                stdout_path=output, stop=stop)))
            thread.start()
            try:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    if output.exists() and output.read_text(encoding="utf-8") == "途中":
                        break
                    time.sleep(.02)
                self.assertTrue(thread.is_alive())
                self.assertEqual(output.read_text(encoding="utf-8"), "途中")
                thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertTrue(results[0].timed_out)
                self.assertEqual(results[0].stdout, "途中")
                self.assertEqual(output.read_text(encoding="utf-8"), "途中")
                if os.name != "nt":
                    self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            finally:
                stop.set()
                thread.join(5)

    def test_split_utf8_and_newlines(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = ("import sys,time; b='絵\\r\\n'.encode('utf-8'); "
                    "sys.stdout.buffer.write(b[:1]); sys.stdout.flush(); time.sleep(.2); "
                    "sys.stdout.buffer.write(b[1:]); sys.stdout.flush()")
            result = run_process([sys.executable, "-c", code], cwd=Path(tmp), env=os.environ, timeout=5)
            self.assertTrue(result.ok)
            self.assertEqual(result.stdout, "絵\n")

    def call(self, result):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('quick_slide.writer.backends._help_text', return_value='--verbose --include-partial-messages'), \
                 patch('quick_slide.writer.backends._claude_flags', return_value=['--tools', '']), \
                 patch('quick_slide.writer.backends.run_process', return_value=result) as run:
                value = ask_claude('cli', '原稿', job_dir=root, env={}, timeout=1, stop=None)
                args, kwargs = run.call_args
                self.assertIn('stream-json', args[0])
                self.assertIn('--include-partial-messages', args[0])
                self.assertEqual(kwargs['stdout_path'], root / 'events.jsonl')
                return value

    def test_only_final_result_is_used(self):
        events = [{'type': 'system'}, {'type': 'stream_event', 'event': {'delta': {'text': '途中'}}},
                  {'type': 'assistant', 'message': {'content': []}},
                  {'type': 'result', 'is_error': False, 'result': '{"slides": []}'}]
        self.assertEqual(self.call(ProcResult(0, '\n'.join(map(json.dumps, events)))), '{"slides": []}')

    def test_incomplete_stream_is_not_a_script(self):
        for output in ('{"type":"assistant"}', '{"type":', ''):
            with self.subTest(output=output), self.assertRaises(WriterFailed) as error:
                self.call(ProcResult(0, output))
            self.assertEqual(error.exception.kind, 'failed')

    def test_old_cli_requires_upgrade_without_launching(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch('quick_slide.writer.backends._help_text', return_value='--verbose'), \
             patch('quick_slide.writer.backends._claude_flags', return_value=[]), \
             patch('quick_slide.writer.backends.run_process') as run:
            with self.assertRaises(WriterFailed) as error:
                ask_claude('cli', '原稿', job_dir=Path(tmp), env={}, timeout=1, stop=None)
            self.assertEqual(error.exception.kind, 'missing')
            run.assert_not_called()

    def test_timeout_and_error_classification(self):
        for result, kind in ((ProcResult(-9, '', timed_out=True), 'timeout'),
                             (ProcResult(-9, '', stopped=True), 'stopped'),
                             (ProcResult(1, json.dumps({'type': 'result', 'is_error': True,
                                                       'result': 'Not logged in'})), 'auth'),
                             (ProcResult(1, json.dumps({'type': 'result', 'is_error': True,
                                                       'result': 'usage limit reached'})), 'rate')):
            with self.subTest(kind=kind), self.assertRaises(WriterFailed) as error:
                self.call(result)
            self.assertEqual(error.exception.kind, kind)
