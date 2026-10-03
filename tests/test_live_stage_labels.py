"""Live audit: review and registration must not look like a stalled writer."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ai import client, fallback, review, story_memory, tools
from ai.pipeline import PipelineWorker
from db import DB


class LiveStageLabelTests(unittest.TestCase):
    def setUp(self):
        fallback.reset()

    def tearDown(self):
        fallback.reset()

    def test_review_announces_current_stage_before_network_call(self):
        worker = PipelineWorker({}, 'unused.db', 1, 1)
        messages = []
        worker.progress.connect(messages.append)
        def inspect(*args, **kwargs):
            self.assertTrue(any('当前步骤：审稿' in message for message in messages))
            return 90, []
        with patch.object(review, 'review_chapter', side_effect=inspect):
            self.assertEqual(worker._safe_review('上下文', '正文'), (90, []))

    def test_registry_announces_stage_and_does_not_relabel_as_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(str(Path(tmp) / 'synthetic.db'))
            pid = db.create_project('阶段验证')
            cid = db.create_chapter(pid, 1, 1, '第一章', content='合成正文')
            db.update_chapter_meta(cid, memory_pending=1)
            worker = PipelineWorker({}, db.path, pid, 1, use_tools=True)
            worker._auto_extract = False
            messages = []
            worker.progress.connect(messages.append)
            def registry(*args, **kwargs):
                self.assertIn('当前步骤：设定伏笔登记', messages[-1])
                return []
            with patch.object(worker, '_call', return_value='摘要'), \
                    patch.object(tools, 'register_chapter', side_effect=registry):
                worker._finish_chapter(db, cid, '第一章')
            db.close()

    def test_fact_extraction_displays_purpose_without_changing_ledger_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(str(Path(tmp) / 'synthetic.db'))
            worker = PipelineWorker({}, db.path, 1, 1)
            worker._active_db = db
            worker.task_run_id = 1
            messages = []
            worker.progress.connect(messages.append)
            with patch('ai.model_calls.ledger.record_phase'), patch.object(client, '_log_api'), \
                    patch.object(worker, '_call_chain', return_value='{"facts": []}'):
                worker._call(story_memory.SYSTEM_EXTRACT, '合成正文')
            self.assertTrue(any('当前步骤：故事事实提取' in message for message in messages))
            db.close()


if __name__ == '__main__':
    unittest.main()
