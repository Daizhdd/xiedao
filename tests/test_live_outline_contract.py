"""Offline regressions from live MiMo's repeated volume-stage output."""
import unittest
from unittest.mock import patch
from ai import parse, prompts
from ai.pipeline import PipelineWorker


class LiveOutlineContractTests(unittest.TestCase):
    def test_numbered_stage_goals_preserve_parent_instead_of_duplicate_volumes(self):
        text = ('- 第1卷 钟楼守夜人：调查钟楼并阻止失忆。\n\n'
                '- 第1卷阶段目标1（第1-3章·约900字/章）：锁定钟楼为事件源头。\n\n'
                '- 第1卷阶段目标2（第4-7章）：追查钥匙来源。\n\n'
                '- 第2卷 新线索：追查齿轮的来源。\n'
                '- 第2卷阶段目标1（第8-9章）：确认齿轮来历。')
        items = parse.parse_batch(text, '卷纲')
        self.assertEqual([d['title'] for _, d in items], ['第1卷 钟楼守夜人', '第2卷 新线索'])
        self.assertIn('锁定钟楼', items[0][1]['content'])
        self.assertIn('追查钥匙', items[0][1]['content'])
        self.assertNotIn('确认齿轮', items[0][1]['content'])
        self.assertIn('确认齿轮', items[1][1]['content'])

    def test_true_duplicate_volumes_are_reformatted_instead_of_silently_dropped(self):
        worker = PipelineWorker({}, 'unused.db', 1, 1)
        text = '- 第1卷 第一稿：方向甲\n- 第1卷 第二稿：方向乙'
        with patch.object(worker, '_call', return_value='- 第1卷 明确方案：合并必要情节') as repair:
            items = worker._parse_with_repair(text, '卷纲', expected_range=(1, 1))
        self.assertEqual(len(items), 1)
        repair.assert_called_once()
        self.assertIn('方向甲', repair.call_args.args[1])
        self.assertIn('方向乙', repair.call_args.args[1])

    def test_only_stage_goal_without_a_parent_is_not_a_valid_volume(self):
        items = parse.parse_batch('- 第1卷阶段目标1（第1章）：阶段内容', '卷纲')
        self.assertFalse(PipelineWorker._items_sane(items, '卷纲'))

    def test_missing_or_extra_chapter_numbers_require_one_repair_before_acceptance(self):
        worker = PipelineWorker({}, 'unused.db', 1, 1)
        with patch.object(worker, '_call', return_value='- 第1章 起步：调查\n- 第2章 结局：修钟') as repair:
            items = worker._parse_with_repair('- 第1章 起步：调查\n- 第3章 越界：额外情节',
                '章纲', start_num=1, expected_range=(1, 2))
        self.assertEqual([d['title'] for _, d in items], ['第1章 起步', '第2章 结局'])
        repair.assert_called_once()

    def test_bad_repair_is_rejected_before_outline_persistence(self):
        worker = PipelineWorker({}, 'unused.db', 1, 1)
        with patch.object(worker, '_call', return_value='- 第1章 重复：甲\n- 第1章 重复：乙'):
            with self.assertRaisesRegex(RuntimeError, '两次解析失败'):
                worker._parse_with_repair('- 第1章 仅一章：缺第二章', '章纲', expected_range=(1, 2))

    def test_volume_prompt_uses_book_length_and_requires_single_entry_per_volume(self):
        prompt = prompts.gen_volumes_plan({'title': '测试', 'plan_chapters': 2}, '', total=1)
        self.assertIn('每卷 2 章', prompt)
        self.assertIn('最终只输出 1 行', prompt)
        self.assertIn('禁止另列', prompt)


if __name__ == '__main__':
    unittest.main()
