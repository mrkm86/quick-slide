"""場面の並列生成、途中保存、再開、検査後の修正範囲。"""
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from quick_slide.writer import motion as M
from quick_slide.writer.backends import WriterFailed
from quick_slide.server.vocabulary import planning_message


def plan(n=4):
    return {'title': '試験（架空）', 'design': '共通の造形', 'config': {'chapters': []},
            'scenes': [{'id': f's{i}', 'title': f'場面{i}', 'beats': 2, 'brief': f'変化{i}'} for i in range(n)]}


def scene(spec):
    return {**{k: spec[k] for k in ('id', 'title', 'beats')}, 'html': '<p>試験（架空）</p>', 'talk': ['一', '二']}


class MotionWriterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.calls = []
        self.guard = threading.Lock()
        self.active = self.peak = 0
        self.plan = plan()
        self.fail = set()
        self.barrier = None
        self.progress = []

    def ask(self, backend, exe, prompt, **kw):
        name = kw['job_dir'].name
        with self.guard:
            self.calls.append(name)
        if name == 'plan':
            return copy.deepcopy(self.plan)
        if name == 'titles':
            return {'titles': {s['id']: '短' + s['id'] for s in self.plan['scenes']}}
        spec = json.JSONDecoder().raw_decode(prompt.split('# 対象場面\n', 1)[1])[0]
        with self.guard:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if self.barrier and int(spec['id'][1:]) < self.barrier.parties:
                self.barrier.wait(timeout=5)
            if name in self.fail:
                raise WriterFailed('timeout', '時間切れ')
            self.assertIn('共通の造形', prompt)
            self.assertIn('原稿（架空）', prompt)
            return scene(spec)
        finally:
            with self.guard:
                self.active -= 1

    def write(self, **kw):
        args = dict(prompt='原稿（架空）', backend='claude', exe='cli', env={}, timeout=1,
                    stop=threading.Event(), checkpoint_dir=self.root / 'cache', job_dir=self.root / 'run',
                    parallel=4, progress=lambda *v: self.progress.append(v))
        args.update(kw)
        with patch.object(M, 'ask', side_effect=self.ask):
            return M.write(**args)

    def test_parallel_generation_preserves_plan_order_and_common_header(self):
        self.barrier = threading.Barrier(4)
        data = self.write()
        self.assertEqual(self.peak, 4)
        self.assertEqual([s['id'] for s in data['slides']], ['s0', 's1', 's2', 's3'])
        self.assertEqual(data['config'], self.plan['config'])
        self.assertNotIn('design', data)
        self.assertEqual(self.progress[-1], ('motion_scenes', 4, 4))

    def test_scene_parallelism_cannot_exceed_eight(self):
        self.plan = plan(12)
        self.barrier = threading.Barrier(8)
        data = self.write(parallel=12)
        self.assertEqual(self.peak, 8)
        self.assertEqual(len(data['slides']), 12)

    def test_resume_uses_finished_scenes_after_timeout(self):
        self.barrier = threading.Barrier(4)
        self.fail = {'s2'}
        with self.assertRaises(WriterFailed) as error:
            self.write()
        self.assertEqual(error.exception.kind, 'timeout')
        self.assertIn('3/4', error.exception.message)
        self.fail.clear()
        self.barrier = None
        self.calls.clear()
        data = self.write(job_dir=self.root / 'retry')
        self.assertEqual(self.calls, ['s2'])
        self.assertEqual(len(data['slides']), 4)

    def test_only_failed_scene_is_rewritten(self):
        previous = self.write()
        self.calls.clear()
        data = self.write(previous=previous, problems=['NG: 2枚目（id s1）: はみ出し'])
        self.assertEqual(self.calls, ['s1'])
        self.assertEqual(previous, data)

    def test_rail_titles_are_shortened_without_rewriting_html_and_survive_resume(self):
        previous = self.write()
        self.calls.clear()
        data = self.write(previous=previous, problems=['NG: 2枚目（id s1）: 章のレールの題が長い'])
        self.assertEqual(self.calls, ['titles'])
        self.assertEqual([s['html'] for s in data['slides']], [s['html'] for s in previous['slides']])
        self.assertTrue(all(s['title'].startswith('短') for s in data['slides']))
        self.calls.clear()
        self.assertEqual(self.write(), data)
        self.assertEqual(self.calls, [])

    def test_mixed_rail_and_layout_errors_rewrite_only_the_bad_html(self):
        previous = self.write()
        self.calls.clear()
        self.write(previous=previous, problems=['NG: 2枚目（id s1）: 章のレールの題が長い',
                                                'NG: 1枚目（id s0）: はみ出し'])
        self.assertEqual(set(self.calls), {'titles', 's0'})

    def test_global_problem_replans_and_rewrites_even_if_plan_is_unchanged(self):
        previous = self.write()
        self.calls.clear()
        self.write(previous=previous, problems=['NG: 共通 CSS が不正'])
        self.assertEqual(set(self.calls), {'plan', 's0', 's1', 's2', 's3'})

    def test_changed_input_or_backend_does_not_reuse_old_scenes(self):
        self.write()
        for kw in ({'prompt': '変更した原稿（架空）'}, {'backend': 'codex'}):
            self.calls.clear()
            self.write(**kw)
            self.assertEqual(len(self.calls), 5)

    def test_bad_checkpoint_is_rebuilt(self):
        self.write()
        for p in (self.root / 'cache').rglob('s1.json'):
            p.write_text('{broken', encoding='utf-8')
        self.calls.clear()
        self.write()
        self.assertEqual(self.calls, ['s1'])

    def test_invalid_plans_and_scene_identity_are_rejected(self):
        for value in ({'scenes': []}, {'design': '造形', 'scenes': [self.plan['scenes'][0]] * 2}):
            with self.subTest(value=value), self.assertRaises(WriterFailed):
                M._plan(value, None)
        with self.assertRaises(WriterFailed):
            M._plan(plan(), 13)
        with self.assertRaises(WriterFailed):
            M._scene(scene(self.plan['scenes'][1]), self.plan['scenes'][0])
        with self.assertRaises(WriterFailed):
            M._scene({**scene(self.plan['scenes'][0]), 'talk': []}, self.plan['scenes'][0])

    def test_shutdown_stops_before_scene_calls(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(WriterFailed) as error:
            self.write(stop=stop)
        self.assertEqual(error.exception.kind, 'stopped')
        self.assertEqual(self.calls, [])

    def test_planner_gets_materials_but_not_implementation_manual(self):
        text = "# 守ること\n安全の決まり\n# 台本の決まり\n巨大な実装の手引き\n# 利用者の依頼\n依頼\n# 原稿\n原稿"
        prompt = M.planning_prompt(text)
        self.assertIn('安全の決まり', prompt)
        self.assertIn('依頼', prompt)
        self.assertIn('原稿', prompt)
        self.assertNotIn('巨大な実装の手引き', prompt)
        self.assertIn('config', prompt)

    def test_progress_uses_saved_scene_count(self):
        self.assertIn('3 場面中 2 場面', planning_message('writing', {'phase': 'motion_scenes', 'done': 2, 'total': 3}))
        self.assertIn('共通の見た目', planning_message('writing', {'phase': 'motion_planning'}))
