"""別資料・別バックエンドでもCLIの総数を抑え、待機中に停止できる。"""
import queue
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from quick_slide.writer import backends as B


class WriterLimitsTests(unittest.TestCase):
    def test_two_callers_share_eight_slots(self):
        entered = queue.Queue()
        release = threading.Event()
        lock = threading.Lock()
        active = peak = 0

        def run(label, *, stop=None):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            entered.put(label)
            try:
                self.assertTrue(release.wait(5))
                return label
            finally:
                with lock:
                    active -= 1

        first, second = B._limit_cli(run), B._limit_cli(run)
        with ThreadPoolExecutor(max_workers=12) as pool:
            jobs = [pool.submit(first if i % 2 else second, i) for i in range(12)]
            try:
                for _ in range(8):
                    entered.get(timeout=3)
                with self.assertRaises(queue.Empty):
                    entered.get(timeout=.2)
            finally:
                release.set()
            self.assertEqual(sorted(j.result(timeout=3) for j in jobs), list(range(12)))
        self.assertEqual(peak, 8)

    def test_stop_while_waiting_does_not_start_a_cli(self):
        slots = threading.BoundedSemaphore(1)
        slots.acquire()
        stop = threading.Event()
        calls = []
        runner = B._limit_cli(lambda *, stop: calls.append('started'))
        with patch.object(B, '_cli_slots', slots), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(runner, stop=stop)
            stop.set()
            with self.assertRaises(B.WriterFailed) as error:
                future.result(timeout=2)
            self.assertEqual(error.exception.kind, 'stopped')
        self.assertEqual(calls, [])
        slots.release()

    def test_exception_releases_the_slot(self):
        @B._limit_cli
        def fail():
            raise ValueError('failure')

        @B._limit_cli
        def succeed():
            return 'ok'

        with patch.object(B, '_cli_slots', threading.BoundedSemaphore(1)):
            with self.assertRaises(ValueError):
                fail()
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertEqual(pool.submit(succeed).result(timeout=2), 'ok')
