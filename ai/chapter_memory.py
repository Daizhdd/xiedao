"""Retryable chapter summaries, rolling memory, and registration."""
import task_ledger as ledger
from ai import prompts, story_memory, tools as atools, fallback as fb
from ai.context import memory_fingerprint, MemoryDigest, iter_volume_rows
from ai.usage_budget import BudgetPaused
from ai.task_errors import Stopped

def finish(self, db, cid, name):
    """正文已落盘后的可重试阶段，前缀缓存按正文及摘要版本校验。"""
    ch = db.get_chapter(cid)
    self._active_chapter = cid
    best = ch['content'] or ''
    if not best.strip():
        return
    rows = [r for r in db.get_chapter_headers(self.pid) if r['volume'] == ch['volume']]
    pos = next(i for i, r in enumerate(rows) if r['id'] == cid)
    if (not ch['memory_pending'] and (ch['summary'] or '').strip()
            and ch['prefix_summary']
            and ch['prefix_hash'] == memory_fingerprint(iter_volume_rows(
                db, self.pid, ch['volume'], ch['chapter_no'], cid))):
        if not any(r['content_length'] for r in rows[pos + 1:]):
            db.set_volume_summary(self.pid, ch['volume'], ch['prefix_summary'])
        return
    digest, rollup = MemoryDigest(), ''
    for row in iter_volume_rows(db, self.pid, ch['volume'], ch['chapter_no'], cid):
        self._active_chapter = row['id']
        if self._stop:
            raise Stopped()
        if (row['content'] or '').strip() and not (row['summary'] or '').strip():
            summary = (self._call(prompts.SYSTEM_SUMMARY,
                                 prompts.summary_prompt(row['content']), temperature=0.3) or '').strip()
            if not summary:
                raise RuntimeError('模型未返回章摘要，可再次运行补全')
            db.update_chapter_meta(row['id'], summary=summary)
            row = db.get_chapter(row['id'])
        digest.add(row)
        fingerprint = digest.hexdigest()
        if row['prefix_hash'] == fingerprint and row['prefix_summary']:
            rollup = row['prefix_summary']
            continue
        if (row['summary'] or '').strip():
            rollup = (self._call(prompts.SYSTEM_ROLLUP,
                                prompts.rollup_prompt(rollup, row['chapter_no'],
                                                     row['title'], row['summary']),
                                temperature=0.3) or '').strip()
            if not rollup:
                raise RuntimeError('模型未返回卷摘要，可再次运行补全')
        db.update_chapter_meta(row['id'], prefix_summary=rollup, prefix_hash=fingerprint)
    self._active_chapter = cid

    # 只有覆盖所有已有正文时才更新全卷摘要，重写前章不能污染整卷缓存。
    if not any(r['content_length'] for r in rows[pos + 1:]):
        db.set_volume_summary(self.pid, ch['volume'], rollup)
    else:
        db.set_volume_summary(self.pid, ch['volume'], '')

    # 5) 登记 agent：自主登记新设定/新伏笔/回收伏笔（工具调用，失败不伤正文）
    if self._tools_ok and ch['memory_pending']:
        if self.task_run_id:
            ledger.record_phase(db, self.task_run_id, cid, '设定伏笔登记', 'running',
                                input_hash=story_memory.body_hash(best))
        try:
            self.progress.emit('  ⏳ 当前步骤：设定伏笔登记')
            extra = {'budget': self._budget} if self._budget is not None else {}
            events = fb.guard(
                "登记", self.cfg,
                lambda c: atools.register_chapter(
                    c, db, self.pid, ch["chapter_no"], name, best,
                    log=lambda m: self.progress.emit(m),
                    cancel_event=self._cancel_event, chapter_id=cid, **extra),
                on_switch=self.progress.emit, configs=self._fallback_configs)
            if not events:
                self.progress.emit("  📄 本章无可登记内容")
            if self.task_run_id:
                ledger.record_phase(db, self.task_run_id, cid, '设定伏笔登记', 'completed')
        except BudgetPaused:
            if self.task_run_id:
                ledger.record_phase(db, self.task_run_id, cid, '设定伏笔登记', 'paused',
                                    error_kind='BudgetPaused')
            raise
        except Exception as e:  # noqa
            if self._stop:
                raise Stopped() from e
            self._tools_ok = False
            self.progress.emit(
                f"  ⚠ 登记不可用（{e}），后续章节跳过该步；正文不受影响")
            if self.task_run_id:
                ledger.record_phase(db, self.task_run_id, cid, '设定伏笔登记', 'failed',
                                    error_kind=type(e).__name__)
        finally:
            if self.task_run_id and self._budget is not None:
                self._save_usage(db)

    auto_extract = (self._auto_extract if self._auto_extract is not None else
                    db.get_setting(story_memory.AUTO_EXTRACT_SETTING, 'off') == 'on')
    if self.use_tools and ch['memory_pending'] and auto_extract:
        if self.task_run_id:
            ledger.record_phase(db, self.task_run_id, cid, '故事事实提取', 'running',
                                input_hash=story_memory.body_hash(best))
        try:
            count = story_memory.extract_candidates(
                db, cid, lambda system, user: self._call(
                    system, user, temperature=0.2, retries=0))
            if count:
                self.progress.emit(f'  📖 提取 {count} 条故事事实候选，等待作者确认')
            if self.task_run_id:
                ledger.record_phase(db, self.task_run_id, cid, '故事事实提取', 'completed')
        except BudgetPaused:
            if self.task_run_id:
                ledger.record_phase(db, self.task_run_id, cid, '故事事实提取', 'paused',
                                    error_kind='BudgetPaused')
            raise
        except Exception as e:  # noqa
            self.progress.emit(f'  ⚠ 故事事实提取失败（{e}），可在章节编辑器重试')
            if self.task_run_id:
                ledger.record_phase(db, self.task_run_id, cid, '故事事实提取', 'failed',
                                    error_kind=type(e).__name__)

    # 完成记录在工作线程持久化，不能依赖 UI 消费信号后才落盘。
    # 先记完成再清 pending：任一时刻退出都能跳过正文或恢复后处理。
    if self.task_run_id:
        ledger.record_step(db, self.task_run_id, f'chapter:{cid}', 'completed', name)
    db.update_chapter_meta(cid, memory_pending=0)
    self.chapter_done.emit(cid, name)
