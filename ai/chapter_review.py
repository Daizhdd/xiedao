"""Evidence-backed review and rewrite selection."""
import hashlib
import task_ledger as ledger
from ai import prompts, prefs as aprefs, fallback as fb, review as areview, story_memory, evidence_review
from ai.usage_budget import BudgetPaused
from ai.task_errors import Stopped

def safe_review(self, user, text, db=None, chapter=None):
    """审稿失败/乱码不再当作通过：返回 (None, [])，由 _do_chapter 把章节
    标注为「AI草稿·未审」；默认模型连续异常时自动换用其他已配置模型。"""
    kwargs = {'log': self.progress.emit,
              'thinking': aprefs.thinking_for(self._prefs, 'reviewer')}
    if db is not None and chapter is not None:
        kwargs['sources'] = evidence_review.sources_for(db, chapter)
        def remember_review(key, raw, detail):
            self._review_payloads[key] = raw
            self._review_blocks[key] = evidence_review.has_unresolved_severe(
                detail['issues'])
        kwargs['on_structured'] = remember_review
    if self._budget is not None:
        kwargs['budget'] = self._budget
        kwargs['cancel_event'] = self._cancel_event
    if db is not None and chapter is not None and self.task_run_id:
        ledger.record_phase(db, self.task_run_id, chapter['id'], '审稿', 'running',
                            input_hash=hashlib.sha256(text.encode('utf-8')).hexdigest())
    try:
        self.progress.emit('  ⏳ 当前步骤：审稿')
        result = fb.guard(
            "审稿", self.cfg,
            lambda c: areview.review_chapter(c, user, text, **kwargs),
            on_switch=self.progress.emit, configs=self._fallback_configs)
        if db is not None and chapter is not None and self.task_run_id:
            ledger.record_phase(db, self.task_run_id, chapter['id'], '审稿',
                                'completed' if result[0] is not None else 'unreviewed')
        return result
    except BudgetPaused:
        raise
    except Exception as e:  # noqa
        if self._stop:
            raise Stopped() from e
        self.progress.emit(f"  ⚠ 审稿失败（{e}），本章将标注为「AI草稿·未审」")
        if db is not None and chapter is not None and self.task_run_id:
            ledger.record_phase(db, self.task_run_id, chapter['id'], '审稿', 'failed',
                                error_kind=type(e).__name__)
        return None, []
    finally:
        if db is not None and self.task_run_id and self._budget is not None:
            self._save_usage(db)


def select_draft(self, db, ch, user, best):
    # 审稿、定向重写和复审共用同一份证据上下文。
    # 严重问题不能由总分抵消；未审稿仍需作者复核。
    final_score = None
    final_blocked = False
    self._review_payloads = {}
    self._review_blocks = {}
    if self.review:
        score, issues = self._safe_review(user, best, db, ch)
        self._issue_pool.extend(issues)
        if score is not None:
            final_score = score
            final_blocked = self._review_blocks.get(story_memory.body_hash(best), False)
            if (score < self.min_score or final_blocked) and issues:
                reason = '有严重问题待核' if final_blocked else f'未过 {self.min_score}'
                self.progress.emit(
                    f"  🔍 审稿 {score} 分（{reason}）：{issues[0][:40]}")
                rw = (self._call(prompts.SYSTEM_WRITER,
                                 areview.rewrite_prompt(user, best, issues),
                                 temperature=0.85) or "").strip()
                if rw:
                    score2, iss2 = self._safe_review(user, rw, db, ch)
                    self._issue_pool.extend(iss2)
                    if score2 is None:
                        # 复审失败时无法证明重写稿更好，宁可用已审稿的初稿
                        self.progress.emit(
                            "  ⚠ 复审未完成，无法比较——保留已审稿的初稿")
                    else:
                        blocked2 = self._review_blocks.get(
                            story_memory.body_hash(rw), False)
                        if (not blocked2, score2) >= (not final_blocked, score):
                            self.progress.emit(f"  🔍 复审 {score2} 分，采用重写稿")
                            best = rw
                            final_score = score2
                            final_blocked = blocked2
                            self.n_rewrites += 1
                        else:
                            self.progress.emit(
                                f"  🔍 复审 {score2} 分仍有更严重的问题，保留初稿")
            elif final_score >= self.min_score:
                self.progress.emit(f"  🔍 审稿 {final_score} 分，通过")
            else:
                self.progress.emit(
                    f"  🔍 审稿 {final_score} 分（低于门槛且未给出问题清单，"
                    "无法定向重写）")
        else:
            self._review_stats["unreviewed"] += 1
            self.progress.emit("  ⚠ 本章未完成审稿，质量未知，请优先复核")
    else:
        self._review_stats["unreviewed"] += 1
    if final_score is None:
        chapter_status = "AI草稿·未审"
    elif final_blocked:
        chapter_status = "AI草稿·待核"
        self._review_stats["blocked"] += 1
    elif final_score < self.min_score:
        chapter_status = "AI草稿·低分"
        self._review_stats["low"] += 1
    else:
        chapter_status = "AI草稿"
        self._review_stats["passed"] += 1

    return best, chapter_status
